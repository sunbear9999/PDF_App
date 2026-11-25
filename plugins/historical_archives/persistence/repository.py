"""Project-scoped saved searches and import ledger."""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

from ..models import ArchiveQuery, ArchiveResult, stable_json


SAVED_TABLE = "plugin_historical_saved_searches"
IMPORT_TABLE = "plugin_historical_imports"


class HistoricalArchiveRepository:
    def __init__(self, api) -> None:
        self._api = api
        self._lock = threading.RLock()

    @staticmethod
    def register_tables(api) -> None:
        api.db.register_table("historical_saved_searches", {
            "id": "TEXT PRIMARY KEY",
            "name": "TEXT NOT NULL",
            "query_json": "TEXT NOT NULL",
            "created_at": "TEXT NOT NULL",
            "last_run_at": "TEXT",
            "result_count": "INTEGER DEFAULT 0",
        })
        api.db.register_table("historical_imports", {
            "provider_key": "TEXT PRIMARY KEY",
            "source_id": "TEXT NOT NULL",
            "path": "TEXT NOT NULL",
            "checksum": "TEXT",
            "provenance_json": "TEXT NOT NULL",
            "imported_at": "TEXT NOT NULL",
        })

    def save_search(self, name: str, query: ArchiveQuery) -> str:
        search_id = str(uuid.uuid4())
        now = self._now()
        with self._lock:
            self._api.db.execute(
                f"INSERT INTO {SAVED_TABLE}(id,name,query_json,created_at) VALUES(?,?,?,?)",
                (search_id, name.strip() or query.original_query, stable_json(query.to_dict()), now),
            )
        return search_id

    def list_searches(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._api.db.execute(
                f"SELECT id,name,query_json,created_at,last_run_at,result_count "
                f"FROM {SAVED_TABLE} ORDER BY created_at DESC"
            )
        output = []
        for row in rows:
            try:
                query = ArchiveQuery.from_dict(json.loads(row[2]))
            except (ValueError, TypeError, json.JSONDecodeError):
                continue
            output.append({
                "id": row[0],
                "name": row[1],
                "query": query,
                "created_at": row[3],
                "last_run_at": row[4],
                "result_count": row[5] or 0,
            })
        return output

    def mark_search_run(self, search_id: str, result_count: int) -> None:
        with self._lock:
            self._api.db.execute(
                f"UPDATE {SAVED_TABLE} SET last_run_at=?,result_count=? WHERE id=?",
                (self._now(), int(result_count), search_id),
            )

    def delete_search(self, search_id: str) -> None:
        with self._lock:
            self._api.db.execute(f"DELETE FROM {SAVED_TABLE} WHERE id=?", (search_id,))

    def mark_imported(self, result: ArchiveResult, import_result, provenance) -> None:
        with self._lock:
            self._api.db.execute(
                f"INSERT OR REPLACE INTO {IMPORT_TABLE}"
                "(provider_key,source_id,path,checksum,provenance_json,imported_at) "
                "VALUES(?,?,?,?,?,?)",
                (
                    result.provider_key,
                    import_result.source_id,
                    import_result.path,
                    import_result.checksum,
                    stable_json(provenance),
                    self._now(),
                ),
            )

    def imported_record(self, provider_key: str):
        with self._lock:
            rows = self._api.db.execute(
                f"SELECT source_id,path,checksum,provenance_json,imported_at "
                f"FROM {IMPORT_TABLE} WHERE provider_key=?",
                (provider_key,),
            )
        if not rows:
            return None
        row = rows[0]
        current_path = row[1]
        for source in self._api.sources.list():
            if source.get("id") == row[0]:
                current_path = source.get("path") or current_path
                break
        return {
            "source_id": row[0],
            "path": current_path,
            "checksum": row[2],
            "provenance": json.loads(row[3] or "{}"),
            "imported_at": row[4],
        }

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
