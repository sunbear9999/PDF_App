import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pandas as pd
from PySide6.QtWidgets import QApplication, QFileDialog

from core.events.domains.document_events import DocumentIntent
from plugins.locallaws.gui.laws_dock import LocalLawsDock
from plugins.locallaws.law_manager import LocalLawManager


APP = QApplication.instance() or QApplication([])


class _Signal:
    def __init__(self):
        self.calls = []

    def emit(self, *args):
        self.calls.append(args)


class _Manager:
    row_to_record = LocalLawManager.row_to_record

    def __init__(self, download_dir):
        self.download_dir = str(download_dir)

    def get_installed_dbs(self):
        return [{"city": "Cody", "state": "WY", "size_mb": 1.0, "file_id": "Cody_WY"}]


def _frame():
    return pd.DataFrame([
        {"header": "### 3-2-6: SALES TO MINORS:", "content": "A licensee shall not sell alcohol to a minor."},
        {"header": "### 4-1-1: HEALTH LAWS:", "content": "The health officer may inspect premises."},
    ])


def _dock(tmp_path, monkeypatch):
    (tmp_path / "cody_wy.parquet").touch()
    monkeypatch.setattr(pd, "read_parquet", lambda _path: _frame())
    signal = _Signal()
    api = SimpleNamespace(
        notify=lambda *_args, **_kwargs: None,
        event_bus=SimpleNamespace(document_action_requested=signal),
    )
    context = SimpleNamespace(project_manager=SimpleNamespace(project_filepath="project.pdfproj"))
    dock = LocalLawsDock(api, _Manager(tmp_path), context)
    dock.db_selector.setCurrentIndex(1)
    return dock, signal


def test_locallaws_dock_uses_headers_searches_and_opens_exact_quote(tmp_path, monkeypatch):
    dock, _signal = _dock(tmp_path, monkeypatch)

    assert dock.law_list.item(0).text() == "3-2-6: SALES TO MINORS:"
    assert "N/A" not in dock.law_list.item(0).text()
    dock.search_input.setText("health inspect")
    assert dock.law_list.count() == 1
    assert "HEALTH LAWS" in dock.law_list.item(0).text()

    opened = dock.open_citation({
        "source_id": "Cody_WY",
        "quote": "shall not sell alcohol to a minor",
        "source_locator": {"section": "3-2-6"},
    })

    assert opened is True
    assert "SALES TO MINORS" in dock.law_list.currentItem().text()
    assert dock.text_viewer.extraSelections()


def test_exported_law_pdf_is_sent_through_project_ingestion(tmp_path, monkeypatch):
    dock, signal = _dock(tmp_path, monkeypatch)
    dock.law_list.setCurrentRow(0)
    output = tmp_path / "law.pdf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_args, **_kwargs: (str(output), "PDF"))

    dock._export_pdf()

    assert output.exists()
    intent, payload = signal.calls[-1]
    assert intent is DocumentIntent.ADD_FILES
    assert payload.paths == [str(output)]
