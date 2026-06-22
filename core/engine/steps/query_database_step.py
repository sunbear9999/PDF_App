"""
core/engine/steps/query_database_step.py

Runs a raw SQL SELECT query against the project SQLite database and returns
results as a JSON-serializable list. Useful for building non-LLM workflows that
inspect project data (documents, analyses, tags, notes, etc.).

Only SELECT statements are allowed; write operations should use DATABASE_WRITE.
"""
from __future__ import annotations

import json
import sqlite3

from core.engine.steps.base_step import BaseStep
from core.plugins.plugin_step_protocol import StepContext


class QueryDatabaseStep(BaseStep):
    step_type = "QUERY_DATABASE"
    label = "Query Database"
    category = "Data"
    description = "Run a SELECT query against the project database and return rows as JSON."
    input_schema = {
        "sql": {"type": "string", "label": "SQL SELECT statement", "required": True},
        "output_format": {"type": "string", "label": "Output format: 'list' | 'first' | 'count'"},
        "max_rows": {"type": "integer", "label": "Max rows to return (default 100)"},
    }

    def execute(self, context: StepContext, inputs: dict):
        sql = str(inputs.get("sql") or "").strip()
        if not sql:
            return self.build_result("Error: sql is required")
        # Safety: only allow SELECT
        if not sql.upper().lstrip().startswith("SELECT"):
            return self.build_result("Error: only SELECT statements are allowed")

        pm = context.project_manager
        db_path = getattr(pm, "db_path", None) or (getattr(pm, "db_docs", None) and getattr(pm.db_docs, "db_path", None))
        if not db_path:
            return self.build_result("Error: could not locate project database path")

        max_rows = int(inputs.get("max_rows") or 100)
        fmt = str(inputs.get("output_format") or "list").lower()

        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(sql)
            rows = cur.fetchmany(max_rows)
            conn.close()
        except Exception as e:
            return self.build_result(f"SQL Error: {e}")

        data = [dict(r) for r in rows]
        if fmt == "first":
            return self.build_result(json.dumps(data[0]) if data else "{}")
        if fmt == "count":
            return self.build_result(str(len(data)))
        return self.build_result(json.dumps(data))
