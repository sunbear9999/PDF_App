from __future__ import annotations

import unittest
import sys
import types
from types import SimpleNamespace

if "PySide6" not in sys.modules:
    qtcore = types.ModuleType("PySide6.QtCore")
    class _QObject:
        def __init__(self, *args, **kwargs): pass
    class _Signal:
        def __init__(self, *args, **kwargs): self._callbacks = []
        def connect(self, callback): self._callbacks.append(callback)
        def emit(self, *args):
            for callback in list(self._callbacks): callback(*args)
    qtcore.QObject, qtcore.Signal = _QObject, _Signal
    sys.modules["PySide6"] = types.ModuleType("PySide6")
    sys.modules["PySide6.QtCore"] = qtcore

from core.models.data_dock_models import (
    ChartConfig, DataGridState, DataProvenance, ExtractedGrid, GridCoordinate, GridSelection,
)
from core.registries.data_provider_registry import DataProviderRegistry, GridExtractorSpec
from core.services.chart_data_service import ChartDataAdapter
from core.services.data_dock_service import DataDockService
from core.services.grid_mapping_service import GridMappingError, GridMappingService
from core.engine.steps.extract_pdf_grid_step import ExtractPdfGridStep


def state():
    return DataGridState(
        dataset_id="data_1", version=3, headers=["Label", "A", "B"],
        row_headers=["1", "2"], rows=[["x", "1", "2"], ["y", "3", "4"]],
    )


class GridMappingTests(unittest.TestCase):
    def test_column_vector_maps_to_row_headers(self):
        target = state()
        target.row_headers = ["", ""]
        selection = GridSelection("data_1", 3, [
            GridCoordinate("row_header", row=0), GridCoordinate("row_header", row=1),
        ])
        extracted = ExtractedGrid([["Alpha"], ["Beta"]], [[[]], [[]]], "words", provenance=DataProvenance(page_number=0))
        plan = GridMappingService().plan(target, selection, extracted)
        self.assertEqual([item.value for item in plan.assignments], ["Alpha", "Beta"])
        self.assertFalse(plan.requires_preview)

    def test_transpose_is_deterministic(self):
        target = state()
        selection = GridSelection("data_1", 3, [
            GridCoordinate("data", 0, 1), GridCoordinate("data", 1, 1),
        ])
        plan = GridMappingService().plan(target, selection, ExtractedGrid([["8", "9"]]))
        self.assertEqual(plan.orientation, "transposed")
        self.assertEqual([item.value for item in plan.assignments], ["8", "9"])

    def test_square_matrix_exposes_orientation_choice(self):
        target = state(); target.rows = [["", ""], ["", ""]]; target.headers = ["A", "B"]
        selection = GridSelection("data_1", 3, [GridCoordinate("data", row, column) for row in range(2) for column in range(2)])
        extracted = ExtractedGrid([["1", "2"], ["3", "4"]])
        direct = GridMappingService().plan(target, selection, extracted)
        transposed = GridMappingService().plan(target, selection, extracted, "transposed")
        self.assertTrue(direct.requires_preview)
        self.assertEqual([item.value for item in transposed.assignments], ["1", "3", "2", "4"])

    def test_mismatch_and_stale_selection_fail(self):
        target = state()
        with self.assertRaises(GridMappingError):
            GridMappingService().plan(target, GridSelection("data_1", 3, [GridCoordinate("data", 0, 0)]), ExtractedGrid([["1", "2"]]))
        with self.assertRaises(GridMappingError):
            GridMappingService().plan(target, GridSelection("data_1", 2, [GridCoordinate("data", 0, 0)]), ExtractedGrid([["1"]]))

    def test_mixed_headers_accept_empty_uneditable_corner(self):
        target = state(); target.headers[0] = ""; target.row_headers[0] = ""
        selection = GridSelection("data_1", 3, [
            GridCoordinate("column_header", column=0), GridCoordinate("row_header", row=0),
            GridCoordinate("data", row=0, column=0),
        ])
        plan = GridMappingService().plan(target, selection, ExtractedGrid([["", "Quarter"], ["North", "12"]]))
        self.assertEqual(plan.orientation, "masked_direct")
        self.assertEqual([item.value for item in plan.assignments], ["Quarter", "North", "12"])

    def test_conflicts_require_preview_and_patch_is_atomic(self):
        service = DataDockService(provider_registry=DataProviderRegistry())
        target = state(); service._track(target)
        selection = GridSelection("data_1", 3, [GridCoordinate("column_header", column=1)])
        plan = service.plan_pdf_fill("data_1", 3, selection, ExtractedGrid([["Revenue"]], strategy="words"))
        self.assertEqual(type(plan).from_dict(plan.to_dict()).assignments[0].value, "Revenue")
        self.assertTrue(plan.requires_preview)
        with self.assertRaises(ValueError):
            service.apply_grid_patch(plan)
        result = service.apply_grid_patch(plan, "overwrite")
        self.assertEqual(target.headers[1], "Revenue")
        self.assertEqual(target.version, 4)
        self.assertIn("column_header:1", result.applied)
        self.assertIn("column_header:1", target.cell_provenance)


