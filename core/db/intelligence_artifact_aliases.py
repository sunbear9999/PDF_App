"""Artifact ID aliases: where a retired artifact's identity and review went.

When re-extraction retires an artifact (older random IDs, changed segmentation)
its human decisions are carried to the matching new occurrence and the
mapping is kept here. Ambiguous cases are recorded with ``needs_review`` and
no target, rather than silently resetting the person's work.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

MIGRATED = "migrated"
NEEDS_REVIEW = "needs_review"
MAX_ALIAS_BATCH = 128


def ensure_table(conn: Any) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS intelligence_artifact_aliases ("
        " old_artifact_id TEXT PRIMARY KEY, new_artifact_id TEXT,"
        " status TEXT NOT NULL, reason TEXT, created_at TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_intel_aliases_new "
        "ON intelligence_artifact_aliases(new_artifact_id)"
    )


def record_alias(conn: Any, old_id: str, new_id: Optional[str], status: str, reason: str) -> None:
    conn.execute(
        "INSERT INTO intelligence_artifact_aliases "
        "(old_artifact_id, new_artifact_id, status, reason, created_at) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(old_artifact_id) DO UPDATE SET new_artifact_id=excluded.new_artifact_id, "
        "status=excluded.status, reason=excluded.reason",
        (old_id, new_id, status, reason, datetime.now(timezone.utc).isoformat()),
    )


def resolve_alias(conn: Any, artifact_id: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT old_artifact_id, new_artifact_id, status, reason FROM "
        "intelligence_artifact_aliases WHERE old_artifact_id=?", (artifact_id,),
    ).fetchone()
    if not row:
        return None
    return {"old_artifact_id": row[0], "new_artifact_id": row[1],
            "status": row[2], "reason": row[3]}


def by_ids(conn, identities):
    identities = list(dict.fromkeys(identities))
    if len(identities) > MAX_ALIAS_BATCH:
        raise ValueError(f"Read at most {MAX_ALIAS_BATCH} aliases per batch.")
    if not conn or not identities:
        return {}
    slots = ",".join("?" for _ in identities)
    rows = conn.execute("SELECT old_artifact_id,new_artifact_id,status,reason FROM "
                        f"intelligence_artifact_aliases WHERE old_artifact_id IN ({slots})", identities)
    return {row[0]: dict(zip(("old_artifact_id", "new_artifact_id", "status", "reason"), row))
            for row in rows.fetchall()}


def resolve_current_id(conn: Any, artifact_id: str, *, get_artifact=None, get_alias=None) -> str:
    """Follow confirmed correspondence only; never guess through conflicts/cycles."""
    if not conn:
        return artifact_id
    identity, seen = artifact_id, set()
    while identity:
        if identity in seen:
            raise ValueError('Artifact migration alias cycle requires review.')
        seen.add(identity)
        alias = get_alias(identity) if get_alias else resolve_alias(conn,identity)
        if not alias or alias['status'] != MIGRATED:
            return identity
        following = alias.get('new_artifact_id')
        target = get_artifact(following) if get_artifact else conn.execute(
            'SELECT source_id,artifact_type FROM intelligence_artifacts WHERE artifact_id=?',(following,)).fetchone()
        if not following or not target:
            raise ValueError('The confirmed migration destination is missing. Review its correspondence.')
        previous = get_artifact(identity) if get_artifact else conn.execute(
            'SELECT source_id,artifact_type FROM intelligence_artifacts WHERE artifact_id=?',(identity,)).fetchone()
        transition = (("citation_entry", "document_note"), ("in_text_citation", "note_reference"))
        compatible = previous and previous[0] == target[0] and (previous[1], target[1]) in transition
        if previous and tuple(previous) != tuple(target) and not compatible:
            raise ValueError('The migration destination has a different source or artifact type. Review its correspondence.')
        identity = following
    return artifact_id
