from __future__ import annotations

import base64
import json
import math
import re
import uuid
import io
from dataclasses import dataclass
from typing import Any, Optional

from core.models.data_dock_models import DataGridState, DataProvenance
from core.utils.numeric_utils import parse_number


@dataclass
class PdfDataSelectionResult:
    selection_type: str
    provenance: DataProvenance
    image_data: bytes
    image_mime_type: str = "image/png"
    table_state: Optional[DataGridState] = None

    def workflow_images(self) -> list[dict]:
        return [{
            "data": base64.b64encode(self.image_data).decode("ascii"),
            "mime_type": self.image_mime_type,
        }]


class PdfDataSelectionService:
    """Headless PDF-region rendering and deterministic data classification."""

    def __init__(self, data_dock_service, provider_registry=None, grid_extraction_service=None):
        self.data_dock_service = data_dock_service
        self.provider_registry = provider_registry or data_dock_service.provider_registry
        if grid_extraction_service is None:
            from core.services.pdf_grid_extraction_service import PdfGridExtractionService
            grid_extraction_service = PdfGridExtractionService(self.provider_registry)
        self.grid_extraction_service = grid_extraction_service

    def inspect(self, pdf_path: str, page_number: int, bbox: list[float]) -> PdfDataSelectionResult:
        import fitz

        provenance = DataProvenance(
            source_path=pdf_path,
            pdf_path=pdf_path,
            page_number=page_number,
            bounding_box_coordinates=[list(bbox)],
        )
        with fitz.open(pdf_path) as doc:
            page = doc.load_page(page_number)
            clip = fitz.Rect(bbox)
            pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), clip=clip, alpha=False)
            image_data = pix.tobytes("png")
            chart_score, raster_kind = self._chart_evidence(page, clip, image_data)
            if chart_score < 3 and raster_kind != "chart":
                from core.models.data_dock_models import PdfRegion
                extracted = self.grid_extraction_service.extract(PdfRegion(pdf_path, page_number, list(bbox)))
                extracted_rows, extracted_columns = extracted.shape
                if extracted_rows >= 2 and extracted_columns >= 2:
                    table = self.data_dock_service._state_from_matrix(
                        extracted.cells, "Selected PDF Data", page_number, pdf_path, bbox,
                    )
                    if table:
                        table.provenance = provenance
                        return PdfDataSelectionResult("table", provenance, image_data, table_state=table)
            table = self.data_dock_service._extract_table_from_region(
                page, page_number, pdf_path, clip, 0, lax=False
            )
            if table and self.data_dock_service._matrix_quality_ok([table.headers] + table.rows):
                # Chart labels can look tabular to text-alignment extractors.
                # Strong chart evidence wins unless the raster itself contains
                # a repeated row/column grid.
                if chart_score >= 3 or raster_kind == "chart":
                    return PdfDataSelectionResult("chart", provenance, image_data)
                table.provenance = provenance
                return PdfDataSelectionResult("table", provenance, image_data, table_state=table)

            for provider in self.provider_registry.selection_classifiers():
                kind = provider.classify_selection(
                    {"pdf_path": pdf_path, "page_number": page_number, "bbox": list(bbox), "image_data": image_data},
                    provenance,
                )
                if kind in {"table", "chart", "unsupported"}:
                    return PdfDataSelectionResult(kind, provenance, image_data)

            if chart_score >= 2:
                return PdfDataSelectionResult("chart", provenance, image_data)
            if raster_kind == "table":
                return PdfDataSelectionResult("table", provenance, image_data)
            return PdfDataSelectionResult("unsupported", provenance, image_data)

    @staticmethod
    def validate_prompt_managed_blueprint(blueprint) -> None:
        """Reject feature providers that bypass Prompt Manager instructions."""
        for step in getattr(blueprint, "steps", []) or []:
            if getattr(step, "step_type", "") not in {"LLM_QUERY", "LLM_SCHEMA_QUERY"}:
                continue
            if not getattr(step, "prompt_key", None) or not getattr(step, "required_prompt_keys", None):
                raise ValueError("Vision workflow LLM steps must declare Prompt Manager keys.")
            system_prompt = str(getattr(step, "system_prompt", "") or "")
            literal = re.sub(r"\{prompt:[^}]+\}", "", system_prompt).strip()
            if literal:
                raise ValueError("Vision workflow system instructions must come from Prompt Manager keys.")
            query = str((getattr(step, "inputs", {}) or {}).get("query", ""))
            if "{prompt:" not in query or re.sub(r"\{prompt:[^}]+\}", "", query).strip():
                raise ValueError("Vision workflow query instructions must come from Prompt Manager keys.")

    @staticmethod
    def _chart_evidence(page, clip, image_data: bytes) -> tuple[int, str]:
        selected_text = (page.get_text("text", clip=clip) or "").lower()
        try:
            context_clip = type(clip)(clip.x0 - 24, clip.y0 - 72, clip.x1 + 24, clip.y1 + 36) & page.rect
            context_text = (page.get_text("text", clip=context_clip) or "").lower()
        except Exception:
            context_text = selected_text
        chart_caption = bool(re.search(r"\b(chart|graph)\b", context_text))
        numeric_labels = len(re.findall(r"(?<!\w)[+\-−]?(?:\d[\d,]*(?:\.\d+)?|\.\d+)%?(?!\w)", selected_text))
        drawing_count = 0
        try:
            for drawing in page.get_drawings():
                rect = drawing.get("rect")
                if rect and rect.intersects(clip):
                    drawing_count += 1
        except Exception:
            pass
        image_count = 0
        try:
            for image in page.get_image_info() or []:
                rect = image.get("bbox")
                if rect and type(clip)(rect).intersects(clip):
                    image_count += 1
        except Exception:
            pass
        has_graphics = drawing_count > 0 or image_count > 0
        raster_kind = PdfDataSelectionService._raster_geometry_kind(image_data)
        score = 0
        if chart_caption and has_graphics:
            score += 8
        if numeric_labels >= 2 and has_graphics:
            score += 2
        if drawing_count >= 12:
            score += 2
        if raster_kind == "chart":
            score += 2
        elif raster_kind == "table":
            score -= 4
        return score, raster_kind

    @staticmethod
    def _raster_geometry_kind(image_data: bytes) -> str:
        """Conservative deterministic grid-versus-axes detector."""
        try:
            from PIL import Image
            rgb = Image.open(io.BytesIO(image_data)).convert("RGB")
            rgb.thumbnail((256, 256))
            image = rgb.convert("L")
            width, height = image.size
            if width < 24 or height < 24:
                return "unsupported"
            pixels = image.load()

            def longest_dark_run(values) -> int:
                longest = current = 0
                for pixel in values:
                    if pixel < 150:
                        current += 1
                        longest = max(longest, current)
                    else:
                        current = 0
                return longest

            horizontal_lines = [
                y for y in range(height)
                if longest_dark_run(pixels[x, y] for x in range(width)) >= width * 0.45
            ]
            vertical_lines = [
                x for x in range(width)
                if longest_dark_run(pixels[x, y] for y in range(height)) >= height * 0.45
            ]

            def band_count(indices) -> int:
                count = 0
                previous = None
                for index in indices:
                    if previous is None or index > previous + 1:
                        count += 1
                    previous = index
                return count

            horizontal = band_count(horizontal_lines)
            vertical = band_count(vertical_lines)
            if horizontal >= 3 and vertical >= 3:
                return "table"
            if horizontal >= 1 and vertical >= 1:
                return "chart"
            quantized = rgb.quantize(colors=16)
            palette = quantized.getpalette() or []
            counts = quantized.getcolors(maxcolors=16) or []
            total = max(1, width * height)
            white_share = 0.0
            saturated_regions = 0
            for count, palette_index in counts:
                offset = palette_index * 3
                red, green, blue = palette[offset:offset + 3]
                share = count / total
                if min(red, green, blue) > 225:
                    white_share += share
                elif max(red, green, blue) - min(red, green, blue) > 45 and share >= 0.015:
                    saturated_regions += 1
            if white_share >= 0.45 and 2 <= saturated_regions <= 10:
                return "chart"
            return "unsupported"
        except Exception:
            return "unsupported"

    def grid_from_chart_analysis(
        self,
        raw: Any,
        provenance: DataProvenance,
        *,
        trace_id: str = "",
        model: str = "",
        source_asset_id: str = "",
    ) -> DataGridState:
        if isinstance(raw, str):
            raw = json.loads(raw)
        if not isinstance(raw, dict) or not isinstance(raw.get("series"), list):
            raise ValueError("Chart analysis did not return the required structured result.")

        category_title = str(raw.get("category_title") or "Category").strip() or "Category"
        series_payloads = [item for item in raw["series"] if isinstance(item, dict)]
        headers = [category_title]
        labels: list[str] = []
        point_maps: list[dict[str, dict]] = []
        for index, series in enumerate(series_payloads):
            name = str(series.get("name") or f"Series {index + 1}").strip()
            headers.append(name or f"Series {index + 1}")
            points = {}
            for point in series.get("points") or []:
                if (
                    not isinstance(point, dict)
                    or isinstance(point.get("value"), bool)
                    or not isinstance(point.get("value"), (int, float))
                ):
                    continue
                value = float(point["value"])
                if not math.isfinite(value):
                    continue
                label = str(point.get("label") or "").strip() or str(len(labels) + 1)
                if label not in labels:
                    labels.append(label)
                points[label] = point
            point_maps.append(points)
        if not point_maps or not labels:
            raise ValueError("The chart analysis contained no readable numeric points.")

        rows = []
        cell_provenance = {}
        for row_index, label in enumerate(labels):
            row = [label]
            for series_index, points in enumerate(point_maps, start=1):
                point = points.get(label)
                if not point:
                    row.append("")
                    continue
                number = float(point["value"])
                row.append("%g" % number)
                source_text = str(point.get("source_text") or "").strip()
                status = self._validated_evidence_status(point, number, source_text)
                cell_provenance[f"{row_index}:{series_index}"] = {
                    "source": "vision_chart_analysis",
                    "evidence_status": status,
                    "source_text": source_text,
                    "trace_id": trace_id,
                    "model": model,
                }
            rows.append(row)

        return DataGridState(
            dataset_id=f"data_{uuid.uuid4()}",
            name=str(raw.get("title") or "Extracted Chart Data").strip() or "Extracted Chart Data",
            headers=headers,
            row_headers=[str(index + 1) for index in range(len(rows))],
            rows=rows,
            provenance=provenance,
            cell_provenance=cell_provenance,
            metadata={
                "source_kind": "chart_vision",
                "trace_id": trace_id,
                "model": model,
                "source_asset_id": source_asset_id,
                "value_axis_title": str(raw.get("value_axis_title") or ""),
            },
            dirty=True,
        )

    @staticmethod
    def _validated_evidence_status(point: dict, value: float, source_text: str) -> str:
        if str(point.get("evidence_status") or "").lower() != "exact":
            return "estimated"
        if not source_text or re.search(r"(?:~|≈|about|approx|roughly)", source_text, re.I):
            return "estimated"
        parsed = parse_number(source_text)
        if parsed is None or not math.isclose(parsed.value, value, rel_tol=1e-9, abs_tol=1e-9):
            return "estimated"
        return "exact"
