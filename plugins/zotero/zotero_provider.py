"""
plugins/zotero/zotero_provider.py

Implements the CitationProvider protocol so Zotero items automatically
appear in the main citation dock table alongside PDF-extracted entries.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .zotero_db import ZoteroDB
from .zotero_formatter import ZoteroFormatter


class ZoteroProvider:
    """
    CitationProvider that reads from the local Zotero SQLite database.

    Registered with CitationAppService via::

        api.citation_service.register_provider("zotero", provider)

    ``collection_id`` can be set to limit which collection's items are
    contributed to the citation table.  None means all items.
    """

    def __init__(self, db: ZoteroDB, formatter: ZoteroFormatter):
        self._db = db
        self._formatter = formatter
        self._collection_id: Optional[int] = None
        # Cache: {doc_id: citation_dict} — refreshed on get_entries()
        self._cache: Dict[str, Dict] = {}

    def set_collection_filter(self, collection_id: Optional[int]) -> None:
        self._collection_id = collection_id
        self._cache.clear()

    # ------------------------------------------------------------------
    # CitationProvider protocol
    # ------------------------------------------------------------------

    def get_entries(self) -> List[Dict]:
        """Return all Zotero items as app citation dicts (refreshes cache)."""
        if not self._db.is_available():
            return []
        self._cache.clear()
        items = self._db.get_items(collection_id=self._collection_id)
        entries = []
        for item in items:
            cit = self._formatter.to_citation_dict(item)
            self._cache[cit["doc_id"]] = cit
            entries.append(cit)
        return entries

    def get_entry(self, doc_id: str) -> Optional[Dict]:
        """Return a single citation dict by its 'zotero:{id}' doc_id."""
        if doc_id in self._cache:
            return self._cache[doc_id]
        if not self._db.is_available():
            return None
        item = self._db.get_item_by_doc_id(doc_id)
        if item is None:
            return None
        cit = self._formatter.to_citation_dict(item)
        self._cache[doc_id] = cit
        return cit

    def on_project_open(self, filepath: str) -> None:
        self._cache.clear()

    def on_project_close(self) -> None:
        self._cache.clear()
