"""Persistent storage for the Papyrus intelligence layer."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core.db.base_db import BaseDB
from core.models.intelligence_models import ArtifactEdgeRecord, ArtifactRecord


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS intelligence_artifacts (
    artifact_id        TEXT PRIMARY KEY,
    source_id          TEXT,
    source_path        TEXT,
    source_type        TEXT,
    artifact_type      TEXT NOT NULL,
    storage_ref_type   TEXT,
    storage_ref_id     TEXT,
    parent_artifact_id TEXT,
    exact_text         TEXT,
    normalized_text    TEXT,
    locator_json       TEXT NOT NULL DEFAULT '{}',
    section_path_json  TEXT NOT NULL DEFAULT '[]',
    metadata_json      TEXT NOT NULL DEFAULT '{}',
    confidence         REAL,
    created_by         TEXT,
    extractor_name     TEXT,
    extractor_version  TEXT,
    model_role         TEXT,
    model_name         TEXT,
    input_hash         TEXT,
    status             TEXT NOT NULL DEFAULT 'complete',
    created_at         TEXT,
    updated_at         TEXT,
    UNIQUE(source_id, artifact_type, storage_ref_type, storage_ref_id)
);
CREATE INDEX IF NOT EXISTS idx_intel_artifacts_source
    ON intelligence_artifacts(source_id);
CREATE INDEX IF NOT EXISTS idx_intel_artifacts_type
    ON intelligence_artifacts(artifact_type);
CREATE INDEX IF NOT EXISTS idx_intel_artifacts_storage
    ON intelligence_artifacts(storage_ref_type, storage_ref_id);
CREATE INDEX IF NOT EXISTS idx_intel_artifacts_hash
    ON intelligence_artifacts(input_hash);
CREATE INDEX IF NOT EXISTS idx_intel_artifacts_status
    ON intelligence_artifacts(status);

CREATE TABLE IF NOT EXISTS intelligence_stage_status (
    source_id          TEXT NOT NULL,
    stage              TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'not_run',
    input_hash         TEXT,
    settings_hash      TEXT,
    prompt_key         TEXT,
    prompt_version     TEXT,
    extractor_name     TEXT,
    extractor_version  TEXT,
    model_role         TEXT,
    model_name         TEXT,
    job_id             TEXT,
    error_text         TEXT,
    progress_current   INTEGER DEFAULT 0,
    progress_total     INTEGER DEFAULT 0,
    created_at         TEXT,
    updated_at         TEXT,
    last_run_at        TEXT,
    PRIMARY KEY(source_id, stage)
);
CREATE INDEX IF NOT EXISTS idx_intel_stage_status_status
    ON intelligence_stage_status(status);
CREATE INDEX IF NOT EXISTS idx_intel_stage_status_job
    ON intelligence_stage_status(job_id);

CREATE TABLE IF NOT EXISTS intelligence_artifact_embeddings (
    artifact_id        TEXT PRIMARY KEY,
    source_id          TEXT,
    embedding_ref_type TEXT NOT NULL DEFAULT 'chroma',
    embedding_ref_id   TEXT,
    status             TEXT NOT NULL DEFAULT 'not_run',
    model_role         TEXT,
    model_name         TEXT,
    input_hash         TEXT,
    created_at         TEXT,
    updated_at         TEXT
);
CREATE INDEX IF NOT EXISTS idx_intel_artifact_embeddings_source
    ON intelligence_artifact_embeddings(source_id);
CREATE INDEX IF NOT EXISTS idx_intel_artifact_embeddings_status
    ON intelligence_artifact_embeddings(status);

CREATE TABLE IF NOT EXISTS intelligence_artifact_edges (
    edge_id             TEXT PRIMARY KEY,
    source_artifact_id  TEXT NOT NULL,
    target_artifact_id  TEXT NOT NULL,
    edge_type           TEXT NOT NULL,
    confidence          REAL,
    created_by          TEXT,
    metadata_json       TEXT NOT NULL DEFAULT '{}',
    created_at          TEXT,
    UNIQUE(source_artifact_id, target_artifact_id, edge_type)
);
CREATE INDEX IF NOT EXISTS idx_intel_edges_source
    ON intelligence_artifact_edges(source_artifact_id);
CREATE INDEX IF NOT EXISTS idx_intel_edges_target
    ON intelligence_artifact_edges(target_artifact_id);
CREATE INDEX IF NOT EXISTS idx_intel_edges_type
    ON intelligence_artifact_edges(edge_type);

CREATE TABLE IF NOT EXISTS intelligence_source_settings (
    source_id      TEXT PRIMARY KEY,
    processing_mode TEXT,
    settings_json TEXT NOT NULL DEFAULT '{}',
    updated_at    TEXT
);

CREATE TABLE IF NOT EXISTS intelligence_jobs (
    job_id                   TEXT PRIMARY KEY,
    source_id                TEXT,
    artifact_id              TEXT,
    job_type                 TEXT,
    stage                    TEXT,
    priority                 INTEGER DEFAULT 100,
    status                   TEXT,
    progress_current         INTEGER DEFAULT 0,
    progress_total           INTEGER DEFAULT 0,
    model_role               TEXT,
    model_name               TEXT,
    input_hash               TEXT,
    output_artifact_ids_json TEXT NOT NULL DEFAULT '[]',
    settings_json            TEXT NOT NULL DEFAULT '{}',
    error_text               TEXT,
    created_at               TEXT,
    updated_at               TEXT,
    started_at               TEXT,
    finished_at              TEXT
);
CREATE INDEX IF NOT EXISTS idx_intel_jobs_source
    ON intelligence_jobs(source_id);
CREATE INDEX IF NOT EXISTS idx_intel_jobs_status
    ON intelligence_jobs(status);
"""


