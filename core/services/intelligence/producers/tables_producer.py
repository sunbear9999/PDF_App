"""Tables stage: document tables, or the grid of a CSV/TSV/XLSX data source.

Data files are loaded through the DataSourceLoaderSpec their SourceFormatSpec
names, then published as one ``table`` artifact plus one ``table_row``
artifact per row. Row text is "Header: value; …" so keyword/semantic search
finds rows, and the locator keeps row/column identity instead of a fake page.
"""
from __future__ import annotations

from typing import Any, List

from core.models.intelligence_models import ArtifactRecord, ProcessingStage
from core.models.producer_contract import (
    ProducerBlocked, ProducerContext, ProducerResult,
)
from core.models.producer_models import ProducerIdentity, deterministic
from core.services.intelligence.producers.extraction_producers import ExtractionProducer

MAX_ROWS = 20000


class TablesProducer:
    producer_id = "table_extraction"

    def __init__(self, document_extraction_service: Any, artifact_service: Any,
                 source_format_registry: Any = None, data_provider_registry: Any = None):
        self._documents = ExtractionProducer(
            ProcessingStage.TABLES.value, document_extraction_service, artifact_service,
        )
        self._artifacts = artifact_service
        self._formats = source_format_registry
        self._loaders = data_provider_registry

    def identity(self, context: ProducerContext) -> ProducerIdentity:
        name = "data_grid_loader" if context.category == "data" else self.producer_id
        return deterministic(name, context.producer_version)

    def run(self, context: ProducerContext) -> ProducerResult:
        if context.category != "data":
            return self._documents.run(context)
        grid = self._load_grid(context)
        identity = self.identity(context)
        db = self._artifacts.db
        headers = [str(h) for h in getattr(grid, "headers", []) or []]
        rows = list(getattr(grid, "rows", []) or [])
        table_id = ArtifactRecord.stable_id(context.source_id, "table", "data_grid", "sheet")
        records = [self._table_record(context, table_id, grid, headers, len(rows))]
        for index, row in enumerate(rows[:MAX_ROWS]):
            if index % 1000 == 0:
                context.raise_if_cancelled()
            records.append(self._row_record(context, table_id, headers, index, row))
        ids = db.upsert_artifacts([identity.stamp(record) for record in records])
        return ProducerResult(
            output_ids=list(ids), identity=identity,
            diagnostics={"rows": len(rows), "rows_published": min(len(rows), MAX_ROWS)},
        )

    def _load_grid(self, context: ProducerContext):
        source_type = context.source.get("source_type") or ""
        spec = self._formats.get(source_type) if self._formats else None
        loader_id = getattr(spec, "data_loader_id", None)
        loader = self._loaders.get_data_source_loader(loader_id) if self._loaders and loader_id else None
        if not loader or not loader.callback:
            raise ProducerBlocked(f"No data loader is registered for '{source_type}' files.")
        if not loader.is_available():
            raise ProducerBlocked(loader.unavailable_reason or "The data loader is unavailable.")
        return loader.callback(context.path)

    def _table_record(self, context, table_id, grid, headers, row_count) -> ArtifactRecord:
        caption = getattr(grid, "name", "") or context.source.get("title") or ""
        return ArtifactRecord(
            artifact_id=table_id, source_id=context.source_id, source_path=context.path,
            source_type=context.source.get("source_type"), artifact_type="table",
            storage_ref_type="data_grid", storage_ref_id="sheet",
            exact_text="\n".join(filter(None, [caption, " | ".join(headers)])),
            locator={"sheet": caption, "row_count": row_count, "columns": headers},
            metadata={"stage": ProcessingStage.TABLES.value, "caption": caption,
                      "headers": headers, "row_count": row_count},
            input_hash=self._artifacts.text_input_hash(" | ".join(headers)),
        )

    def _row_record(self, context, table_id, headers, index, row) -> ArtifactRecord:
        cells: List[str] = ["" if value is None else str(value) for value in row]
        pairs = [
            f"{headers[i] if i < len(headers) else f'Column {i + 1}'}: {value}"
            for i, value in enumerate(cells) if value.strip()
        ]
        text = "; ".join(pairs)
        return ArtifactRecord(
            artifact_id=ArtifactRecord.stable_id(
                context.source_id, "table_row", "data_grid", str(index)
            ),
            source_id=context.source_id, source_path=context.path,
            source_type=context.source.get("source_type"), artifact_type="table_row",
            storage_ref_type="data_grid", storage_ref_id=str(index),
            parent_artifact_id=table_id, exact_text=text, normalized_text=text.lower(),
            locator={"row_index": index, "columns": headers},
            metadata={"stage": ProcessingStage.TABLES.value, "cells": cells},
            input_hash=self._artifacts.text_input_hash(text),
        )
