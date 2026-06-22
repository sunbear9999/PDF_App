"""Reusable project-source statistics for adaptive workflows."""
from __future__ import annotations

import json
import os
from typing import Callable

from core.engine.steps.base_step import BaseStep
from core.plugins.plugin_step_protocol import StepContext


MetricCollector = Callable[[dict, StepContext], object]


class SourceStatisticsStep(BaseStep):
    """Collect lightweight source facts without coupling them to one blueprint.

    New metrics can be exposed everywhere by calling ``register_metric``; every
    blueprint then selects them through the ``metrics`` input.
    """

    step_type = "SOURCE_STATISTICS"
    label = "Source Statistics"
    category = "Context"
    description = "Collect reusable statistics such as page counts and file sizes."
    input_schema = {
        "allowed_docs": {"type": "array", "label": "Allowed Documents"},
        "metrics": {"type": "array", "label": "Metrics"},
    }
    output_schema = {
        "source_count": {"type": "integer"},
        "totals": {"type": "object"},
        "sources": {"type": "array"},
    }
    _metric_collectors: dict[str, MetricCollector] = {}

    @classmethod
    def register_metric(cls, name: str, collector: MetricCollector) -> None:
        if name and callable(collector):
            cls._metric_collectors[name] = collector

    def execute(self, context: StepContext, inputs: dict):
        pm = context.project_manager
        if not pm or not hasattr(pm, "list_sources"):
            return self.build_result(json.dumps({"source_count": 0, "totals": {}, "sources": []}))

        allowed = self._normalize_list(inputs.get("allowed_docs"))
        allowed_names = {os.path.basename(value).lower() for value in allowed}
        requested = self._normalize_list(inputs.get("metrics")) or [
            "page_count", "file_size_bytes", "indexed_chunk_count"
        ]

        rows = []
        for source in pm.list_sources():
            path = source.get("path", "")
            name = os.path.basename(path)
            if allowed_names and name.lower() not in allowed_names and path.lower() not in {v.lower() for v in allowed}:
                continue
            row = {
                "source_id": source.get("id", ""),
                "doc_name": name,
                "source_type": source.get("source_type", "pdf"),
            }
            for metric in requested:
                collector = self._collectors().get(metric)
                if collector:
                    try:
                        row[metric] = collector(source, context)
                    except Exception:
                        row[metric] = 0
            rows.append(row)

        totals = {}
        for metric in requested:
            values = [row.get(metric) for row in rows]
            numeric = [value for value in values if isinstance(value, (int, float))]
            if numeric:
                totals[metric] = sum(numeric)

        result = {
            "source_count": len(rows),
            "pdf_count": sum(row["source_type"] == "pdf" for row in rows),
            "video_count": sum(row["source_type"] == "video" for row in rows),
            "totals": totals,
            "sources": rows,
        }
        return self.build_result(json.dumps(result, ensure_ascii=False))

    @classmethod
    def _collectors(cls):
        defaults: dict[str, MetricCollector] = {
            "page_count": cls._page_count,
            "file_size_bytes": cls._file_size,
            "indexed_chunk_count": cls._indexed_chunk_count,
        }
        defaults.update(cls._metric_collectors)
        return defaults

    @staticmethod
    def _page_count(source: dict, _context: StepContext) -> int:
        if source.get("source_type", "pdf") != "pdf":
            return 0
        metadata = source.get("metadata") or {}
        for key in ("page_count", "pages", "num_pages"):
            if metadata.get(key) is not None:
                return int(metadata[key])
        path = source.get("path", "")
        if not path or not os.path.exists(path):
            return 0
        import fitz
        with fitz.open(path) as document:
            return len(document)

    @staticmethod
    def _file_size(source: dict, _context: StepContext) -> int:
        path = source.get("path", "")
        return os.path.getsize(path) if path and os.path.exists(path) else 0

    @staticmethod
    def _indexed_chunk_count(source: dict, context: StepContext) -> int:
        lm = context.llm_manager
        collection = getattr(lm, "collection", None) if lm else None
        if not collection or not hasattr(collection, "get"):
            return 0
        name = os.path.basename(source.get("path", ""))
        try:
            result = collection.get(where={"doc_name": name}, include=[])
            return len(result.get("ids", []))
        except Exception:
            return 0

    @staticmethod
    def _normalize_list(value) -> list[str]:
        if not value:
            return []
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except Exception:
                value = [part.strip() for part in value.split(",")]
        if not isinstance(value, list):
            value = [value]
        return [str(item).strip() for item in value if str(item).strip()]
