# gui/main_window/__init__.py
import os
from PyQt6.QtWidgets import QMainWindow, QComboBox
from PyQt6.QtGui import QShortcut, QKeySequence
from PyQt6.QtCore import Qt, QSettings, QTimer

from gui.components.pdf_viewer import PDFViewer
from gui.components.workspace_view import WorkspaceView
from gui.theme import ThemeManager
from gui.components.help_dialog import HelpDialog
from services.thread_manager import ThreadManager
from .ui_builder import MainWindowUI
from .project_handler import MainWindowProjects
from .ai_handler import MainWindowAI


class MainWindow(QMainWindow):
    def __init__(self, workspace_ctrl, pdf_ctrl, ocr_ctrl, persistence_ctrl):
        super().__init__()
        self.setWindowTitle("PDF Workspace")
        self.resize(1400, 900)
        self.setMinimumSize(1000, 700)

        # Dependency injection
        self.workspace_controller = workspace_ctrl
        self.pdf_controller = pdf_ctrl
        self.ocr_controller = ocr_ctrl
        self.persistence_controller = persistence_ctrl

        # For backward compatibility
        self.project_manager = pdf_ctrl.pdf_service.project_manager

        self.theme_manager = ThemeManager()
        self.thread_manager = ThreadManager(self)
        self.ai_indexing_worker = None
        self.current_file_path = None
        self.settings = QSettings("PDFMultitool", "Workspace")

        # PDF selector for backward compatibility (used by workspace view)
        self.pdf_selector = QComboBox()
        self.pdf_selector.currentIndexChanged.connect(self._on_pdf_dropdown_changed)

        # Initialize UI components
        self.ui_builder = MainWindowUI(self)
        self.project_handler = MainWindowProjects(self)
        self.ai_handler = MainWindowAI(self)

        self.ui_builder.build_all()

        self.theme_manager.theme_changed.connect(self.ui_builder.update_theme)
        self.ui_builder.update_theme(self.theme_manager.get_theme())

        self.autosave_timer = QTimer(self)
        self.autosave_timer.timeout.connect(self.project_handler.autosave_project)
        self.autosave_timer.start(5 * 60 * 1000)

        last_project = self.settings.value("last_project", "")
        if last_project and os.path.exists(last_project):
            self.project_handler.load_project(last_project)

        QTimer.singleShot(1500, self.ai_handler.trigger_background_preload)
        if self.settings.value("show_help_on_startup", True, type=bool):
            QTimer.singleShot(500, self.show_help_window)

    def show_help_window(self):
        self.help_dialog = HelpDialog(self)
        self.help_dialog.show()

    def _on_theme_changed(self, theme_name):
        if theme_name == "Custom":
            self.theme_manager.edit_custom_theme(self)
        self.settings.setValue("theme", theme_name)
        self.theme_manager.set_theme(theme_name)

    def update_theme(self, theme):
        for dock in self.dock_widgets.values():
            if hasattr(dock, "update_theme"):
                dock.update_theme(theme)
        if hasattr(self.viewer, "update_theme"):
            self.viewer.update_theme(theme)

    def _clear_ui_for_new_project(self):
        self.current_file_path = None
        self.pdf_selector.blockSignals(True)
        self.pdf_selector.clear()
        self.pdf_selector.blockSignals(False)

        if hasattr(self.viewer, 'scene') and self.viewer.scene:
            self.viewer.scene.clear()
        if hasattr(self.viewer, 'doc'):
            self.viewer.doc = None

        if "Notes" in self.dock_widgets:
            notes_dock = self.dock_widgets["Notes"]
            for i in reversed(range(notes_dock.scroll_layout.count())):
                widget = notes_dock.scroll_layout.itemAt(i).widget()
                if widget:
                    widget.deleteLater()
            if hasattr(self, 'workspace_view'):
                self.workspace_view.scene_obj.clear()
                self.workspace_view.nodes.clear()
                self.workspace_view.edges.clear()

    def _on_annotation_clicked(self, annotation):
        # Placeholder hook for annotation click handling.
        # Keep the app stable if the viewer emits this signal.
        pass

    # Delegate methods for menu compatibility
    def _new_project(self):
        self.project_handler.new_project()

    def _open_project(self):
        self.project_handler.open_project()

    def _save_project_as(self):
        self.project_handler.save_project_as()

    def _load_project(self, path):
        self.project_handler.load_project(path)

    def _add_pdf(self):
        self.project_handler.add_pdf()

    def switch_to_pdf(self, pdf_path):
        self.project_handler.switch_to_pdf(pdf_path)

    def save_project(self):
        self.project_handler.save_project()

    def autosave_project(self):
        self.project_handler.autosave_project()

    def _on_theme_changed(self, theme_name):
        if theme_name == "Custom":
            self.theme_manager.edit_custom_theme(self)
        self.settings.setValue("theme", theme_name)
        self.theme_manager.set_theme(theme_name)

    def _sync_tools_with_file(self, file_path):
        dialog = QFileDialog(self, "Open Project")
        dialog.setNameFilter("PDF Project (*.pdfproj);;All Files (*)")
        if dialog.exec():
            path = dialog.selectedFiles()[0]
            self._load_project(path)

    def _save_project_as(self):
        if not self.project_manager.project_filepath:
            QMessageBox.warning(self, "No Project", "Create or open a project first.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save Project As", "", "PDF Project (*.pdfproj)")
        if path:
            if not path.lower().endswith(".pdfproj"):
                path += ".pdfproj"
            old_path = self.project_manager.project_filepath
            old_chroma_dir = old_path + "_chroma_db"
            new_chroma_dir = path + "_chroma_db"
            if "Notes" in self.dock_widgets and hasattr(self.dock_widgets["Notes"], "save_workspace_state"):
                self.dock_widgets["Notes"].save_workspace_state()
            self.pdf_controller.save_all_docs()
            if self.project_manager._conn:
                self.project_manager._conn.close()
                self.project_manager._conn = None
            try:
                shutil.copy2(old_path, path)
                if os.path.exists(old_chroma_dir):
                    if os.path.exists(new_chroma_dir):
                        shutil.rmtree(new_chroma_dir)
                    shutil.copytree(old_chroma_dir, new_chroma_dir)
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to copy project database: {e}")
                self.project_manager._init_db()
                return
            self.project_manager.project_filepath = path
            self.project_manager.project_name = os.path.basename(path).replace(".pdfproj", "")
            self.project_manager._init_db()
            cursor = self.project_manager._conn.cursor()
            cursor.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                           ("project_name", self.project_manager.project_name))
            self.project_manager._conn.commit()
            self.dock_widgets["LLM Chat"].refresh_project_ui()
            self.settings.setValue("last_project", path)
            self.setWindowTitle(f"PDF Workspace - {self.project_manager.project_name}")

    def _load_project(self, path):
        if self.project_manager.project_filepath:
            self.save_project()
        if self.project_manager.load_project(path):
            self._clear_ui_for_new_project()
            self.settings.setValue("last_project", self.project_manager.project_filepath)
            self.setWindowTitle(f"PDF Workspace - {self.project_manager.project_name}")
            self._refresh_pdf_dropdown()
            self.dock_widgets["LLM Chat"].refresh_project_ui()
            pdf_paths = self.pdf_controller.get_pdf_paths()
            if pdf_paths:
                self.switch_to_pdf(pdf_paths[0])
        else:
            QMessageBox.warning(self, "Error", "Failed to load project file.")

    def _add_pdf(self):
        if not self.project_manager.project_filepath:
            QMessageBox.warning(self, "No Project", "Please Create or Open a Project first.")
            return
        file_paths, _ = QFileDialog.getOpenFileNames(self, "Add PDFs to Project", "", "PDF Files (*.pdf)")
        if file_paths:
            added_paths = self.pdf_controller.add_pdfs(file_paths)
            self._refresh_pdf_dropdown()
            self.switch_to_pdf(added_paths[-1] if added_paths else file_paths[-1])

    def _refresh_pdf_dropdown(self):
        self.pdf_selector.blockSignals(True)
        self.pdf_selector.clear()
        for path in self.pdf_controller.get_pdf_paths():
            self.pdf_selector.addItem(os.path.basename(path), userData=path)
        self.pdf_selector.blockSignals(False)

    def _on_pdf_dropdown_changed(self, index):
        if index >= 0:
            pdf_path = self.pdf_selector.itemData(index)
            self.switch_to_pdf(pdf_path)

    def switch_to_pdf(self, pdf_path):
        if not os.path.exists(pdf_path):
            return
        idx = self.pdf_selector.findData(pdf_path)
        if idx >= 0 and self.pdf_selector.currentIndex() != idx:
            self.pdf_selector.blockSignals(True)
            self.pdf_selector.setCurrentIndex(idx)
            self.pdf_selector.blockSignals(False)
        if self.current_file_path == pdf_path and self.viewer.doc:
            return
        self.current_file_path = pdf_path
        self.pdf_controller.set_active_file(pdf_path)
        doc = self.pdf_controller.get_doc(pdf_path)
        if doc:
            success = self.viewer.load_document(doc)
            if success:
                self._check_needs_ocr()
                self._check_needs_argument_map()
                self._sync_tools_with_file(pdf_path)
            else:
                QMessageBox.warning(self, "Error", "Failed to load the PDF document.")
        else:
            QMessageBox.warning(self, "Error", "Failed to access the file from the filesystem.")

    def autosave_project(self):
        if self.project_manager.project_filepath:
            try:
                if "Notes" in self.dock_widgets and hasattr(self.dock_widgets["Notes"], "save_workspace_state"):
                    self.dock_widgets["Notes"].save_workspace_state()
                self.pdf_controller.save_all_docs()
            except Exception as e:
                print(f"Background autosave failed: {e}")

    def save_project(self):
        if not self.project_manager.project_filepath:
            return
        try:
            if "Notes" in self.dock_widgets and hasattr(self.dock_widgets["Notes"], "save_workspace_state"):
                self.dock_widgets["Notes"].save_workspace_state()
            self.pdf_controller.save_all_docs()
            QMessageBox.information(self, "Success", "Project and all highlights saved successfully!")
        except Exception as e:
            QMessageBox.warning(self, "Save Error", f"Error saving project: {str(e)}")

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
        search_paths = allowed_paths if allowed_paths else self.pdf_controller.get_pdf_paths()
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
                doc = self.pdf_controller.get_doc(path)
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
                        self.pdf_controller.mark_dirty(path)
                        if path == self.current_file_path:
                            self.viewer.reload_page(page_num)
                        break
                if found_any and forced_annot_id:
                    break
            except Exception as e:
                print(f"Error adding AI annotation to {path}: {e}")
        if found_any and emit_signal:
            self.viewer.annot_manager.note_added.emit()
        return found_any

    def _mark_current_dirty(self):
        if self.current_file_path:
            self.pdf_controller.mark_dirty(self.current_file_path)

    def _build_argument_map_banner(self):
        self.argument_map_banner = QFrame()
        self.argument_map_banner.setFixedHeight(50)
        banner_layout = QHBoxLayout(self.argument_map_banner)
        banner_layout.setContentsMargins(20, 0, 10, 0)
        self.lbl_argument_map_banner = QLabel(
            "🧠 This PDF has no argument map. Generate one now — it takes about a minute and greatly improves LLM results."
        )
        banner_layout.addWidget(self.lbl_argument_map_banner)
        banner_layout.addStretch()
        self.btn_generate_argument_map_banner = QPushButton("Generate Argument Map")
        self.btn_generate_argument_map_banner.setObjectName("ArgumentMapButton")
        self.btn_generate_argument_map_banner.clicked.connect(self._trigger_argument_map_generation)
        banner_layout.addWidget(self.btn_generate_argument_map_banner)
        btn_dismiss = QPushButton("Dismiss")
        btn_dismiss.setObjectName("ArgumentMapDismissButton")
        btn_dismiss.clicked.connect(self.argument_map_banner.hide)
        banner_layout.addWidget(btn_dismiss)
        self.argument_map_banner.hide()

    def _check_needs_ocr(self):
        if not self.viewer.doc:
            return
        try:
            pages_to_check = min(3, len(self.viewer.doc))
            total_text = "".join([self.viewer.doc.load_page(i).get_text() for i in range(pages_to_check)])
            if len(total_text.strip()) < 50:
                pass
        except Exception:
            pass

    def _check_needs_argument_map(self):
        if not self.current_file_path:
            return
        self._set_argument_map_button_state(running=False)

    def _trigger_auto_ocr(self):
        self.toggle_tool_panel("OCR")

    def _trigger_argument_map_generation(self):
        if not self.current_file_path:
            return
        self._set_argument_map_button_state(running=True)
        self.start_background_indexing([self.current_file_path])

    def _toggle_argument_map_generation(self):
        if getattr(self, 'ai_indexing_worker', None) and self.ai_indexing_worker.isRunning():
            self.ai_indexing_worker.stop()
            self.ai_indexing_worker.wait(3000)
            self._show_indexing_status("⚠️ Argument map generation canceled.")
            self._set_argument_map_button_state(running=False)
            return
        self._trigger_argument_map_generation()

    def _set_argument_map_button_state(self, running: bool):
        pass

    def _sync_tools_with_file(self, file_path):
        self.dock_widgets["Notes"].refresh_notes()
        self.dock_widgets["LLM Chat"].refresh_project_ui()
        for t in ["OCR", "Audio (TTS)"]:
            if hasattr(self.dock_widgets[t], "sync_file"):
                self.dock_widgets[t].sync_file(file_path)

    def toggle_tool_panel(self, tool_name):
        if tool_name == "Close Tool":
            for dock in self.dock_widgets.values():
                dock.hide()
        else:
            if tool_name in self.dock_widgets:
                dock = self.dock_widgets[tool_name]
                if dock.isVisible():
                    dock.hide()
                else:
                    dock.show()
                    dock.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)

    def closeEvent(self, event):
        self.thread_manager.stop_all_workers()
        if self.ai_indexing_worker and self.ai_indexing_worker.isRunning():
            self.ai_indexing_worker.stop()
            self.ai_indexing_worker.wait(3000)
        event.accept()
