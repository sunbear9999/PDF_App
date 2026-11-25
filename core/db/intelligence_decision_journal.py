"""Project-local, immutable review receipts. Undo inputs never supply trusted snapshots."""
import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone


def ensure_table(conn):
    conn.execute("INSERT OR IGNORE INTO intelligence_schema_migrations VALUES (5,?,?)", ("project-local guarded review decision receipts", datetime.now(timezone.utc).isoformat()))
    conn.execute("CREATE TABLE IF NOT EXISTS intelligence_review_decisions (decision_id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL, kind TEXT NOT NULL, payload_json TEXT NOT NULL, undone INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL)")


@contextmanager
def transaction(db):
    name = "decision_" + uuid.uuid4().hex
    db._conn.execute(f"SAVEPOINT {name}")
    try:
        yield
        db._conn.execute(f"RELEASE SAVEPOINT {name}")
    except Exception:
        db._conn.execute(f"ROLLBACK TO SAVEPOINT {name}")
        db._conn.execute(f"RELEASE SAVEPOINT {name}")
        raise


def save(db, artifact_id, kind, payload, revision=None):
    identity = str(uuid.uuid4())
    db._conn.execute("INSERT INTO intelligence_review_decisions VALUES (?,?,?,?,0,?)",
        (identity, artifact_id, kind, json.dumps(payload, ensure_ascii=False), datetime.now(timezone.utc).isoformat()))
    return {"decision_id": identity, "artifact_id": artifact_id, "kind": kind, "after_revision": revision}


def load(db, receipt, artifact_id, kind):
    identity = (receipt or {}).get("decision_id")
    row = db._conn.execute("SELECT artifact_id,kind,payload_json,undone FROM intelligence_review_decisions WHERE decision_id=?", (identity,)).fetchone()
    if not row or row[0] != artifact_id or row[1] != kind or row[3]:
        raise ValueError("This undo receipt is unavailable in this project or has already been used. Reload and inspect history.")
    return json.loads(row[2])


def finish_undo(db, receipt):
    db._conn.execute("UPDATE intelligence_review_decisions SET undone=1 WHERE decision_id=?", (receipt["decision_id"],))
