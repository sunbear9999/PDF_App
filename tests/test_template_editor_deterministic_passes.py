"""Headless (offscreen) tests for the Analysis Modes Builder's deterministic
pre-filter pass configuration UI — checking a pass reveals a parameter form,
values round-trip through save/reload, and the widget degrades gracefully
when no registry is supplied.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from core.registries.deterministic_pass_registry import DeterministicPassRegistry, DeterministicPassSpec
from gui.docks.unified_research.components.template_editor import TemplateEditorDialog

APP = QApplication.instance() or QApplication([])


def _make_registry():
    registry = DeterministicPassRegistry()
    registry.register(DeterministicPassSpec(
        pass_id="quote_candidate_rank",
        pass_name="Quote Candidates",
        description="Ranks by quote-candidate score.",
        run_fn=lambda pass_input: None,
        param_schema={
            "top_n": {"type": "integer", "label": "Max blocks to keep", "default": 25, "min": 1, "max": 200},
        },
    ))
    registry.register(DeterministicPassSpec(
        pass_id="date_dense_rank",
        pass_name="Date-Dense Blocks",
        description="Ranks by date density.",
        run_fn=lambda pass_input: None,
        param_schema={
            "top_n": {"type": "integer", "label": "Max blocks to keep", "default": 10, "min": 1, "max": 100},
        },
    ))
    return registry


def _make_project_manager():
    pm = MagicMock()
    pm.get_analysis_templates.return_value = []
    return pm


class TestTemplateEditorDeterministicPasses(unittest.TestCase):
    def test_checking_a_pass_reveals_its_param_form(self):
        dlg = TemplateEditorDialog(_make_project_manager(), theme=None, parent=None, deterministic_pass_registry=_make_registry())
        self.assertEqual(dlg.det_pass_list.count(), 2)
        self.assertEqual(len(dlg._det_pass_forms), 0)

        item = dlg.det_pass_list.item(0)
        item.setCheckState(Qt.CheckState.Checked)  # triggers itemChanged -> _rebuild_det_pass_forms

        self.assertEqual(len(dlg._det_pass_forms), 1)
        self.assertIn("quote_candidate_rank", dlg._det_pass_forms)

    @patch("gui.docks.unified_research.components.template_editor.QMessageBox.information")
    def test_save_and_reload_round_trips_per_pass_params(self, _mock_info):
        registry = _make_registry()
        pm = _make_project_manager()
        dlg = TemplateEditorDialog(pm, theme=None, parent=None, deterministic_pass_registry=registry)

        dlg.title_input.setText("My Custom Mode")
        item = dlg.det_pass_list.item(0)
        item.setCheckState(Qt.CheckState.Checked)
        form = dlg._det_pass_forms["quote_candidate_rank"]
        form.set_values({"top_n": 42})

        dlg._save_current()

        saved_template = next(t for t in dlg.templates if t["title"] == "My Custom Mode")
        passes = saved_template["deterministic_passes"]
        self.assertEqual(len(passes), 1)
        self.assertEqual(passes[0]["pass_id"], "quote_candidate_rank")
        self.assertEqual(passes[0]["params"]["top_n"], 42)

        # Reload into a fresh dialog and confirm the checkbox + value restore.
        dlg2 = TemplateEditorDialog(pm, theme=None, parent=None, deterministic_pass_registry=registry)
        dlg2.templates = dlg.templates
        dlg2._load_template_list()
        dlg2._on_template_selected(dlg2.list_widget.item(0))

        checked_item = dlg2.det_pass_list.item(0)
        self.assertEqual(checked_item.checkState(), Qt.CheckState.Checked)
        self.assertEqual(dlg2._det_pass_forms["quote_candidate_rank"].get_values()["top_n"], 42)

    @patch("gui.docks.unified_research.components.template_editor.QMessageBox.information")
    def test_unchecking_a_pass_removes_it_from_saved_template(self, _mock_info):
        registry = _make_registry()
        pm = _make_project_manager()
        dlg = TemplateEditorDialog(pm, theme=None, parent=None, deterministic_pass_registry=registry)
        dlg.title_input.setText("No Passes Mode")
        # default from _clear_editor() checks quote_candidate_rank — uncheck it
        item = dlg.det_pass_list.item(0)
        item.setCheckState(Qt.CheckState.Unchecked)

        dlg._save_current()
        saved_template = next(t for t in dlg.templates if t["title"] == "No Passes Mode")
        self.assertEqual(saved_template["deterministic_passes"], [])

    @patch("gui.docks.unified_research.components.template_editor.QMessageBox.information")
    def test_gracefully_hides_when_no_registry_supplied(self, _mock_info):
        dlg = TemplateEditorDialog(_make_project_manager(), theme=None, parent=None, deterministic_pass_registry=None)
        self.assertFalse(dlg.det_pass_box.isVisible())
        self.assertEqual(dlg.det_pass_list.count(), 0)
        # save must not crash even with no registry
        dlg.title_input.setText("No Registry Mode")
        dlg._save_current()
        saved_template = next(t for t in dlg.templates if t["title"] == "No Registry Mode")
        self.assertEqual(saved_template["deterministic_passes"], [])


if __name__ == "__main__":
    unittest.main()