class IntelligenceDB(BaseDB):
    def init_tables(self) -> None:
        if not self._conn:
            return
        for stmt in _SCHEMA_SQL.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                self._conn.execute(stmt)
        try:
            self._conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS intelligence_artifacts_fts
                USING fts5(
                    artifact_id UNINDEXED,
                    source_id UNINDEXED,
                    artifact_type UNINDEXED,
                    exact_text,
                    normalized_text,
                    metadata_json
                )
                """
            )
        except Exception:
            # Some lightweight SQLite builds omit FTS5. Keyword search falls
            # back to indexed LIKE queries in that case.
            pass

    def upsert_artifact(self, artifact: ArtifactRecord) -> str:
        values = artifact.db_values()
        now = _now()
        values["created_at"] = values.get("created_at") or now
        values["updated_at"] = now
        self._conn.execute(
            """
            INSERT INTO intelligence_artifacts (
                artifact_id, source_id, source_path, source_type, artifact_type,
                storage_ref_type, storage_ref_id, parent_artifact_id,
                exact_text, normalized_text, locator_json, section_path_json,
                metadata_json, confidence, created_by, extractor_name,
                extractor_version, model_role, model_name, input_hash, status,
                created_at, updated_at
            ) VALUES (
                :artifact_id, :source_id, :source_path, :source_type, :artifact_type,
                :storage_ref_type, :storage_ref_id, :parent_artifact_id,
                :exact_text, :normalized_text, :locator_json, :section_path_json,
                :metadata_json, :confidence, :created_by, :extractor_name,
                :extractor_version, :model_role, :model_name, :input_hash, :status,
                :created_at, :updated_at
            )
            ON CONFLICT(artifact_id) DO UPDATE SET
                source_id=excluded.source_id,
                source_path=excluded.source_path,
                source_type=excluded.source_type,
                artifact_type=excluded.artifact_type,
                storage_ref_type=excluded.storage_ref_type,
                storage_ref_id=excluded.storage_ref_id,
                parent_artifact_id=excluded.parent_artifact_id,
                exact_text=excluded.exact_text,
                normalized_text=excluded.normalized_text,
                locator_json=excluded.locator_json,
                section_path_json=excluded.section_path_json,
                metadata_json=excluded.metadata_json,
                confidence=excluded.confidence,
                created_by=excluded.created_by,
                extractor_name=excluded.extractor_name,
                extractor_version=excluded.extractor_version,
                model_role=excluded.model_role,
                model_name=excluded.model_name,
                input_hash=excluded.input_hash,
                status=excluded.status,
                updated_at=excluded.updated_at
            """,
            values,
        )
        self._upsert_artifact_fts(values)
        return artifact.artifact_id

    def upsert_artifacts(self, artifacts: List[ArtifactRecord]) -> List[str]:
        if not artifacts or not self._conn:
            return []
        ids = []
        for artifact in artifacts:
            ids.append(self.upsert_artifact(artifact))
        return ids

    def get_artifact(self, artifact_id: str) -> Optional[Dict[str, Any]]:
        if not self._conn or not artifact_id:
            return None
        cur = self._conn.execute(
            "SELECT * FROM intelligence_artifacts WHERE artifact_id=?",
            (artifact_id,),
        )
        row = cur.fetchone()
        return self._artifact_row_to_dict(row) if row else None

    def list_artifacts(
        self,
        source_id: Optional[str] = None,
        artifact_type: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        if not self._conn:
            return []
        clauses = []
        params: List[Any] = []
        if source_id:
            clauses.append("source_id=?")
            params.append(source_id)
        if artifact_type:
            clauses.append("artifact_type=?")
            params.append(artifact_type)
        if status:
            clauses.append("status=?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, int(limit)))
        cur = self._conn.execute(
            f"SELECT * FROM intelligence_artifacts {where} ORDER BY updated_at DESC LIMIT ?",
            tuple(params),
        )
        return [self._artifact_row_to_dict(row) for row in cur.fetchall()]

    def search_artifacts(
        self,
        query: str,
        artifact_types: Optional[List[str]] = None,
        source_ids: Optional[List[str]] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        if not self._conn or not query.strip():
            return []
        fts_rows = self._search_artifacts_fts(query, artifact_types, source_ids, limit)
        if fts_rows:
            return fts_rows
        clauses = ["(exact_text LIKE ? OR normalized_text LIKE ? OR metadata_json LIKE ?)"]
        needle = f"%{query.strip()}%"
        params: List[Any] = [needle, needle, needle]
        if artifact_types:
            clauses.append(f"artifact_type IN ({','.join('?' for _ in artifact_types)})")
            params.extend(artifact_types)
        if source_ids:
            clauses.append(f"source_id IN ({','.join('?' for _ in source_ids)})")
            params.extend(source_ids)
        params.append(max(1, int(limit)))
        cur = self._conn.execute(
            "SELECT * FROM intelligence_artifacts WHERE "
            + " AND ".join(clauses)
            + " ORDER BY confidence DESC, updated_at DESC LIMIT ?",
            tuple(params),
        )
        return [self._artifact_row_to_dict(row) for row in cur.fetchall()]

    def upsert_stage_status(self, source_id: str, stage: str, values: Dict[str, Any]) -> None:
        if not self._conn or not source_id or not stage:
            return
        payload = dict(values or {})
        now = _now()
        payload.setdefault("created_at", now)
        payload["updated_at"] = now
        status = payload.get("status", "not_run")
        last_run_at = payload.get("last_run_at")
        if not last_run_at and status in {"complete", "failed", "stale"}:
            last_run_at = now
        self._conn.execute(
            """
            INSERT INTO intelligence_stage_status (
                source_id, stage, status, input_hash, settings_hash, prompt_key,
                prompt_version, extractor_name, extractor_version, model_role,
                model_name, job_id, error_text, progress_current, progress_total,
                created_at, updated_at, last_run_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id, stage) DO UPDATE SET
                status=excluded.status,
                input_hash=excluded.input_hash,
                settings_hash=excluded.settings_hash,
                prompt_key=excluded.prompt_key,
                prompt_version=excluded.prompt_version,
                extractor_name=excluded.extractor_name,
                extractor_version=excluded.extractor_version,
                model_role=excluded.model_role,
                model_name=excluded.model_name,
                job_id=excluded.job_id,
                error_text=excluded.error_text,
                progress_current=excluded.progress_current,
                progress_total=excluded.progress_total,
                updated_at=excluded.updated_at,
                last_run_at=excluded.last_run_at
            """,
            (
                source_id,
                stage,
                status,
                payload.get("input_hash"),
                payload.get("settings_hash"),
                payload.get("prompt_key"),
                payload.get("prompt_version"),
                payload.get("extractor_name"),
                payload.get("extractor_version"),
                payload.get("model_role"),
                payload.get("model_name"),
                payload.get("job_id"),
                payload.get("error_text"),
                payload.get("progress_current", 0),
                payload.get("progress_total", 0),
                payload.get("created_at"),
                payload.get("updated_at"),
                last_run_at,
            ),
        )

    def get_stage_statuses(self, source_id: str) -> Dict[str, Dict[str, Any]]:
        if not self._conn or not source_id:
            return {}
        cur = self._conn.execute(
            """
            SELECT source_id, stage, status, input_hash, settings_hash, prompt_key,
                   prompt_version, extractor_name, extractor_version, model_role,
                   model_name, job_id, error_text, progress_current, progress_total,
                   created_at, updated_at, last_run_at
            FROM intelligence_stage_status
            WHERE source_id=?
            """,
            (source_id,),
        )
        result: Dict[str, Dict[str, Any]] = {}
        for row in cur.fetchall():
            data = {
                "source_id": row[0],
                "stage": row[1],
                "status": row[2],
                "input_hash": row[3],
                "settings_hash": row[4],
                "prompt_key": row[5],
                "prompt_version": row[6],
                "extractor_name": row[7],
                "extractor_version": row[8],
                "model_role": row[9],
                "model_name": row[10],
                "job_id": row[11],
                "error_text": row[12],
                "progress_current": row[13],
                "progress_total": row[14],
                "created_at": row[15],
                "updated_at": row[16],
                "last_run_at": row[17],
            }
            result[str(row[1])] = data
        return result

    def mark_source_stages_status(
        self,
        source_id: str,
        status: str,
        stages: Optional[List[str]] = None,
    ) -> int:
        if not self._conn or not source_id:
            return 0
        now = _now()
        if stages:
            params: List[Any] = [status, now, source_id, *stages]
            cur = self._conn.execute(
                "UPDATE intelligence_stage_status SET status=?, updated_at=? "
                f"WHERE source_id=? AND stage IN ({','.join('?' for _ in stages)})",
                tuple(params),
            )
        else:
            cur = self._conn.execute(
                "UPDATE intelligence_stage_status SET status=?, updated_at=? WHERE source_id=?",
                (status, now, source_id),
            )
        return cur.rowcount or 0

    def upsert_artifact_embedding(self, artifact_id: str, values: Dict[str, Any]) -> None:
        if not self._conn or not artifact_id:
            return
        payload = dict(values or {})
        now = _now()
        payload.setdefault("created_at", now)
        payload["updated_at"] = now
        self._conn.execute(
            """
            INSERT INTO intelligence_artifact_embeddings (
                artifact_id, source_id, embedding_ref_type, embedding_ref_id,
                status, model_role, model_name, input_hash, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(artifact_id) DO UPDATE SET
                source_id=excluded.source_id,
                embedding_ref_type=excluded.embedding_ref_type,
                embedding_ref_id=excluded.embedding_ref_id,
                status=excluded.status,
                model_role=excluded.model_role,
                model_name=excluded.model_name,
                input_hash=excluded.input_hash,
                updated_at=excluded.updated_at
            """,
            (
                artifact_id,
                payload.get("source_id"),
                payload.get("embedding_ref_type", "chroma"),
                payload.get("embedding_ref_id"),
                payload.get("status", "not_run"),
                payload.get("model_role"),
                payload.get("model_name"),
                payload.get("input_hash"),
                payload.get("created_at"),
                payload.get("updated_at"),
            ),
        )

    def mark_artifact_embeddings_status(self, source_id: str, status: str) -> int:
        if not self._conn or not source_id:
            return 0
        cur = self._conn.execute(
            "UPDATE intelligence_artifact_embeddings SET status=?, updated_at=? WHERE source_id=?",
            (status, _now(), source_id),
        )
        return cur.rowcount or 0

    def upsert_edge(self, edge: ArtifactEdgeRecord) -> str:
        if not self._conn:
            return edge.edge_id
        self._conn.execute(
            """
            INSERT INTO intelligence_artifact_edges (
                edge_id, source_artifact_id, target_artifact_id, edge_type,
                confidence, created_by, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(edge_id) DO UPDATE SET
                source_artifact_id=excluded.source_artifact_id,
                target_artifact_id=excluded.target_artifact_id,
                edge_type=excluded.edge_type,
                confidence=excluded.confidence,
                created_by=excluded.created_by,
                metadata_json=excluded.metadata_json
            """,
            (
                edge.edge_id,
                edge.source_artifact_id,
                edge.target_artifact_id,
                edge.edge_type,
                edge.confidence,
                edge.created_by,
                json.dumps(edge.metadata, ensure_ascii=False),
                edge.created_at or _now(),
            ),
        )
        return edge.edge_id

    def list_edges(self, artifact_id: str) -> List[Dict[str, Any]]:
        if not self._conn or not artifact_id:
            return []
        cur = self._conn.execute(
            """
            SELECT edge_id, source_artifact_id, target_artifact_id, edge_type,
                   confidence, created_by, metadata_json, created_at
            FROM intelligence_artifact_edges
            WHERE source_artifact_id=? OR target_artifact_id=?
            ORDER BY created_at DESC
            """,
            (artifact_id, artifact_id),
        )
        return [
            {
                "edge_id": row[0],
                "source_artifact_id": row[1],
                "target_artifact_id": row[2],
                "edge_type": row[3],
                "confidence": row[4],
                "created_by": row[5],
                "metadata": _json(row[6], {}),
                "created_at": row[7],
            }
            for row in cur.fetchall()
        ]

    def get_source_settings(self, source_id: str) -> Dict[str, Any]:
        if not self._conn or not source_id:
            return {}
        cur = self._conn.execute(
            "SELECT processing_mode, settings_json FROM intelligence_source_settings WHERE source_id=?",
            (source_id,),
        )
        row = cur.fetchone()
        if not row:
            return {}
        settings = _json(row[1], {})
        if row[0]:
            settings["processing_mode"] = row[0]
        return settings

    def set_source_settings(self, source_id: str, settings: Dict[str, Any]) -> None:
        if not self._conn or not source_id:
            return
        settings = dict(settings or {})
        processing_mode = settings.get("processing_mode")
        self._conn.execute(
            """
            INSERT INTO intelligence_source_settings (source_id, processing_mode, settings_json, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(source_id) DO UPDATE SET
                processing_mode=excluded.processing_mode,
                settings_json=excluded.settings_json,
                updated_at=excluded.updated_at
            """,
            (source_id, processing_mode, json.dumps(settings, ensure_ascii=False), _now()),
        )

    def mark_source_artifacts_status(self, source_id: str, status: str) -> int:
        if not self._conn or not source_id:
            return 0
        cur = self._conn.execute(
            "UPDATE intelligence_artifacts SET status=?, updated_at=? WHERE source_id=?",
            (status, _now(), source_id),
        )
        self.mark_source_stages_status(source_id, status)
        return cur.rowcount or 0

    def delete_derived_artifacts(self, source_id: str, stage: Optional[str] = None) -> int:
        if not self._conn or not source_id:
            return 0
        preserve = ("source", "annotation", "workspace_node_ref")
        params: List[Any] = [source_id, *preserve]
        clause = "source_id=? AND artifact_type NOT IN (?,?,?)"
        if stage:
            clause += " AND json_extract(metadata_json, '$.stage')=?"
            params.append(stage)
        cur = self._conn.execute(
            f"DELETE FROM intelligence_artifacts WHERE {clause}",
            tuple(params),
        )
        self._conn.execute(
            """
            DELETE FROM intelligence_artifact_edges
            WHERE source_artifact_id NOT IN (SELECT artifact_id FROM intelligence_artifacts)
               OR target_artifact_id NOT IN (SELECT artifact_id FROM intelligence_artifacts)
            """
        )
        try:
            self._conn.execute(
                "DELETE FROM intelligence_artifacts_fts "
                "WHERE artifact_id NOT IN (SELECT artifact_id FROM intelligence_artifacts)"
            )
        except Exception:
            pass
        if stage:
            self.mark_source_stages_status(source_id, "not_run", stages=[stage])
        return cur.rowcount or 0

    def _upsert_artifact_fts(self, values: Dict[str, Any]) -> None:
        try:
            self._conn.execute(
                "DELETE FROM intelligence_artifacts_fts WHERE artifact_id=?",
                (values.get("artifact_id"),),
            )
            self._conn.execute(
                """
                INSERT INTO intelligence_artifacts_fts (
                    artifact_id, source_id, artifact_type, exact_text,
                    normalized_text, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    values.get("artifact_id"),
                    values.get("source_id"),
                    values.get("artifact_type"),
                    values.get("exact_text") or "",
                    values.get("normalized_text") or "",
                    values.get("metadata_json") or "{}",
                ),
            )
        except Exception:
            pass

    def _search_artifacts_fts(
        self,
        query: str,
        artifact_types: Optional[List[str]],
        source_ids: Optional[List[str]],
        limit: int,
    ) -> List[Dict[str, Any]]:
        try:
            clauses = ["intelligence_artifacts_fts MATCH ?"]
            params: List[Any] = [_fts_query(query)]
            if artifact_types:
                clauses.append(f"a.artifact_type IN ({','.join('?' for _ in artifact_types)})")
                params.extend(artifact_types)
            if source_ids:
                clauses.append(f"a.source_id IN ({','.join('?' for _ in source_ids)})")
                params.extend(source_ids)
            params.append(max(1, int(limit)))
            cur = self._conn.execute(
                """
                SELECT a.*
                FROM intelligence_artifacts_fts f
                JOIN intelligence_artifacts a ON a.artifact_id=f.artifact_id
                WHERE """ + " AND ".join(clauses) + """
                ORDER BY bm25(intelligence_artifacts_fts), a.confidence DESC, a.updated_at DESC
                LIMIT ?
                """,
                tuple(params),
            )
            return [self._artifact_row_to_dict(row) for row in cur.fetchall()]
        except Exception:
            return []

    def record_job(self, job_id: str, values: Dict[str, Any]) -> None:
        if not self._conn or not job_id:
            return
        payload = dict(values or {})
        payload.setdefault("job_id", job_id)
        payload.setdefault("created_at", _now())
        payload["updated_at"] = _now()
        payload["settings_json"] = json.dumps(payload.get("settings") or {}, ensure_ascii=False)
        payload["output_artifact_ids_json"] = json.dumps(
            payload.get("output_artifact_ids") or [], ensure_ascii=False
        )
        self._conn.execute(
            """
            INSERT INTO intelligence_jobs (
                job_id, source_id, artifact_id, job_type, stage, priority, status,
                progress_current, progress_total, model_role, model_name, input_hash,
                output_artifact_ids_json, settings_json, error_text,
                created_at, updated_at, started_at, finished_at
            ) VALUES (
                :job_id, :source_id, :artifact_id, :job_type, :stage, :priority, :status,
                :progress_current, :progress_total, :model_role, :model_name, :input_hash,
                :output_artifact_ids_json, :settings_json, :error_text,
                :created_at, :updated_at, :started_at, :finished_at
            )
            ON CONFLICT(job_id) DO UPDATE SET
                source_id=excluded.source_id,
                artifact_id=excluded.artifact_id,
                job_type=excluded.job_type,
                stage=excluded.stage,
                priority=excluded.priority,
                status=excluded.status,
                progress_current=excluded.progress_current,
                progress_total=excluded.progress_total,
                model_role=excluded.model_role,
                model_name=excluded.model_name,
                input_hash=excluded.input_hash,
                output_artifact_ids_json=excluded.output_artifact_ids_json,
                settings_json=excluded.settings_json,
                error_text=excluded.error_text,
                updated_at=excluded.updated_at,
                started_at=excluded.started_at,
                finished_at=excluded.finished_at
            """,
            {
                "job_id": payload.get("job_id"),
                "source_id": payload.get("source_id"),
                "artifact_id": payload.get("artifact_id"),
                "job_type": payload.get("job_type"),
                "stage": payload.get("stage"),
                "priority": payload.get("priority", 100),
                "status": payload.get("status"),
                "progress_current": payload.get("progress_current", 0),
                "progress_total": payload.get("progress_total", 0),
                "model_role": payload.get("model_role"),
                "model_name": payload.get("model_name"),
                "input_hash": payload.get("input_hash"),
                "output_artifact_ids_json": payload.get("output_artifact_ids_json"),
                "settings_json": payload.get("settings_json"),
                "error_text": payload.get("error_text"),
                "created_at": payload.get("created_at"),
                "updated_at": payload.get("updated_at"),
                "started_at": payload.get("started_at"),
                "finished_at": payload.get("finished_at"),
            },
        )

    @staticmethod
    def _artifact_row_to_dict(row) -> Dict[str, Any]:
        keys = [
            "artifact_id", "source_id", "source_path", "source_type", "artifact_type",
            "storage_ref_type", "storage_ref_id", "parent_artifact_id",
            "exact_text", "normalized_text", "locator_json", "section_path_json",
            "metadata_json", "confidence", "created_by", "extractor_name",
            "extractor_version", "model_role", "model_name", "input_hash", "status",
            "created_at", "updated_at",
        ]
        data = dict(zip(keys, row))
        data["locator"] = _json(data.pop("locator_json"), {})
        data["section_path"] = _json(data.pop("section_path_json"), [])
        data["metadata"] = _json(data.pop("metadata_json"), {})
        return data


def _json(raw: Any, default: Any) -> Any:
    if raw in (None, ""):
        return default
    try:
        return json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return default


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fts_query(query: str) -> str:
    terms = [
        term.replace('"', "").strip()
        for term in str(query or "").split()
        if term.replace('"', "").strip()
    ]
    if not terms:
        return '""'
    return " OR ".join(f'"{term}"' for term in terms[:12])
