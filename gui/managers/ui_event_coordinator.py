# gui/managers/ui_event_coordinator.py
"""
Centralises all bus → GUI signal wiring that was previously scattered across
MainWindow.__init__.  Instantiated once by MainWindow after all services and
docks are ready.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject

from core.events.domains.document_events import (
    DocumentEvent, DocumentEventPayload,
    DocumentIntent,
    AnnotationIntent, AnnotationPayload,
)
from core.events.domains.project_events import ProjectEvent, ProjectEventPayload, ProjectIntent
from core.events.domains.workspace_events import WorkspaceEvent, WorkspaceEventPayload

if TYPE_CHECKING:
    from gui.main_window import MainWindow
    from gui.app_context import AppContext


class UIEventCoordinator(QObject):
    """
    Wires EventBus signals to the GUI layer.

    Every connection here replaces a direct bus.X.connect() call that was
    previously in MainWindow.__init__.  Keeping them here makes MainWindow
    leaner and makes the event→handler mapping easy to find and audit.
    """

    def __init__(self, main_window: "MainWindow", app_context: "AppContext", parent=None) -> None:
        super().__init__(parent)
        self._w = main_window
        self._ctx = app_context
        bus = app_context.bus
        self._bus = bus

        # Project lifecycle
        bus.project_loaded.connect(self._on_project_loaded)
        bus.project_clearing_started.connect(self._on_project_clearing)
        bus.project_saved.connect(self._on_project_saved)
        bus.project_action_requested.connect(self._on_project_action)

        # Document lifecycle
        bus.pdf_switched.connect(self._on_pdf_switched)
        bus.document_action_requested.connect(self._on_document_action)

        # Annotation passthrough to viewer
        bus.annotation_action_requested.connect(self._on_annotation_action)

        # Workspace state
        bus.workspace_state_restored.connect(self._on_workspace_state_restored)

    # ----------------------------------------------------------------
    # Project
    # ----------------------------------------------------------------

    def _on_project_loaded(self, event, payload: ProjectEventPayload) -> None:
        if event != ProjectEvent.LOADED:
            return
        w = self._w
        # Refresh document explorer
        if hasattr(w, "doc_explorer") and hasattr(w.doc_explorer, "refresh_list"):
            try:
                w.doc_explorer.refresh_list()
            except RuntimeError:
                pass
        # Restore last workspace layout
        if hasattr(w, "layout_manager"):
            try:
                w.layout_manager.restore_last_session()
            except RuntimeError:
                pass

    def _on_project_clearing(self, event, payload) -> None:
        w = self._w
        # Clear the PDF viewer
        viewer = getattr(w, "viewer", None)
        if viewer and hasattr(viewer, "clear"):
            try:
                viewer.clear()
            except RuntimeError:
                pass
        w.current_file_path = None
        # Refresh doc explorer
        if hasattr(w, "doc_explorer") and hasattr(w.doc_explorer, "refresh_list"):
            try:
                w.doc_explorer.refresh_list()
            except RuntimeError:
                pass

    def _on_project_saved(self, event, payload) -> None:
        if hasattr(self._w, "_show_save_indicator"):
            try:
                self._w._show_save_indicator()
            except RuntimeError:
                pass

    def _on_project_action(self, intent, payload) -> None:
        if intent == ProjectIntent.FLUSH_UI_STATES:
            # Ask all registered research docks to persist their state
            dm = getattr(self._w, "dock_manager", None)
            if dm:
                for widget in dm.get_inner_widgets("research"):
                    if hasattr(widget, "save_state"):
                        try:
                            widget.save_state()
                        except RuntimeError:
                            pass

    # ----------------------------------------------------------------
    # Document
    # ----------------------------------------------------------------

    def _on_pdf_switched(self, event, payload: DocumentEventPayload) -> None:
        if event != DocumentEvent.PDF_SWITCHED:
            return
        path = getattr(payload, "path", None)
        if path is not None:
            self._w.current_file_path = path

    def _on_document_action(self, intent, payload) -> None:
        if intent == DocumentIntent.SHOW_OCR_BANNER:
            w = self._w
            if hasattr(w, "ocr_banner"):
                try:
                    w.ocr_banner.show()
                except RuntimeError:
                    pass

    # ----------------------------------------------------------------
    # Annotations
    # ----------------------------------------------------------------

    def _on_annotation_action(self, intent: AnnotationIntent, payload: AnnotationPayload) -> None:
        viewer = getattr(self._w, "viewer", None)
        if not viewer:
            return
        annot_mgr = getattr(viewer, "annot_manager", None)
        if not annot_mgr:
            return
        try:
            if intent == AnnotationIntent.EDIT_POPUP:
                if hasattr(annot_mgr, "open_edit_popup"):
                    annot_mgr.open_edit_popup(payload.target_annot)
            elif intent == AnnotationIntent.FORCE_REDRAW:
                if hasattr(annot_mgr, "redraw"):
                    annot_mgr.redraw()
            elif intent == AnnotationIntent.JUMP_TO_PAGE:
                if hasattr(viewer, "go_to_page") and payload.page_num is not None:
                    viewer.go_to_page(payload.page_num)
        except RuntimeError:
            pass

    # ----------------------------------------------------------------
    # Workspace
    # ----------------------------------------------------------------

    def _on_workspace_state_restored(self, event, payload: WorkspaceEventPayload) -> None:
        pass  # WorkspaceView handles its own state via bus subscription
