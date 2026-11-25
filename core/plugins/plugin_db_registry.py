"""
core/plugins/plugin_db_registry.py

Singleton registry that collects CREATE TABLE DDL contributed by plugins.
DatabaseSchema.init_database() iterates this registry to create plugin tables.

Usage in a plugin::

    def on_load(self, api):
        api.db.register_table("stock_positions", {
            "id": "TEXT PRIMARY KEY",
            "ticker": "TEXT NOT NULL",
            "shares": "REAL DEFAULT 0",
            "updated_at": "TEXT",
        })
"""
from __future__ import annotations

import re
from typing import Dict


_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class PluginTableRegistry:
    """Collects plugin-contributed SQLite table schemas.

    Tables are prefixed with ``plugin_`` to avoid collisions with core tables.
    Schemas survive plugin hot-reload (tables are never DROPped automatically
    because plugin data should persist between reloads).
    """

    _instance: "PluginTableRegistry | None" = None

    @classmethod
    def get_instance(cls) -> "PluginTableRegistry":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:
        # table_name (with plugin_ prefix) → {plugin_id, schema_dict}
        self._tables: Dict[str, dict] = {}

    def register_table(self, table_name: str, schema_dict: Dict[str, str], plugin_id: str = "") -> None:
        """Register a plugin table.

        :param table_name: Logical table name (``plugin_`` prefix added automatically).
        :param schema_dict: Column name → SQLite type string, e.g.
                            ``{"id": "TEXT PRIMARY KEY", "value": "TEXT"}``.
        :param plugin_id: Plugin that owns this table; used for cleanup tracking.
        """
        safe_name = table_name if table_name.startswith("plugin_") else f"plugin_{table_name}"
        if not _IDENTIFIER_RE.fullmatch(safe_name):
            raise ValueError(f"Invalid plugin table name: {table_name!r}")
        if not schema_dict or any(not _IDENTIFIER_RE.fullmatch(col) for col in schema_dict):
            raise ValueError("Plugin table column names must be safe SQL identifiers.")
        self._tables[safe_name] = {"plugin_id": plugin_id, "schema": dict(schema_dict)}

    def ensure_tables(self, conn, plugin_id: str = "") -> None:
        """Create registered tables immediately for an already-open project."""
        for table_name, meta in self._tables.items():
            if plugin_id and meta.get("plugin_id") != plugin_id:
                continue
            columns = ", ".join(
                f"{name} {definition}" for name, definition in meta["schema"].items()
            )
            conn.execute(f"CREATE TABLE IF NOT EXISTS {table_name} ({columns})")

    def get_tables(self) -> Dict[str, dict]:
        return dict(self._tables)

    def remove_by_plugin(self, plugin_id: str) -> None:
        """Remove table registrations for a plugin (does NOT DROP the actual table)."""
        self._tables = {k: v for k, v in self._tables.items()
                        if v.get("plugin_id") != plugin_id}
