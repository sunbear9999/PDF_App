from __future__ import annotations

import sqlite3
from typing import List, Optional

from core.db.base_db import BaseDB
from core.models.analysis_models import EvidenceQuote


class EvidenceDB(BaseDB):
    """Storage for per-chunk evidence quotes extracted during hierarchical analysis.

    Uses the same thread-safe fallback pattern as DocumentDB._execute_analysis_write():
    if the shared connection raises ProgrammingError ("created in a thread"), open a
    fresh connection to the project file for the write.
    """

    # ------------------------------------------------------------------ reads

    def get_quote(self, quote_id: str) -> Optional[EvidenceQuote]:
        if not self._conn:
            return None
        try:
            cursor = self._conn.cursor()
            cursor.execute(
                "SELECT id,run_id,doc_path,chunk_id,exact_text,snippet,page_num,"
                "note_text,importance_score,confidence_score,suggested_node_type,"
                "quote_role,tags_json,created_at FROM analysis_evidence_quotes WHERE id=?",
                (quote_id,),
            )
            row = cursor.fetchone()
            return self._row_to_quote(row) if row else None
        except Exception:
            return None

    def get_quotes_for_run(self, run_id: str) -> List[EvidenceQuote]:
        if not self._conn:
            return []
        try:
            cursor = self._conn.cursor()
            cursor.execute(
                "SELECT id,run_id,doc_path,chunk_id,exact_text,snippet,page_num,"
                "note_text,importance_score,confidence_score,suggested_node_type,"
                "quote_role,tags_json,created_at FROM analysis_evidence_quotes "
                "WHERE run_id=? ORDER BY chunk_id, importance_score DESC",
                (run_id,),
            )
            return [self._row_to_quote(r) for r in cursor.fetchall()]
        except Exception:
            return []

    def get_quotes_for_chunk(self, run_id: str, chunk_id: int) -> List[EvidenceQuote]:
        if not self._conn:
            return []
        try:
            cursor = self._conn.cursor()
            cursor.execute(
                "SELECT id,run_id,doc_path,chunk_id,exact_text,snippet,page_num,"
                "note_text,importance_score,confidence_score,suggested_node_type,"
                "quote_role,tags_json,created_at FROM analysis_evidence_quotes "
                "WHERE run_id=? AND chunk_id=? ORDER BY importance_score DESC",
                (run_id, chunk_id),
            )
            return [self._row_to_quote(r) for r in cursor.fetchall()]
        except Exception:
            return []

    # ----------------------------------------------------------------- writes

    def save_quote(self, eq: EvidenceQuote) -> None:
        sql = (
            "INSERT OR REPLACE INTO analysis_evidence_quotes "
            "(id,run_id,doc_path,chunk_id,exact_text,snippet,page_num,"
            "note_text,importance_score,confidence_score,suggested_node_type,"
            "quote_role,tags_json,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
        )
        params = (
            eq.quote_id, eq.run_id, eq.doc_path, eq.chunk_id,
            eq.exact_text, eq.snippet, eq.page_num,
            eq.note_text, eq.importance_score, eq.confidence_score,
            eq.suggested_node_type, eq.quote_role, eq.tags_json, eq.created_at,
        )
        self._execute_write(sql, params)

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

    @staticmethod
    def _row_to_quote(row: tuple) -> EvidenceQuote:
        return EvidenceQuote(
            quote_id=row[0],
            run_id=row[1],
            doc_path=row[2],
            chunk_id=row[3],
            exact_text=row[4],
            snippet=row[5],
            page_num=row[6],
            note_text=row[7],
            importance_score=float(row[8]),
            confidence_score=float(row[9]),
            suggested_node_type=row[10],
            quote_role=row[11],
            tags_json=row[12],
            created_at=row[13],
        )
