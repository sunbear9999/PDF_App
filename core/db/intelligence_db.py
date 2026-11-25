"""Persistent storage for the Papyrus intelligence layer."""
from __future__ import annotations

import json
import hashlib
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core.db.base_db import BaseDB
from core.models.artifact_families import (
    ARTIFACT_FAMILIES as _ARTIFACT_FAMILIES,
    artifact_family as _artifact_family,
    artifact_types_for_families as _artifact_types_for_families,
)
from core.models.intelligence_models import (
    ArtifactEdgeRecord,
    ArtifactQuery,
    ArtifactRecord,
    ArtifactReviewState,
)
from core.db.intelligence_artifact_hashing import (
    artifact_payload_hash as _artifact_payload_hash,
    hash_values_from_row as _artifact_hash_values_from_row,
    refresh_identity as _refresh_identity,
)
from core.db import entity_lexicon_queries as _entity_lexicon
from core.db import intelligence_entity_resolution as _entity_resolution
from core.db import intelligence_decision_journal as _decision_journal
from core.db import intelligence_artifact_aliases as _aliases
from core.db import intelligence_fts_index as _fts_index
from core.db import intelligence_source_revisions as _source_revisions
from core.db.intelligence_stage_identity import (
    ensure_identity_column as _ensure_identity_column,
    record_run_identity as _record_run_identity,
    run_identities as _run_identities,
)
from core.utils.bounded_cache import BoundedCache

SOURCE_REVISION_CACHE_ENTRIES = 4096
# Retrieval results are keyed by project state, so entries from older states
# never hit again; keep only the newest when a project opens.
RETRIEVAL_CACHE_KEEP = 500


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS intelligence_schema_migrations (
    version       INTEGER PRIMARY KEY,
    description   TEXT NOT NULL,
    applied_at    TEXT NOT NULL
);
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
    normalized_key     TEXT,
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
    payload_hash       TEXT,
    status             TEXT NOT NULL DEFAULT 'complete',
    review_state       TEXT NOT NULL DEFAULT 'unreviewed',
    revision           INTEGER NOT NULL DEFAULT 1,
    supersedes_artifact_id TEXT,
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

CREATE INDEX IF NOT EXISTS idx_intel_artifacts_cursor
    ON intelligence_artifacts(updated_at DESC, artifact_id DESC);

CREATE TABLE IF NOT EXISTS intelligence_artifact_revisions (
    revision_id         TEXT PRIMARY KEY,
    artifact_id         TEXT NOT NULL,
    revision            INTEGER NOT NULL,
    snapshot_json       TEXT NOT NULL DEFAULT '{}',
    review_state        TEXT NOT NULL DEFAULT 'unreviewed',
    created_by          TEXT,
    created_at          TEXT,
    UNIQUE(artifact_id, revision)
);
CREATE INDEX IF NOT EXISTS idx_intel_revisions_artifact
    ON intelligence_artifact_revisions(artifact_id, revision);
CREATE TABLE IF NOT EXISTS intelligence_artifact_proposals (
    artifact_id   TEXT NOT NULL,
    payload_hash  TEXT NOT NULL,
    proposal_json TEXT NOT NULL,
    detected_at   TEXT NOT NULL,
    PRIMARY KEY(artifact_id, payload_hash)
);
CREATE INDEX IF NOT EXISTS idx_intel_proposals_artifact
    ON intelligence_artifact_proposals(artifact_id, detected_at);

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

CREATE TABLE IF NOT EXISTS intelligence_stage_runs (
    run_id                   TEXT PRIMARY KEY,
    source_id                TEXT NOT NULL,
    stage                    TEXT NOT NULL,
    status                   TEXT NOT NULL,
    input_hash               TEXT,
    settings_hash            TEXT,
    dependency_hash          TEXT,
    producer_name            TEXT,
    producer_version         TEXT,
    prompt_key               TEXT,
    prompt_version           TEXT,
    model_role               TEXT,
    model_name               TEXT,
    output_artifact_ids_json TEXT NOT NULL DEFAULT '[]',
    error_text               TEXT,
    started_at               TEXT,
    finished_at              TEXT,
    created_at               TEXT
);
CREATE INDEX IF NOT EXISTS idx_intel_stage_runs_source
    ON intelligence_stage_runs(source_id, stage, created_at);
CREATE INDEX IF NOT EXISTS idx_intel_stage_runs_status
    ON intelligence_stage_runs(status);

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

CREATE TABLE IF NOT EXISTS intelligence_edge_assertions (
    assertion_id        TEXT PRIMARY KEY,
    edge_id             TEXT NOT NULL,
    source_artifact_id  TEXT NOT NULL,
    target_artifact_id  TEXT NOT NULL,
    edge_type           TEXT NOT NULL,
    evidence_artifact_id TEXT,
    confidence          REAL,
    created_by          TEXT,
    producer_version    TEXT,
    metadata_json       TEXT NOT NULL DEFAULT '{}',
    review_state        TEXT NOT NULL DEFAULT 'unreviewed',
    created_at          TEXT,
    UNIQUE(edge_id, evidence_artifact_id, created_by)
);
CREATE INDEX IF NOT EXISTS idx_intel_edge_assertions_edge
    ON intelligence_edge_assertions(edge_id);
CREATE INDEX IF NOT EXISTS idx_intel_edge_assertions_evidence
    ON intelligence_edge_assertions(evidence_artifact_id);

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

CREATE TABLE IF NOT EXISTS intelligence_analysis_runs (
    mode          TEXT NOT NULL,
    params_hash   TEXT NOT NULL,
    doc_hash      TEXT NOT NULL,
    source_id     TEXT,
    output_json   TEXT NOT NULL DEFAULT '{}',
    created_at    TEXT,
    updated_at    TEXT,
    PRIMARY KEY(mode, params_hash, doc_hash)
);
CREATE INDEX IF NOT EXISTS idx_intel_analysis_runs_source
    ON intelligence_analysis_runs(source_id);

CREATE TABLE IF NOT EXISTS intelligence_backfill_state (
    source_id          TEXT PRIMARY KEY,
    selected_stages_json TEXT NOT NULL DEFAULT '[]',
    completed_stages_json TEXT NOT NULL DEFAULT '[]',
    status             TEXT NOT NULL DEFAULT 'pending',
    error_text         TEXT,
    updated_at         TEXT
);

CREATE TABLE IF NOT EXISTS intelligence_entity_rules (
    rule_id       TEXT PRIMARY KEY,
    rule_type     TEXT NOT NULL,
    entity_type   TEXT,
    source_key    TEXT,
    target_artifact_id TEXT,
    other_key     TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at    TEXT,
    UNIQUE(rule_type, entity_type, source_key, target_artifact_id, other_key)
);
CREATE INDEX IF NOT EXISTS idx_intel_entity_rules_lookup
    ON intelligence_entity_rules(rule_type, entity_type, source_key);
CREATE INDEX IF NOT EXISTS idx_intel_entity_rules_target
    ON intelligence_entity_rules(target_artifact_id);

CREATE TABLE IF NOT EXISTS intelligence_retrieval_cache (
    query_hash          TEXT NOT NULL,
    project_state_hash  TEXT NOT NULL,
    plan_json           TEXT NOT NULL DEFAULT '{}',
    fused_json          TEXT NOT NULL DEFAULT '[]',
    created_at          TEXT,
    PRIMARY KEY(query_hash, project_state_hash)
);

