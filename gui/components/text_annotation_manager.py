"""
gui/components/text_annotation_manager.py

Highlight/note creation for reflowable text formats (docx, epub, txt, md,
html, rtf, doc). Mirrors the subset of AnnotationManager's public surface
that generic call sites actually use (chat_streamer.py's
`annot_manager.trigger_similar_context`) plus a context-menu-driven
highlight action.

Selections use raw block coordinates mapped from the displayed Qt document.
The shared modeless editor verifies the extraction/file snapshot before saving.
"""
from __future__ import annotations

from PySide6.QtWidgets import QMenu

from core.events.event_bus import EventBus


class TextAnnotationManager:
    def __init__(self, viewer, app_context=None):
        self._viewer = viewer
        self._ctx = app_context
        self.bus = EventBus.get_instance()

    def is_selecting(self) -> bool:
        cursor = self._viewer.browser.textCursor()
        return cursor.hasSelection()

    def selected_text(self) -> str:
        return self._viewer.browser.textCursor().selectedText()

    def show_context_menu(self, global_pos) -> None:
        cursor = self._viewer.browser.textCursor()
        if not cursor.hasSelection():
            return
        menu = QMenu(self._viewer)
        highlight_action = menu.addAction("Highlight Selection")
        note_action = menu.addAction("Add Note to Selection")
        search_action = menu.addAction("Find Similar Passages")
        chosen = menu.exec(global_pos)
        if chosen is highlight_action:
            self.create_highlight_from_selection()
        elif chosen is note_action:
            self.create_highlight_from_selection(prompt_note=True)
        elif chosen is search_action:
            self.trigger_similar_context(cursor.selectedText())

    def create_highlight_from_selection(self, prompt_note: bool = False) -> None:
        cursor = self._viewer.browser.textCursor()
        if not cursor.hasSelection():
            return
        selection = self._viewer.selected_source()
        if selection:
            from gui.components.dialogs.reference_note_dialog import open_selection_note
            open_selection_note(self._ctx, selection, parent=self._viewer)

    def trigger_similar_context(self, selected_text: str) -> None:
        """
        Dispatches the same "find similar passages" blueprint that
        AnnotationManager._dispatch_ai_blueprint("similar", ...) uses for
        PDFs — per rules.md 1.1, all LLM features go through a blueprint +
        WorkflowRunnerService, never a direct llm_manager call.
        """
        if not selected_text or not selected_text.strip():
            return
        from core.engine.default_blueprints import DefaultBlueprints
        from core.events.domains.workflow_events import WorkflowIntent, WorkflowPayload

        allowed_docs = []
        if self._ctx and getattr(self._ctx, "project_manager", None):
            from gui.utils.document_helpers import active_pdf_paths
            allowed_docs = active_pdf_paths(self._ctx.project_manager)

        blueprint = DefaultBlueprints.get_similar_context_blueprint(selected_text, allowed_docs)
        active_model = self._ctx.get_active_ai_model() if self._ctx else ""
        self.bus.workflow_action_requested.emit(
            WorkflowIntent.RUN_BLUEPRINT,
            WorkflowPayload(
                blueprint=blueprint,
                initial_state={"selected_model": active_model},
                job_name=blueprint.name,
            ),
        )
