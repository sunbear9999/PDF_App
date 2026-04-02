# gui/tabs/ocr_tab.py
import os
import threading
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
                             QRadioButton, QButtonGroup, QTextEdit, QPushButton)
from PyQt6.QtCore import Qt, pyqtSignal
from core.ocr_engine import run_ocr_on_pdf

class OCRTab(QWidget):
    # Signals for safe background thread -> UI updates
    progress_updated = pyqtSignal(int, int)
    ocr_completed = pyqtSignal(str, str, str) # text, ui_mode, save_path

    def __init__(self, parent=None, main_window=None):
        super().__init__(parent)
        self.main_window = main_window
        layout = QVBoxLayout(self)

        # Header
        self.header = QLabel("OCR Engine")
        self.header.setStyleSheet("font-size: 24px; font-weight: bold; margin-bottom: 10px;")
        layout.addWidget(self.header)

        # Top Frame (Options)
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

        # Text Area
        self.text_area = QTextEdit()
        self.text_area.setReadOnly(True)
        self.text_area.setStyleSheet("background-color: #1e1e1e; border: 1px solid #555; font-size: 14px; padding: 10px;")
        layout.addWidget(self.text_area, 1)

        # Control Frame
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

        # Wire up signals
        self.progress_updated.connect(self._update_progress_ui)
        self.ocr_completed.connect(self._finalize_ocr)

    def get_output_mode(self):
        if self.rb_text.isChecked(): return "text"
        if self.rb_new.isChecked(): return "save_new"
        if self.rb_replace.isChecked(): return "replace"

    def sync_file(self, file_path):
        self.text_area.clear()
        self.status_label.setText(f"Target: {os.path.basename(file_path)}")
        self.status_label.setStyleSheet("color: gray;")

    def update_progress(self, current_page, total_pages):
        self.progress_updated.emit(current_page, total_pages)

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
        thread = threading.Thread(target=self._process_ocr_logic, args=(current_file, mode), daemon=True)
        thread.start()

    def _process_ocr_logic(self, file_path, ui_mode):
        save_path = None
        engine_mode = "text"

        if ui_mode == "save_new":
            engine_mode = "pdf"
            base, ext = os.path.splitext(file_path)
            save_path = f"{base}_ocr{ext}"
        elif ui_mode == "replace":
            engine_mode = "pdf"
            save_path = file_path
            
        result_text = run_ocr_on_pdf(file_path, mode=engine_mode, save_path=save_path, progress_callback=self.update_progress)
        self.ocr_completed.emit(result_text, ui_mode, save_path if save_path else "")

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
                    # Force eviction of the old document from cache
                    if save_path in pm.open_docs:
                        if not pm.open_docs[save_path].is_closed:
                            pm.open_docs[save_path].close()
                        del pm.open_docs[save_path]
                    
                    # Fetch the new PyMuPDF document object and load it into the viewer
                    new_doc = pm.get_doc(save_path)
                    if new_doc:
                        self.main_window.viewer.load_document(new_doc)
                        
                elif ui_mode == "save_new":
                    # Automatically add the new PDF to the project and switch to it
                    self.main_window.project_manager.add_pdf(save_path)
                    self.main_window._refresh_pdf_dropdown()
                    self.main_window.switch_to_pdf(save_path)
                    
            self.status_label.setText(msg)
            self.status_label.setStyleSheet("color: #00cc66;")
            
        self.run_ocr_btn.setEnabled(True)