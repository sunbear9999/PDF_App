import os
import shutil

from PyQt6.QtWidgets import QFileDialog, QMessageBox


class MainWindowProjects:
    """
    Project lifecycle operations for MainWindow.

    Extracted to keep `gui/main_window.py` from becoming a GUI orchestrator "god file".
    """

    def __init__(self, main_window):
        self.main_window = main_window

    @property
    def w(self):
        return self.main_window

    def clear_ui_for_new_project(self) -> None:
        w = self.w

        w.current_file_path = None
        w.pdf_selector.blockSignals(True)
        w.pdf_selector.clear()
        w.pdf_selector.blockSignals(False)

        if hasattr(w.viewer, "scene") and w.viewer.scene:
            w.viewer.scene.clear()
        if hasattr(w.viewer, "doc"):
            w.viewer.doc = None

        if "Notes" in w.dock_widgets:
            notes_dock = w.dock_widgets["Notes"]
            for i in reversed(range(notes_dock.scroll_layout.count())):
                widget = notes_dock.scroll_layout.itemAt(i).widget()
                if widget:
                    widget.deleteLater()
            if hasattr(w, "workspace_view"):
                w.workspace_view.scene_obj.clear()
                w.workspace_view.nodes.clear()
                w.workspace_view.edges.clear()

    def new_project(self) -> None:
        w = self.w

        path, _ = QFileDialog.getSaveFileName(
            w, "Create New Project", "", "PDF Project (*.pdfproj)"
        )
        if not path:
            return

        if not path.lower().endswith(".pdfproj"):
            path += ".pdfproj"

        if w.project_manager.project_filepath:
            w.save_project()

        self.clear_ui_for_new_project()
        w.project_manager.create_project(path)
        w.settings.setValue("last_project", w.project_manager.project_filepath)
        self.refresh_pdf_dropdown()
        w.setWindowTitle(f"PDF Workspace - {w.project_manager.project_name}")
        w.dock_widgets["LLM Chat"].refresh_project_ui()

    def open_project(self) -> None:
        w = self.w
        dialog = QFileDialog(w, "Open Project")
        dialog.setNameFilter("PDF Project (*.pdfproj);;All Files (*)")
        if not dialog.exec():
            return

        path = dialog.selectedFiles()[0]
        self.load_project(path)

    def save_project_as(self) -> None:
        w = self.w
        if not w.project_manager.project_filepath:
            QMessageBox.warning(w, "No Project", "Create or open a project first.")
            return

        path, _ = QFileDialog.getSaveFileName(
            w, "Save Project As", "", "PDF Project (*.pdfproj)"
        )
        if not path:
            return

        if not path.lower().endswith(".pdfproj"):
            path += ".pdfproj"

        old_path = w.project_manager.project_filepath
        old_chroma_dir = old_path + "_chroma_db"
        new_chroma_dir = path + "_chroma_db"

        if "Notes" in w.dock_widgets and hasattr(
            w.dock_widgets["Notes"], "save_workspace_state"
        ):
            w.dock_widgets["Notes"].save_workspace_state()

        w.pdf_controller.save_all_docs()

        if w.project_manager._conn:
            w.project_manager._conn.close()
            w.project_manager._conn = None

        try:
            shutil.copy2(old_path, path)
            if os.path.exists(old_chroma_dir):
                if os.path.exists(new_chroma_dir):
                    shutil.rmtree(new_chroma_dir)
                shutil.copytree(old_chroma_dir, new_chroma_dir)
        except Exception as e:
            QMessageBox.warning(
                w, "Error", f"Failed to copy project database: {e}"
            )
            w.project_manager._init_db()
            return

        w.project_manager.project_filepath = path
        w.project_manager.project_name = os.path.basename(path).replace(
            ".pdfproj", ""
        )
        w.project_manager._init_db()

        cursor = w.project_manager._conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
            ("project_name", w.project_manager.project_name),
        )
        w.project_manager._conn.commit()

        w.dock_widgets["LLM Chat"].refresh_project_ui()
        w.settings.setValue("last_project", path)
        w.setWindowTitle(f"PDF Workspace - {w.project_manager.project_name}")

    def load_project(self, path: str) -> None:
        w = self.w

        if w.project_manager.project_filepath:
            w.save_project()

        if w.project_manager.load_project(path):
            self.clear_ui_for_new_project()
            w.settings.setValue("last_project", w.project_manager.project_filepath)
            w.setWindowTitle(f"PDF Workspace - {w.project_manager.project_name}")
            self.refresh_pdf_dropdown()
            w.dock_widgets["LLM Chat"].refresh_project_ui()

            pdf_paths = w.pdf_controller.get_pdf_paths()
            if pdf_paths:
                w.switch_to_pdf(pdf_paths[0])
            return

        QMessageBox.warning(w, "Error", "Failed to load project file.")

    def add_pdf(self) -> None:
        w = self.w
        if not w.project_manager.project_filepath:
            QMessageBox.warning(
                w, "No Project", "Please Create or Open a Project first."
            )
            return

        file_paths, _ = QFileDialog.getOpenFileNames(
            w, "Add PDFs to Project", "", "PDF Files (*.pdf)"
        )
        if not file_paths:
            return

        added_paths = w.pdf_controller.add_pdfs(file_paths)
        self.refresh_pdf_dropdown()
        w.switch_to_pdf(added_paths[-1] if added_paths else file_paths[-1])

    def refresh_pdf_dropdown(self) -> None:
        w = self.w
        w.pdf_selector.blockSignals(True)
        w.pdf_selector.clear()
        for path in w.pdf_controller.get_pdf_paths():
            w.pdf_selector.addItem(os.path.basename(path), userData=path)
        w.pdf_selector.blockSignals(False)

    def on_pdf_dropdown_changed(self, index: int) -> None:
        w = self.w
        if index < 0:
            return

        pdf_path = w.pdf_selector.itemData(index)
        w.switch_to_pdf(pdf_path)

    def switch_to_pdf(self, pdf_path: str) -> None:
        w = self.w

        if not os.path.exists(pdf_path):
            return

        idx = w.pdf_selector.findData(pdf_path)
        if idx >= 0 and w.pdf_selector.currentIndex() != idx:
            w.pdf_selector.blockSignals(True)
            w.pdf_selector.setCurrentIndex(idx)
            w.pdf_selector.blockSignals(False)

        if w.current_file_path == pdf_path and w.viewer.doc:
            return

        w.current_file_path = pdf_path
        w.pdf_controller.set_active_file(pdf_path)
        doc = w.pdf_controller.get_doc(pdf_path)

        if not doc:
            QMessageBox.warning(
                w, "Error", "Failed to access the file from the filesystem."
            )
            return

        success = w.viewer.load_document(doc)
        if not success:
            QMessageBox.warning(w, "Error", "Failed to load the PDF document.")
            return

        w._check_needs_ocr()
        w._check_needs_argument_map()
        w._sync_tools_with_file(pdf_path)

    def autosave_project(self) -> None:
        w = self.w
        if not w.project_manager.project_filepath:
            return

        try:
            if "Notes" in w.dock_widgets and hasattr(
                w.dock_widgets["Notes"], "save_workspace_state"
            ):
                w.dock_widgets["Notes"].save_workspace_state()
            w.pdf_controller.save_all_docs()
        except Exception as e:
            print(f"Background autosave failed: {e}")

    def save_project(self) -> None:
        w = self.w
        if not w.project_manager.project_filepath:
            return

        try:
            if "Notes" in w.dock_widgets and hasattr(
                w.dock_widgets["Notes"], "save_workspace_state"
            ):
                w.dock_widgets["Notes"].save_workspace_state()
            w.pdf_controller.save_all_docs()
            QMessageBox.information(
                w, "Success", "Project and all highlights saved successfully!"
            )
        except Exception as e:
            QMessageBox.warning(w, "Save Error", f"Error saving project: {str(e)}")

