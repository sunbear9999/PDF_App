from __future__ import annotations

import json
from typing import Dict, List, Optional

from core.db.base_db import BaseDB
from core.models.data_dock_models import ChartConfig, DataGridState, DataProvenance


class DataDockDB(BaseDB):
    def list_datasets(self) -> List[Dict]:
        if not self._conn:
            return []
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT id, name, created_at, updated_at FROM data_dock_datasets ORDER BY updated_at DESC, name ASC"
        )
        return [
            {"dataset_id": row[0], "name": row[1], "created_at": row[2], "updated_at": row[3], "is_persisted": True}
            for row in cursor.fetchall()
        ]

    def get_dataset(self, dataset_id: str) -> Optional[DataGridState]:
        if not self._conn or not dataset_id:
            return None
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT id, name, grid_json, provenance_json, column_types_json, created_at, updated_at "
            "FROM data_dock_datasets WHERE id = ?",
            (dataset_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        grid = self._load_json(row[2], {})
        state = DataGridState(
            dataset_id=row[0],
            name=row[1] or grid.get("name") or "Untitled Dataset",
            headers=list(grid.get("headers") or []),
            row_headers=list(grid.get("row_headers") or []),
            rows=[list(r) for r in grid.get("rows") or []],
            column_types=self._load_json(row[4], {}),
            provenance=DataProvenance.from_dict(self._load_json(row[3], {})),
            cell_provenance=dict(grid.get("cell_provenance") or {}),
            metadata=dict(grid.get("metadata") or {}),
            is_persisted=True,
            dirty=False,
            version=int(grid.get("version") or 1),
            created_at=row[5],
            updated_at=row[6],
        )
        return state

    def upsert_dataset(self, state: DataGridState) -> DataGridState:
        if not self._conn:
            return state
        grid_json = json.dumps(
            {
                "headers": state.headers,
                "row_headers": state.row_headers,
                "rows": state.rows,
                "cell_provenance": state.cell_provenance,
                "metadata": state.metadata,
                "version": state.version,
            },
            ensure_ascii=False,
        )
        provenance_json = json.dumps(state.provenance.to_dict(), ensure_ascii=False)
        column_types_json = json.dumps(state.column_types, ensure_ascii=False)
        self._conn.execute(
            """
            INSERT INTO data_dock_datasets
                (id, name, grid_json, provenance_json, column_types_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP), CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,
                grid_json=excluded.grid_json,
                provenance_json=excluded.provenance_json,
                column_types_json=excluded.column_types_json,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                state.dataset_id,
                state.name,
                grid_json,
                provenance_json,
                column_types_json,
                state.created_at,
            ),
        )
        self._conn.commit()
        saved = self.get_dataset(state.dataset_id) or state
        saved.is_persisted = True
        saved.dirty = False
        return saved

    def delete_dataset(self, dataset_id: str) -> None:
        if not self._conn or not dataset_id:
            return
        self._conn.execute("DELETE FROM data_dock_datasets WHERE id = ?", (dataset_id,))
        self._conn.commit()

    def list_charts(self) -> List[Dict]:
        if not self._conn:
            return []
        cursor = self._conn.cursor()
        cursor.execute("SELECT id, dataset_id, name, created_at, updated_at FROM data_dock_charts ORDER BY updated_at DESC")
        return [
            {"chart_id": row[0], "dataset_id": row[1], "name": row[2], "created_at": row[3], "updated_at": row[4]}
            for row in cursor.fetchall()
        ]

    def get_chart(self, chart_id: str) -> Optional[ChartConfig]:
        if not self._conn or not chart_id:
            return None
        cursor = self._conn.cursor()
        cursor.execute("SELECT id, dataset_id, name, config_json, created_at, updated_at FROM data_dock_charts WHERE id = ?", (chart_id,))
        row = cursor.fetchone()
        if not row:
            return None
        data = self._load_json(row[3], {})
        data.update({"chart_id": row[0], "dataset_id": row[1], "name": row[2], "created_at": row[4], "updated_at": row[5]})
        return ChartConfig.from_dict(data)

    def upsert_chart(self, config: ChartConfig) -> ChartConfig:
        if not self._conn:
            return config
        self._conn.execute(
            """
            INSERT INTO data_dock_charts (id, dataset_id, name, config_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP), CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
                dataset_id=excluded.dataset_id,
                name=excluded.name,
                config_json=excluded.config_json,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                config.chart_id,
                config.dataset_id,
                config.name,
                json.dumps(config.to_dict(), ensure_ascii=False),
                config.created_at,
            ),
        )
        self._conn.commit()
        return self.get_chart(config.chart_id) or config

    def _load_json(self, raw: str, default):
        try:
            return json.loads(raw) if raw else default
        except Exception:
            return default
