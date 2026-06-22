"""
gui/components/dialogs/metadata_request_dialog.py

Shown when the source evaluator cannot automatically find a DOI or journal
name for a PDF. The user can supply the missing values, or skip them.

Emits source_eval_state_changed(METADATA_PROVIDED) on close so the backend
evaluation pipeline can resume without any terminal I/O.
"""
from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
    QVBoxLayout, QFrame,
)

from core.events.event_bus import EventBus
from core.events.domains.evaluation_events import (
    SourceEvalEvent,
    SourceEvalEventPayload,
)


class MetadataRequestDialog(QDialog):
    """
    Prompts the user for a missing DOI and/or journal name before
    continuing the source evaluation pipeline.
    """

    def __init__(
        self,
        pdf_path: str,
        correlation_id: str,
        missing_fields: list[str],
        prefill_doi: Optional[str] = None,
        prefill_journal: Optional[str] = None,
        theme: Optional[dict] = None,
        parent=None,
    ) -> None:
        super().__init__(parent, Qt.WindowType.Dialog)
        self._pdf_path = pdf_path
        self._correlation_id = correlation_id
        self._missing = set(missing_fields)
        self._theme = theme or {}
        self._bus: EventBus = EventBus.get_instance()

        self.setWindowTitle("Source Evaluator — Metadata Needed")
        self.setMinimumWidth(460)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        self._build_ui(prefill_doi, prefill_journal)
        self._apply_theme()
        self.finished.connect(self._on_finished)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self, prefill_doi, prefill_journal) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 16)
        root.setSpacing(12)

        # Header
        filename = os.path.basename(self._pdf_path)
        header = QLabel(f"<b>Metadata needed for:</b><br>{filename}")
        header.setWordWrap(True)
        root.addWidget(header)

        # Info banner
        banner = QLabel(
            "The evaluator could not automatically find the following fields.\n"
            "Providing them improves score accuracy, but you can skip either one."
        )
        banner.setWordWrap(True)
        banner.setObjectName("InfoBanner")
        root.addWidget(banner)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        root.addWidget(sep)

        # Form
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(10)

        self._doi_edit: Optional[QLineEdit] = None
        self._journal_edit: Optional[QLineEdit] = None

        if "doi" in self._missing:
            self._doi_edit = QLineEdit(prefill_doi or "")
            self._doi_edit.setPlaceholderText("e.g. 10.1038/s41586-020-2649-2")
            form.addRow("DOI:", self._doi_edit)

        if "journal" in self._missing:
            self._journal_edit = QLineEdit(prefill_journal or "")
            self._journal_edit.setPlaceholderText("e.g. Nature, PLOS ONE")
            form.addRow("Journal Name:", self._journal_edit)

        root.addLayout(form)

        # Disclaimer
        disclaimer = QLabel(
            "<i>Note: supplied values are used for this evaluation only and "
            "are not stored permanently in the document record.</i>"
        )
        disclaimer.setWordWrap(True)
        disclaimer.setObjectName("DisclaimerLabel")
        root.addWidget(disclaimer)

        # Buttons
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    def _apply_theme(self) -> None:
        t = self._theme
        if not t:
            return
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {t.get('bg_panel', '#2b2b2b')};
                color: {t.get('text_main', '#ffffff')};
            }}
            QLabel {{
                color: {t.get('text_main', '#ffffff')};
            }}
            QLabel#InfoBanner {{
                background-color: {t.get('bg_input', '#3a3a3a')};
                border: 1px solid {t.get('border', '#555')};
                border-radius: 6px;
                padding: 8px;
            }}
            QLabel#DisclaimerLabel {{
                color: {t.get('text_secondary', '#aaaaaa')};
                font-size: 11px;
            }}
            QLineEdit {{
                background-color: {t.get('bg_input', '#3a3a3a')};
                color: {t.get('text_main', '#ffffff')};
                border: 1px solid {t.get('border', '#555555')};
                border-radius: 4px;
                padding: 6px;
            }}
            QPushButton {{
                background-color: {t.get('accent', '#4a90d9')};
                color: #ffffff;
                border: none;
                padding: 6px 16px;
                border-radius: 4px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {t.get('accent_hover', '#357abd')};
            }}
        """)

    # ------------------------------------------------------------------
    # Emit result back to backend
    # ------------------------------------------------------------------

    def _on_finished(self, result: int) -> None:
        doi = (self._doi_edit.text().strip() if self._doi_edit else None) or None
        journal = (self._journal_edit.text().strip() if self._journal_edit else None) or None
        self._bus.source_eval_state_changed.emit(
            SourceEvalEvent.METADATA_PROVIDED,
            SourceEvalEventPayload(
                pdf_path=self._pdf_path,
                correlation_id=self._correlation_id,
                doi=doi,
                journal=journal,
            ),
        )