CREATE TABLE IF NOT EXISTS intelligence_knowledge_revisions (
    source_id     TEXT NOT NULL,
    family        TEXT NOT NULL,
    revision      INTEGER NOT NULL DEFAULT 0,
    updated_at    TEXT,
    PRIMARY KEY(source_id, family)
);
"""


# Explicit column order so SELECTs stay stable even when normalized_key was
# appended by an ALTER TABLE migration (which places it last physically).
_ARTIFACT_COLUMNS = (
    "artifact_id, source_id, source_path, source_type, artifact_type, "
    "storage_ref_type, storage_ref_id, parent_artifact_id, exact_text, "
    "normalized_text, normalized_key, locator_json, section_path_json, "
    "metadata_json, confidence, created_by, extractor_name, extractor_version, "
    "model_role, model_name, input_hash, payload_hash, status, review_state, revision, "
    "supersedes_artifact_id, created_at, updated_at"
)
MAX_ARTIFACT_BATCH = 128


class IntelligenceDB(BaseDB):
    def __init__(self, manager) -> None:
        super().__init__(manager)
        self._write_lock = threading.RLock()
        self._source_revision_cache = BoundedCache(SOURCE_REVISION_CACHE_ENTRIES)

    def init_tables(self) -> None:
        if not self._conn:
            return
        with self._write_lock:
            self._conn.execute("SAVEPOINT intelligence_schema_init")
            try:
                for stmt in _SCHEMA_SQL.strip().split(";"):
                    stmt = stmt.strip()
                    if stmt:
                        self._conn.execute(stmt)
                self._migrate_add_columns()
                _ensure_identity_column(self._conn)
                _source_revisions.ensure_tables(self._conn)
                _aliases.ensure_table(self._conn)
                _decision_journal.ensure_table(self._conn)
                from core.db.intelligence_run_queries import ensure_tables
                ensure_tables(self._conn)
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
                    # Some lightweight SQLite builds omit FTS5. Keyword search
                    # falls back to indexed LIKE queries.
                    pass
                _fts_index.ensure_table(self._conn, _now())
                self.prune_retrieval_cache()
                self._conn.execute(
                    "INSERT OR IGNORE INTO intelligence_schema_migrations "
                    "(version, description, applied_at) VALUES (?, ?, ?)",
                    (2, "artifact revisions, stage runs, assertions, review and entity rules", _now()),
                )
                self._conn.execute(
                    "INSERT OR IGNORE INTO intelligence_schema_migrations "
                    "(version, description, applied_at) VALUES (?, ?, ?)",
                    (3, "semantic artifact hashes and scoped knowledge revisions", _now()),
                )
                self._conn.execute(
                    "INSERT OR IGNORE INTO intelligence_schema_migrations "
                    "(version, description, applied_at) VALUES (?, ?, ?)",
                    (4, "producer proposals beside reviewed artifact revisions", _now()),
                )
                self._conn.execute("RELEASE SAVEPOINT intelligence_schema_init")
            except Exception:
                self._conn.execute("ROLLBACK TO SAVEPOINT intelligence_schema_init")
                self._conn.execute("RELEASE SAVEPOINT intelligence_schema_init")
                raise

    def _migrate_add_columns(self) -> None:
        """Additively add columns to pre-existing intelligence tables."""
        if not self._conn:
            return
        try:
            cur = self._conn.execute("PRAGMA table_info(intelligence_artifacts)")
            columns = {row[1] for row in cur.fetchall()}
        except Exception:
            return
        migrations = {
            "normalized_key": "TEXT",
            "review_state": "TEXT NOT NULL DEFAULT 'unreviewed'",
            "revision": "INTEGER NOT NULL DEFAULT 1",
            "supersedes_artifact_id": "TEXT",
            "payload_hash": "TEXT",
        }
        for name, definition in migrations.items():
            if name not in columns:
                try:
                    self._conn.execute(
                        f"ALTER TABLE intelligence_artifacts ADD COLUMN {name} {definition}"
                    )
                except Exception:
                    pass
        # Index is created here (not in _SCHEMA_SQL) so it runs after the column
        # exists on both fresh and migrated tables.
        try:
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_intel_artifacts_norm_key "
                "ON intelligence_artifacts(normalized_key)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_intel_mentions_scope "
                "ON intelligence_artifacts(parent_artifact_id, source_id, artifact_type, status, review_state)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_artifact_source_page "
                "ON intelligence_artifacts(source_id, json_extract(locator_json, '$.page'))"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_intel_artifacts_review "
                "ON intelligence_artifacts(review_state)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_intel_artifacts_payload_hash "
                "ON intelligence_artifacts(payload_hash)"
            )
        except Exception:
            pass

    def record_artifact_alias(self, old_id: str, new_id, status: str, reason: str = "") -> None:
        with self._write_lock:
            _aliases.record_alias(self._conn, old_id, new_id, status, reason)

    def resolve_current_artifact_id(self, artifact_id: str) -> str:
        return _aliases.resolve_current_id(self._conn,artifact_id)

    def resolve_artifact_alias(self, artifact_id: str):
        return _aliases.resolve_alias(self._conn, artifact_id) if self._conn else None

    def record_source_revision(self, source_id: str, content_hash: str, media_type: str = "") -> str:
        """Note the file content a stage is about to read; returns its id."""
        with self._write_lock:
            revision = _source_revisions.record_source_revision(
                self._conn, source_id, content_hash, media_type)
        self._source_revision_cache[source_id] = revision
        return revision

    def current_source_revision(self, source_id: str) -> str:
        if source_id not in self._source_revision_cache:
            self._source_revision_cache[source_id] = _source_revisions.current_source_revision(
                self._conn, source_id)
        return self._source_revision_cache[source_id]

    def upsert_artifact(self, artifact: ArtifactRecord) -> str:
        values = artifact.db_values()
        if artifact.source_id and self._conn:
            _source_revisions.pin_source_revision(
                values, self.current_source_revision(artifact.source_id))
        values["payload_hash"] = values.get("payload_hash") or _artifact_payload_hash(values)
        now = _now()
        values["created_at"] = values.get("created_at") or now
        values["updated_at"] = now
        with self._write_lock:
            existing = self._conn.execute(
                "SELECT payload_hash, review_state FROM intelligence_artifacts WHERE artifact_id=?",
                (artifact.artifact_id,),
            ).fetchone()
            # A producer rerun must not replace a value that a person has
            # corrected, verified, or rejected. The producer proposal remains
            # available from the source and can be reconsidered explicitly.
            if existing and str(existing[0] or "") != values["payload_hash"]:
                stored = self.get_artifact(artifact.artifact_id)
                if stored and _artifact_payload_hash(
                    _artifact_hash_values_from_row(stored)
                ) == values["payload_hash"]:
                    # Same content (legacy hash or identity-only change).
                    _refresh_identity(self._conn, values, existing[1])
                    return artifact.artifact_id
            if existing and existing[1] not in {"corrected", "verified", "rejected"} \
                    and str(existing[0] or "") != values["payload_hash"]:
                # Content changed by a producer: keep old and new as history.
                values["revision"] = _source_revisions.record_machine_revision(
                    self._conn, stored, values)
            if existing and existing[1] in {"corrected", "verified", "rejected"}:
                if str(existing[0] or "") != values["payload_hash"]:
                    self._conn.execute(
                        "INSERT OR IGNORE INTO intelligence_artifact_proposals "
                        "(artifact_id, payload_hash, proposal_json, detected_at) "
                        "VALUES (?, ?, ?, ?)",
                        (artifact.artifact_id, values["payload_hash"],
                         json.dumps(artifact.__dict__, ensure_ascii=False, default=str), now),
                    )
                return artifact.artifact_id
            if existing and str(existing[0] or "") == values["payload_hash"]:
                _refresh_identity(self._conn, values, existing[1])
                return artifact.artifact_id
            self._conn.execute(
            """
            INSERT INTO intelligence_artifacts (
                artifact_id, source_id, source_path, source_type, artifact_type,
                storage_ref_type, storage_ref_id, parent_artifact_id,
                exact_text, normalized_text, normalized_key, locator_json, section_path_json,
                metadata_json, confidence, created_by, extractor_name,
                extractor_version, model_role, model_name, input_hash, payload_hash, status,
                review_state, revision, supersedes_artifact_id, created_at, updated_at
            ) VALUES (
                :artifact_id, :source_id, :source_path, :source_type, :artifact_type,
                :storage_ref_type, :storage_ref_id, :parent_artifact_id,
                :exact_text, :normalized_text, :normalized_key, :locator_json, :section_path_json,
                :metadata_json, :confidence, :created_by, :extractor_name,
                :extractor_version, :model_role, :model_name, :input_hash, :payload_hash, :status,
                :review_state, :revision, :supersedes_artifact_id, :created_at, :updated_at
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
                normalized_key=excluded.normalized_key,
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
                payload_hash=excluded.payload_hash,
                status=excluded.status,
                review_state=CASE
                    WHEN intelligence_artifacts.review_state IN ('corrected','rejected')
                    THEN intelligence_artifacts.review_state
                    ELSE excluded.review_state
                END,
                revision=MAX(intelligence_artifacts.revision, excluded.revision),
                supersedes_artifact_id=COALESCE(
                    excluded.supersedes_artifact_id,
                    intelligence_artifacts.supersedes_artifact_id
                ),
                updated_at=excluded.updated_at
            """,
            values,
            )
            self._upsert_artifact_fts(values, replace_existing=existing is not None)
            self._bump_knowledge_revision(
                values.get("source_id"), _artifact_family(values.get("artifact_type"))
            )
        return artifact.artifact_id

    def upsert_artifacts(self, artifacts: List[ArtifactRecord]) -> List[str]:
        if not artifacts or not self._conn:
            return []
        with self._write_lock:
            self._conn.execute("SAVEPOINT intelligence_artifact_batch")
            try:
                ids = [self.upsert_artifact(artifact) for artifact in artifacts]
                self._conn.execute("RELEASE SAVEPOINT intelligence_artifact_batch")
                return ids
            except Exception:
                self._conn.execute("ROLLBACK TO SAVEPOINT intelligence_artifact_batch")
                self._conn.execute("RELEASE SAVEPOINT intelligence_artifact_batch")
                raise

    def backfill_payload_hashes(
        self, source_id: Optional[str] = None, *, limit: int = 100000
    ) -> int:
        """Lazily hash legacy artifacts without changing semantic timestamps."""
        if not self._conn:
            return 0
        clauses = ["payload_hash IS NULL"]
        params: List[Any] = []
        if source_id:
            clauses.append("source_id=?")
            params.append(source_id)
        params.append(max(1, int(limit)))
        rows = self._conn.execute(
            f"SELECT {_ARTIFACT_COLUMNS} FROM intelligence_artifacts WHERE "
            + " AND ".join(clauses) + " LIMIT ?",
            tuple(params),
        ).fetchall()
        with self._write_lock:
            for raw in rows:
                row = self._artifact_row_to_dict(raw)
                payload_hash = _artifact_payload_hash(
                    _artifact_hash_values_from_row(row)
                )
                self._conn.execute(
                    "UPDATE intelligence_artifacts SET payload_hash=? "
                    "WHERE artifact_id=? AND payload_hash IS NULL",
                    (payload_hash, row["artifact_id"]),
                )
        return len(rows)

    def _bump_knowledge_revision(
        self, source_id: Optional[str], family: str
    ) -> None:
        if not self._conn or not family:
            return
        self._conn.execute(
            """
            INSERT INTO intelligence_knowledge_revisions (
                source_id, family, revision, updated_at
            ) VALUES (?, ?, 1, ?)
            ON CONFLICT(source_id, family) DO UPDATE SET
                revision=intelligence_knowledge_revisions.revision + 1,
                updated_at=excluded.updated_at
            """,
            (str(source_id or "__project__"), family, _now()),
        )

    def get_artifact(self, artifact_id: str) -> Optional[Dict[str, Any]]:
        if not self._conn or not artifact_id:
            return None
        cur = self._conn.execute(
            f"SELECT {_ARTIFACT_COLUMNS} FROM intelligence_artifacts WHERE artifact_id=?",
            (artifact_id,),
        )
        row = cur.fetchone()
        return self._artifact_row_to_dict(row) if row else None

    def get_artifacts_by_ids(self, artifact_ids):
        identities = list(dict.fromkeys(artifact_ids))
        if len(identities) > MAX_ARTIFACT_BATCH:
            raise ValueError(f"Read at most {MAX_ARTIFACT_BATCH} artifacts per batch.")
        if not self._conn or not identities:
            return {}
        slots = ",".join("?" for _ in identities)
        rows = self._conn.execute(
            f"SELECT {_ARTIFACT_COLUMNS} FROM intelligence_artifacts WHERE artifact_id IN ({slots})", identities)
        return {row[0]: self._artifact_row_to_dict(row) for row in rows.fetchall()}

    def get_artifact_aliases(self, artifact_ids):
        return _aliases.by_ids(self._conn, artifact_ids)

    def iter_artifacts_by_ids(self,artifact_ids):
        """Stream explicit identities through the same bounded bulk reader."""
        if not self._conn:
            return
        batch,seen=[],set()
        for identity in artifact_ids:
            if identity in seen:
                continue
            seen.add(identity); batch.append(identity)
            if len(batch)==MAX_ARTIFACT_BATCH:
                yield from self.get_artifacts_by_ids(batch).values()
                batch=[]
        if batch:
            yield from self.get_artifacts_by_ids(batch).values()

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
            f"SELECT {_ARTIFACT_COLUMNS} FROM intelligence_artifacts {where} "
            "ORDER BY updated_at DESC LIMIT ?",
            tuple(params),
        )
        return [self._artifact_row_to_dict(row) for row in cur.fetchall()]

    def count_artifact_query(self, query: ArtifactQuery) -> int:
        from core.db.intelligence_artifact_queries import compile_filters
        if not self._conn:
            return 0
        clauses, params = compile_filters(query, _artifact_types_for_families)
        where = " AND ".join(clauses) or "1"
        return int(self._conn.execute(f"SELECT COUNT(*) FROM intelligence_artifacts a WHERE {where}", params).fetchone()[0])

    def artifact_counts(self, source_id=None):
        """Aggregate overview counts without materializing artifact payloads."""
        from core.db.intelligence_overview_counts import counts
        return counts(self._conn, source_id)

    def query_artifacts(self, query: ArtifactQuery) -> Dict[str, Any]:
        """Bounded, revision-checked page using the shared typed query contract."""
        from core.db.intelligence_artifact_queries import page
        return page(self, query, _ARTIFACT_COLUMNS, _artifact_types_for_families)

    def query_event_dates(self,query,filters):
        """Reuse artifact paging with interval-aware prefilters for event projections."""
        from core.db.event_projection_queries import calendar_filters
        from core.db.intelligence_artifact_queries import page
        mode='event_dates:'+json.dumps([filters.date_from,filters.date_to])
        return page(self,query,_ARTIFACT_COLUMNS,_artifact_types_for_families,mode,calendar_filters(filters))

    def document_entity_positions(self, source_id, canonical_ids):
        from core.db.document_link_queries import entity_positions
        return entity_positions(self, source_id, canonical_ids)

    def document_entity_mentions(self, source_id, pages):
        from core.db.document_link_queries import entity_mentions
        return entity_mentions(self, source_id, pages)

    def document_link_sources(self, source_id, pages):
        from core.db.document_link_queries import sources
        return sources(self, source_id, pages)

    def iter_artifacts(self, query: ArtifactQuery):
        """Stream all matching rows; intervening edits invalidate the read."""
        from core.db.intelligence_artifact_queries import iterate
        return iterate(self, query)

    def query_entities(self, query: ArtifactQuery) -> Dict[str, Any]:
        """Select canonical entities through eligible, source-local mentions."""
        from core.db.intelligence_artifact_queries import page
        return page(self, query, _ARTIFACT_COLUMNS, _artifact_types_for_families, "entities")

    def facet_counts(self, query: ArtifactQuery, field: str, entities: bool = False) -> Dict[str, int]:
        """Rows per value of one field (entity type, kind, review state, role) for filter menus."""
        from core.db.intelligence_artifact_queries import facets
        return facets(self, query, field, _artifact_types_for_families, entities)

    def iter_entities(self, query: ArtifactQuery):
        from core.db.intelligence_artifact_queries import iterate
        return iterate(self, query, entities=True)

    def entity_contexts(self, entity_ids, query: ArtifactQuery, limit=100):
        from core.db.intelligence_artifact_queries import entity_contexts
        return entity_contexts(self, entity_ids, query, _ARTIFACT_COLUMNS, _artifact_types_for_families, limit) if self._conn else []

    def filter_artifacts(self, artifact_ids, query: ArtifactQuery):
        """Bulk canonical hydration using the same restrictions as cursor queries."""
        from core.db.intelligence_artifact_queries import compile_filters
        if not self._conn or not artifact_ids:
            return []
        clauses, params = compile_filters(query, _artifact_types_for_families)
        rows = []
        ids = list(dict.fromkeys(artifact_ids))
        for offset in range(0, len(ids), 400):
            chunk = ids[offset:offset + 400]
            where = " AND ".join(clauses + [f"a.artifact_id IN ({','.join('?' for _ in chunk)})"])
            found = self._conn.execute(
                f"SELECT {_ARTIFACT_COLUMNS} FROM intelligence_artifacts a WHERE {where}",
                (*params, *chunk),
            ).fetchall()
            rows.extend(self._artifact_row_to_dict(row) for row in found)
        return rows

    def save_artifact_revision(
        self,
        artifact_id: str,
        *,
        exact_text: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        review_state: str = ArtifactReviewState.CORRECTED.value,
        created_by: str = "user",
        field_changes: Optional[Dict[str, Any]] = None,
        provenance_confirmed: bool = False,
        extra_artifact_types: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Create an immutable revision and make it the current artifact value."""
        current = self.get_artifact(artifact_id)
        if not current or not self._conn:
            return None
        from core.services.intelligence.artifact_edit_schema import (
            validate_artifact_changes,
        )
        patch = dict(field_changes or {})
        if exact_text is not None:
            patch["exact_text"] = exact_text
        if metadata is not None:
            patch["metadata"] = {**(current.get("metadata") or {}), **metadata}
        if patch:
            patch = validate_artifact_changes(
                current, patch, provenance_confirmed=provenance_confirmed,
                extra_types=extra_artifact_types or (),
            )
        snapshot = {**current, **patch}
        snapshot["review_state"] = patch.get("review_state", review_state)
        if snapshot["review_state"] not in {state.value for state in ArtifactReviewState}:
            raise ValueError("Invalid artifact review state.")
        revision = int(current.get("revision") or 1) + 1
        snapshot["revision"] = revision
        if "exact_text" in patch:
            snapshot["normalized_text"] = (
                (snapshot["exact_text"] or "").strip().lower() or None
            )
        payload_hash = _artifact_payload_hash(
            _artifact_hash_values_from_row(snapshot)
        )
        revision_id = f"artifact_revision:{uuid.uuid5(uuid.NAMESPACE_URL, f'{artifact_id}|{revision}')}"
        now = _now()
        with self._write_lock:
            self._conn.execute("SAVEPOINT intelligence_artifact_revision")
            try:
                self._save_revision_values(
                    artifact_id, snapshot, revision_id, revision, payload_hash,
                    now, created_by, current,
                )
                updated = self.get_artifact(artifact_id)
                if updated:
                    self._upsert_artifact_fts(_artifact_hash_values_from_row(updated))
                self._conn.execute("RELEASE SAVEPOINT intelligence_artifact_revision")
            except Exception:
                self._conn.execute("ROLLBACK TO SAVEPOINT intelligence_artifact_revision")
                self._conn.execute("RELEASE SAVEPOINT intelligence_artifact_revision")
                raise
        return updated

    def _save_revision_values(
        self, artifact_id, snapshot, revision_id, revision, payload_hash,
        now, created_by, current,
    ):
        self._conn.execute(
                """
                INSERT INTO intelligence_artifact_revisions (
                    revision_id, artifact_id, revision, snapshot_json,
                    review_state, created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    revision_id,
                    artifact_id,
                    revision,
                    json.dumps(snapshot, ensure_ascii=False, default=str),
                    snapshot["review_state"],
                    created_by,
                    now,
                ),
            )
        values = _artifact_hash_values_from_row(snapshot)
        values.update({
            "artifact_id": artifact_id, "review_state": snapshot["review_state"],
            "revision": revision, "payload_hash": payload_hash,
            "updated_at": now,
        })
        editable_columns = [
            "source_id", "source_path", "source_type", "artifact_type",
            "storage_ref_type", "storage_ref_id", "parent_artifact_id",
            "exact_text", "normalized_text", "normalized_key", "locator_json",
            "section_path_json", "metadata_json", "confidence", "created_by",
            "extractor_name", "extractor_version", "model_role", "model_name",
            "input_hash", "status", "review_state", "revision",
            "supersedes_artifact_id", "payload_hash", "updated_at",
        ]
        self._conn.execute(
            "UPDATE intelligence_artifacts SET "
            + ", ".join(f"{name}=:{name}" for name in editable_columns)
            + " WHERE artifact_id=:artifact_id", values,
        )
        self._bump_knowledge_revision(
            current.get("source_id"),
            _artifact_family(current.get("artifact_type")),
        )
        if (snapshot.get("source_id"), snapshot.get("artifact_type")) != (
            current.get("source_id"), current.get("artifact_type")
        ):
            self._bump_knowledge_revision(
                snapshot.get("source_id"),
                _artifact_family(snapshot.get("artifact_type")),
            )
        if current.get("artifact_type") in {"sentence", "transcript_segment"}:
            self.upsert_stage_status(
                str(current.get("source_id") or ""), "claims",
                {"status": "stale", "error_text": "Source span revised"},
            )
        elif current.get("artifact_type") == "argument_unit":
            self.upsert_stage_status(
                str(current.get("source_id") or ""), "graph_seeds",
                {"status": "stale", "error_text": "Argument unit revised"},
            )

    def set_artifact_review_state(
        self, artifact_id: str, review_state: str, *, created_by: str = "user"
    ) -> Optional[Dict[str, Any]]:
        allowed = {state.value for state in ArtifactReviewState}
        if review_state not in allowed:
            raise ValueError(f"Unknown artifact review state: {review_state}")
        return self.save_artifact_revision(
            artifact_id,
            review_state=review_state,
            created_by=created_by,
        )

    def list_artifact_revisions(self, artifact_id: str) -> List[Dict[str, Any]]:
        if not self._conn or not artifact_id:
            return []
        cur = self._conn.execute(
            """
            SELECT revision_id, artifact_id, revision, snapshot_json,
                   review_state, created_by, created_at
            FROM intelligence_artifact_revisions
            WHERE artifact_id=? ORDER BY revision DESC
            """,
            (artifact_id,),
        )
        return [
            {
                "revision_id": row[0],
                "artifact_id": row[1],
                "revision": row[2],
                "snapshot": _json(row[3], {}),
                "review_state": row[4],
                "created_by": row[5],
                "created_at": row[6],
            }
            for row in cur.fetchall()
        ]

    def search_artifacts(
        self, query: str, artifact_types: Optional[List[str]] = None,
        source_ids: Optional[List[str]] = None, limit: int = 20, *,
        include_stale: bool = False, review_states: Optional[List[str]] = None,
        artifact_query: Optional[ArtifactQuery] = None,
    ) -> List[Dict[str, Any]]:
        from core.db.intelligence_artifact_queries import search
        from core.models.intelligence_models import SourceScope
        if not self._conn or not query.strip():
            return []
        typed = artifact_query or ArtifactQuery(
            source_scope=SourceScope(source_ids=source_ids or []),
            artifact_types=artifact_types or [],
            statuses=["complete", "stale"] if include_stale else ["complete"],
            review_states=review_states or ["unreviewed", "verified", "corrected"],
            current_only=True,
        )
        return search(self, query, typed, _ARTIFACT_COLUMNS, _artifact_types_for_families, _fts_query, limit)

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

    def interrupt_unfinished_stages(self, before: str, message: str, live=frozenset()) -> int:
        """Stages left queued or running with no job behind them (an earlier session, or a job
        that stopped). ``live`` holds the (source_id, stage) pairs that do have a job."""
        if not self._conn:
            return 0
        with self._write_lock:
            now = _now()
            rows = [tuple(row) for row in self._conn.execute(
                "SELECT source_id, stage FROM intelligence_stage_status "
                "WHERE status IN ('queued','running') AND updated_at < ?", (before,))]
            orphaned = [key for key in rows if key not in live]
            self._conn.executemany(
                "UPDATE intelligence_stage_status SET status='cancelled', error_text=?, job_id=NULL, updated_at=? "
                "WHERE source_id=? AND stage=? AND status IN ('queued','running')",
                [(message, now, *key) for key in orphaned])
            self._conn.executemany(
                "UPDATE intelligence_stage_runs SET status='cancelled', error_text=?, finished_at=? "
                "WHERE source_id=? AND stage=? AND status IN ('queued','running') AND created_at < ?",
                [(message, now, *key, before) for key in orphaned])
            self._conn.commit()
        return len(orphaned)

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

    def list_artifact_proposals(self, artifact_id: str) -> List[Dict[str, Any]]:
        """Show producer observations withheld from a reviewed artifact."""
        if not self._conn or not artifact_id:
            return []
        rows = self._conn.execute(
            "SELECT payload_hash, proposal_json, detected_at "
            "FROM intelligence_artifact_proposals WHERE artifact_id=? "
            "ORDER BY detected_at DESC LIMIT 100", (artifact_id,),
        ).fetchall()
        return [
            {"payload_hash": row[0], "proposal": _json(row[1], {}),
             "detected_at": row[2]}
            for row in rows
        ]

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

    def record_stage_run(self, run_id: str, values: Dict[str, Any]) -> None:
        if not self._conn or not run_id:
            return
        payload = dict(values or {})
        payload["run_id"] = run_id
        payload.setdefault("created_at", _now())
        payload["output_artifact_ids_json"] = json.dumps(
            payload.get("output_artifact_ids") or [], ensure_ascii=False
        )
        self._conn.execute(
            """
            INSERT INTO intelligence_stage_runs (
                run_id, source_id, stage, status, input_hash, settings_hash,
                dependency_hash, producer_name, producer_version, prompt_key,
                prompt_version, model_role, model_name,
                output_artifact_ids_json, error_text, started_at, finished_at,
                created_at
            ) VALUES (
                :run_id, :source_id, :stage, :status, :input_hash, :settings_hash,
                :dependency_hash, :producer_name, :producer_version, :prompt_key,
                :prompt_version, :model_role, :model_name,
                :output_artifact_ids_json, :error_text, :started_at, :finished_at,
                :created_at
            )
            ON CONFLICT(run_id) DO UPDATE SET
                status=excluded.status,
                input_hash=excluded.input_hash,
                settings_hash=excluded.settings_hash,
                dependency_hash=excluded.dependency_hash,
                producer_name=excluded.producer_name,
                producer_version=excluded.producer_version,
                prompt_key=excluded.prompt_key,
                prompt_version=excluded.prompt_version,
                model_role=excluded.model_role,
                model_name=excluded.model_name,
                output_artifact_ids_json=excluded.output_artifact_ids_json,
                error_text=excluded.error_text,
                started_at=COALESCE(
                    intelligence_stage_runs.started_at, excluded.started_at
                ),
                finished_at=excluded.finished_at
            """,
            {
                "run_id": payload.get("run_id"),
                "source_id": payload.get("source_id"),
                "stage": payload.get("stage"),
                "status": payload.get("status"),
                "input_hash": payload.get("input_hash"),
                "settings_hash": payload.get("settings_hash"),
                "dependency_hash": payload.get("dependency_hash"),
                "producer_name": payload.get("producer_name"),
                "producer_version": payload.get("producer_version"),
                "prompt_key": payload.get("prompt_key"),
                "prompt_version": payload.get("prompt_version"),
                "model_role": payload.get("model_role"),
                "model_name": payload.get("model_name"),
                "output_artifact_ids_json": payload.get("output_artifact_ids_json"),
                "error_text": payload.get("error_text"),
                "started_at": payload.get("started_at"),
                "finished_at": payload.get("finished_at"),
                "created_at": payload.get("created_at"),
            },
        )

    def record_stage_run_identity(self, run_id: str, identity: Dict[str, Any]) -> None:
        with self._write_lock:
            _record_run_identity(self._conn, run_id, identity)

    def stage_run_identities(self, run_ids) -> Dict[str, Dict[str, Any]]:
        return _run_identities(self._conn, run_ids)

    def list_stage_runs(
        self, source_id: Optional[str] = None, stage: Optional[str] = None, limit: int = 200
    ) -> List[Dict[str, Any]]:
        from core.models.intelligence_models import StageRunQuery, SourceScope
        query = StageRunQuery(source_scope=SourceScope(source_ids=[source_id] if source_id else []),
                              stages=[stage] if stage else [], limit=max(1, min(int(limit), 1000)))
        return self.query_stage_runs(query)["items"]

    def latest_stage_run(self, source_id: str, stage: str) -> Optional[Dict[str, Any]]:
        runs = self.list_stage_runs(source_id=source_id, stage=stage, limit=1)
        return runs[0] if runs else None

    def query_stage_runs(self, query) -> Dict[str, Any]:
        from core.db.intelligence_run_queries import query_runs
        with self._write_lock:
            page = query_runs(self, query)
            identities = self.stage_run_identities([row["run_id"] for row in page["items"]])
            for row in page["items"]:
                if identity := identities.get(row["run_id"]):
                    row["metadata"] = {"producer": identity}
            return page

    def stage_run_counts(self, source_id=None):
        if not self._conn:
            return {}
        rows = self._conn.execute("SELECT status,COUNT(*) FROM intelligence_stage_runs WHERE (? IS NULL OR source_id=?) GROUP BY status", (source_id, source_id))
        return dict(rows.fetchall())

    def set_backfill_state(
        self,
        source_id: str,
        *,
        selected_stages: Optional[List[str]] = None,
        completed_stages: Optional[List[str]] = None,
        status: str = "pending",
        error_text: str = "",
    ) -> None:
        if not self._conn or not source_id:
            return
        self._conn.execute(
            """
            INSERT INTO intelligence_backfill_state (
                source_id, selected_stages_json, completed_stages_json,
                status, error_text, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id) DO UPDATE SET
                selected_stages_json=excluded.selected_stages_json,
                completed_stages_json=excluded.completed_stages_json,
                status=excluded.status,
                error_text=excluded.error_text,
                updated_at=excluded.updated_at
            """,
            (
                source_id,
                json.dumps(selected_stages or [], ensure_ascii=False),
                json.dumps(completed_stages or [], ensure_ascii=False),
                status,
                error_text or None,
                _now(),
            ),
        )

    def list_backfill_state(self) -> List[Dict[str, Any]]:
        if not self._conn:
            return []
        cur = self._conn.execute(
            """
            SELECT source_id, selected_stages_json, completed_stages_json,
                   status, error_text, updated_at
            FROM intelligence_backfill_state ORDER BY updated_at DESC
            """
        )
        return [
            {
                "source_id": row[0],
                "selected_stages": _json(row[1], []),
                "completed_stages": _json(row[2], []),
                "status": row[3],
                "error_text": row[4],
                "updated_at": row[5],
            }
            for row in cur.fetchall()
        ]

    def upsert_artifact_embedding(self, artifact_id: str, values: Dict[str, Any]) -> None:
        if not self._conn or not artifact_id:
            return
        payload = dict(values or {})
        existing = self.get_artifact_embedding(artifact_id)
        comparable = {key: payload.get(key) for key in (
            "source_id", "embedding_ref_id", "model_role", "model_name", "input_hash")}
        comparable.update(status=payload.get("status", "not_run"),
                          embedding_ref_type=payload.get("embedding_ref_type", "chroma"))
        if existing and all(existing.get(key) == value for key, value in comparable.items()):
            return
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
        self._bump_knowledge_revision(payload.get("source_id"), "embedding")

    def get_artifact_embedding(self, artifact_id: str) -> Optional[Dict[str, Any]]:
        if not self._conn or not artifact_id:
            return None
        row = self._conn.execute(
            "SELECT artifact_id, source_id, embedding_ref_type, embedding_ref_id, "
            "status, model_role, model_name, input_hash, created_at, updated_at "
            "FROM intelligence_artifact_embeddings WHERE artifact_id=?",
            (artifact_id,),
        ).fetchone()
        if not row:
            return None
        keys = [
            "artifact_id", "source_id", "embedding_ref_type", "embedding_ref_id",
            "status", "model_role", "model_name", "input_hash", "created_at",
            "updated_at",
        ]
        return dict(zip(keys, row))

    def list_artifact_embeddings(self, source_id: str) -> List[Dict[str, Any]]:
        if not self._conn:
            return []
        rows = self._conn.execute(
            "SELECT artifact_id, source_id, embedding_ref_type, embedding_ref_id, "
            "status, model_role, model_name, input_hash, created_at, updated_at "
            "FROM intelligence_artifact_embeddings WHERE source_id=?",
            (source_id,),
        ).fetchall()
        keys = [
            "artifact_id", "source_id", "embedding_ref_type", "embedding_ref_id",
            "status", "model_role", "model_name", "input_hash", "created_at",
            "updated_at",
        ]
        return [dict(zip(keys, row)) for row in rows]

    def delete_artifact_embeddings(self, artifact_ids: List[str]) -> int:
        if not self._conn or not artifact_ids:
            return 0
        placeholders = ",".join("?" for _ in artifact_ids)
        sources = self._conn.execute(
            f"SELECT DISTINCT source_id FROM intelligence_artifact_embeddings WHERE artifact_id IN ({placeholders})",
            tuple(artifact_ids),
        ).fetchall()
        cur = self._conn.execute(
            f"DELETE FROM intelligence_artifact_embeddings "
            f"WHERE artifact_id IN ({placeholders})",
            tuple(artifact_ids),
        )
        if cur.rowcount:
            for source in sources:
                self._bump_knowledge_revision(source[0], "embedding")
        return cur.rowcount or 0

    def mark_artifact_embeddings_status(self, source_id: str, status: str) -> int:
        if not self._conn or not source_id:
            return 0
        cur = self._conn.execute(
            "UPDATE intelligence_artifact_embeddings SET status=?, updated_at=? WHERE source_id=? AND status!=?",
            (status, _now(), source_id, status),
        )
        if cur.rowcount:
            self._bump_knowledge_revision(source_id, "embedding")
        return cur.rowcount or 0

    def count_artifact_embeddings(
        self, source_id: Optional[str] = None, status: str = "complete"
    ) -> int:
        """Count canonical vector records, optionally for one source."""
        if not self._conn:
            return 0
        clauses = []
        params: List[Any] = []
        if source_id:
            clauses.append("source_id=?")
            params.append(source_id)
        if status:
            clauses.append("status=?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        row = self._conn.execute(
            f"SELECT COUNT(*) FROM intelligence_artifact_embeddings {where}",
            tuple(params),
        ).fetchone()
        return int(row[0] or 0) if row else 0

    def upsert_edge(self, edge: ArtifactEdgeRecord) -> str:
        if not self._conn:
            return edge.edge_id
        with self._write_lock:
            metadata_json = json.dumps(
                edge.metadata, sort_keys=True, ensure_ascii=False
            )
            existing = self._conn.execute(
                "SELECT confidence, metadata_json FROM intelligence_artifact_edges "
                "WHERE edge_id=?", (edge.edge_id,),
            ).fetchone()
            edge_changed = (
                not existing
                or max(float(existing[0] or 0.0), float(edge.confidence or 0.0))
                != float(existing[0] or 0.0)
                or json.dumps(_json(existing[1], {}), sort_keys=True, ensure_ascii=False)
                != metadata_json
            )
            if edge_changed:
                self._conn.execute(
                    """
                INSERT INTO intelligence_artifact_edges (
                    edge_id, source_artifact_id, target_artifact_id, edge_type,
                    confidence, created_by, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(edge_id) DO UPDATE SET
                    confidence=MAX(
                        COALESCE(intelligence_artifact_edges.confidence, 0),
                        COALESCE(excluded.confidence, 0)
                    ),
                    created_by='aggregate',
                    metadata_json=excluded.metadata_json
                    """,
                    (
                    edge.edge_id,
                    edge.source_artifact_id,
                    edge.target_artifact_id,
                    edge.edge_type,
                    edge.confidence,
                    edge.created_by,
                    metadata_json,
                    edge.created_at or _now(),
                    ),
                )
            evidence_ids = list(edge.evidence_artifact_ids or [])
            if not evidence_ids:
                evidence_ids = [None]
            assertion_changed = False
            for evidence_id in evidence_ids:
                assertion_changed = (
                    self._upsert_edge_assertion(edge, evidence_id)
                    or assertion_changed
                )
            if edge_changed or assertion_changed:
                source_row = self._conn.execute(
                    "SELECT source_id FROM intelligence_artifacts WHERE artifact_id=?",
                    (edge.source_artifact_id,),
                ).fetchone()
                self._bump_knowledge_revision(
                    source_row[0] if source_row else None, "relation"
                )
        return edge.edge_id

    def _upsert_edge_assertion(
        self, edge: ArtifactEdgeRecord, evidence_artifact_id: Optional[str]
    ) -> bool:
        assertion_key = "|".join([
            edge.edge_id,
            evidence_artifact_id or "",
            edge.created_by or "core",
            edge.producer_version or "",
        ])
        assertion_id = (
            "edge_assertion:"
            + str(uuid.uuid5(uuid.NAMESPACE_URL, assertion_key))
        )
        metadata_json = json.dumps(edge.metadata, sort_keys=True, ensure_ascii=False)
        existing = self._conn.execute(
            "SELECT confidence, producer_version, metadata_json "
            "FROM intelligence_edge_assertions WHERE assertion_id=?",
            (assertion_id,),
        ).fetchone()
        if existing and (
            float(existing[0] or 0.0) == float(edge.confidence or 0.0)
            and str(existing[1] or "") == str(edge.producer_version or "")
            and json.dumps(_json(existing[2], {}), sort_keys=True, ensure_ascii=False)
            == metadata_json
        ):
            return False
        self._conn.execute(
            """
            INSERT INTO intelligence_edge_assertions (
                assertion_id, edge_id, source_artifact_id, target_artifact_id,
                edge_type, evidence_artifact_id, confidence, created_by,
                producer_version, metadata_json, review_state, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(assertion_id) DO UPDATE SET
                confidence=excluded.confidence,
                producer_version=excluded.producer_version,
                metadata_json=excluded.metadata_json,
                review_state=CASE
                    WHEN intelligence_edge_assertions.review_state IN ('verified','corrected','rejected')
                    THEN intelligence_edge_assertions.review_state
                    ELSE excluded.review_state
                END
            """,
            (
                assertion_id,
                edge.edge_id,
                edge.source_artifact_id,
                edge.target_artifact_id,
                edge.edge_type,
                evidence_artifact_id,
                edge.confidence,
                edge.created_by,
                edge.producer_version,
                metadata_json,
                ArtifactReviewState.UNREVIEWED.value,
                edge.created_at or _now(),
            ),
        )
        return True

    def list_edge_assertions(self, edge_id: str) -> List[Dict[str, Any]]:
        if not self._conn or not edge_id:
            return []
        cur = self._conn.execute(
            """
            SELECT assertion_id, edge_id, source_artifact_id, target_artifact_id,
                   edge_type, evidence_artifact_id, confidence, created_by,
                   producer_version, metadata_json, review_state, created_at
            FROM intelligence_edge_assertions
            WHERE edge_id=? ORDER BY created_at DESC
            """,
            (edge_id,),
        )
        keys = [
            "assertion_id", "edge_id", "source_artifact_id",
            "target_artifact_id", "edge_type", "evidence_artifact_id",
            "confidence", "created_by", "producer_version", "metadata_json",
            "review_state", "created_at",
        ]
        result = []
        for row in cur.fetchall():
            item = dict(zip(keys, row))
            item["metadata"] = _json(item.pop("metadata_json"), {})
            result.append(item)
        return result

    def set_edge_assertion_review_state(
        self, assertion_id: str, review_state: str
    ) -> Optional[Dict[str, Any]]:
        if review_state not in {state.value for state in ArtifactReviewState}:
            raise ValueError(f"Invalid review state: {review_state}")
        with self._write_lock:
            row = self._conn.execute(
                "SELECT a.edge_id, a.review_state, i.source_id "
                "FROM intelligence_edge_assertions a "
                "LEFT JOIN intelligence_artifacts i "
                "ON i.artifact_id=a.source_artifact_id WHERE a.assertion_id=?",
                (assertion_id,),
            ).fetchone()
            if row and row[1] != review_state:
                self._conn.execute(
                    "UPDATE intelligence_edge_assertions SET review_state=? "
                    "WHERE assertion_id=?", (review_state, assertion_id),
                )
                self._bump_knowledge_revision(row[2], "relation")
        if not row:
            return None
        return next(
            (
                assertion for assertion in self.list_edge_assertions(row[0])
                if assertion.get("assertion_id") == assertion_id
            ),
            None,
        )

    def list_edges_for_artifacts(self, artifact_ids) -> List[Dict[str, Any]]:
        from core.db.intelligence_edge_queries import edges_for_artifacts
        with self._write_lock:
            return edges_for_artifacts(self, artifact_ids)

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
        result = []
        for row in cur.fetchall():
            edge = {
                "edge_id": row[0],
                "source_artifact_id": row[1],
                "target_artifact_id": row[2],
                "edge_type": row[3],
                "confidence": row[4],
                "created_by": row[5],
                "metadata": _json(row[6], {}),
                "created_at": row[7],
            }
            edge["assertions"] = self.list_edge_assertions(edge["edge_id"])
            result.append(edge)
        return result

    def citation_assignments(self, source_id: Optional[str] = None, *, artifact_ids=None) -> Dict[str, Optional[str]]:
        from core.db.intelligence_citation_queries import assignments
        with self._write_lock:
            return assignments(self, source_id, artifact_ids)

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
            """
            UPDATE intelligence_artifacts SET status=?, updated_at=?
            WHERE source_id=? AND artifact_type NOT IN ('source','annotation')
            """,
            (status, _now(), source_id),
        )
        self.mark_source_stages_status(source_id, status)
        return cur.rowcount or 0

    def mark_artifacts_by_stages_status(
        self, source_id: str, stages: List[str], status: str
    ) -> int:
        if not self._conn or not source_id or not stages:
            return 0
        placeholders = ",".join("?" for _ in stages)
        cur = self._conn.execute(
            "UPDATE intelligence_artifacts SET status=?, updated_at=? "
            f"WHERE source_id=? AND json_extract(metadata_json, '$.stage') IN ({placeholders})",
            (status, _now(), source_id, *stages),
        )
        return cur.rowcount or 0

    def mark_artifact_ids_status(self, artifact_ids: List[str], status: str) -> int:
        """Mark only superseded canonical rows and bump affected revisions once."""
        if not self._conn or not artifact_ids:
            return 0
        placeholders = ",".join("?" for _ in artifact_ids)
        rows = self._conn.execute(
            f"SELECT {_ARTIFACT_COLUMNS} FROM intelligence_artifacts "
            f"WHERE artifact_id IN ({placeholders})",
            tuple(artifact_ids),
        ).fetchall()
        changed = [
            self._artifact_row_to_dict(row) for row in rows
            if str(self._artifact_row_to_dict(row).get("status") or "") != status
        ]
        if not changed:
            return 0
        now = _now()
        updates = []
        for row in changed:
            updated = {**row, "status": status}
            payload_hash = _artifact_payload_hash(
                _artifact_hash_values_from_row(updated)
            )
            updates.append((status, payload_hash, now, row["artifact_id"]))
        self._conn.executemany(
            "UPDATE intelligence_artifacts SET status=?, payload_hash=?, "
            "updated_at=? WHERE artifact_id=?",
            updates,
        )
        for source_id, family in {
            (row.get("source_id"), _artifact_family(row.get("artifact_type")))
            for row in changed
        }:
            self._bump_knowledge_revision(source_id, family)
        return len(changed)

    def archive_derived_artifacts(
        self, source_id: str, stage: Optional[str] = None
    ) -> int:
        if not self._conn or not source_id:
            return 0
        clauses = [
            "source_id=?",
            "artifact_type NOT IN ('source','annotation','workspace_node_ref')",
        ]
        params: List[Any] = [source_id]
        if stage:
            clauses.append("json_extract(metadata_json, '$.stage')=?")
            params.append(stage)
        cur = self._conn.execute(
            "UPDATE intelligence_artifacts "
            "SET status='archived', updated_at=? WHERE "
            + " AND ".join(clauses),
            (_now(), *params),
        )
        if stage:
            self.mark_source_stages_status(source_id, "not_run", stages=[stage])
        return cur.rowcount or 0

    def delete_derived_artifacts(self, source_id: str, stage: Optional[str] = None) -> int:
        """Permanently purge derived data.

        User-facing flows should call ``archive_derived_artifacts`` unless an
        explicitly confirmed permanent purge was requested.
        """
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
        self._conn.execute(
            """
            DELETE FROM intelligence_edge_assertions
            WHERE source_artifact_id NOT IN (SELECT artifact_id FROM intelligence_artifacts)
               OR target_artifact_id NOT IN (SELECT artifact_id FROM intelligence_artifacts)
            """
        )
        try:
            _fts_index.purge_orphans(self._conn)
        except Exception:
            pass
        if stage:
            self.mark_source_stages_status(source_id, "not_run", stages=[stage])
        return cur.rowcount or 0

    def purge_source_intelligence(self, source_id: str) -> int:
        """Delete all intelligence state owned by one source.

        This is intentionally broader than ``delete_derived_artifacts``: source
        removal must also erase ledgers, revisions, embeddings and source rows.
        The caller removes vectors from the external index first.
        """
        if not self._conn or not source_id:
            return 0
        artifact_count = int(self._conn.execute(
            "SELECT COUNT(*) FROM intelligence_artifacts WHERE source_id=?",
            (source_id,),
        ).fetchone()[0] or 0)
        while True:
            rows = self._conn.execute(
                "SELECT artifact_id FROM intelligence_artifacts "
                "WHERE source_id=? LIMIT ?", (source_id, MAX_ARTIFACT_BATCH),
            ).fetchall()
            artifact_ids = [row[0] for row in rows]
            if not artifact_ids:
                break
            self._purge_artifact_batch(artifact_ids)
            # Release SQLite's transaction between bounded batches. The purge
            # is resumable and UI reads can interleave instead of waiting for
            # an entire book's artifacts to disappear.
            self._conn.commit()
        for table in (
            "intelligence_stage_status", "intelligence_stage_runs", "intelligence_source_settings",
            "intelligence_jobs", "intelligence_analysis_runs",
            "intelligence_backfill_state", "intelligence_knowledge_revisions",
            "intelligence_source_revisions",
        ):
            self._conn.execute(f"DELETE FROM {table} WHERE source_id=?", (source_id,))
        self._conn.execute("DELETE FROM intelligence_retrieval_cache")
        self._conn.commit()
        return artifact_count

    def _purge_artifact_batch(self, artifact_ids: List[str]) -> None:
        marks = ",".join("?" for _ in artifact_ids)
        for table, column in (
            ("intelligence_artifact_revisions", "artifact_id"),
            ("intelligence_artifact_proposals", "artifact_id"),
            ("intelligence_review_decisions", "artifact_id"),
            ("intelligence_entity_rules", "target_artifact_id"),
            ("intelligence_artifact_embeddings", "artifact_id"),
        ):
            self._conn.execute(
                f"DELETE FROM {table} WHERE {column} IN ({marks})", artifact_ids
            )
        for table, columns in (
            ("intelligence_artifact_aliases", ("old_artifact_id", "new_artifact_id")),
            ("intelligence_artifact_edges", ("source_artifact_id", "target_artifact_id")),
            ("intelligence_edge_assertions", (
                "source_artifact_id", "target_artifact_id", "evidence_artifact_id",
            )),
        ):
            where = " OR ".join(f"{column} IN ({marks})" for column in columns)
            self._conn.execute(
                f"DELETE FROM {table} WHERE {where}", artifact_ids * len(columns)
            )
        try:
            _fts_index.purge_ids(self._conn, artifact_ids)
        except Exception:
            pass
        self._conn.execute(
            f"DELETE FROM intelligence_artifacts WHERE artifact_id IN ({marks})",
            artifact_ids,
        )

    def list_intelligence_source_ids(self) -> List[str]:
        if not self._conn:
            return []
        rows = self._conn.execute(
            "SELECT DISTINCT source_id FROM intelligence_artifacts "
            "WHERE source_id IS NOT NULL AND source_id<>''"
        ).fetchall()
        return [row[0] for row in rows]

    def _upsert_artifact_fts(self, values: Dict[str, Any], *, replace_existing=True) -> None:
        try:
            _fts_index.upsert(self._conn, values, replace_existing=replace_existing)
        except Exception:
            pass

    def reindex_source_fts(self, source_id: str) -> List[str]:
        if not self._conn or not source_id:
            return []
        rows = self.list_artifacts(source_id=source_id, limit=100000)
        for row in rows:
            self._upsert_artifact_fts({
                "artifact_id": row.get("artifact_id"),
                "source_id": row.get("source_id"),
                "artifact_type": row.get("artifact_type"),
                "exact_text": row.get("exact_text"),
                "normalized_text": row.get("normalized_text"),
                "metadata_json": json.dumps(
                    row.get("metadata") or {}, ensure_ascii=False
                ),
            })
        return [row["artifact_id"] for row in rows]

    def _search_artifacts_fts(
        self,
        query: str,
        artifact_types: Optional[List[str]],
        source_ids: Optional[List[str]],
        limit: int,
        *,
        include_stale: bool = False,
        review_states: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        try:
            clauses = ["intelligence_artifacts_fts MATCH ?"]
            params: List[Any] = [_fts_query(query)]
            if not include_stale:
                clauses.append("a.status='complete'")
            if review_states:
                clauses.append(
                    f"a.review_state IN ({','.join('?' for _ in review_states)})"
                )
                params.extend(review_states)
            if artifact_types:
                clauses.append(f"a.artifact_type IN ({','.join('?' for _ in artifact_types)})")
                params.extend(artifact_types)
            if source_ids:
                clauses.append(f"a.source_id IN ({','.join('?' for _ in source_ids)})")
                params.extend(source_ids)
            params.append(max(1, int(limit)))
            select_cols = ", ".join(
                f"a.{col.strip()}" for col in _ARTIFACT_COLUMNS.split(",")
            )
            cur = self._conn.execute(
                f"""
                SELECT {select_cols}
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

    def update_artifact_metadata(self, artifact_id: str, updates: Dict[str, Any]) -> None:
        """Merge ``updates`` into an artifact's metadata_json in place.

        Used by later deterministic stages (e.g. CLAIMS writes class_label /
        class_confidence onto existing sentence artifacts) without rebuilding
        the whole record.
        """
        if not self._conn or not artifact_id or not updates:
            return
        current = self.get_artifact(artifact_id)
        if not current:
            return
        metadata = dict(current.get("metadata") or {})
        if not isinstance(metadata, dict):
            metadata = {}
        metadata.update(updates)
        if metadata == (current.get("metadata") or {}):
            return
        updated = {**current, "metadata": metadata}
        payload_hash = _artifact_payload_hash(_artifact_hash_values_from_row(updated))
        self._conn.execute(
            "UPDATE intelligence_artifacts SET metadata_json=?, payload_hash=?, "
            "updated_at=? WHERE artifact_id=?",
            (json.dumps(metadata, ensure_ascii=False), payload_hash, _now(), artifact_id),
        )
        self._upsert_artifact_fts(_artifact_hash_values_from_row(updated))
        self._bump_knowledge_revision(
            current.get("source_id"), _artifact_family(current.get("artifact_type"))
        )

    # -- Entity resolution helpers -----------------------------------------

    def find_canonical_entity(
        self, normalized_key: str, entity_type: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Return the canonical entity artifact for a normalized_key, if any.

        Canonical entities are ``artifact_type='entity'`` rows; resolution
        groups every mention of the same real-world entity under one
        normalized_key across all sources in the project.
        """
        if not self._conn or not normalized_key:
            return None
        clauses = ["artifact_type='entity'", "normalized_key=?"]
        params: List[Any] = [normalized_key]
        if entity_type:
            clauses.append("json_extract(metadata_json, '$.entity_type')=?")
            params.append(entity_type)
        cur = self._conn.execute(
            f"SELECT {_ARTIFACT_COLUMNS} FROM intelligence_artifacts "
            f"WHERE {' AND '.join(clauses)} ORDER BY created_at ASC LIMIT 1",
            tuple(params),
        )
        row = cur.fetchone()
        return self._artifact_row_to_dict(row) if row else None

    def list_entities_by_keys(self, keys: List[str], limit: int = 50) -> List[Dict[str, Any]]:
        """Canonical entity artifacts whose normalized_key is in ``keys``.

        Used by entity-anchored retrieval to resolve query terms to canonical
        entities across the whole project (source_id is null on canonicals).
        """
        if not self._conn or not keys:
            return []
        placeholders = ",".join("?" for _ in keys)
        cur = self._conn.execute(
            f"SELECT {_ARTIFACT_COLUMNS} FROM intelligence_artifacts "
            f"WHERE artifact_type='entity' AND normalized_key IN ({placeholders}) "
            "LIMIT ?",
            (*keys, max(1, int(limit))),
        )
        return [self._artifact_row_to_dict(row) for row in cur.fetchall()]

    def list_entity_mentions(
        self, normalized_key: str, entity_type: str, limit: int = 100000
    ) -> List[Dict[str, Any]]:
        if not self._conn or not normalized_key or not entity_type:
            return []
        cur = self._conn.execute(
            f"""
            SELECT {_ARTIFACT_COLUMNS} FROM intelligence_artifacts
            WHERE artifact_type='entity_mention'
              AND normalized_key=?
              AND json_extract(metadata_json, '$.entity_type')=?
              AND status='complete'
              AND review_state NOT IN ('rejected','superseded')
            ORDER BY created_at ASC LIMIT ?
            """,
            (normalized_key, entity_type, max(1, int(limit))),
        )
        return [self._artifact_row_to_dict(row) for row in cur.fetchall()]

    def list_entity_mentions_for_canonical(
        self, canonical_artifact_id: str, limit: int = 100000
    ) -> List[Dict[str, Any]]:
        if not self._conn or not canonical_artifact_id:
            return []
        cur = self._conn.execute(
            f"SELECT {_ARTIFACT_COLUMNS} FROM intelligence_artifacts "
            # Unary + keeps SQLite on idx_intel_mentions_scope; on the type or
            # status index every lookup scanned all mentions (quadratic).
            "WHERE +artifact_type='entity_mention' AND parent_artifact_id=? "
            "AND +status='complete' AND review_state NOT IN ('rejected','superseded') "
            "ORDER BY created_at ASC LIMIT ?",
            (canonical_artifact_id, max(1, int(limit))),
        )
        return [self._artifact_row_to_dict(row) for row in cur.fetchall()]

    def set_entity_rule(
        self, rule_type: str, entity_type: str, source_key: str,
        *, target_artifact_id: str = "", other_key: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        rule_id = f"entity_rule:{uuid.uuid5(uuid.NAMESPACE_URL, '|'.join([
            rule_type or '', entity_type or '', source_key or '',
            target_artifact_id or '', other_key or '',
        ]))}"
        self._conn.execute(
            """
            INSERT INTO intelligence_entity_rules (
                rule_id, rule_type, entity_type, source_key,
                target_artifact_id, other_key, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(rule_id) DO UPDATE SET metadata_json=excluded.metadata_json
            """,
            (
                rule_id, rule_type, entity_type, source_key, target_artifact_id,
                other_key, json.dumps(metadata or {}, ensure_ascii=False), _now(),
            ),
        )
        return rule_id

    def entity_lexicon_rows(self):
        return _entity_lexicon.lexicon_rows(self._conn) if self._conn else []

    def words_used_lowercase(self, words):
        return _entity_lexicon.words_used_lowercase(self._conn, words) if self._conn else set()

    def entity_lexicon_version(self):
        return _entity_lexicon.lexicon_version(self._conn) if self._conn else None

    def entity_grouping_constraints(self, entity_type: str):
        if not self._conn:
            return []
        rows = self._conn.execute("SELECT source_key,other_key FROM intelligence_entity_rules WHERE entity_type=? AND rule_type IN ('alias','never_merge')", (entity_type,))
        return [{"source_key": row[0], "other_key": row[1]} for row in rows]

    def resolve_entity_alias(self, entity_type: str, source_key: str) -> Optional[str]:
        if not self._conn:
            return None
        return _entity_resolution.alias_target(self._conn,entity_type,source_key)

    def resolve_entity_mention_parent(self, mention_artifact_id: str) -> Optional[str]:
        if not self._conn:
            return None
        row = self._conn.execute(
            "SELECT target_artifact_id FROM intelligence_entity_rules "
            "WHERE rule_type='mention_parent' AND source_key=? "
            "ORDER BY created_at DESC LIMIT 1",
            (mention_artifact_id,),
        ).fetchone()
        return _entity_resolution.resolve_target(self._conn,row[0]) if row and row[0] else None

    def move_entity_mentions(
        self, mention_ids: List[str], target_artifact_id: str
    ) -> int:
        if not self._conn or not mention_ids or not target_artifact_id:
            return 0
        cur = self._conn.execute(
            "UPDATE intelligence_artifacts SET parent_artifact_id=?, "
            "review_state='corrected', revision=revision+1, updated_at=? "
            f"WHERE artifact_id IN ({','.join('?' for _ in mention_ids)}) "
            "AND artifact_type='entity_mention'",
            (target_artifact_id, _now(), *mention_ids),
        )
        return cur.rowcount or 0

    def count_artifacts(self) -> int:
        if not self._conn:
            return 0
        try:
            cur = self._conn.execute("SELECT COUNT(*), MAX(updated_at) FROM intelligence_artifacts")
            row = cur.fetchone()
            return int(row[0] or 0)
        except Exception:
            return 0

    def state_signature(self) -> str:
        """Cheap artifact/edge/review fingerprint for retrieval-cache invalidation."""
        if not self._conn:
            return "0"
        try:
            artifact_row = self._conn.execute(
                """
                SELECT COUNT(*), COALESCE(MAX(updated_at),''),
                       COALESCE(SUM(revision), 0)
                FROM intelligence_artifacts
                """
            ).fetchone()
            edge_row = self._conn.execute(
                """
                SELECT COUNT(*), COALESCE(MAX(created_at),'')
                FROM intelligence_edge_assertions
                """
            ).fetchone()
            return ":".join([
                str(int(artifact_row[0] or 0)),
                str(artifact_row[1] or ""),
                str(int(artifact_row[2] or 0)),
                str(int(edge_row[0] or 0)),
                str(edge_row[1] or ""),
            ])
        except Exception:
            return "0"

    def knowledge_signature(
        self,
        *,
        source_ids: Optional[List[str]] = None,
        artifact_families: Optional[List[str]] = None,
    ) -> str:
        """Stable signature for the exact knowledge scope a consumer reads."""
        if not self._conn:
            return "0"
        selected_sources = sorted({str(value) for value in source_ids or [] if value})
        families = sorted({str(value) for value in artifact_families or [] if value})
        artifact_types = _artifact_types_for_families(families)
        clauses = [
            "artifact_type NOT IN ('analysis_result','analysis_answer',"
            "'schema_extraction','graph_suggestion','analysis_quote',"
            "'analysis_node_suggestion')"
        ]
        params: List[Any] = []
        if selected_sources:
            clauses.append(
                f"(source_id IN ({','.join('?' for _ in selected_sources)}) "
                "OR source_id IS NULL)"
            )
            params.extend(selected_sources)
        if artifact_types:
            clauses.append(
                f"artifact_type IN ({','.join('?' for _ in artifact_types)})"
            )
            params.extend(artifact_types)
        where = " AND ".join(clauses)
        artifact_row = self._conn.execute(
            "SELECT COUNT(*), COALESCE(MAX(updated_at),''), "
            "COALESCE(SUM(revision),0), COALESCE(SUM(payload_hash IS NULL),0) "
            f"FROM intelligence_artifacts WHERE {where}",
            tuple(params),
        ).fetchone()
        revision_clauses = []
        revision_params: List[Any] = []
        if selected_sources:
            scoped = [*selected_sources, "__project__"]
            revision_clauses.append(
                f"source_id IN ({','.join('?' for _ in scoped)})"
            )
            revision_params.extend(scoped)
        if families:
            revision_clauses.append(
                f"family IN ({','.join('?' for _ in families)})"
            )
            revision_params.extend(families)
        revision_where = (
            "WHERE " + " AND ".join(revision_clauses)
            if revision_clauses else ""
        )
        revision_rows = self._conn.execute(
            "SELECT source_id, family, revision FROM intelligence_knowledge_revisions "
            f"{revision_where} ORDER BY source_id, family",
            tuple(revision_params),
        ).fetchall()
        payload = {
            "sources": selected_sources,
            "families": families,
            "artifact_aggregate": list(artifact_row or (0, "", 0, 0)),
            "revisions": [list(row) for row in revision_rows],
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:32]

    def analysis_input_signature(
        self, source_ids: Optional[List[str]] = None
    ) -> str:
        """Fingerprint analysis inputs without including cached outputs."""
        try:
            return self.knowledge_signature(
                source_ids=source_ids,
                artifact_families=[
                    "source", "structure", "argument", "summary",
                    "annotation", "relation",
                ],
            )
        except Exception:
            return "0"

    def list_artifacts_by_ids(self, artifact_ids: List[str]) -> List[Dict[str, Any]]:
        return list(self.iter_artifacts_by_ids(artifact_ids or []))

    # -- Analysis-run cache (analysis mode outputs) ------------------------

    def get_analysis_run(self, mode: str, params_hash: str, doc_hash: str) -> Optional[Dict[str, Any]]:
        if not self._conn:
            return None
        cur = self._conn.execute(
            "SELECT output_json, created_at, updated_at FROM intelligence_analysis_runs "
            "WHERE mode=? AND params_hash=? AND doc_hash=?",
            (mode, params_hash, doc_hash),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {"output": _json(row[0], {}), "created_at": row[1], "updated_at": row[2]}

    def save_analysis_run(
        self, mode: str, params_hash: str, doc_hash: str, output: Any, source_id: Optional[str] = None
    ) -> None:
        if not self._conn:
            return
        now = _now()
        self._conn.execute(
            """
            INSERT INTO intelligence_analysis_runs (
                mode, params_hash, doc_hash, source_id, output_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(mode, params_hash, doc_hash) DO UPDATE SET
                source_id=excluded.source_id,
                output_json=excluded.output_json,
                updated_at=excluded.updated_at
            """,
            (mode, params_hash, doc_hash, source_id,
             json.dumps(output, ensure_ascii=False), now, now),
        )

    def clear_analysis_runs(self, source_id: Optional[str] = None, mode: Optional[str] = None) -> int:
        if not self._conn:
            return 0
        clauses, params = [], []
        if source_id:
            clauses.append("source_id=?")
            params.append(source_id)
        if mode:
            clauses.append("mode=?")
            params.append(mode)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        cur = self._conn.execute(f"DELETE FROM intelligence_analysis_runs {where}", tuple(params))
        return cur.rowcount or 0

    # -- Retrieval cache (fused/reranked candidate sets) -------------------

    def get_retrieval_cache(self, query_hash: str, project_state_hash: str) -> Optional[Dict[str, Any]]:
        if not self._conn:
            return None
        cur = self._conn.execute(
            "SELECT plan_json, fused_json, created_at FROM intelligence_retrieval_cache "
            "WHERE query_hash=? AND project_state_hash=?",
            (query_hash, project_state_hash),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {"plan": _json(row[0], {}), "fused": _json(row[1], []), "created_at": row[2]}

    def save_retrieval_cache(
        self, query_hash: str, project_state_hash: str, plan: Any, fused: Any
    ) -> None:
        if not self._conn:
            return
        self._conn.execute(
            """
            INSERT INTO intelligence_retrieval_cache (
                query_hash, project_state_hash, plan_json, fused_json, created_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(query_hash, project_state_hash) DO UPDATE SET
                plan_json=excluded.plan_json,
                fused_json=excluded.fused_json,
                created_at=excluded.created_at
            """,
            (query_hash, project_state_hash,
             json.dumps(plan, ensure_ascii=False),
             json.dumps(fused, ensure_ascii=False), _now()),
        )

    def prune_retrieval_cache(self, keep: int = RETRIEVAL_CACHE_KEEP) -> int:
        if not self._conn:
            return 0
        cur = self._conn.execute(
            "DELETE FROM intelligence_retrieval_cache WHERE rowid NOT IN ("
            "SELECT rowid FROM intelligence_retrieval_cache ORDER BY created_at DESC LIMIT ?)",
            (max(0, int(keep)),),
        )
        return cur.rowcount or 0

    def clear_retrieval_cache(self) -> int:
        if not self._conn:
            return 0
        cur = self._conn.execute("DELETE FROM intelligence_retrieval_cache")
        return cur.rowcount or 0

    @staticmethod
    def _artifact_row_to_dict(row) -> Dict[str, Any]:
        keys = [
            "artifact_id", "source_id", "source_path", "source_type", "artifact_type",
            "storage_ref_type", "storage_ref_id", "parent_artifact_id",
            "exact_text", "normalized_text", "normalized_key", "locator_json", "section_path_json",
            "metadata_json", "confidence", "created_by", "extractor_name",
            "extractor_version", "model_role", "model_name", "input_hash", "payload_hash", "status",
            "review_state", "revision", "supersedes_artifact_id",
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


def _encode_cursor(updated_at: str, artifact_id: str) -> str:
    raw = json.dumps([updated_at or "", artifact_id or ""], ensure_ascii=False)
    return raw.encode("utf-8").hex()


def _decode_cursor(cursor: str) -> tuple[str, str]:
    if not cursor:
        return "", ""
    try:
        value = json.loads(bytes.fromhex(cursor).decode("utf-8"))
        if isinstance(value, list) and len(value) == 2:
            return str(value[0]), str(value[1])
    except Exception:
        pass
    return "", ""


def _fts_query(query: str) -> str:
    terms = [
        term.replace('"', "").strip()
        for term in str(query or "").split()
        if term.replace('"', "").strip()
    ]
    if not terms:
        return '""'
    return " OR ".join(f'"{term}"' for term in terms[:12])
