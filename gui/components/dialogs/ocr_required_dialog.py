"""Prompt shown when intelligence is blocked because a document needs OCR.

The parse stage checks every PDF's text layer before any intelligence runs;
when the check fails the stage is *blocked* with reason ``needs_ocr`` and this
dialog offers the fix in one click: install the OCR engine if it is missing,
then OCR the file. Processing resumes on its own once OCR rewrites the file.
"""
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout,
)

from core.events.domains.help_events import HelpIntent, HelpPayload
from core.events.domains.intelligence_events import IntelligenceEvent, ProcessingStatePayload
from core.events.domains.tool_events import OCRIntent, OCRPayload, OCRStatus
from gui.components.artifact_actions import bind_artifact_action
from gui.components.model_dependency_tasks import ModelDependencyWorker

OCR_DEPENDENCY_ID = "tesseract_ocr"
HELP_TOPIC = "core.ocr.required"
ACTION_MOUNT = "toolbar:ocr:required"
MAX_ERROR_CHARS = 400
FALLBACK_THEME = {
    "bg_main": "#1e1e1e", "bg_input": "#252525", "text_main": "#ffffff",
    "text_muted": "#aaaaaa", "border": "#444444", "accent": "#b366ff",
}

TITLE = "OCR needed before intelligence"
EXPLANATION = (
    "Intelligence (entities, dates, citations, arguments, search) reads a document's text layer, "
    "so it has been paused for this document. OCR adds an invisible, searchable text layer; the "
    "page images stay exactly as they are. Processing resumes automatically when OCR finishes."
)
IMAGE_EXPLANATION = (
    "Images are read with the Tesseract OCR engine. Install it and this source is processed again."
)
ENGINE_MISSING = "The OCR engine (Tesseract) is not installed yet. Install it first — your system will ask for permission."
ENGINE_READY = "OCR engine ready."
RUN_REPLACE_TIP = "Adds the text layer to this file in place. The original page images are kept."
RUN_COPY_TIP = "Writes a searchable copy next to the original (…_ocr.pdf) and adds it to the project."


