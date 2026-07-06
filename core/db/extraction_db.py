"""
Persistent storage for the deterministic extraction layer.

Tables managed here:
  extraction_blocks        — cached DocumentBlock objects per document
  extraction_results       — cached extraction outputs (entities, dates, tables, etc.)
  extraction_citation_index — per-document citation index entries
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core.db.base_db import BaseDB


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS extraction_blocks (
    block_id          TEXT PRIMARY KEY,
    doc_id            TEXT NOT NULL,
    page_num          INTEGER,
    block_type        TEXT,
    exact_text        TEXT,
    normalized_text   TEXT,
    bbox_json         TEXT,
    font_size         REAL,
    is_bold           INTEGER,
    heading_level     INTEGER,
    section_path_json TEXT,
    has_superscript   INTEGER DEFAULT 0,
    extractor_name    TEXT,
    extractor_version TEXT,
    created_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_exblocks_doc
    ON extraction_blocks(doc_id);

CREATE TABLE IF NOT EXISTS extraction_results (
    id                TEXT PRIMARY KEY,
    doc_id            TEXT NOT NULL,
    extractor_name    TEXT NOT NULL,
    extractor_version TEXT NOT NULL,
    result_type       TEXT NOT NULL,
    result_json       TEXT NOT NULL,
    cache_key         TEXT NOT NULL,
    created_at        TEXT,
    UNIQUE(doc_id, extractor_name, result_type)
);
CREATE INDEX IF NOT EXISTS idx_exresults_doc
    ON extraction_results(doc_id);

CREATE TABLE IF NOT EXISTS extraction_citation_index (
    entry_id   TEXT PRIMARY KEY,
    doc_id     TEXT NOT NULL,
    entry_type TEXT NOT NULL,
    entry_json TEXT NOT NULL,
    page_num   INTEGER,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_excit_doc
    ON extraction_citation_index(doc_id);
CREATE INDEX IF NOT EXISTS idx_excit_type
    ON extraction_citation_index(doc_id, entry_type);
"""


