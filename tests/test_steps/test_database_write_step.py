"""Tests for DatabaseWriteStep using an in-memory SQLite DB."""
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock
from core.engine.steps.database_write_step import DatabaseWriteStep
from core.engine.execution_result import ExecutionResult
from core.plugins.plugin_step_protocol import StepContext


def _make_context(db_path=None):
    ctx = MagicMock(spec=StepContext)
    ctx.state = {}
    ctx.abort_check = lambda: False
    ctx.project_manager = MagicMock()
    ctx.project_manager.project_filepath = db_path
    return ctx


class TestDatabaseWriteStep(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        # Pre-create the table in the temp DB
        conn = sqlite3.connect(self.tmp.name)
        conn.execute("CREATE TABLE IF NOT EXISTS test_table (key TEXT, value TEXT)")
        conn.commit()
        conn.close()

    def tearDown(self):
        os.unlink(self.tmp.name)

    def test_returns_execution_result(self):
        step = DatabaseWriteStep()
        ctx = _make_context(self.tmp.name)
        inputs = {"table": "test_table", "payload": {"key": "hello", "value": "world"}}
        result = step.execute(ctx, inputs)
        self.assertIsInstance(result, ExecutionResult)

    def test_success_message(self):
        step = DatabaseWriteStep()
        ctx = _make_context(self.tmp.name)
        inputs = {"table": "test_table", "payload": {"key": "k1", "value": "v1"}}
        result = step.execute(ctx, inputs)
        self.assertIn("Success", str(result.raw_value))

    def test_missing_table_returns_failure(self):
        step = DatabaseWriteStep()
        ctx = _make_context(self.tmp.name)
        inputs = {"table": "", "payload": {"key": "k"}}
        result = step.execute(ctx, inputs)
        self.assertIn("Failed", str(result.raw_value))

    def test_missing_project_path_returns_failure(self):
        step = DatabaseWriteStep()
        ctx = _make_context(None)
        ctx.project_manager.project_filepath = None
        inputs = {"table": "test_table", "payload": {"key": "k"}}
        result = step.execute(ctx, inputs)
        self.assertIn("Failed", str(result.raw_value))

    def test_row_written_to_db(self):
        step = DatabaseWriteStep()
        ctx = _make_context(self.tmp.name)
        inputs = {"table": "test_table", "payload": {"key": "check_key", "value": "check_val"}}
        step.execute(ctx, inputs)
        conn = sqlite3.connect(self.tmp.name)
        rows = conn.execute("SELECT key, value FROM test_table WHERE key='check_key'").fetchall()
        conn.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], "check_val")


if __name__ == "__main__":
    unittest.main()
