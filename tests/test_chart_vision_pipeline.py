import sqlite3
import tempfile
import unittest
import os
import io

try:
    import fitz
except ImportError:  # pragma: no cover - production dependency
    fitz = None

from core.db.media_asset_db import MediaAssetDB
from core.engine.blueprints.chart_vision import (
    build_chart_analysis_blueprint,
)
from core.engine.action_model import ActionStep, AIActionBlueprint
from core.models.data_dock_models import DataGridState, DataProvenance
from core.prompt_manager import PromptManager
from core.models.prompt_models import collect_step_prompt_usage
from core.registries.data_provider_registry import DataProviderRegistry
from core.services.data_dock_service import DataDockService
from core.services.pdf_data_selection_service import PdfDataSelectionService


class _MediaManager:
    def __init__(self):
        self._conn = sqlite3.connect(":memory:")
        self._conn.execute(
            "CREATE TABLE media_assets (id TEXT PRIMARY KEY, mime_type TEXT, sha256 TEXT UNIQUE, data BLOB, metadata_json TEXT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
        )


class ChartVisionPipelineTests(unittest.TestCase):
    def test_deterministic_raster_classifier_distinguishes_charts_and_table_grid(self):
        from PIL import Image, ImageDraw

        def png(draw_callback):
            image = Image.new("RGB", (320, 220), "white")
            draw_callback(ImageDraw.Draw(image))
            output = io.BytesIO()
            image.save(output, format="PNG")
            return output.getvalue()

        bar = png(lambda draw: (
            draw.line((35, 20, 35, 190), fill="black", width=3),
            draw.line((35, 190, 300, 190), fill="black", width=3),
            draw.rectangle((65, 110, 100, 190), fill="#268bd2"),
            draw.rectangle((125, 65, 160, 190), fill="#2aa198"),
            draw.rectangle((185, 90, 220, 190), fill="#cb4b16"),
        ))
        line = png(lambda draw: (
            draw.line((35, 20, 35, 190), fill="black", width=3),
            draw.line((35, 190, 300, 190), fill="black", width=3),
            draw.line((50, 160, 110, 100, 180, 130, 275, 45), fill="#268bd2", width=4),
        ))
        pie = png(lambda draw: (
            draw.pieslice((70, 20, 270, 220), 0, 120, fill="#268bd2"),
            draw.pieslice((70, 20, 270, 220), 120, 230, fill="#2aa198"),
            draw.pieslice((70, 20, 270, 220), 230, 360, fill="#cb4b16"),
        ))
        table = png(lambda draw: (
            [draw.line((30, y, 290, y), fill="black", width=2) for y in (30, 75, 120, 165, 205)],
            [draw.line((x, 30, x, 205), fill="black", width=2) for x in (30, 115, 205, 290)],
        ))

        classifier = PdfDataSelectionService._raster_geometry_kind
        self.assertEqual(classifier(bar), "chart")
        self.assertEqual(classifier(line), "chart")
        self.assertEqual(classifier(pie), "chart")
        self.assertEqual(classifier(table), "table")

    @unittest.skipUnless(fitz is not None, "PyMuPDF is required")
    def test_pdf_selection_detects_bordered_table_and_vector_chart(self):
        handle, path = tempfile.mkstemp(suffix=".pdf")
        os.close(handle)
        try:
            doc = fitz.open()
            page = doc.new_page()
            for x in (50, 150, 250):
                page.draw_line((x, 50), (x, 140))
            for y in (50, 80, 110, 140):
                page.draw_line((50, y), (250, y))
            for y, row in zip((70, 100, 130), (("Month", "Value"), ("Jan", "-10."), ("Feb", "20"))):
                page.insert_text((60, y), row[0])
                page.insert_text((160, y), row[1])
            page.insert_text((320, 50), "Revenue chart")
            for index, height in enumerate((30, 50, 40, 70, 60, 80)):
                page.draw_rect(fitz.Rect(330 + index * 35, 160 - height, 350 + index * 35, 160), fill=(0.2, 0.5, 0.8))
            page.insert_text((50, 230), "This is ordinary prose, not structured data.")
            doc.save(path)
            doc.close()

            dock = DataDockService(provider_registry=DataProviderRegistry())
            service = PdfDataSelectionService(dock)
            table = service.inspect(path, 0, [45, 45, 255, 145])
            original_extract = dock._extract_table_from_region
            dock._extract_table_from_region = lambda *args, **kwargs: DataGridState(
                "false-table", headers=["0", "1"], rows=[["10", "20"], ["30", "40"]]
            )
            chart = service.inspect(path, 0, [315, 60, 560, 170])
            dock._extract_table_from_region = original_extract
            unsupported = service.inspect(path, 0, [45, 205, 350, 250])

            self.assertEqual(table.selection_type, "table")
            self.assertEqual(table.table_state.rows, [["Jan", "-10."], ["Feb", "20"]])
            self.assertEqual(chart.selection_type, "chart")
            self.assertTrue(chart.image_data.startswith(b"\x89PNG"))
            self.assertEqual(unsupported.selection_type, "unsupported")
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_vision_blueprints_use_only_prompt_manager_keys(self):
        manager = PromptManager()
        for blueprint in (build_chart_analysis_blueprint(),):
            step = blueprint.steps[0]
            self.assertEqual(step.step_type, "LLM_QUERY")
            self.assertEqual(step.required_model_capabilities, ["vision"])
            self.assertTrue(step.required_prompt_keys)
            self.assertTrue(all(manager.get_prompt(key) for key in step.required_prompt_keys))
            usage = collect_step_prompt_usage(step)
            self.assertTrue(set(step.required_prompt_keys).issubset(set(usage.explicit)))
            self.assertNotIn("Classify the attached", step.system_prompt or "")
            self.assertNotIn("Mark a value exact", step.system_prompt or "")

    def test_feature_rejects_inline_vision_system_instructions(self):
        blueprint = AIActionBlueprint("bad", "bad", steps=[ActionStep(
            step_id="bad",
            step_type="LLM_QUERY",
            prompt_key="Chart Analysis System v1",
            required_prompt_keys=["Chart Analysis System v1"],
            system_prompt="hardcoded instruction",
        )])

        with self.assertRaisesRegex(ValueError, "Prompt Manager"):
            PdfDataSelectionService.validate_prompt_managed_blueprint(blueprint)

    def test_chart_result_builds_clean_grid_and_estimate_provenance(self):
        dock = DataDockService(provider_registry=DataProviderRegistry())
        service = PdfDataSelectionService(dock)
        raw = {
            "title": "Quarterly Revenue",
            "category_title": "Quarter",
            "value_axis_title": "USD millions",
            "series": [{
                "name": "Revenue",
                "points": [
                    {"label": "Q1", "value": 10, "evidence_status": "exact", "source_text": "10"},
                    {"label": "Q2", "value": 12.5, "evidence_status": "estimated", "source_text": "axis estimate"},
                    {"label": "Q3", "value": 15, "evidence_status": "exact", "source_text": "about 15"},
                ],
            }],
        }

        state = service.grid_from_chart_analysis(raw, DataProvenance(pdf_path="sample.pdf"), trace_id="trace-1", model="vision")

        self.assertEqual(state.headers, ["Quarter", "Revenue"])
        self.assertEqual(state.rows, [["Q1", "10"], ["Q2", "12.5"], ["Q3", "15"]])
        self.assertEqual(state.cell_provenance["0:1"]["evidence_status"], "exact")
        self.assertEqual(state.cell_provenance["1:1"]["evidence_status"], "estimated")
        self.assertEqual(state.cell_provenance["2:1"]["evidence_status"], "estimated")

        dock._track(state)
        dock.update_cell(state.dataset_id, 1, 1, "13")
        self.assertNotIn("1:1", state.cell_provenance)

    def test_media_assets_are_deduplicated_and_project_contained(self):
        manager = _MediaManager()
        db = MediaAssetDB(manager)
        first = db.put(b"png-data", "image/png", {"kind": "chart"})
        second = db.put(b"png-data", "image/png", {"kind": "chart"})

        self.assertEqual(first, second)
        self.assertEqual(db.get(first)["data"], b"png-data")


if __name__ == "__main__":
    unittest.main()
