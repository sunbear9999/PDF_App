import os
import uuid
import fitz
from PyQt6.QtCore import QThread

from core.ai_indexing_worker import AIIndexingWorker

class PreloadWorker(QThread):
    def __init__(self, llm_manager, model, parent=None):
        super().__init__(parent)
        self.llm_manager = llm_manager
        self.model = model

    def run(self):
        self.llm_manager.preload_model(self.model)


class MainWindowAI:
    """AI-related operations for MainWindow."""

    def __init__(self, main_window):
        self.main_window = main_window

    @property
    def w(self):
        return self.main_window

    def trigger_background_preload(self):
        try:
            default_model = self.w.dock_widgets["LLM Chat"].model_combo.currentText()
            llm_manager = self.w.dock_widgets["LLM Chat"].llm_manager
            self.w.preload_worker = PreloadWorker(llm_manager, default_model, parent=self.w)
            self.w.preload_worker.start()
        except Exception as e:
            print(f"Could not trigger preload: {e}")

    def show_indexing_status(self, message):
        if not message:
            return
        if self.w.indexing_in_progress:
            self.w.indexing_status_label.setText(message)
            self.w.indexing_status_label.setVisible(True)
        self.w.status_bar.showMessage(message, 0)

    def start_background_indexing(self, pdf_paths=None):
        if getattr(self.w, 'ai_indexing_worker', None) and self.w.ai_indexing_worker.isRunning():
            return

        if not self.w.pdf_controller.project_filepath:
            return

        queue = pdf_paths if pdf_paths else self.w.pdf_controller.get_unmapped_pdfs()
        if not queue:
            self.show_indexing_status("✅ No PDFs selected for GraphRAG indexing.")
            return

        self.w.indexing_in_progress = True
        self.w.indexing_status_label.setVisible(True)
        model_name = self.w.dock_widgets["LLM Chat"].model_combo.currentText()
        self.w.ai_indexing_worker = AIIndexingWorker(
            self.w.dock_widgets["LLM Chat"].llm_manager,
            model_name,
            self.w.pdf_controller.project_filepath,
            pdf_paths=queue,
            parent=self.w,
        )
        self.w.ai_indexing_worker.progress.connect(self.show_indexing_status)
        self.w.ai_indexing_worker.pdf_mapped.connect(lambda path: self.show_indexing_status(f"Mapped: {os.path.basename(path)}"))
        self.w.ai_indexing_worker.finished_all.connect(self._on_indexing_finished)

        if hasattr(self.w, 'workspace_view'):
            self.w.workspace_view.lock_ai_tools()

        self.show_indexing_status("⏳ Background AI indexing started...")
        if "LLM Chat" in self.w.dock_widgets and hasattr(self.w.dock_widgets["LLM Chat"], 'lock_llm_tools'):
            self.w.dock_widgets["LLM Chat"].lock_llm_tools()
        self.w.ai_indexing_worker.start()

    def _on_indexing_finished(self, success, msg):
        if success:
            self.show_indexing_status("✅ Background AI indexing complete.")
        else:
            self.show_indexing_status(f"❌ Background AI indexing failed: {msg}")
        self.w.indexing_in_progress = False
        self.w.indexing_status_label.setVisible(False)
        self.w._set_argument_map_button_state(running=False)
        self.w._check_needs_argument_map()

        if hasattr(self.w, 'workspace_view'):
            self.w.workspace_view.unlock_ai_tools()
        if "LLM Chat" in self.w.dock_widgets and hasattr(self.w.dock_widgets["LLM Chat"], 'unlock_llm_tools'):
            self.w.dock_widgets["LLM Chat"].unlock_llm_tools()

    def add_ai_annotation(self, quote, note, target_doc_name=None, allowed_paths=None, forced_annot_id=None, emit_signal=True):
        if not quote:
            return False
        clean_quote = quote.strip()
        words = clean_quote.split()
        if not words:
            return False
        chunks = []
        if len(words) <= 6:
            chunks = [" ".join(words)]
        else:
            for i in range(0, len(words), 4):
                chunk = " ".join(words[i:i+6])
                if chunk.strip():
                    chunks.append(chunk)
        search_paths = allowed_paths if allowed_paths else self.w.pdf_controller.get_pdf_paths()
        if target_doc_name:
            filtered_paths = []
            for p in search_paths:
                if target_doc_name.lower().strip() in os.path.basename(p).lower():
                    filtered_paths.append(p)
            if filtered_paths:
                search_paths = filtered_paths
        found_any = False
        for path in search_paths:
            try:
                doc = self.w.pdf_controller.get_doc(path)
                if not doc:
                    continue
                for page_num in range(len(doc)):
                    page = doc.load_page(page_num)
                    rects = page.search_for(clean_quote)
                    if not rects and len(chunks) > 1:
                        rects = []
                        for chunk in chunks:
                            res = page.search_for(chunk)
                            if res:
                                rects.extend(res)
                    if rects:
                        quads = [r.quad for r in rects]
                        annot = page.add_highlight_annot(quads)
                        annot.set_colors(stroke=(0.7, 0.4, 1.0))
                        annot_id_to_use = forced_annot_id if forced_annot_id else f"AINote|{uuid.uuid4()}"
                        annot_info = {
                            "title": annot_id_to_use,
                            "content": note,
                            "subject": clean_quote,
                        }
                        annot.set_info(info=annot_info)
                        annot.update()
                        found_any = True
                        self.w.pdf_controller.mark_dirty(path)
                        if path == self.w.current_file_path:
                            self.w.viewer.reload_page(page_num)
                        break
                if found_any and forced_annot_id:
                    break
            except Exception as e:
                print(f"Error adding AI annotation to {path}: {e}")
        if found_any and emit_signal:
            self.w.viewer.annot_manager.note_added.emit()
        return found_any