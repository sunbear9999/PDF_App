"""Producer identity recorded per stage run (additive column + accessors).

Kept beside IntelligenceDB rather than inside it: the run row says which
algorithm or model *actually* ran for that stage, including any fallback.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Iterable


def ensure_identity_column(conn: Any) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(intelligence_stage_runs)")}
    if "identity_json" not in columns:
        conn.execute(
            "ALTER TABLE intelligence_stage_runs "
            "ADD COLUMN identity_json TEXT NOT NULL DEFAULT '{}'"
        )


def record_run_identity(conn: Any, run_id: str, identity: Dict[str, Any]) -> None:
    if conn and run_id and identity:
        conn.execute(
            "UPDATE intelligence_stage_runs SET identity_json=? WHERE run_id=?",
            (json.dumps(identity, ensure_ascii=False, sort_keys=True), run_id),
        )


def run_identities(conn: Any, run_ids: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    ids = [run_id for run_id in run_ids if run_id]
    if not conn or not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT run_id, identity_json FROM intelligence_stage_runs WHERE run_id IN ({marks})",
        ids,
    ).fetchall()
    result: Dict[str, Dict[str, Any]] = {}
    for run_id, raw in rows:
        try:
            result[run_id] = json.loads(raw or "{}")
        except ValueError:
            result[run_id] = {}
    return result
