from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
import socket
import threading
import time
from dataclasses import dataclass
from typing import Iterable

SESSION_COOKIE = "papyrus_web_session"
CSRF_HEADER = "x-papyrus-csrf"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def private_interfaces() -> list[dict[str, str]]:
    """Return concrete bindable addresses; never return a wildcard address."""
    found: dict[str, str] = {"127.0.0.1": "This computer only"}
    candidates: set[str] = set()
    # psutil reads the operating system's interface table directly and is more
    # reliable than hostname resolution on machines whose hostname maps to
    # 127.0.1.1. Papyrus already ships psutil; keep this optional for lean dev
    # environments.
    try:
        import psutil
        for addresses in psutil.net_if_addrs().values():
            for address in addresses:
                if address.family == socket.AF_INET:
                    candidates.add(address.address)
    except (ImportError, OSError, PermissionError):
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET, socket.SOCK_STREAM):
            candidates.add(info[4][0])
    except OSError:
        pass
    # UDP connect performs route selection without sending a packet.
    for probe in ("10.255.255.255", "192.168.255.255"):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        except OSError:
            continue
        try:
            sock.connect((probe, 9))
            candidates.add(sock.getsockname()[0])
        except OSError:
            pass
        finally:
            sock.close()
    for address in candidates:
        try:
            ip = ipaddress.ip_address(address)
            if ip.version == 4 and (ip.is_private or ip.is_loopback) and not ip.is_unspecified:
                found[address] = "Local network — phones and laptops" if not ip.is_loopback else "This computer only — phones cannot connect"
        except ValueError:
            continue
    return [{"address": address, "label": label} for address, label in sorted(found.items(), key=lambda item: item[0] != "127.0.0.1")]


@dataclass
class Session:
    session_id: str
    csrf: str
    device_name: str
    remote_ip: str
    user_agent_hash: str
    created_at: float
    expires_at: float


class AuthManager:
    def __init__(self, session_ttl: int = 12 * 60 * 60, pair_ttl: int = 5 * 60):
        self.session_ttl = session_ttl
        self.pair_ttl = pair_ttl
        self._lock = threading.RLock()
        self._sessions: dict[str, Session] = {}
        self._attempts: dict[str, list[float]] = {}
        self.rotate_pairing()

    def rotate_pairing(self) -> tuple[str, str]:
        with self._lock:
            self._pair_token = secrets.token_urlsafe(32)
            self._pair_code = f"{secrets.randbelow(1_000_000):06d}"
            self._pair_expires = time.time() + self.pair_ttl
            self._pair_used = False
            return self._pair_token, self._pair_code

    @property
    def pair_token(self) -> str:
        return self._pair_token

    @property
    def pair_code(self) -> str:
        return self._pair_code

    @property
    def pair_expires(self) -> float:
        return self._pair_expires

    def pair(self, presented: str, device_name: str, remote_ip: str, user_agent: str) -> Session | None:
        now = time.time()
        with self._lock:
            attempts = [t for t in self._attempts.get(remote_ip, []) if now - t < 60]
            if len(attempts) >= 8:
                return None
            attempts.append(now)
            self._attempts[remote_ip] = attempts
            valid = (
                not self._pair_used
                and now < self._pair_expires
                and (hmac.compare_digest(presented, self._pair_token) or hmac.compare_digest(presented, self._pair_code))
            )
            if not valid:
                return None
            self._pair_used = True
            session_id = secrets.token_urlsafe(32)
            session = Session(
                session_id=session_id,
                csrf=secrets.token_urlsafe(24),
                device_name=(device_name or "Browser")[:80],
                remote_ip=remote_ip,
                user_agent_hash=_digest(user_agent or ""),
                created_at=now,
                expires_at=now + self.session_ttl,
            )
            self._sessions[_digest(session_id)] = session
            return session

    def authenticate(self, session_id: str | None, remote_ip: str, user_agent: str) -> Session | None:
        if not session_id:
            return None
        now = time.time()
        with self._lock:
            session = self._sessions.get(_digest(session_id))
            if not session or session.expires_at <= now:
                if session:
                    self._sessions.pop(_digest(session_id), None)
                return None
            if session.remote_ip != remote_ip or not hmac.compare_digest(session.user_agent_hash, _digest(user_agent or "")):
                return None
            return session

    def validate_csrf(self, session: Session, token: str | None) -> bool:
        return bool(token and hmac.compare_digest(session.csrf, token))

    def revoke(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(_digest(session_id), None)

    def revoke_hash(self, session_hash: str) -> None:
        with self._lock:
            self._sessions.pop(session_hash, None)

    def revoke_prefix(self, prefix: str) -> bool:
        with self._lock:
            key = next((item for item in self._sessions if item.startswith(prefix)), None)
            if not key:
                return False
            self._sessions.pop(key, None)
            return True

    def revoke_all(self) -> None:
        with self._lock:
            self._sessions.clear()
            self.rotate_pairing()

    def list_sessions(self) -> list[dict]:
        now = time.time()
        with self._lock:
            expired = [key for key, value in self._sessions.items() if value.expires_at <= now]
            for key in expired:
                self._sessions.pop(key, None)
            return [
                {
                    "id": key[:12],
                    "device_name": value.device_name,
                    "remote_ip": value.remote_ip,
                    "created_at": value.created_at,
                    "expires_at": value.expires_at,
                }
                for key, value in self._sessions.items()
            ]


def allowed_hosts(bind_host: str, port: int) -> set[str]:
    values = {bind_host, f"{bind_host}:{port}", "localhost", f"localhost:{port}"}
    if bind_host == "127.0.0.1":
        values |= {"127.0.0.1", f"127.0.0.1:{port}"}
    return values
