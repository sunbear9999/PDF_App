# gui/dock_panels/ocr_dock.py
import os
from PyQt6.QtWidgets import (QDockWidget, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QRadioButton, QButtonGroup, QTextEdit, QPushButton)
from PyQt6.QtCore import Qt, pyqtSignal, QThread

class OCRWorker(QThread):
    progress_updated = pyqtSignal(int, int)
    ocr_completed = pyqtSignal(str, str, str)

    def __init__(self, file_path, ui_mode, parent=None):
        super().__init__(parent)
        self.file_path = file_path
        self.ui_mode = ui_mode

    def run(self):
        save_path = None
        engine_mode = "text"

        if self.ui_mode == "save_new":
            engine_mode = "pdf"
            base, ext = os.path.splitext(self.file_path)
            save_path = f"{base}_ocr{ext}"
        elif self.ui_mode == "replace":
            engine_mode = "pdf"
            save_path = self.file_path

        def cb(cur, tot):
            self.progress_updated.emit(cur, tot)

        from core.ocr_engine import run_ocr_on_pdf
        result_text = run_ocr_on_pdf(self.file_path, mode=engine_mode, save_path=save_path, progress_callback=cb)
        self.ocr_completed.emit(result_text, self.ui_mode, save_path if save_path else "")

class OCRDockWidget(QDockWidget):
    def __init__(self, main_window=None, parent=None):
        super().__init__("OCR", parent)
        self.main_window = main_window
        self.setAllowedAreas(Qt.DockWidgetArea.RightDockWidgetArea)
        self.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable |
                         QDockWidget.DockWidgetFeature.DockWidgetMovable |
                         QDockWidget.DockWidgetFeature.DockWidgetFloatable)

        widget = QWidget()
        self.setWidget(widget)
        layout = QVBoxLayout(widget)

        self.header = QLabel("OCR Engine")
        layout.addWidget(self.header)

        top_layout = QHBoxLayout()
        self.mode_group = QButtonGroup(self)

        self.rb_text = QRadioButton("Extract Text")
        self.rb_new = QRadioButton("Save New PDF")
        self.rb_replace = QRadioButton("Replace Original")
        self.rb_text.setChecked(True)

        self.mode_group.addButton(self.rb_text, 1)
        self.mode_group.addButton(self.rb_new, 2)
        self.mode_group.addButton(self.rb_replace, 3)

        top_layout.addWidget(self.rb_text)
        top_layout.addWidget(self.rb_new)
        top_layout.addWidget(self.rb_replace)
        top_layout.addStretch()
        layout.addLayout(top_layout)

        self.text_area = QTextEdit()
        self.text_area.setReadOnly(True)
        layout.addWidget(self.text_area, 1)

        control_layout = QHBoxLayout()
        self.run_ocr_btn = QPushButton("Run OCR")
        self.run_ocr_btn.clicked.connect(self.start_ocr_thread)
        control_layout.addWidget(self.run_ocr_btn)

        self.status_label = QLabel("Ready")
        control_layout.addWidget(self.status_label)
        control_layout.addStretch()
        layout.addLayout(control_layout)

    def update_theme(self, theme):
        self.theme = theme
        # Remove inline styles, rely on global QSS

    def get_output_mode(self):
        if self.rb_text.isChecked(): return "text"
        if self.rb_new.isChecked(): return "save_new"
        if self.rb_replace.isChecked(): return "replace"

    def sync_file(self, file_path):
        self.text_area.clear()
        self.status_label.setText(f"Target: {os.path.basename(file_path)}")

    def _update_progress_ui(self, current_page, total_pages):
        self.status_label.setText(f"Processing Page {current_page}/{total_pages}...")

    def start_ocr_thread(self):
        current_file = self.main_window.current_file_path
        if not current_file:
            self.status_label.setText("No document loaded in viewer.")
            return

        self.run_ocr_btn.setEnabled(False)
        self.text_area.clear()

        mode = self.get_output_mode()

        self.ocr_worker = OCRWorker(current_file, mode, parent=self)
        self.ocr_worker.progress_updated.connect(self._update_progress_ui)
        self.ocr_worker.ocr_completed.connect(self._finalize_ocr)
        self.ocr_worker.start()

    def _finalize_ocr(self, text, ui_mode, save_path):
        if text.startswith("OCR Engine Error"):
            self.text_area.setPlainText(text)
            self.status_label.setText("Failed")
        else:
            self.text_area.setPlainText(text)
            msg = "OCR Complete!"
            if ui_mode != "text":
                msg += f" Saved to {os.path.basename(save_path)}"

                if ui_mode == "replace":
                    self.main_window.ocr_controller.refresh_document_cache(save_path)
                    new_doc = self.main_window.ocr_controller.get_doc(save_path)
                    if new_doc:
                        self.main_window.ocr_controller.load_document_in_viewer(new_doc)

                elif ui_mode == "save_new":
                    self.main_window.ocr_controller.add_pdf_to_project(save_path)
                    self.main_window.ocr_controller.refresh_pdf_dropdown()
                    self.main_window.ocr_controller.switch_to_file(save_path)

            self.status_label.setText(msg)

        self.run_ocr_btn.setEnabled(True)