from __future__ import annotations

from core.engine.steps.base_step import BaseStep
from core.models.data_dock_models import PdfRegion
from core.plugins.plugin_step_protocol import StepContext
from core.services.pdf_grid_extraction_service import PdfGridExtractionService


class ExtractPdfGridStep(BaseStep):
    step_type = "EXTRACT_PDF_GRID"
    label = "Extract PDF Grid"
    category = "Data"
    description = "Deterministically extract a structured text grid from a PDF rectangle."
    input_schema = {
        "pdf_path": {"type": "string", "label": "PDF Path", "required": True},
        "page_number": {"type": "integer", "label": "Page (0-indexed)", "required": True},
        "bbox": {"type": "json", "label": "Bounding Box [x0,y0,x1,y1]", "required": True},
        "expected_rows": {"type": "integer", "label": "Expected Rows"},
        "expected_columns": {"type": "integer", "label": "Expected Columns"},
    }
    output_schema = {"grid": {"type": "object", "label": "Extracted Grid"}}

    def execute(self, context: StepContext, inputs: dict):
        bbox = inputs.get("bbox") or []
        if isinstance(bbox, str):
            import json
            bbox = json.loads(bbox)
        region = PdfRegion(
            str(inputs.get("pdf_path") or ""), int(inputs.get("page_number") or 0), list(bbox),
            str(inputs.get("request_id") or ""),
        )
        service = PdfGridExtractionService(context.data_provider_registry)
        grid = service.extract(
            region,
            expected_rows=int(inputs["expected_rows"]) if inputs.get("expected_rows") else None,
            expected_columns=int(inputs["expected_columns"]) if inputs.get("expected_columns") else None,
        )
        return self.build_result(grid.to_dict())
