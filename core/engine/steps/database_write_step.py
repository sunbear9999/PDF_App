"""
core/engine/steps/database_write_step.py

Migrated from MasterActionRunner._run_db_write().
"""
from __future__ import annotations

import sqlite3

from core.engine.steps.base_step import BaseStep
from core.plugins.plugin_step_protocol import StepContext


class DatabaseWriteStep(BaseStep):
    step_type = "DATABASE_WRITE"
    label = "Database Write"
    category = "Persistence"
    description = "Persist workflow output to a project table."
    input_schema = {
        "table": {"type": "string", "label": "Table Name", "required": True},
        "payload": {"type": "object", "label": "Row Data", "required": True},
    }

    def execute(self, context: StepContext, inputs: dict):
        table_name = inputs.get("table")
        payload = inputs.get("payload", {})
        pm = context.project_manager

        if not pm or not pm.project_filepath or not table_name or not payload:
            return self.build_result("Failed: Missing DB Context or Payload")

        try:
            conn = sqlite3.connect(pm.project_filepath, timeout=10.0)
            cursor = conn.cursor()
            columns = ", ".join(payload.keys())
            placeholders = ", ".join(["?"] * len(payload))
            values = tuple(payload.values())
            query = f"INSERT OR REPLACE INTO {table_name} ({columns}) VALUES ({placeholders})"
            cursor.execute(query, values)
            conn.commit()
            conn.close()
            return self.build_result(f"Success: Wrote to {table_name}")
        except Exception as e:
            return self.build_result(f"DB Error: {e}")
