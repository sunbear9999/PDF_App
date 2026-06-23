import os
import sqlite3
import sys
import tempfile
import types
import unittest

if "PySide6" not in sys.modules:
    qtcore = types.ModuleType("PySide6.QtCore")
    class _QObject:
        def __init__(self, *args, **kwargs):
            pass
    class _Signal:
        def __init__(self, *args, **kwargs):
            self._callbacks = []
        def connect(self, cb):
            self._callbacks.append(cb)
        def emit(self, *args, **kwargs):
            for cb in list(self._callbacks):
                cb(*args, **kwargs)
    qtcore.QObject = _QObject
    qtcore.Signal = _Signal
    pyside = types.ModuleType("PySide6")
    sys.modules["PySide6"] = pyside
    sys.modules["PySide6.QtCore"] = qtcore

from core.db.data_dock_db import DataDockDB
from core.models.data_dock_models import ChartConfig, DataGridState, DataProvenance
from core.models.ontology_model import EntityType, RelationType
from core.ontology.registry import OntologyRegistry
from core.registries.data_provider_registry import ChartTypeSpec, DataCleanerSpec, DataProviderRegistry, GridActionSpec
from core.registries.workspace_registry import build_default_workspace_node_type_registry
from core.services.data_dock_service import DataDockService


class _DataDockManager:
    def __init__(self, path):
        self.project_filepath = path
        self._conn = sqlite3.connect(path)
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS data_dock_datasets (
                id TEXT PRIMARY KEY,
                name TEXT,
                grid_json TEXT NOT NULL,
                provenance_json TEXT NOT NULL DEFAULT '{}',
                column_types_json TEXT NOT NULL DEFAULT '{}',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS data_dock_charts (
                id TEXT PRIMARY KEY,
                dataset_id TEXT,
                name TEXT,
                config_json TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        self.db = DataDockDB(self)

    def list_data_dock_datasets(self):
        return self.db.list_datasets()

    def get_data_dock_dataset(self, dataset_id):
        return self.db.get_dataset(dataset_id)

    def save_data_dock_dataset(self, state):
        return self.db.upsert_dataset(state)

    def delete_data_dock_dataset(self, dataset_id):
        return self.db.delete_dataset(dataset_id)

    def save_project(self):
        self._conn.commit()

    def close(self):
        self._conn.close()


class _FakeTable:
    bbox = [0, 0, 100, 100]

    def extract(self):
        return [["Date", "AAPL"], ["Jan", "10"], ["Feb", "11"]]


class _FakeFinder:
    tables = [_FakeTable()]


class _FakeRect:
    x0 = 0
    y0 = 0
    x1 = 200
    y1 = 200


class _FakePage:
    rect = _FakeRect()

    def find_tables(self):
        return _FakeFinder()

    def get_text(self, mode):
        if mode == "dict":
            return {"blocks": [{"type": 1, "bbox": [20, 20, 80, 80]}]}
        if mode == "text":
            return "Figure 1. Price chart"
        if mode == "words":
            return []
        return ""


class _FakeDoc:
    def __len__(self):
        return 1

    def load_page(self, index):
        return _FakePage()


class _NoisyPage(_FakePage):
    def find_tables(self):
        return types.SimpleNamespace(tables=[])

    def get_text(self, mode):
        if mode == "words":
            return [
                [10, 10, 34, 20, "This"], [39, 10, 52, 20, "is"], [57, 10, 90, 20, "page"],
                [95, 10, 130, 20, "text"], [135, 10, 150, 20, "1"],
                [10, 30, 40, 40, "with"], [45, 30, 90, 40, "random"], [95, 30, 120, 40, "2024"],
            ]
        if mode == "dict":
            return {"blocks": []}
        if mode == "text":
            return "This is page text 1\nwith random 2024"
        return ""


class _NoisyDoc:
    def __len__(self):
        return 1

    def load_page(self, index):
        return _NoisyPage()


class DataDockServiceTests(unittest.TestCase):
    def test_default_parser_preserves_provenance(self):
        registry = DataProviderRegistry()
        provenance = DataProvenance(pdf_path="/tmp/source.pdf", page_number=2, bounding_box_coordinates=[[1, 2, 3, 4]], selection_text="Year Value\n2020 10")
        state = registry.parse_selection("Year\tValue\n2020\t10\n2021\t15", provenance)

        self.assertEqual(state.headers, ["Year", "Value"])
        self.assertEqual(state.row_headers, ["1", "2"])
        self.assertEqual(state.rows, [["2020", "10"], ["2021", "15"]])
        self.assertEqual(state.provenance.pdf_path, "/tmp/source.pdf")
        self.assertEqual(state.provenance.page_number, 2)

    def test_memory_only_until_explicit_save_and_saved_survives_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_path = os.path.join(tmp, "data_test.pdfproj")
            pm = _DataDockManager(project_path)
            service = DataDockService(pm, DataProviderRegistry())

            state = service.new_dataset("Working Table", rows=1, columns=2)
            service.update_grid(state.dataset_id, ["A", "B"], [["1", "2"]])
            service.update_grid(state.dataset_id, ["A", "B"], [["1", "2"]], row_headers=["Day 1"])

            self.assertFalse(state.is_persisted)
            self.assertEqual(pm.list_data_dock_datasets(), [])

            saved = service.save_dataset(state.dataset_id)
            self.assertTrue(saved.is_persisted)
            self.assertFalse(saved.dirty)
            self.assertEqual(len(pm.list_data_dock_datasets()), 1)

            pm.save_project()
            pm.close()
            pm2 = _DataDockManager(project_path)
            reloaded = pm2.get_data_dock_dataset(saved.dataset_id)
            self.assertIsNotNone(reloaded)
            self.assertEqual(reloaded.headers, ["A", "B"])
            self.assertEqual(reloaded.row_headers, ["Day 1"])
            self.assertEqual(reloaded.rows, [["1", "2"]])
            pm2.close()

    def test_clear_memory_drops_unsaved_but_not_project_saved_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            pm = _DataDockManager(os.path.join(tmp, "data_test.pdfproj"))
            service = DataDockService(pm, DataProviderRegistry())
            unsaved = service.new_dataset("Unsaved", rows=1, columns=1)
            saved = service.new_dataset("Saved", rows=1, columns=1)
            service.save_dataset(saved.dataset_id)

            service.clear_memory()
            ids = {item["dataset_id"] for item in service.list_datasets()}

            self.assertNotIn(unsaved.dataset_id, ids)
            self.assertIn(saved.dataset_id, ids)
            pm.close()

    def test_project_save_flushes_open_data_dock_edits(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_path = os.path.join(tmp, "data_test.pdfproj")
            pm = _DataDockManager(project_path)
            service = DataDockService(pm, DataProviderRegistry())

            state = service.new_dataset("Autosaved", rows=1, columns=1)
            service.update_cell(state.dataset_id, 0, 0, "42")

            saved = service.save_all_open_datasets()
            self.assertEqual(len(saved), 1)
            self.assertTrue(saved[0].is_persisted)
            self.assertFalse(saved[0].dirty)

            pm.save_project()
            pm.close()
            pm2 = _DataDockManager(project_path)
            reloaded = pm2.get_data_dock_dataset(state.dataset_id)
            self.assertIsNotNone(reloaded)
            self.assertEqual(reloaded.name, "Autosaved")
            self.assertEqual(reloaded.rows, [["42"]])
            pm2.close()

    def test_transforms_and_summary(self):
        service = DataDockService(provider_registry=DataProviderRegistry())
        state = service.new_dataset("Numbers", rows=0, columns=0)
        service.update_grid(state.dataset_id, ["A", "B", ""], [["1", "2", ""], ["", "", ""], ["3", "4", ""]])
        service.update_grid(state.dataset_id, ["A", "B", ""], [["1", "2", ""], ["", "", ""], ["3", "4", ""]], row_headers=["Jan", "blank", "Feb"])
        service.drop_empty(state.dataset_id)

        self.assertEqual(state.headers, ["A", "B"])
        self.assertEqual(state.row_headers, ["Jan", "Feb"])
        self.assertEqual(state.rows, [["1", "2"], ["3", "4"]])

        service.transpose(state.dataset_id)
        self.assertEqual(state.headers, ["A", "B"])
        self.assertEqual(state.row_headers, ["A", "B"])
        self.assertEqual(state.rows, [["1", "3"], ["2", "4"]])

        summary = service.selection_summary(["1", "$2", "bad", "3%", "(4)", "", "1,000.50"])
        self.assertEqual(summary["count"], 7)
        self.assertEqual(summary["numeric_count"], 5)
        self.assertAlmostEqual(summary["sum"], 1002.5)
        self.assertAlmostEqual(summary["average"], 200.5)
        self.assertEqual(summary["min"], -4.0)
        self.assertEqual(summary["max"], 1000.5)
        self.assertEqual(service.format_number(100000), "100,000")

    def test_add_metric_row_and_column(self):
        service = DataDockService(provider_registry=DataProviderRegistry())
        state = service.new_dataset("Metrics", rows=0, columns=0)
        service.update_grid(
            state.dataset_id,
            ["A", "B", "C"],
            [["1", "2", "100,000"], ["3", "4", "200,000"]],
            row_headers=["R1", "R2"],
        )

        service.add_metric(state.dataset_id, "column", "average", [0, 1], "Avg AB")
        self.assertEqual(state.headers[-1], "Avg AB")
        self.assertEqual(state.rows[0][-1], "1.5")
        self.assertEqual(state.rows[1][-1], "3.5")

        service.add_metric(state.dataset_id, "row", "sum", [0, 1], "Sum Rows")
        self.assertEqual(state.row_headers[-1], "Sum Rows")
        self.assertEqual(state.rows[-1][0], "4")
        self.assertEqual(state.rows[-1][2], "300,000")

    def test_add_metric_for_selection_leaves_unselected_cells_blank(self):
        service = DataDockService(provider_registry=DataProviderRegistry())
        state = service.new_dataset("Selection Metrics", rows=0, columns=0)
        service.update_grid(
            state.dataset_id,
            ["A", "B", "C"],
            [["1", "2", "9"], ["3", "4", "9"], ["5", "6", "9"]],
            row_headers=["R1", "R2", "R3"],
        )

        service.add_metric_for_selection(state.dataset_id, "column", "average", rows=[0, 1], columns=[0, 1], label="Avg AB")
        self.assertEqual(state.headers, ["A", "B", "Avg AB", "C"])
        self.assertEqual(state.rows[0], ["1", "2", "1.5", "9"])
        self.assertEqual(state.rows[1], ["3", "4", "3.5", "9"])
        self.assertEqual(state.rows[2], ["5", "6", "", "9"])

        service.add_metric_for_selection(state.dataset_id, "row", "sum", rows=[0, 1], columns=[0, 1], label="Sum R1 R2")
        self.assertEqual(state.row_headers, ["R1", "R2", "Sum R1 R2", "R3"])
        self.assertEqual(state.rows[2], ["4", "6", "", ""])

    def test_cleaners_promote_fill_split_merge_and_infer(self):
        service = DataDockService(provider_registry=DataProviderRegistry())
        state = service.new_dataset("Messy", rows=0, columns=0)
        service.update_grid(
            state.dataset_id,
            [" keep ", "Label", "Combined"],
            [[" Year ", "A", "North;West"], ["2020", "Jan", "1200;North"], ["2021", "", "1300;West"]],
            row_headers=[" r1 ", " r2 ", " r3 "],
        )

        service.trim_whitespace(state.dataset_id)
        self.assertEqual(state.headers[0], "keep")
        self.assertEqual(state.row_headers[0], "r1")

        service.promote_row_to_headers(state.dataset_id, 0)
        self.assertEqual(state.headers, ["Year", "A", "North;West"])
        self.assertEqual(state.rows[0][0], "2020")

        service.fill_down(state.dataset_id, [1])
        self.assertEqual(state.rows[1][1], "Jan")

        service.split_column(state.dataset_id, 2, ";")
        self.assertEqual(state.headers[2:4], ["North;West", "North;West 2"])
        self.assertEqual(state.rows[0][2:4], ["1200", "North"])

        service.merge_columns(state.dataset_id, [2, 3], " / ")
        self.assertIn("North;West", state.headers[2])

        service.normalize_numbers(state.dataset_id, [0])
        self.assertEqual(state.rows[0][0], "2020")

        service.infer_column_types(state.dataset_id)
        self.assertEqual(state.column_types["Year"], "Number")
        self.assertIn("cleaning_history", state.metadata)

    def test_promote_column_to_row_headers(self):
        service = DataDockService(provider_registry=DataProviderRegistry())
        state = service.new_dataset("Rows", rows=0, columns=0)
        service.update_grid(state.dataset_id, ["Name", "Value"], [["Jan", "1"], ["Feb", "2"]])

        service.promote_column_to_row_headers(state.dataset_id, 0)

        self.assertEqual(state.headers, ["Value"])
        self.assertEqual(state.row_headers, ["Jan", "Feb"])
        self.assertEqual(state.rows, [["1"], ["2"]])

    def test_richer_state_and_chart_config_roundtrip(self):
        provenance = DataProvenance(
            source_id="source:1",
            source_path="/tmp/source.pdf",
            source_type="pdf",
            pdf_path="/tmp/source.pdf",
            page_number=4,
            bounding_box_coordinates=[[1, 2, 3, 4]],
            selection_text="raw",
            parent_dataset_id="data_parent",
            selection_ref="sel-1",
        )
        state = DataGridState(
            dataset_id="data_x",
            name="X",
            headers=["A"],
            row_headers=["r1"],
            rows=[["1"]],
            provenance=provenance,
            cell_provenance={"0:0": {"source": "cell"}},
            metadata={"cleaning_history": [{"action": "trim"}]},
        )
        reloaded = DataGridState.from_dict(state.to_dict())

        self.assertEqual(reloaded.provenance.source_id, "source:1")
        self.assertEqual(reloaded.cell_provenance["0:0"]["source"], "cell")
        self.assertEqual(reloaded.metadata["cleaning_history"][0]["action"], "trim")

        config = ChartConfig(
            chart_id="chart_x",
            title="Cost",
            subtitle="Annual",
            chart_type="bar",
            x_title="Time",
            y_title="Cost of living",
            show_data_labels=True,
            color_overrides={"2020": "#ff0000"},
            series=[{"name": "Cost"}],
            source_selection={"cells": [[0, 0], [0, 1]]},
            export_options={"format": "png"},
        )
        parsed = ChartConfig.from_dict(config.to_dict())
        self.assertEqual(parsed.title, "Cost")
        self.assertTrue(parsed.show_data_labels)
        self.assertEqual(parsed.color_overrides["2020"], "#ff0000")

    def test_data_dock_registry_plugin_extension_points(self):
        registry = DataProviderRegistry()
        registry.register_chart_type(ChartTypeSpec(chart_type="heatmap", label="Heatmap", plugin_id="p1"))
        registry.register_cleaner(DataCleanerSpec(cleaner_id="p1.clean", label="Plugin Clean", plugin_id="p1"))
        registry.register_grid_action(GridActionSpec(action_id="p1.action", label="Plugin Action", plugin_id="p1"))
        registry.register_palette("plugin", ["#123456"])

        self.assertIn("heatmap", registry.chart_types())
        self.assertIn("p1.clean", registry.cleaners())
        self.assertIn("p1.action", registry.grid_actions())
        self.assertEqual(registry.palette("plugin"), ["#123456"])

        registry.remove_plugin("p1")
        self.assertNotIn("heatmap", registry.chart_types())
        self.assertNotIn("p1.clean", registry.cleaners())
        self.assertNotIn("p1.action", registry.grid_actions())

    def test_word_geometry_selection_reconstructs_table_rows(self):
        service = DataDockService(provider_registry=DataProviderRegistry())
        payload = {
            "text": "flattened fallback",
            "context": {
                "page_num": 0,
                "words": [
                    [10, 10, 30, 20, "Date"],
                    [90, 10, 120, 20, "AAPL"],
                    [150, 10, 190, 20, "MSFT"],
                    [10, 30, 45, 40, "Jan"],
                    [90, 30, 110, 40, "10"],
                    [150, 30, 175, 40, "20"],
                    [10, 50, 45, 60, "Feb"],
                    [90, 50, 110, 60, "11"],
                    [150, 50, 175, 60, "22"],
                ],
            },
        }

        state = service.dataset_from_selection(payload)

        self.assertEqual(state.headers, ["Date", "AAPL", "MSFT"])
        self.assertEqual(state.rows, [["Jan", "10", "20"], ["Feb", "11", "22"]])

    def test_extract_document_returns_tables_without_chart_inventory(self):
        service = DataDockService(provider_registry=DataProviderRegistry())

        states = service.extract_document(_FakeDoc(), "/tmp/stocks.pdf")

        self.assertEqual(len(states), 1)
        self.assertEqual(states[0].name, "P1: Date | AAPL")
        self.assertEqual(states[0].headers, ["Date", "AAPL"])
        self.assertEqual(states[0].rows, [["Jan", "10"], ["Feb", "11"]])

    def test_numeric_parser_handles_embedded_negative_and_trailing_period(self):
        service = DataDockService(provider_registry=DataProviderRegistry())

        self.assertEqual(service.coerce_number("Revenue: -12.5%."), -12.5)
        self.assertEqual(service.coerce_number("Loss (1,234.50)."), -1234.5)
        self.assertEqual(service.coerce_number("2024 result 7."), 7.0)

        summary = service.selection_summary(["down −4.5 points", "$2,000.", "none"])
        self.assertEqual(summary["numeric_count"], 2)
        self.assertEqual(summary["sum"], 1995.5)

    def test_extract_document_ignores_non_table_page_text(self):
        service = DataDockService(provider_registry=DataProviderRegistry())

        states = service.extract_document(_NoisyDoc(), "/tmp/noise.pdf")

        self.assertEqual(states, [])

    def test_workspace_and_ontology_registrations(self):
        workspace_registry = build_default_workspace_node_type_registry()
        ontology = OntologyRegistry()

        self.assertIsNotNone(workspace_registry.get("workspace.node.data"))
        self.assertIsNotNone(workspace_registry.get("workspace.node.chart"))
        self.assertEqual(ontology.get_entity_blueprint(EntityType.CHART.value).type_key, EntityType.CHART.value)
        self.assertTrue(
            ontology.validate_relation(
                RelationType.DATA_FLOW.value,
                EntityType.DATA_TABLE.value,
                EntityType.CHART.value,
            )
        )
        self.assertFalse(
            ontology.validate_relation(
                RelationType.DATA_FLOW.value,
                EntityType.CHART.value,
                EntityType.DATA_TABLE.value,
            )
        )


if __name__ == "__main__":
    unittest.main()
