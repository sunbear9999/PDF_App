from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from core.services.reference.citation_sync import CitationSyncResult


DEFAULT_ZOTERO_LOCAL_API_BASE_URL = "http://localhost:23119/api/"
PYZOTERO_DOCS_URL = "https://pyzotero.readthedocs.io/en/latest/"
ZOTERO_API_KEYS_URL = "https://www.zotero.org/settings/keys"


@dataclass
class PyZoteroProbeResult:
    package_available: bool = False
    local_api_available: bool = False
    write_capable: bool = False
    message: str = ""
    details: Dict[str, Any] = field(default_factory=dict)


def default_zotero_settings() -> Dict[str, Any]:
    return {
        "auto_add_pdfs_to_zotero": False,
        "pyzotero_local_api_base_url": DEFAULT_ZOTERO_LOCAL_API_BASE_URL,
        "pyzotero_library_id": "",
        "pyzotero_library_type": "user",
        "pyzotero_api_key": "",
        "target_collection_mode": "project_named",
        "target_collection_key": "",
        "target_collection_name": "",
    }


def get_zotero_setting(config: Any, key: str) -> Any:
    return config.get(key, default_zotero_settings().get(key))


class PyZoteroClient:
    """Small plugin-local wrapper around PyZotero and Zotero's local API."""

    def __init__(
        self,
        *,
        library_id: str = "",
        library_type: str = "user",
        api_key: str = "",
        local_api_base_url: str = DEFAULT_ZOTERO_LOCAL_API_BASE_URL,
        timeout: float = 1.5,
        zotero_factory: Any = None,
    ):
        self.library_id = str(library_id or "").strip()
        self.library_type = str(library_type or "user").strip() or "user"
        self.api_key = str(api_key or "").strip()
        self.local_api_base_url = (local_api_base_url or DEFAULT_ZOTERO_LOCAL_API_BASE_URL).rstrip("/") + "/"
        self.timeout = timeout
        self._zotero_factory = zotero_factory
        self._remote = None
        self._last_probe: Optional[PyZoteroProbeResult] = None

    def probe(self) -> PyZoteroProbeResult:
        package_available = self._load_zotero_factory() is not None
        local_available = self._probe_local_api()
        has_write_credentials = bool(self.library_id and self.api_key and self.library_type)
        write_ok = bool(package_available and has_write_credentials)
        details = {"library_type": self.library_type, "library_id": self.library_id}
        if not package_available:
            message = "PyZotero is not installed in this Papyrus environment."
        elif write_ok:
            message = "PyZotero write sync is configured. The Web API will be used only when adding PDFs."
        elif local_available:
            message = (
                "Local Zotero communication is enabled. Metadata sync works locally; "
                "PDF write sync also needs a Zotero API key with write access."
            )
        else:
            message = (
                "Zotero local communication is not reachable. In Zotero, enable Settings > "
                "Advanced > Allow other applications on this computer to communicate with Zotero."
            )
        self._last_probe = PyZoteroProbeResult(
            package_available=package_available,
            local_api_available=local_available,
            write_capable=write_ok,
            message=message,
            details=details,
        )
        return self._last_probe

    def can_write(self) -> bool:
        result = self._last_probe or self.probe()
        return bool(result.write_capable)

    def list_collections(self) -> List[Dict[str, Any]]:
        try:
            collections = self._remote_client().collections()
        except Exception:
            return []
        return [self._normalize_collection(c) for c in collections if isinstance(c, dict)]

    def list_local_items(self, limit: int = 250) -> List[Dict[str, Any]]:
        """Read Zotero items from the local API without using the Web API."""
        queries = [
            {"limit": str(limit), "itemType": "-attachment || note"},
            {"limit": str(limit)},
        ]
        last_error = ""
        for query in queries:
            try:
                data = self._local_get_json("users/0/items", query)
                if isinstance(data, list):
                    return [
                        self._normalize_api_item(item)
                        for item in data
                        if isinstance(item, dict)
                        and self._api_item_type(item) not in {"attachment", "note"}
                    ]
            except RuntimeError as exc:
                last_error = str(exc)
                continue
        if last_error:
            raise RuntimeError(last_error)
        return []

    def list_local_collections(self) -> List[Dict[str, Any]]:
        try:
            data = self._local_get_json("users/0/collections", {})
        except RuntimeError:
            return []
        if not isinstance(data, list):
            return []
        return [self._normalize_collection(item) for item in data if isinstance(item, dict)]

    def ensure_collection(self, collection_name: str) -> str:
        if not collection_name:
            return ""
        for collection in self.list_collections():
            if collection.get("name") == collection_name:
                return str(collection.get("key") or collection_name)
        response = self._remote_client().create_collections([{"name": collection_name}])
        return self._created_key(response) or collection_name

    def add_pdf(
        self,
        pdf_path: str,
        citation: Dict[str, Any],
        *,
        collection_key: str = "",
        collection_name: str = "",
    ) -> Dict[str, Any]:
        if not os.path.isfile(pdf_path):
            raise RuntimeError(f"PDF does not exist: {pdf_path}")
        zot = self._remote_client()
        item = self._citation_to_item(citation, pdf_path)
        if collection_key:
            item["collections"] = [collection_key]
        created = zot.create_items([item])
        parent_key = self._created_key(created)
        if not parent_key:
            raise RuntimeError(f"Zotero item creation did not return a key: {created}")
        attachment_result = zot.attachment_simple([pdf_path], parentid=parent_key)
        if collection_key:
            try:
                parent_item = zot.item(parent_key)
                zot.addto_collection(collection_key, parent_item)
            except Exception:
                pass
        return {
            "key": parent_key,
            "item": dict(item, key=parent_key, itemType=item.get("itemType", "journalArticle")),
            "attachment": attachment_result,
            "collection": collection_key or collection_name,
        }

    def _load_zotero_factory(self):
        if self._zotero_factory:
            return self._zotero_factory
        try:
            from pyzotero import zotero
        except ImportError:
            return None
        self._zotero_factory = zotero.Zotero
        return self._zotero_factory

    def _remote_client(self):
        if self._remote is None:
            factory = self._load_zotero_factory()
            if factory is None:
                raise RuntimeError("PyZotero is not installed.")
            if not self.library_id or not self.api_key:
                raise RuntimeError("Zotero library ID and API key are required for write sync.")
            self._remote = factory(self.library_id, self.library_type, self.api_key)
        return self._remote

    def _probe_local_api(self) -> bool:
        url = urllib.parse.urljoin(self.local_api_base_url, "")
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout):
                return True
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
            return False

    def _local_get_json(self, path: str, query: Dict[str, str]) -> Any:
        encoded = urllib.parse.urlencode(query)
        url = urllib.parse.urljoin(self.local_api_base_url, path.lstrip("/"))
        if encoded:
            url = f"{url}?{encoded}"
        req = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "Zotero-API-Version": "3"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8") or "[]")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(str(exc)) from exc

    def _normalize_collection(self, collection: Dict[str, Any]) -> Dict[str, Any]:
        data = collection.get("data") if isinstance(collection.get("data"), dict) else collection
        return {
            "key": data.get("key") or collection.get("key"),
            "name": data.get("name") or collection.get("name") or "(Untitled Collection)",
            "parent": data.get("parentCollection") or collection.get("parentCollection"),
        }

    def _normalize_api_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
        data = item.get("data") if isinstance(item.get("data"), dict) else item
        key = data.get("key") or item.get("key") or ""
        creators = []
        for creator in data.get("creators") or []:
            if not isinstance(creator, dict):
                continue
            creators.append({
                "first": creator.get("firstName") or creator.get("first") or "",
                "last": creator.get("lastName") or creator.get("last") or creator.get("name") or "",
                "type": creator.get("creatorType") or creator.get("type") or "author",
            })
        authors_display = "; ".join(
            f"{c['last']}, {c['first']}" if c.get("first") else c.get("last", "")
            for c in creators
            if c.get("type") in ("author", "editor", "seriesEditor") and (c.get("last") or c.get("first"))
        )
        date = data.get("date", "") or ""
        return {
            "item_id": key,
            "item_type": data.get("itemType") or "",
            "key": key,
            "doc_id": f"zotero:{key}",
            "title": data.get("title", ""),
            "date": date,
            "year": date[:4],
            "publicationTitle": data.get("publicationTitle", ""),
            "publisher": data.get("publisher", ""),
            "volume": data.get("volume", ""),
            "issue": data.get("issue", ""),
            "pages": data.get("pages", ""),
            "DOI": data.get("DOI", ""),
            "url": data.get("url", ""),
            "ISBN": data.get("ISBN", ""),
            "ISSN": data.get("ISSN", ""),
            "abstractNote": data.get("abstractNote", ""),
            "creators": creators,
            "authors_display": authors_display,
            "dateAdded": data.get("dateAdded", ""),
            "dateModified": data.get("dateModified", ""),
            "accessDate": data.get("accessDate", ""),
            "fields": dict(data),
            "_source": "local_api",
            "_has_pdf": False,
        }

    def _api_item_type(self, item: Dict[str, Any]) -> str:
        data = item.get("data") if isinstance(item.get("data"), dict) else item
        return str(data.get("itemType") or data.get("item_type") or "")

    def _created_key(self, response: Any) -> str:
        if isinstance(response, dict):
            success = response.get("success")
            if isinstance(success, dict) and success:
                return str(next(iter(success.values())))
            if response.get("key"):
                return str(response["key"])
        if isinstance(response, list) and response:
            first = response[0]
            if isinstance(first, dict):
                data = first.get("data") if isinstance(first.get("data"), dict) else first
                if data.get("key"):
                    return str(data["key"])
        return ""

    def _citation_to_item(self, citation: Dict[str, Any], pdf_path: str) -> Dict[str, Any]:
        fields = citation.get("fields") if isinstance(citation.get("fields"), dict) else {}
        item_type = fields.get("itemType") or fields.get("item_type") or citation.get("item_type") or "journalArticle"
        item = {
            "itemType": item_type,
            "title": citation.get("title") or os.path.splitext(os.path.basename(pdf_path))[0],
            "creators": self._creators_from_citation(citation),
            "date": citation.get("year") or citation.get("date") or fields.get("date") or "",
            "DOI": citation.get("doi") or citation.get("DOI") or fields.get("DOI") or "",
            "url": citation.get("url") or fields.get("url") or "",
            "abstractNote": citation.get("abstract") or fields.get("abstractNote") or "",
        }
        for target, sources in {
            "publicationTitle": ("journal", "publicationTitle"),
            "volume": ("volume",),
            "issue": ("issue",),
            "pages": ("pages",),
            "publisher": ("publisher",),
            "accessDate": ("accessDate",),
        }.items():
            value = next((citation.get(s) or fields.get(s) for s in sources if citation.get(s) or fields.get(s)), "")
            if value:
                item[target] = value
        return {k: v for k, v in item.items() if v not in ("", None, [])}

    def _creators_from_citation(self, citation: Dict[str, Any]) -> List[Dict[str, str]]:
        creators = citation.get("creators")
        if isinstance(creators, list) and creators:
            normalized = []
            for creator in creators:
                if not isinstance(creator, dict):
                    continue
                normalized.append({
                    "creatorType": creator.get("creatorType") or creator.get("type") or "author",
                    "firstName": creator.get("firstName") or creator.get("first") or "",
                    "lastName": creator.get("lastName") or creator.get("last") or creator.get("name") or "",
                })
            if normalized:
                return normalized
        authors = citation.get("authors") or citation.get("author") or ""
        parsed = []
        for raw in str(authors).replace(" and ", ";").split(";"):
            name = raw.strip()
            if not name:
                continue
            if "," in name:
                last, first = [p.strip() for p in name.split(",", 1)]
            else:
                parts = name.split()
                first, last = (" ".join(parts[:-1]), parts[-1]) if len(parts) > 1 else ("", name)
            parsed.append({"creatorType": "author", "firstName": first, "lastName": last})
        return parsed


