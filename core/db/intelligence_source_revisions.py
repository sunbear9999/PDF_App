"""Source revisions and machine (producer) revision history for artifacts.

* A **source revision** identifies the exact file content a stage read
  (``srcrev:<content-hash prefix>``). Producer identity on every artifact
  records it, so a source jump can tell when the file changed after
  extraction instead of silently trusting old offsets.
* **Machine revisions**: when a producer rerun changes an unreviewed
  artifact's content, the previous and new values are kept as immutable
  revision rows (``created_by`` = the producer), so history shows what
  automatic processing changed — not only human edits.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from core.models.source_anchor_models import revision_id_for

_SCHEMA = """
CREATE TABLE IF NOT EXISTS intelligence_source_revisions (
    source_id     TEXT NOT NULL,
    revision_id   TEXT NOT NULL,
    content_hash  TEXT NOT NULL,
    media_type    TEXT,
    created_at    TEXT NOT NULL,
    PRIMARY KEY(source_id, revision_id)
);
CREATE INDEX IF NOT EXISTS idx_intel_source_revisions_source
    ON intelligence_source_revisions(source_id, created_at)
"""


def ensure_tables(conn: Any) -> None:
    for statement in _SCHEMA.strip().split(";"):
        if statement.strip():
            conn.execute(statement)


def record_source_revision(conn: Any, source_id: str, content_hash: str,
                           media_type: str = "") -> str:
    revision_id = revision_id_for(content_hash)
    if conn and source_id and revision_id:
        conn.execute(
            "INSERT OR IGNORE INTO intelligence_source_revisions "
            "(source_id, revision_id, content_hash, media_type, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (source_id, revision_id, content_hash, media_type, _now()),
        )
    return revision_id


def current_source_revision(conn: Any, source_id: str) -> str:
    if not conn or not source_id:
        return ""
    row = conn.execute(
        "SELECT revision_id FROM intelligence_source_revisions WHERE source_id=? "
        "ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (source_id,),
    ).fetchone()
    return row[0] if row else ""


def pin_source_revision(values: Dict[str, Any], revision_id: str) -> None:
    """Record the source revision inside the producer identity (not hashed)."""
    if not revision_id:
        return
    try:
        metadata = json.loads(values.get("metadata_json") or "{}")
    except (TypeError, ValueError):
        return
    producer = metadata.get("producer")
    if isinstance(producer, dict) and not producer.get("source_revision"):
        producer["source_revision"] = revision_id
        values["metadata_json"] = json.dumps(metadata, ensure_ascii=False)


def record_machine_revision(conn: Any, previous: Optional[Dict[str, Any]],
                            values: Dict[str, Any]) -> int:
    """Keep the old and new machine values; return the new revision number."""
    if not previous:
        return 1
    artifact_id = previous["artifact_id"]
    old_revision = int(previous.get("revision") or 1)
    has_history = conn.execute(
        "SELECT 1 FROM intelligence_artifact_revisions WHERE artifact_id=? LIMIT 1",
        (artifact_id,),
    ).fetchone()
    if not has_history:
        _insert(conn, artifact_id, old_revision, previous, _producer(previous))
    snapshot = {**previous, **_row_from_values(values)}
    _insert(conn, artifact_id, old_revision + 1, snapshot, _producer(snapshot))
    return old_revision + 1


def _insert(conn, artifact_id: str, revision: int, snapshot: Dict[str, Any], actor: str):
    revision_id = f"artifact_revision:{uuid.uuid5(uuid.NAMESPACE_URL, f'{artifact_id}|{revision}')}"
    conn.execute(
        "INSERT OR IGNORE INTO intelligence_artifact_revisions "
        "(revision_id, artifact_id, revision, snapshot_json, review_state, created_by, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (revision_id, artifact_id, revision,
         json.dumps(snapshot, ensure_ascii=False, default=str),
         snapshot.get("review_state") or "unreviewed", actor, _now()),
    )


def _row_from_values(values: Dict[str, Any]) -> Dict[str, Any]:
    row = {key: value for key, value in values.items() if not key.endswith("_json")}
    for key in ("locator", "section_path", "metadata"):
        try:
            row[key] = json.loads(values.get(f"{key}_json") or "null")
        except (TypeError, ValueError):
            row[key] = None
    return row


def _producer(row: Dict[str, Any]) -> str:
    producer = (row.get("metadata") or {}).get("producer") or {}
    name = producer.get("producer_id") or row.get("extractor_name") or "producer"
    return f"machine:{name}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
