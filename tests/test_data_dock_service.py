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
from core.models.data_dock_models import DataProvenance
from core.models.ontology_model import EntityType, RelationType
from core.ontology.registry import OntologyRegistry
from core.registries.data_provider_registry import DataProviderRegistry
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

        summary = service.selection_summary(["1", "$2", "bad", "3%"])
        self.assertEqual(summary["count"], 3)
        self.assertAlmostEqual(summary["sum"], 6.0)

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

    def test_extract_document_detects_tables_and_chart_regions(self):
        service = DataDockService(provider_registry=DataProviderRegistry())

        states = service.extract_document(_FakeDoc(), "/tmp/stocks.pdf")

        self.assertEqual(len(states), 2)
        self.assertEqual(states[0].name, "Page 1 Table 1")
        self.assertEqual(states[0].headers, ["Date", "AAPL"])
        self.assertEqual(states[0].rows, [["Jan", "10"], ["Feb", "11"]])
        self.assertEqual(states[1].headers, ["Page", "Detected Item", "x0", "y0", "x1", "y1"])

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
