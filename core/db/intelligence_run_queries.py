"""Bounded run history with query-bound cursors and a persistent edit clock."""
import json

from core.models.intelligence_models import StageRunQuery, SourceScope
from core.db.intelligence_artifact_queries import (cursor_state, fingerprint,
    ArtifactSnapshotChanged, MAX_PAGE_SIZE)

RUN_COLUMNS = ("run_id source_id stage status input_hash settings_hash dependency_hash "
               "producer_name producer_version prompt_key prompt_version model_role model_name "
               "output_artifact_ids_json error_text started_at finished_at created_at").split()


def ensure_tables(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS intelligence_run_history_clock (id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL)")
    conn.execute("INSERT OR IGNORE INTO intelligence_run_history_clock VALUES (1,0)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_intel_runs_page ON intelligence_stage_runs(COALESCE(created_at,'') DESC,run_id DESC)")
    for event in ("INSERT", "UPDATE", "DELETE"):
        conn.execute(f"CREATE TRIGGER IF NOT EXISTS intel_runs_clock_{event.lower()} AFTER {event} ON intelligence_stage_runs BEGIN UPDATE intelligence_run_history_clock SET revision=revision+1 WHERE id=1; END")


def coerce_run_query(query):
    if isinstance(query, StageRunQuery):
        return query
    values = dict(query)
    if isinstance(values.get("source_scope"), dict):
        values["source_scope"] = SourceScope(**values["source_scope"])
    return StageRunQuery(**values)


def _clauses(query):
    clauses, params = [], []
    scope = query.source_scope
    if scope.unresolved_refs or (not scope.source_ids and (scope.source_paths or scope.display_names)):
        clauses.append("0")
    for key, values in (("source_id", scope.source_ids), ("stage", query.stages),
                        ("status", query.statuses), ("model_name", query.model_names)):
        if values:
            clauses.append(f"{key} IN (SELECT value FROM json_each(?))")
            params.append(json.dumps(values))
    if query.text.strip():
        text = query.text.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        fields = ("stage", "status", "producer_name", "model_name", "error_text")
        clauses.append("(" + " OR ".join(f"{key} LIKE ? ESCAPE '\\'" for key in fields) + ")")
        params.extend([f"%{text}%"] * len(fields))
    return clauses, params


def query_runs(db, query):
    query = coerce_run_query(query)
    if not db._conn:
        return {"items": [], "next_cursor": ""}
    revision = str(db._conn.execute("SELECT revision FROM intelligence_run_history_clock WHERE id=1").fetchone()[0])
    try:
        key = cursor_state(query, "runs", revision)
    except ArtifactSnapshotChanged as exc:
        return {"items": [], "next_cursor": "", "invalidated": True, "reason": str(exc)}
    clauses, params = _clauses(query)
    if key:
        clauses.append("(COALESCE(created_at,'')<? OR (COALESCE(created_at,'')=? AND run_id<?))")
        params.extend([key[0], key[0], key[1]])
    limit = max(1, min(int(query.limit or 100), MAX_PAGE_SIZE))
    rows = db._conn.execute("SELECT " + ",".join(RUN_COLUMNS) + " FROM intelligence_stage_runs WHERE " +
        (" AND ".join(clauses) or "1") + " ORDER BY COALESCE(created_at,'') DESC,run_id DESC LIMIT ?", (*params, limit+1)).fetchall()
    items = [hydrate(row) for row in rows[:limit]]
    cursor = ""
    if len(rows) > limit:
        last = items[-1]
        cursor = json.dumps({"query": fingerprint(query, "runs"), "revision": revision,
                             "key": [last.get("created_at") or "", last["run_id"]]}).encode().hex()
    return {"items": items, "next_cursor": cursor, "snapshot_revision": revision}


def hydrate(row):
    item = dict(zip(RUN_COLUMNS, row))
    item["output_artifact_ids"] = json.loads(item.pop("output_artifact_ids_json") or "[]")
    return item