class OcrRequiredDialog(QDialog):
    def __init__(self, app_context, details, parent=None):
        super().__init__(parent)
        self.ctx = app_context
        self.details = dict(details or {})
        self.path = str(self.details.get("path") or "")
        self.source_id = str(self.details.get("source_id") or "")
        self.image_only = self.details.get("reason") == "needs_ocr_engine"
        self._installer = None
        self._running = False
        self._connections = []
        self.setWindowTitle(TITLE)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._build()
        self._bind_actions()
        self._register_help()
        bus = getattr(app_context, "bus", None)
        if bus is not None:
            self._connect(bus.ocr_status_updated, self._on_ocr_status)
            self._connect(bus.theme_changed, lambda _event, theme: self.apply_theme(theme))
        manager = getattr(app_context, "theme_manager", None)
        self.apply_theme(manager.get_theme() if manager else FALLBACK_THEME)
        self._refresh_engine()

    def apply_theme(self, theme):
        t = {**FALLBACK_THEME, **(theme or {})}
        self.setStyleSheet(
            f"QDialog {{ background-color: {t['bg_main']}; color: {t['text_main']}; }}"
            f"QLabel {{ color: {t['text_main']}; background: transparent; }}"
            f"QProgressBar {{ background: {t['bg_input']}; border: 1px solid {t['border']}; "
            f"border-radius: 4px; color: {t['text_main']}; text-align: center; }}"
            f"QProgressBar::chunk {{ background: {t['accent']}; border-radius: 3px; }}"
            f"QPushButton:default {{ background-color: {t['accent']}; color: #ffffff; }}"
            f"QPushButton:disabled {{ color: {t['text_muted']}; }}"
        )

    # ------------------------------------------------------------------ UI

    def _build(self):
        layout = QVBoxLayout(self)
        name = os.path.basename(self.path) or "This document"
        headline = QLabel(f"“{name}” has no usable text yet.", self)
        headline.setStyleSheet("font-weight: 700;")
        layout.addWidget(headline)
        for text in (self.details.get("summary") or "", IMAGE_EXPLANATION if self.image_only else EXPLANATION):
            if text:
                layout.addWidget(self._wrapped(text))
        self.engine_label = self._wrapped("")
        layout.addWidget(self.engine_label)
        self.progress = QProgressBar(self)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)
        self.status = self._wrapped("")
        layout.addWidget(self.status)
        bar = QHBoxLayout()
        self.install_button = self._button(bar, "Install OCR engine", self.install_engine)
        self.replace_button = self._button(bar, "Run OCR now", self.run_replace, RUN_REPLACE_TIP)
        self.copy_button = self._button(bar, "OCR to a new copy", self.run_copy, RUN_COPY_TIP)
        self.cancel_button = self._button(bar, "Cancel OCR", self.cancel_ocr)
        bar.addStretch()
        self._button(bar, "Help", self.show_help)
        self.close_button = self._button(bar, "Not now", self.close)
        layout.addLayout(bar)
        self.replace_button.setDefault(True)
        self.cancel_button.setVisible(False)
        self.replace_button.setVisible(not self.image_only)
        self.copy_button.setVisible(not self.image_only)

    def _wrapped(self, text):
        label = QLabel(text, self)
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.PlainText)
        return label

    def _button(self, bar, label, callback, tooltip=""):
        button = QPushButton(label, self)
        button.setToolTip(tooltip)
        button.clicked.connect(callback)
        bar.addWidget(button)
        return button

    def _bind_actions(self):
        for name, label in (("run_replace", "Run OCR and Update File"), ("run_copy", "Run OCR to a New Copy"),
                            ("install_engine", "Install OCR Engine"), ("cancel_ocr", "Cancel OCR"),
                            ("show_help", "OCR Requirement Help")):
            identity = "ocr.required." + ("cancel" if name == "cancel_ocr" else
                                          "help" if name == "show_help" else name)
            bind_artifact_action(self.ctx, self, identity, label, getattr(self, name), ACTION_MOUNT)

    def _register_help(self):
        registry = getattr(getattr(self.ctx, "help_service", None), "ui_target_registry", None)
        if registry:
            identity = f"ocr.required.{id(self)}"
            registry.register_widget(identity, self, HELP_TOPIC)
            self.destroyed.connect(lambda *_, r=registry, key=identity: r.unregister(key))

    def _connect(self, signal, slot):
        signal.connect(slot, Qt.ConnectionType.QueuedConnection)
        self._connections.append((signal, slot))

    # --------------------------------------------------------------- engine

    def _engine_ready(self) -> bool:
        service = getattr(self.ctx, "model_dependency_service", None)
        return bool(service and service.status(OCR_DEPENDENCY_ID) == "ready")

    def _engine_installable(self) -> bool:
        service = getattr(self.ctx, "model_dependency_service", None)
        if not service:
            return False
        return any(dep.get("id") == OCR_DEPENDENCY_ID and dep.get("installable")
                   for dep in service.list_dependencies(probe=False))

    def _refresh_engine(self):
        ready = self._engine_ready()
        self.engine_label.setText(ENGINE_READY if ready else (
            ENGINE_MISSING if self._engine_installable()
            else str(self.details.get("ocr_engine_error") or ENGINE_MISSING)))
        busy = self._running or self._installer is not None
        self.install_button.setVisible(not ready)
        self.install_button.setEnabled(not busy and self._engine_installable())
        for button in (self.replace_button, self.copy_button):
            button.setEnabled(ready and not busy and bool(self.path))

    def install_engine(self):
        service = getattr(self.ctx, "model_dependency_service", None)
        if not service or self._installer is not None:
            return
        self._installer = ModelDependencyWorker(service, OCR_DEPENDENCY_ID, QApplication.instance(), action="install")
        self._installer.done.connect(self._on_installed, Qt.ConnectionType.QueuedConnection)
        self._installer.progress.connect(self._on_install_progress, Qt.ConnectionType.QueuedConnection)
        self.status.setText("Installing the OCR engine… approve the system permission prompt.")
        self._installer.start()
        self._refresh_engine()

    def _on_install_progress(self, _identity, _current, _total, message):
        self.status.setText(message)

    def _on_installed(self, identity, ok, error):
        self._installer = None
        bus = getattr(self.ctx, "bus", None)
        if bus is not None:
            bus.intelligence_state_changed.emit(
                IntelligenceEvent.MODEL_DEPENDENCY_CHANGED, ProcessingStatePayload(dependency_id=identity))
        self.status.setText("OCR engine installed." if ok else (error or "Installation failed.")[-MAX_ERROR_CHARS:])
        if ok and self.image_only:
            self._resume_image_source()
        self._refresh_engine()

    def _resume_image_source(self):
        service = getattr(self.ctx, "source_intelligence_service", None)
        if service and self.source_id:
            service.run_missing(self.source_id)
            self.status.setText("OCR engine installed. Processing this image again.")

    # ------------------------------------------------------------------ OCR

    def run_replace(self):
        self._start_ocr("replace")

    def run_copy(self):
        self._start_ocr("save_new")

    def _start_ocr(self, mode):
        if self._running or not self.path or not self._engine_ready():
            return
        self._running = True
        self.progress.setRange(0, 0)
        self.progress.setVisible(True)
        self.cancel_button.setVisible(True)
        self.status.setText("Starting OCR…")
        self._refresh_engine()
        self.ctx.bus.ocr_action_requested.emit(
            OCRIntent.RUN, OCRPayload(file_path=self.path, mode=mode, source_id=self.source_id or None))

    def cancel_ocr(self):
        if self._running:
            self.ctx.bus.ocr_action_requested.emit(OCRIntent.CANCEL, OCRPayload(file_path=self.path))
            self.status.setText("Cancelling OCR…")

    def _on_ocr_status(self, status, payload):
        if getattr(payload, "file_path", None) != self.path or not self._running:
            return
        if status == OCRStatus.RUNNING:
            total = int(getattr(payload, "total", 0) or 0)
            self.progress.setRange(0, total)
            self.progress.setValue(int(getattr(payload, "progress", 0) or 0))
            self.status.setText(getattr(payload, "msg", "") or "Running OCR…")
            return
        self._running = False
        self.progress.setVisible(False)
        self.cancel_button.setVisible(False)
        message = str(getattr(payload, "msg", "") or "")[-MAX_ERROR_CHARS:]
        if status == OCRStatus.COMPLETE:
            resumed = ("Intelligence processing has restarted for this document."
                       if getattr(payload, "mode", "") == "replace"
                       else "The new copy was added to the project and will be processed.")
            self.status.setText(f"{message} {resumed}")
            self.replace_button.setVisible(False)
            self.copy_button.setVisible(False)
            self.close_button.setText("Close")
            return
        self.status.setText(message or "OCR stopped.")
        self._refresh_engine()

    def show_help(self):
        self.ctx.bus.help_action_requested.emit(HelpIntent.SHOW_TOPIC, HelpPayload(topic_id=HELP_TOPIC))

    def closeEvent(self, event):
        # OCR keeps running in the background service; only the installer
        # (bound to this prompt) is cancelled.
        if self._installer is not None:
            self._installer.cancel()
        for signal, slot in self._connections:
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self._connections = []
        super().closeEvent(event)