class ZoteroReadOnlyAdapter:
    """Safe fallback when PyZotero write settings are incomplete."""

    adapter_id = "zotero.read_only"
    display_name = "Zotero Read-Only"

    def __init__(self, message: str = ""):
        self.message = message or (
            "Sync to Zotero is unavailable until PyZotero write settings are configured; "
            "metadata import/copy is still available."
        )

    def can_write(self) -> bool:
        return False

    def sync_pdfs(
        self,
        pdf_paths: Sequence[str],
        citations: Dict[str, Dict[str, Any]],
        *,
        collection_name: str = "",
    ) -> CitationSyncResult:
        return CitationSyncResult(ok=False, message=self.message, synced_ids=[])


class ZoteroOutboundSyncController:
    """Stable service object that delegates to the current concrete adapter."""

    adapter_id = "zotero.controller"
    display_name = "Zotero Sync"

    def __init__(self, adapter: Any):
        self._adapter = adapter

    def set_adapter(self, adapter: Any) -> None:
        self._adapter = adapter

    def can_write(self) -> bool:
        return bool(self._adapter and self._adapter.can_write())

    def sync_pdfs(
        self,
        pdf_paths: Sequence[str],
        citations: Dict[str, Dict[str, Any]],
        *,
        collection_name: str = "",
    ) -> CitationSyncResult:
        return self._adapter.sync_pdfs(
            pdf_paths,
            citations,
            collection_name=collection_name,
        )


class PyZoteroOutboundSyncAdapter:
    """Outbound sync adapter backed by PyZotero's write API methods."""

    adapter_id = "zotero.pyzotero"
    display_name = "PyZotero"

    def __init__(self, client: PyZoteroClient):
        self.client = client

    def can_write(self) -> bool:
        return self.client.can_write()

    def sync_pdfs(
        self,
        pdf_paths: Sequence[str],
        citations: Dict[str, Dict[str, Any]],
        *,
        collection_name: str = "",
    ) -> CitationSyncResult:
        probe = self.client.probe()
        if not probe.write_capable:
            return CitationSyncResult(ok=False, message=probe.message, synced_ids=[])
        collection_key = self.client.ensure_collection(collection_name) if collection_name else ""
        synced = []
        details = {}
        for path in pdf_paths:
            response = self.client.add_pdf(
                path,
                citations.get(path, {}),
                collection_key=collection_key,
                collection_name=collection_name,
            )
            key = str(response.get("key") or path)
            synced.append(key)
            details[path] = response
        return CitationSyncResult(
            ok=True,
            message=f"Synced {len(synced)} PDF{'s' if len(synced) != 1 else ''} to Zotero.",
            synced_ids=synced,
            details=details,
        )
