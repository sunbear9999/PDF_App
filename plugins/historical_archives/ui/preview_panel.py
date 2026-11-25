"""Independently resizable scan, OCR, and metadata preview sections."""
from __future__ import annotations

import json

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .resizable_sections import PersistentSplitter


class ArchivePreviewPanel(QWidget):
    """Result preview with persisted sizes for each information section."""

    def __init__(self, config, open_callback, import_callback, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Ignored)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        self.splitter = PersistentSplitter(
            Qt.Orientation.Vertical,
            config,
            "dock_preview_section_sizes",
            [220, 180, 180],
        )
        self.splitter.setAccessibleName("Archive preview sections")
        self.splitter.addWidget(self._scan_section())
        self.splitter.addWidget(self._text_section("OCR / description", "ocr"))
        self.splitter.addWidget(self._text_section(
            "Metadata, rights, and available files", "metadata"
        ))
        for index in range(3):
            self.splitter.setStretchFactor(index, 1)
        self.splitter.restore_sizes()
        layout.addWidget(self.splitter, 1)
        self.action_status = QLabel(
            "Select a result to review it before importing."
        )
        self.action_status.setWordWrap(True)
        self.action_status.setProperty("muted", True)
        layout.addWidget(self.action_status)
        layout.addLayout(self._actions(open_callback, import_callback))

    def _scan_section(self):
        container = QWidget()
        container.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Ignored
        )
        layout = QVBoxLayout(container)
        layout.setContentsMargins(2, 2, 2, 2)
        self.title = QLabel("Select a result")
        self.title.setWordWrap(True)
        layout.addWidget(self.title)
        self.image = QLabel("No preview loaded")
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setMinimumHeight(60)
        self.image.setScaledContents(False)
        layout.addWidget(self.image, 1)
        return container

    def _text_section(self, heading_text, kind):
        container = QWidget()
        container.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Ignored
        )
        layout = QVBoxLayout(container)
        layout.setContentsMargins(2, 2, 2, 2)
        heading = QLabel(heading_text)
        heading.setObjectName("sectionHeading")
        layout.addWidget(heading)
        editor = QPlainTextEdit()
        editor.setReadOnly(True)
        if kind == "ocr":
            editor.setPlaceholderText("OCR snippet or description")
            editor.setAccessibleName("Archive OCR preview")
            self.ocr = editor
        else:
            editor.setAccessibleName("Archive metadata and rights")
            self.metadata = editor
        layout.addWidget(editor, 1)
        return container

    def _actions(self, open_callback, import_callback):
        actions = QHBoxLayout()
        self.open_button = QPushButton("Open Original")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(open_callback)
        actions.addWidget(self.open_button)
        self.import_button = QPushButton("Import to Project")
        self.import_button.setEnabled(False)
        self.import_button.clicked.connect(import_callback)
        actions.addWidget(self.import_button)
        return actions

    def set_import_status(self, text, state="ready"):
        self.action_status.setText(text)
        self.action_status.setProperty("state", state)
        self.action_status.style().unpolish(self.action_status)
        self.action_status.style().polish(self.action_status)

    def display_result(self, result):
        self.title.setText(f"{result.title}\n{result.date} · {result.publication}")
        self.ocr.setPlainText(result.ocr_snippet or result.description)
        self.metadata.setPlainText(json.dumps({
            "archive": result.provider_id,
            "collection": result.collection,
            "institution": result.institution,
            "places": result.places,
            "rights": result.rights,
            "license": result.license,
            "access_restrictions": result.access_restrictions,
            "assets": [asset.__dict__ for asset in result.assets],
            "match_reasons": result.match_reasons,
            "record_url": result.persistent_url,
        }, indent=2, ensure_ascii=False))

    def display_thumbnail(self, data):
        pixmap = QPixmap()
        if not pixmap.loadFromData(data):
            self.image.setText("Preview unavailable.")
            return
        self.image.setPixmap(pixmap.scaled(
            480, 360,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))
