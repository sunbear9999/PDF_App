"""
Two-level cache for deterministic extraction results.

ExtractionResultStore  — persists to SQLite via ProjectManager.db_extraction
ExtractionCache        — in-memory LRU layer in front of the store (max 20 docs)
"""
from __future__ import annotations

import json
from collections import OrderedDict
from typing import Any, Dict, List, Optional


_MAX_CACHED_DOCS = 20


class ExtractionResultStore:
    """
    Thin service over ExtractionDB that reads and writes typed extraction
    results. Constructed with a ProjectManager reference so it always writes
    to the open project.
    """

    def __init__(self, project_manager) -> None:
        self._pm = project_manager

    # ------------------------------------------------------------------
    # Blocks
    # ------------------------------------------------------------------

    def get_blocks(self, doc_id: str) -> Optional[List[Dict[str, Any]]]:
        db = getattr(self._pm, "db_extraction", None)
        if db is None:
            return None
        rows = db.get_blocks(doc_id)
        return rows if rows else None

    def blocks_exist(self, doc_id: str) -> bool:
        db = getattr(self._pm, "db_extraction", None)
        return db.blocks_exist(doc_id) if db else False

    def save_blocks(self, doc_id: str, blocks: List[Dict[str, Any]]) -> None:
        db = getattr(self._pm, "db_extraction", None)
        if db:
            db.save_blocks(blocks)

    # ------------------------------------------------------------------
    # Generic extraction results
    # ------------------------------------------------------------------

    def get(
        self, doc_id: str, extractor_name: str, result_type: str
    ) -> Optional[Any]:
        db = getattr(self._pm, "db_extraction", None)
        if db is None:
            return None
        return db.get_result(doc_id, extractor_name, result_type)

    def put(
        self,
        doc_id: str,
        extractor_name: str,
        extractor_version: str,
        result_type: str,
        result_data: Any,
        cache_key: str = "",
    ) -> None:
        db = getattr(self._pm, "db_extraction", None)
        if db:
            db.save_result(
                doc_id, extractor_name, extractor_version,
                result_type, result_data, cache_key,
            )

    # ------------------------------------------------------------------
    # Citation index helpers
    # ------------------------------------------------------------------

    def get_citation_entries(
        self, doc_id: str, entry_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        db = getattr(self._pm, "db_extraction", None)
        return db.get_citation_entries(doc_id, entry_type) if db else []

    def save_citation_entry(
        self, entry_id: str, doc_id: str, entry_type: str,
        entry_data: Any, page_num: Optional[int] = None,
    ) -> None:
        db = getattr(self._pm, "db_extraction", None)
        if db:
            db.save_citation_entry(entry_id, doc_id, entry_type, entry_data, page_num)

    # ------------------------------------------------------------------
    # Cache invalidation
    # ------------------------------------------------------------------

    def invalidate(self, doc_id: str) -> None:
        db = getattr(self._pm, "db_extraction", None)
        if db:
            db.delete_blocks(doc_id)
            db.delete_results(doc_id)
            db.delete_citation_index(doc_id)

    def invalidate_extractor(
        self, doc_id: str, extractor_name: str, result_type: str
    ) -> None:
        db = getattr(self._pm, "db_extraction", None)
        if db:
            db.delete_result_type(doc_id, extractor_name, result_type)


class ExtractionCache:
    """
    In-memory LRU cache wrapping ExtractionResultStore.

    Blocks are expensive to produce; this layer prevents re-parsing PDFs
    for every call within a single session. The cache is keyed on doc_id
    and limited to _MAX_CACHED_DOCS documents.
    """

    def __init__(self, project_manager) -> None:
        self._store = ExtractionResultStore(project_manager)
        self._mem: OrderedDict[str, Dict[str, Any]] = OrderedDict()

    @property
    def store(self) -> ExtractionResultStore:
        return self._store

    # ------------------------------------------------------------------
    # Block cache
    # ------------------------------------------------------------------

    def get_blocks(self, doc_id: str) -> Optional[List[Dict[str, Any]]]:
        if doc_id in self._mem and "blocks" in self._mem[doc_id]:
            self._mem.move_to_end(doc_id)
            return self._mem[doc_id]["blocks"]
        blocks = self._store.get_blocks(doc_id)
        if blocks is not None:
            self._set_mem(doc_id, "blocks", blocks)
        return blocks

    def set_blocks(self, doc_id: str, blocks: List[Dict[str, Any]]) -> None:
        self._store.save_blocks(doc_id, blocks)
        self._set_mem(doc_id, "blocks", blocks)

    def blocks_exist(self, doc_id: str) -> bool:
        if doc_id in self._mem and "blocks" in self._mem[doc_id]:
            return True
        return self._store.blocks_exist(doc_id)

    # ------------------------------------------------------------------
    # Generic result cache
    # ------------------------------------------------------------------

    def get(
        self, doc_id: str, extractor_name: str, result_type: str
    ) -> Optional[Any]:
        mem_key = f"{extractor_name}:{result_type}"
        if doc_id in self._mem and mem_key in self._mem[doc_id]:
            self._mem.move_to_end(doc_id)
            return self._mem[doc_id][mem_key]
        result = self._store.get(doc_id, extractor_name, result_type)
        if result is not None:
            self._set_mem(doc_id, mem_key, result)
        return result

    def set(
        self,
        doc_id: str,
        extractor_name: str,
        extractor_version: str,
        result_type: str,
        result_data: Any,
        cache_key: str = "",
    ) -> None:
        self._store.put(
            doc_id, extractor_name, extractor_version,
            result_type, result_data, cache_key,
        )
        self._set_mem(doc_id, f"{extractor_name}:{result_type}", result_data)

    # ------------------------------------------------------------------
    # Invalidation
    # ------------------------------------------------------------------

    def invalidate(self, doc_id: str) -> None:
        self._mem.pop(doc_id, None)
        self._store.invalidate(doc_id)

    def invalidate_extractor(
        self, doc_id: str, extractor_name: str, result_type: str
    ) -> None:
        if doc_id in self._mem:
            self._mem[doc_id].pop(f"{extractor_name}:{result_type}", None)
        self._store.invalidate_extractor(doc_id, extractor_name, result_type)

    def clear_memory(self) -> None:
        self._mem.clear()

    # ------------------------------------------------------------------
    # Internal LRU helpers
    # ------------------------------------------------------------------

    def _set_mem(self, doc_id: str, key: str, value: Any) -> None:
        if doc_id not in self._mem:
            self._mem[doc_id] = {}
        self._mem[doc_id][key] = value
        self._mem.move_to_end(doc_id)
        while len(self._mem) > _MAX_CACHED_DOCS:
            self._mem.popitem(last=False)
