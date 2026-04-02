# gui/tabs/ocr_tab.py
import os
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
                             QRadioButton, QButtonGroup, QTextEdit, QPushButton)
from PyQt6.QtCore import Qt, pyqtSignal, QThread

# --- NEW SAFE OCR WORKER ---
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
        layout = QVBoxLayout(self)

        self.header = QLabel("OCR Engine")
        self.header.setStyleSheet("font-size: 24px; font-weight: bold; margin-bottom: 10px;")
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
        self.text_area.setStyleSheet("background-color: #1e1e1e; border: 1px solid #555; font-size: 14px; padding: 10px;")
        layout.addWidget(self.text_area, 1)

        control_layout = QHBoxLayout()
        self.run_ocr_btn = QPushButton("Run OCR")
        self.run_ocr_btn.setStyleSheet("background-color: #00cc66; color: white; padding: 10px 20px; font-weight: bold;")
        self.run_ocr_btn.clicked.connect(self.start_ocr_thread)
        control_layout.addWidget(self.run_ocr_btn)

        self.status_label = QLabel("Ready")
        self.status_label.setStyleSheet("color: gray; font-size: 14px; margin-left: 10px;")
        control_layout.addWidget(self.status_label)
        control_layout.addStretch()
        layout.addLayout(control_layout)

    def get_output_mode(self):
        if self.rb_text.isChecked(): return "text"
        if self.rb_new.isChecked(): return "save_new"
        if self.rb_replace.isChecked(): return "replace"

    def sync_file(self, file_path):
        self.text_area.clear()
        self.status_label.setText(f"Target: {os.path.basename(file_path)}")
        self.status_label.setStyleSheet("color: gray;")

    def _update_progress_ui(self, current_page, total_pages):
        self.status_label.setText(f"Processing Page {current_page}/{total_pages}...")
        self.status_label.setStyleSheet("color: #ffaa00;")

    def start_ocr_thread(self):
        current_file = self.main_window.current_file_path
        if not current_file:
            self.status_label.setText("No document loaded in viewer.")
            self.status_label.setStyleSheet("color: #ff4444;")
            return
            
        self.run_ocr_btn.setEnabled(False)
        self.text_area.clear()
        
        mode = self.get_output_mode()
        
        # Replaced Python threading with safe PyQt QThread
        self.ocr_worker = OCRWorker(current_file, mode, parent=self)
        self.ocr_worker.progress_updated.connect(self._update_progress_ui)
        self.ocr_worker.ocr_completed.connect(self._finalize_ocr)
        self.ocr_worker.start()

    def _finalize_ocr(self, text, ui_mode, save_path):
        if text.startswith("OCR Engine Error"):
            self.text_area.setPlainText(text)
            self.status_label.setText("Failed")
            self.status_label.setStyleSheet("color: #ff4444;")
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
            self.status_label.setStyleSheet("color: #00cc66;")
            
        self.run_ocr_btn.setEnabled(True)