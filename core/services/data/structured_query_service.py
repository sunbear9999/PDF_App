"""
StructuredQueryService — deterministic aggregate execution over tabular data.

Numeric/aggregate questions that touch a spreadsheet or CSV (sums, averages,
counts, filters, group-bys) are answered by *executing a real query* against
the stored grid, never by an LLM reading cell values out of retrieved text.
An analysis mode's only job around tabular data is to pick *which* aggregate to
run (see the schema helper) — the arithmetic happens here.

Backed by the existing DataDockService / ``data_dock_datasets`` store; no new
storage. Uses pandas (already a project dependency).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
import hashlib
import json

_NUMERIC_OPS = {"sum", "mean", "avg", "average", "min", "max", "median", "std"}
_ALL_OPS = _NUMERIC_OPS | {"count", "nunique"}


class StructuredQueryService:
    def __init__(self, data_dock_service=None) -> None:
        self._data = data_dock_service

    # -- Schema (what an LLM sees to decide which aggregate to run) ---------

    def schema(self, dataset_id: str) -> Dict[str, Any]:
        grid = self._load(dataset_id)
        if grid is None:
            return {"dataset_id": dataset_id, "columns": [], "dtypes": {}}
        df = self._to_frame(grid)
        return {
            "dataset_id": dataset_id,
            "name": getattr(grid, "name", ""),
            "columns": list(df.columns),
            "dtypes": {col: str(dtype) for col, dtype in df.dtypes.items()},
            "row_count": int(len(df)),
        }

    def list_schemas(self) -> List[Dict[str, Any]]:
        if not self._data:
            return []
        out = []
        for meta in self._data.list_datasets():
            ds_id = meta.get("dataset_id") or meta.get("id")
            if ds_id:
                out.append(self.schema(ds_id))
        return out

    # -- Aggregate execution (deterministic; the LLM never does this) ------

    def aggregate(
        self,
        dataset_id: str,
        op: str,
        column: Optional[str] = None,
        group_by: Optional[str] = None,
        filters: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        op = (op or "").strip().lower()
        if op not in _ALL_OPS:
            return {"error": f"Unsupported aggregate '{op}'. Allowed: {sorted(_ALL_OPS)}"}
        grid = self._load(dataset_id)
        if grid is None:
            return {"error": f"Dataset '{dataset_id}' not found"}
        df = self._to_frame(grid)
        original_rows = int(len(df))
        input_hash = hashlib.sha256(json.dumps({"headers": list(df.columns), "rows": df.values.tolist()}, default=str, sort_keys=True).encode()).hexdigest()
        if column is not None and column not in df.columns:
            return {"error": f"column '{column}' not found"}
        if op != "count" and column is None:
            return {"error": f"'{op}' requires a column"}
        try:
            df = self._apply_filters(df, filters)
        except Exception as exc:
            return {"error": f"Filter error: {exc}"}

        if op == "avg" or op == "average":
            op = "mean"

        try:
            if group_by:
                if group_by not in df.columns:
                    return {"error": f"group_by column '{group_by}' not found"}
                result = self._grouped(df, op, column, group_by)
            else:
                result = self._scalar(df, op, column)
        except Exception as exc:
            return {"error": f"Aggregate error: {exc}"}

        return {
            "dataset_id": dataset_id,
            "op": op,
            "column": column,
            "group_by": group_by,
            "filtered_rows": int(len(df)),
            "result": result,
            "derivation": _derivation(grid, dataset_id, input_hash, original_rows, len(df), op, column, group_by, filters),
        }

    # -- internals ---------------------------------------------------------

    def _grouped(self, df, op, column, group_by):
        import pandas as pd
        if op in _NUMERIC_OPS and column:
            if column not in df.columns:
                raise ValueError(f"column '{column}' not found")
            work = df[[group_by]].copy()
            work[column] = pd.to_numeric(df[column], errors="coerce")
            series = getattr(work.groupby(group_by, dropna=False)[column], op)()
        elif op == "nunique" and column:
            series = df.groupby(group_by, dropna=False)[column].nunique()
        else:  # count (or op without a column)
            series = df.groupby(group_by, dropna=False).size()
        return {str(k): _py(v) for k, v in series.to_dict().items()}

    def _scalar(self, df, op, column):
        if op == "count":
            return int(len(df))
        if column is None:
            raise ValueError(f"'{op}' requires a column")
        if column not in df.columns:
            raise ValueError(f"column '{column}' not found")
        col = df[column]
        if op == "nunique":
            return int(col.nunique())
        import pandas as pd
        numeric = pd.to_numeric(col, errors="coerce")
        return _py(getattr(numeric, op)())

    def _apply_filters(self, df, filters):
        import pandas as pd
        for f in filters or []:
            col = f.get("column")
            operator = (f.get("op") or "eq").lower()
            value = f.get("value")
            if col not in df.columns:
                raise ValueError(f"filter column '{col}' not found")
            if operator not in {"eq", "ne", "gt", "lt", "gte", "lte", "contains"}:
                raise ValueError(f"Unsupported filter operator '{operator}'")
            series = df[col]
            if operator in ("gt", "lt", "gte", "lte"):
                series = pd.to_numeric(series, errors="coerce")
                value = float(value)
                if operator == "gt":
                    df = df[series > value]
                elif operator == "lt":
                    df = df[series < value]
                elif operator == "gte":
                    df = df[series >= value]
                else:
                    df = df[series <= value]
            elif operator == "ne":
                df = df[series.astype(str) != str(value)]
            elif operator == "contains":
                df = df[series.astype(str).str.contains(str(value), case=False, na=False, regex=False)]
            else:  # eq
                df = df[series.astype(str) == str(value)]
        return df

    def _load(self, dataset_id: str):
        if not self._data:
            return None
        try:
            return self._data.load_dataset(dataset_id)
        except Exception:
            return None

    def _to_frame(self, grid):
        import pandas as pd
        headers = list(getattr(grid, "headers", []) or [])
        rows = [list(r) for r in getattr(grid, "rows", []) or []]
        if headers:
            width = len(headers)
            rows = [(r + [None] * width)[:width] for r in rows]
            df = pd.DataFrame(rows, columns=headers)
        else:
            df = pd.DataFrame(rows)
        return df


def _py(value):
    """Coerce numpy/pandas scalars to plain JSON-safe Python values."""
    try:
        import math
        if value is None:
            return None
        if hasattr(value, "item"):
            value = value.item()
        if isinstance(value, float) and math.isnan(value):
            return None
        return value
    except Exception:
        return value


def _derivation(grid, dataset_id, input_hash, input_rows, filtered_rows, op, column, group_by, filters):
    provenance = getattr(grid, "provenance", {})
    provenance = provenance.to_dict() if hasattr(provenance, "to_dict") else dict(provenance or {})
    return {"method": "deterministic_aggregate", "dataset_id": dataset_id,
            "dataset_version": getattr(grid, "version", None), "source_provenance": provenance,
            "input_hash": input_hash, "input_rows": input_rows, "operation": op,
            "column": column, "group_by": group_by, "filters": list(filters or []),
            "filtered_rows": int(filtered_rows)}