class RegistryAndChartTests(unittest.TestCase):
    def test_extractor_registration_and_plugin_cleanup(self):
        registry = DataProviderRegistry()
        self.assertTrue({"horizontal_bar", "stacked_bar", "stacked_100_bar", "area", "stacked_area",
                         "donut", "histogram", "box", "heatmap"}.issubset(registry.chart_types()))
        registry.register_grid_extractor(GridExtractorSpec("p.extract", "Extract", plugin_id="plugin"))
        self.assertIn("p.extract", registry.grid_extractors())
        registry.remove_plugin("plugin")
        self.assertNotIn("p.extract", registry.grid_extractors())

    def test_extract_workflow_step_uses_registered_provider(self):
        registry = DataProviderRegistry()
        def broken(_payload):
            raise RuntimeError("plugin failure")
        registry.register_grid_extractor(GridExtractorSpec("broken", "Broken", callback=broken, position=1))
        registry.register_grid_extractor(GridExtractorSpec(
            "test", "Test", callback=lambda payload: ExtractedGrid([["A", "1"]], strategy="test"), position=2,
        ))
        context = SimpleNamespace(data_provider_registry=registry)
        result = ExtractPdfGridStep().execute(context, {
            "pdf_path": "/not/opened.pdf", "page_number": 0, "bbox": [0, 0, 10, 10],
        })
        self.assertEqual(result.raw_value["cells"], [["A", "1"]])

    def test_chart_adapter_aligns_series_and_keeps_missing_values(self):
        target = state()
        target.rows[1][2] = ""
        config = ChartConfig(
            "chart_1", x_field="Label", y_field="A",
            series=[{"name": "A", "y_field": "A"}, {"name": "B", "y_field": "B"}],
        )
        normalized = ChartDataAdapter().adapt(target, config)
        self.assertEqual(normalized["categories"], ["x", "y"])
        self.assertEqual(normalized["series"][0]["values"], [1.0, 3.0])
        self.assertEqual(normalized["series"][1]["values"], [2.0, None])

    def test_deterministic_shape_tools_and_provenance_remap(self):
        service = DataDockService(provider_registry=DataProviderRegistry())
        target = state(); target.cell_provenance["data:0:0"] = {"source_text": "x"}; service._track(target)
        service.sort_rows(target.dataset_id, 1, reverse=True)
        self.assertEqual(target.rows[0][0], "y")
        self.assertEqual(target.cell_provenance["data:1:0"]["source_text"], "x")
        service.unpivot(target.dataset_id, [1, 2])
        self.assertEqual(target.headers[-2:], ["Variable", "Value"])

    def test_pivot_and_join(self):
        service = DataDockService(provider_registry=DataProviderRegistry())
        long = DataGridState("long", headers=["Region", "Metric", "Value"], row_headers=["1", "2"],
                             rows=[["North", "A", "1"], ["North", "B", "2"]])
        lookup = DataGridState("lookup", headers=["Region", "Owner"], row_headers=["1"], rows=[["North", "Ada"]])
        service._track(long); service._track(lookup)
        service.pivot("long", "Region", "Metric", "Value")
        self.assertEqual(long.rows, [["North", "1", "2"]])
        service.join_dataset("long", "lookup", "Region")
        self.assertEqual(long.headers[-1], "Owner")
        self.assertEqual(long.rows[0][-1], "Ada")


if __name__ == "__main__":
    unittest.main()
