# gui/tabs/ocr_tab.py
import os
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
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

class OCRTab(QWidget):
    def __init__(self, parent=None, main_window=None):
        super().__init__(parent)
        self.main_window = main_window
        self.theme = None
        layout = QVBoxLayout(self)

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
        self.header.setStyleSheet(f"font-size: 24px; font-weight: bold; margin-bottom: 10px; color: {theme['text_main']};")
        self.run_ocr_btn.setStyleSheet(f"background-color: {theme['success']}; color: #ffffff; padding: 10px 20px; font-weight: bold; border-radius: 4px; border: none;")
        if self.status_label.text() == "Ready" or self.status_label.text().startswith("Target:"):
            self.status_label.setStyleSheet(f"color: {theme['text_muted']}; font-size: 14px; margin-left: 10px;")

    def get_output_mode(self):
        if self.rb_text.isChecked(): return "text"
        if self.rb_new.isChecked(): return "save_new"
        if self.rb_replace.isChecked(): return "replace"

    def sync_file(self, file_path):
        self.text_area.clear()
        self.status_label.setText(f"Target: {os.path.basename(file_path)}")
        color = self.theme['text_muted'] if self.theme else "gray"
        self.status_label.setStyleSheet(f"color: {color}; font-size: 14px; margin-left: 10px;")

    def _update_progress_ui(self, current_page, total_pages):
        self.status_label.setText(f"Processing Page {current_page}/{total_pages}...")
        color = self.theme['warning'] if self.theme else "#ffaa00"
        self.status_label.setStyleSheet(f"color: {color}; font-size: 14px; margin-left: 10px;")

    def start_ocr_thread(self):
        current_file = self.main_window.current_file_path
        if not current_file:
            self.status_label.setText("No document loaded in viewer.")
            color = self.theme['error'] if self.theme else "#ff4444"
            self.status_label.setStyleSheet(f"color: {color}; font-size: 14px; margin-left: 10px;")
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
            color = self.theme['error'] if self.theme else "#ff4444"
            self.status_label.setStyleSheet(f"color: {color}; font-size: 14px; margin-left: 10px;")
        else:
            self.text_area.setPlainText(text)
            msg = "OCR Complete!"
            if ui_mode != "text":
                msg += f" Saved to {os.path.basename(save_path)}"
                
                if ui_mode == "replace":
                    pm = self.main_window.project_manager
                    if save_path in pm.open_docs:
                        if not pm.open_docs[save_path].is_closed:
                            pm.open_docs[save_path].close()
                        del pm.open_docs[save_path]
                    
                    new_doc = pm.get_doc(save_path)
                    if new_doc:
                        self.main_window.viewer.load_document(new_doc)
                        
                elif ui_mode == "save_new":
                    self.main_window.project_manager.add_pdf(save_path)
                    self.main_window._refresh_pdf_dropdown()
                    self.main_window.switch_to_pdf(save_path)
                    
            self.status_label.setText(msg)
            color = self.theme['success'] if self.theme else "#00cc66"
            self.status_label.setStyleSheet(f"color: {color}; font-size: 14px; margin-left: 10px;")
            
        self.run_ocr_btn.setEnabled(True)