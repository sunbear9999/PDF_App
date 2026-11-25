"""Permissioned, SSRF-resistant outbound HTTP for plugins."""
from __future__ import annotations

import ipaddress
import json
import os
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional
from urllib.parse import urljoin, urlparse

import requests


DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_MAX_BYTES = 8 * 1024 * 1024
DOWNLOAD_CHUNK_BYTES = 64 * 1024
MAX_REDIRECTS = 5
MIN_HOST_INTERVAL_SECONDS = 0.25


class NetworkAccessError(RuntimeError):
    """Base error for denied or failed plugin network requests."""


class NetworkPermissionError(NetworkAccessError):
    """Raised when the user has not granted network access."""


class UnsafeNetworkTargetError(NetworkAccessError):
    """Raised for unsafe schemes, hosts, redirects, or response sizes."""


@dataclass(frozen=True)
class NetworkResponse:
    url: str
    status_code: int
    headers: Dict[str, str]
    content: bytes

    def json(self) -> Any:
        return json.loads(self.content.decode("utf-8"))


class NetworkAccessService:
    """Central outbound-request gate for plugins.

    Grants are user-controlled and persisted outside project files. Each
    request is HTTPS-only, DNS checked against non-public addresses, bounded,
    and revalidated after redirects.
    """

    def __init__(self, user_data_dir: str) -> None:
        self._path = Path(user_data_dir) / "plugin_network_permissions.json"
        self._lock = threading.RLock()
        self._grants = self._load_grants()
        self._sessions: Dict[tuple[str, int], requests.Session] = {}
        self._host_next_request: Dict[str, float] = {}
        self._rate_lock = threading.Lock()

    def is_granted(self, plugin_id: str) -> bool:
        with self._lock:
            return bool(self._grants.get(plugin_id, False))

    def set_granted(self, plugin_id: str, granted: bool) -> None:
        with self._lock:
            self._grants[plugin_id] = bool(granted)
            self._save_grants()

    def close_plugin(self, plugin_id: str) -> None:
        """Close pooled connections owned by an unloading plugin."""
        with self._lock:
            keys = [key for key in self._sessions if key[0] == plugin_id]
            for key in keys:
                self._sessions.pop(key).close()

    def request(
        self,
        plugin_id: str,
        url: str,
        *,
        allowed_hosts: Iterable[str],
        allow_unlisted_host: bool = False,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_bytes: int = DEFAULT_MAX_BYTES,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> NetworkResponse:
        self._require_grant(plugin_id)
        self._require_background_thread()
        allowed = self._normalize_hosts(allowed_hosts)
        self._validate_url(url, allowed, allow_unlisted_host)
        response = None
        try:
            response = self._open_response(
                plugin_id,
                url,
                params=params,
                headers=dict(headers or {}),
                timeout=(min(timeout, 10.0), timeout),
                allowed_hosts=allowed,
                allow_unlisted_host=allow_unlisted_host,
            )
            response.raise_for_status()
            content = self._read_bounded(response, max_bytes, cancel_check)
            return NetworkResponse(
                url=response.url,
                status_code=response.status_code,
                headers={str(k): str(v) for k, v in response.headers.items()},
                content=content,
            )
        except NetworkAccessError:
            raise
        except requests.RequestException as exc:
            raise NetworkAccessError(self._safe_error(exc)) from exc
        finally:
            if response is not None:
                response.close()

    def download(
        self, plugin_id: str, url: str, destination: str, *,
        allowed_hosts: Iterable[str], allow_unlisted_host: bool = False,
        timeout: float = DEFAULT_TIMEOUT_SECONDS, max_bytes: int = 250 * 1024 * 1024,
        headers: Optional[Dict[str, str]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        progress: Optional[Callable[[int, int, str], None]] = None,
    ) -> Dict[str, Any]:
        self._require_grant(plugin_id)
        self._require_background_thread()
        allowed = self._normalize_hosts(allowed_hosts)
        self._validate_url(url, allowed, allow_unlisted_host)
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(f"{target.name}.part")
        received = 0
        response = None
        try:
            response = self._open_response(
                plugin_id,
                url,
                headers=dict(headers or {}),
                timeout=(min(timeout, 10.0), timeout),
                allowed_hosts=allowed,
                allow_unlisted_host=allow_unlisted_host,
            )
            response.raise_for_status()
            total = int(response.headers.get("content-length", "0") or 0)
            if total > max_bytes:
                raise UnsafeNetworkTargetError("Download exceeds the configured size limit.")
            received = self._write_response(
                response, partial, total, max_bytes, cancel_check, progress
            )
            os.replace(partial, target)
            return {
                "path": str(target),
                "url": response.url,
                "content_type": response.headers.get("content-type", ""),
                "size": received,
            }
        except NetworkAccessError:
            raise
        except requests.RequestException as exc:
            raise NetworkAccessError(self._safe_error(exc)) from exc
        finally:
            if response is not None:
                response.close()
            if partial.exists():
                partial.unlink(missing_ok=True)

    def _write_response(
        self, response, partial, total, max_bytes, cancel_check, progress
    ) -> int:
        received = 0
        with partial.open("wb") as stream:
            for chunk in response.iter_content(DOWNLOAD_CHUNK_BYTES):
                if cancel_check and cancel_check():
                    raise NetworkAccessError("Request cancelled.")
                if not chunk:
                    continue
                received += len(chunk)
                if received > max_bytes:
                    raise UnsafeNetworkTargetError("Download exceeded the configured size limit.")
                stream.write(chunk)
                if progress:
                    progress(received, total, "Downloading archive asset")
        return received

    def _open_response(
        self,
        plugin_id: str,
        url: str,
        *,
        headers,
        timeout,
        allowed_hosts,
        allow_unlisted_host,
        params=None,
    ):
        """Follow a small redirect chain, validating before every connection."""
        current = url
        current_params = params
        for _redirect in range(MAX_REDIRECTS + 1):
            self._validate_url(current, allowed_hosts, allow_unlisted_host)
            self._pace_host(urlparse(current).hostname or "")
            response = self._session(plugin_id).get(
                current,
                params=current_params,
                headers=headers,
                timeout=timeout,
                stream=True,
                allow_redirects=False,
            )
            if response.status_code not in {301, 302, 303, 307, 308}:
                return response
            location = response.headers.get("location", "")
            if not location:
                return response
            current = urljoin(response.url, location)
            current_params = None
            response.close()
        raise UnsafeNetworkTargetError("Archive returned too many redirects.")

    def _pace_host(self, host: str) -> None:
        """Apply a small global per-host delay; network calls already run off-thread."""
        with self._rate_lock:
            now = time.monotonic()
            wait = self._host_next_request.get(host, now) - now
            if wait > 0:
                time.sleep(wait)
            self._host_next_request[host] = time.monotonic() + MIN_HOST_INTERVAL_SECONDS

    def _read_bounded(self, response, max_bytes, cancel_check) -> bytes:
        declared = int(response.headers.get("content-length", "0") or 0)
        if declared > max_bytes:
            raise UnsafeNetworkTargetError("Response exceeds the configured size limit.")
        chunks = []
        size = 0
        for chunk in response.iter_content(DOWNLOAD_CHUNK_BYTES):
            if cancel_check and cancel_check():
                raise NetworkAccessError("Request cancelled.")
            if not chunk:
                continue
            size += len(chunk)
            if size > max_bytes:
                raise UnsafeNetworkTargetError("Response exceeded the configured size limit.")
            chunks.append(chunk)
        return b"".join(chunks)

    def _validate_url(
        self, url: str, allowed_hosts: set[str], allow_unlisted_host: bool
    ) -> None:
        parsed = urlparse(str(url or ""))
        if parsed.scheme.lower() != "https" or not parsed.hostname:
            raise UnsafeNetworkTargetError("Only public HTTPS URLs are allowed.")
        if parsed.username or parsed.password:
            raise UnsafeNetworkTargetError("Credentials in URLs are not allowed.")
        host = parsed.hostname.lower().rstrip(".")
        if not allow_unlisted_host and not self._host_is_allowed(host, allowed_hosts):
            raise UnsafeNetworkTargetError(f"Host is not declared by this plugin: {host}")
        try:
            addresses = socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise NetworkAccessError(f"Could not resolve archive host: {host}") from exc
        for address in {item[4][0] for item in addresses}:
            ip = ipaddress.ip_address(address)
            if not ip.is_global:
                raise UnsafeNetworkTargetError("Private or local network targets are blocked.")

    def _session(self, plugin_id: str) -> requests.Session:
        with self._lock:
            key = (plugin_id, threading.get_ident())
            if key not in self._sessions:
                session = requests.Session()
                session.headers.update({
                    "User-Agent": "Papyrus-Historical-Archives/1.0 (+local research client)",
                    "Accept": "application/json",
                })
                self._sessions[key] = session
            return self._sessions[key]

    def _require_grant(self, plugin_id: str) -> None:
        if not self.is_granted(plugin_id):
            raise NetworkPermissionError(
                "Network access is blocked. Grant it in Tools → Plugin Manager."
            )

    @staticmethod
    def _require_background_thread() -> None:
        if threading.current_thread() is threading.main_thread():
            raise NetworkAccessError("Plugin network requests must run in a background task.")

    def _load_grants(self) -> Dict[str, bool]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            return {str(k): bool(v) for k, v in raw.items()} if isinstance(raw, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_grants(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp = self._path.with_suffix(".tmp")
        temp.write_text(json.dumps(self._grants, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temp, self._path)

    @staticmethod
    def _normalize_hosts(hosts: Iterable[str]) -> set[str]:
        return {str(host).lower().strip().rstrip(".") for host in hosts if str(host).strip()}

    @staticmethod
    def _host_is_allowed(host: str, allowed: set[str]) -> bool:
        return any(host == item or host.endswith(f".{item}") for item in allowed)

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        response = getattr(exc, "response", None)
        code = getattr(response, "status_code", None)
        if code == 429:
            return "Archive rate limit reached. Wait before trying again."
        if code in {401, 403}:
            return "Archive denied this request or requires different access."
        if code and code >= 500:
            return f"Archive provider is temporarily unavailable (HTTP {code})."
        return f"Archive request failed (HTTP {code})." if code else "Archive request failed."