class ExtractionDB(BaseDB):
    """Read/write helpers for the extraction persistence layer."""

    # ------------------------------------------------------------------ schema

    def init_tables(self) -> None:
        if not self._conn:
            return
        try:
            for stmt in _SCHEMA_SQL.strip().split(";"):
                stmt = stmt.strip()
                if stmt:
                    self._conn.execute(stmt)
        except Exception:
            pass

    # ---------------------------------------------------------- blocks (read)

    def get_blocks(self, doc_id: str) -> List[Dict[str, Any]]:
        if not self._conn:
            return []
        try:
            cur = self._conn.execute(
                "SELECT block_id,doc_id,page_num,block_type,exact_text,"
                "normalized_text,bbox_json,font_size,is_bold,heading_level,"
                "section_path_json,has_superscript,extractor_name,extractor_version"
                " FROM extraction_blocks WHERE doc_id=? ORDER BY page_num,rowid",
                (doc_id,),
            )
            return [self._block_row_to_dict(r) for r in cur.fetchall()]
        except Exception:
            return []

    def blocks_exist(self, doc_id: str) -> bool:
        if not self._conn:
            return False
        try:
            cur = self._conn.execute(
                "SELECT 1 FROM extraction_blocks WHERE doc_id=? LIMIT 1",
                (doc_id,),
            )
            return cur.fetchone() is not None
        except Exception:
            return False

    # --------------------------------------------------------- blocks (write)

    def save_blocks(self, blocks: List[Dict[str, Any]]) -> None:
        if not blocks:
            return
        sql = (
            "INSERT OR REPLACE INTO extraction_blocks "
            "(block_id,doc_id,page_num,block_type,exact_text,normalized_text,"
            "bbox_json,font_size,is_bold,heading_level,section_path_json,"
            "has_superscript,extractor_name,extractor_version,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
        )
        now = _now()
        rows = [
            (
                b["block_id"], b["doc_id"], b.get("page_num"),
                b.get("block_type", "unknown"),
                b.get("exact_text", ""), b.get("normalized_text", ""),
                json.dumps(b.get("bbox")) if b.get("bbox") else None,
                b.get("font_size"), b.get("is_bold"),
                b.get("heading_level"),
                json.dumps(b.get("section_path", [])),
                1 if b.get("has_superscript") else 0,
                b.get("extractor_name", ""), b.get("extractor_version", ""),
                now,
            )
            for b in blocks
        ]
        self._execute_batch(sql, rows)

    def delete_blocks(self, doc_id: str) -> None:
        self._execute_write(
            "DELETE FROM extraction_blocks WHERE doc_id=?", (doc_id,)
        )

    # ------------------------------------------------------ results (read)

    def get_result(
        self, doc_id: str, extractor_name: str, result_type: str
    ) -> Optional[Any]:
        if not self._conn:
            return None
        try:
            cur = self._conn.execute(
                "SELECT result_json FROM extraction_results "
                "WHERE doc_id=? AND extractor_name=? AND result_type=?",
                (doc_id, extractor_name, result_type),
            )
            row = cur.fetchone()
            return json.loads(row[0]) if row else None
        except Exception:
            return None

    # ------------------------------------------------------ results (write)

    def save_result(
        self,
        doc_id: str,
        extractor_name: str,
        extractor_version: str,
        result_type: str,
        result_data: Any,
        cache_key: str,
    ) -> None:
        import uuid as _uuid
        sql = (
            "INSERT OR REPLACE INTO extraction_results "
            "(id,doc_id,extractor_name,extractor_version,result_type,"
            "result_json,cache_key,created_at) VALUES (?,?,?,?,?,?,?,?)"
        )
        self._execute_write(
            sql,
            (
                str(_uuid.uuid4()), doc_id, extractor_name, extractor_version,
                result_type, json.dumps(result_data), cache_key, _now(),
            ),
        )

    def delete_results(self, doc_id: str) -> None:
        self._execute_write(
            "DELETE FROM extraction_results WHERE doc_id=?", (doc_id,)
        )

    def delete_result_type(
        self, doc_id: str, extractor_name: str, result_type: str
    ) -> None:
        self._execute_write(
            "DELETE FROM extraction_results "
            "WHERE doc_id=? AND extractor_name=? AND result_type=?",
            (doc_id, extractor_name, result_type),
        )

    # ------------------------------------------------- citation index (read)

    def get_citation_entries(
        self, doc_id: str, entry_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        if not self._conn:
            return []
        try:
            if entry_type:
                cur = self._conn.execute(
                    "SELECT entry_id,doc_id,entry_type,entry_json,page_num "
                    "FROM extraction_citation_index "
                    "WHERE doc_id=? AND entry_type=?",
                    (doc_id, entry_type),
                )
            else:
                cur = self._conn.execute(
                    "SELECT entry_id,doc_id,entry_type,entry_json,page_num "
                    "FROM extraction_citation_index WHERE doc_id=?",
                    (doc_id,),
                )
            return [
                {
                    "entry_id": r[0], "doc_id": r[1],
                    "entry_type": r[2], "data": json.loads(r[3]),
                    "page_num": r[4],
                }
                for r in cur.fetchall()
            ]
        except Exception:
            return []

    # ------------------------------------------------ citation index (write)

    def save_citation_entry(
        self,
        entry_id: str,
        doc_id: str,
        entry_type: str,
        entry_data: Any,
        page_num: Optional[int] = None,
    ) -> None:
        self._execute_write(
            "INSERT OR REPLACE INTO extraction_citation_index "
            "(entry_id,doc_id,entry_type,entry_json,page_num,created_at) "
            "VALUES (?,?,?,?,?,?)",
            (entry_id, doc_id, entry_type, json.dumps(entry_data), page_num, _now()),
        )

    def delete_citation_index(self, doc_id: str) -> None:
        self._execute_write(
            "DELETE FROM extraction_citation_index WHERE doc_id=?", (doc_id,)
        )

    # ---------------------------------------------------------------- helpers

    def _execute_write(self, sql: str, params: tuple) -> None:
        try:
            self._conn.execute(sql, params)
            self._conn.commit()
            return
        except sqlite3.ProgrammingError as exc:
            if "created in a thread" not in str(exc):
                raise
        db_path = getattr(self.manager, "project_filepath", None)
        if not db_path:
            raise
        conn = sqlite3.connect(db_path, timeout=10.0)
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute(sql, params)
            conn.commit()
        finally:
            conn.close()

    def _execute_batch(self, sql: str, rows: List[tuple]) -> None:
        if not rows:
            return
        try:
            self._conn.executemany(sql, rows)
            self._conn.commit()
            return
        except sqlite3.ProgrammingError as exc:
            if "created in a thread" not in str(exc):
                raise
        db_path = getattr(self.manager, "project_filepath", None)
        if not db_path:
            return
        conn = sqlite3.connect(db_path, timeout=10.0)
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.executemany(sql, rows)
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _block_row_to_dict(row: tuple) -> Dict[str, Any]:
        return {
            "block_id": row[0], "doc_id": row[1], "page_num": row[2],
            "block_type": row[3], "exact_text": row[4],
            "normalized_text": row[5],
            "bbox": json.loads(row[6]) if row[6] else None,
            "font_size": row[7],
            "is_bold": bool(row[8]) if row[8] is not None else None,
            "heading_level": row[9],
            "section_path": json.loads(row[10]) if row[10] else [],
            "has_superscript": bool(row[11]),
            "extractor_name": row[12], "extractor_version": row[13],
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
