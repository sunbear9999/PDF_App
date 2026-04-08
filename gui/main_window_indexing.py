import os

from core.ai_indexing_worker import AIIndexingWorker


class MainWindowIndexing:
    """Background indexing orchestration for MainWindow."""

    def __init__(self, main_window):
        self.main_window = main_window

    @property
    def w(self):
        return self.main_window

    def show_indexing_status(self, message):
        w = self.w
        if not message:
            return

        if w.indexing_in_progress:
            w.indexing_status_label.setText(message)
            w.indexing_status_label.setVisible(True)
        w.status_bar.showMessage(message, 0)

    def start_background_indexing(self, pdf_paths=None):
        w = self.w

        if getattr(w, "ai_indexing_worker", None) and w.ai_indexing_worker.isRunning():
            return

        if not w.pdf_controller.project_filepath:
            return

        queue = pdf_paths if pdf_paths else w.pdf_controller.get_unmapped_pdfs()
        if not queue:
            self.show_indexing_status("✅ No PDFs selected for GraphRAG indexing.")
            return

        w.indexing_in_progress = True
        w.indexing_status_label.setVisible(True)

        model_name = w.dock_widgets["LLM Chat"].model_combo.currentText()
        w.ai_indexing_worker = AIIndexingWorker(
            w.dock_widgets["LLM Chat"].llm_manager,
            model_name,
            w.pdf_controller.project_filepath,
            pdf_paths=queue,
            parent=w,
        )
        w.ai_indexing_worker.progress.connect(self.show_indexing_status)
        w.ai_indexing_worker.pdf_mapped.connect(
            lambda path: self.show_indexing_status(
                f"Mapped: {os.path.basename(path)}"
            )
        )
        # Preserve original callback routing via MainWindow.
        # MainWindow._on_indexing_finished will delegate back to this helper.
        w.ai_indexing_worker.finished_all.connect(w._on_indexing_finished)

        if hasattr(w, "workspace_view"):
            w.workspace_view.lock_ai_tools()

        self.show_indexing_status("⏳ Background AI indexing started...")
        if "LLM Chat" in w.dock_widgets and hasattr(
            w.dock_widgets["LLM Chat"], "lock_llm_tools"
        ):
            w.dock_widgets["LLM Chat"].lock_llm_tools()

        w.ai_indexing_worker.start()

    def on_indexing_finished(self, success, msg):
        w = self.w

        if success:
            self.show_indexing_status("✅ Background AI indexing complete.")
        else:
            self.show_indexing_status(f"❌ Background AI indexing failed: {msg}")

        w.indexing_in_progress = False
        w.indexing_status_label.setVisible(False)
        w._set_argument_map_button_state(running=False)
        w._check_needs_argument_map()

        if hasattr(w, "workspace_view"):
            w.workspace_view.unlock_ai_tools()

        if "LLM Chat" in w.dock_widgets and hasattr(
            w.dock_widgets["LLM Chat"], "unlock_llm_tools"
        ):
            w.dock_widgets["LLM Chat"].unlock_llm_tools()

