import os
import time
import threading
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QTextEdit, QPushButton, QLabel, 
                             QComboBox, QHBoxLayout, QCheckBox, QSpinBox, QMessageBox)
from PyQt6.QtCore import pyqtSignal

from core.pdf_utils import extract_filtered_blocks
from core.tts_engine import generate_audio

class TTSTab(QWidget):
    status_updated = pyqtSignal(str)
    generation_complete = pyqtSignal(bool, str)

    def __init__(self, parent=None, main_window=None):
        super().__init__(parent)
        self.main_window = main_window
        self.theme = None
        layout = QVBoxLayout(self)
        
        self.instructions = QLabel("1. Extract Text from PDF:")
        layout.addWidget(self.instructions)
        
        # --- Extraction Controls ---
        extract_layout = QHBoxLayout()
        
        extract_layout.addWidget(QLabel("Pages:"))
        self.spin_start = QSpinBox()
        self.spin_start.setMinimum(1)
        
        self.spin_end = QSpinBox()
        self.spin_end.setMinimum(1)
        self.spin_end.setMaximum(9999)
        
        extract_layout.addWidget(self.spin_start)
        extract_layout.addWidget(QLabel("to"))
        extract_layout.addWidget(self.spin_end)
        
        self.chk_ignore = QCheckBox("Ignore Headers/Footers")
        self.chk_ignore.setChecked(True)
        extract_layout.addWidget(self.chk_ignore)
        
        self.btn_fetch = QPushButton("⬇️ Pull Text")
        self.btn_fetch.clicked.connect(self.pull_text)
        extract_layout.addWidget(self.btn_fetch)
        
        extract_layout.addStretch()
        layout.addLayout(extract_layout)
        
        # --- Text Editor ---
        self.text_editor = QTextEdit()
        self.text_editor.setPlaceholderText("Extracted text will appear here. Edit it before generating audio...")
        layout.addWidget(self.text_editor)
        
        # --- TTS Options ---
        opts_layout = QHBoxLayout()
        
        self.voice_combo = QComboBox()
        self.voice_mapping = {} 
        self._load_voices()
        
        opts_layout.addWidget(QLabel("Voice:"))
        opts_layout.addWidget(self.voice_combo)
        
        self.speed_combo = QComboBox()
        self.speed_combo.addItems(["1.0x (Normal)", "1.2x (Fast)", "1.5x (Very Fast)"])
        opts_layout.addWidget(QLabel("Speed:"))
        opts_layout.addWidget(self.speed_combo)
        layout.addLayout(opts_layout)
        
        # --- Status & Actions ---
        self.status_lbl = QLabel("Ready")
        layout.addWidget(self.status_lbl)
        
        self.btn_generate = QPushButton("▶ Generate & Save Audio")
        self.btn_generate.clicked.connect(self.start_generation_thread)
        layout.addWidget(self.btn_generate)

        self.status_updated.connect(self._handle_status_update)
        self.generation_complete.connect(self._on_generation_complete)

    def update_theme(self, theme):
        self.theme = theme
        self.instructions.setStyleSheet(f"font-weight: bold; margin-bottom: 5px; color: {theme['text_main']};")
        self.btn_fetch.setStyleSheet(f"background-color: {theme['bg_input']}; padding: 6px; border: 1px solid {theme['border']};")
        self.btn_generate.setStyleSheet(f"background-color: {theme['accent']}; padding: 12px; font-weight: bold; font-size: 14px; margin-top: 5px; color: #ffffff;")
        if self.status_lbl.text() == "Ready":
            self.status_lbl.setStyleSheet(f"color: {theme['text_muted']}; margin-top: 5px;")

    def _handle_status_update(self, msg):
        self.status_lbl.setText(msg)
        color = self.theme['warning'] if self.theme else "#ffaa00"
        self.status_lbl.setStyleSheet(f"color: {color}; margin-top: 5px;")

    def _load_voices(self):
        self.voice_combo.clear()
        self.voice_mapping.clear()
        
        models_dir = os.path.join(os.getcwd(), "models")
        if os.path.exists(models_dir):
            onnx_files = [f for f in os.listdir(models_dir) if f.endswith(".onnx")]
            for f in sorted(onnx_files):
                display_name = f.replace(".onnx", "").replace("voice", "Voice ").title()
                self.voice_mapping[display_name] = f
                self.voice_combo.addItem(display_name)
                
        if not self.voice_mapping:
            self.voice_combo.addItem("No models found")

    def sync_file(self, filepath):
        self.text_editor.clear()
        if self.main_window.viewer.doc:
            max_pages = len(self.main_window.viewer.doc)
            self.spin_start.setMaximum(max_pages)
            self.spin_end.setMaximum(max_pages)
            self.spin_end.setValue(min(3, max_pages)) 

    def pull_text(self):
        if not self.main_window.current_file_path:
            QMessageBox.warning(self, "Error", "No PDF loaded.")
            return
        
        start = self.spin_start.value()
        end = self.spin_end.value()
        ignore = self.chk_ignore.isChecked()
        
        self.status_lbl.setText("Pulling text...")
        color = self.theme['warning'] if self.theme else "#ffaa00"
        self.status_lbl.setStyleSheet(f"color: {color}; margin-top: 5px;")
        
        text = extract_filtered_blocks(self.main_window.current_file_path, ignore, start, end)
        self.text_editor.setPlainText(text)
        
        self.status_lbl.setText(f"Extracted {len(text)} characters.")
        scolor = self.theme['success'] if self.theme else "#00cc66"
        self.status_lbl.setStyleSheet(f"color: {scolor}; margin-top: 5px;")

    def start_generation_thread(self):
        text_to_read = self.text_editor.toPlainText().strip()
        if not text_to_read:
            QMessageBox.warning(self, "Error", "No text to generate audio from. Pull text first.")
            return
            
        self.btn_generate.setEnabled(False)
        self.status_lbl.setText("Generating audio... This may take a moment.")
        color = self.theme['warning'] if self.theme else "#ffaa00"
        self.status_lbl.setStyleSheet(f"color: {color}; margin-top: 5px;")
        
        display_voice = self.voice_combo.currentText()
        voice_file = self.voice_mapping.get(display_voice, display_voice)
        
        speed_str = self.speed_combo.currentText()
        speed = 1.0
        if "1.2x" in speed_str: speed = 1.2
        elif "1.5x" in speed_str: speed = 1.5
        
        thread = threading.Thread(target=self._generate_audio_logic, args=(text_to_read, voice_file, speed), daemon=True)
        thread.start()

    def _generate_audio_logic(self, text, voice, speed):
        audio_dir = os.path.join(os.getcwd(), "audio")
        os.makedirs(audio_dir, exist_ok=True)
        
        filename = f"tts_output_{int(time.time())}.wav"
        output_filepath = os.path.join(audio_dir, filename)
        
        result = generate_audio(
            text, 
            output_filepath, 
            voice_file=voice, 
            speed=speed, 
            progress_callback=lambda msg: self.status_updated.emit(msg)
        )
        
        self.generation_complete.emit(result is True, filename if result is True else str(result))

    def _on_generation_complete(self, success, message):
        self.btn_generate.setEnabled(True)
        if success:
            self.status_lbl.setText(f"✅ Audio saved to: audio/{message}")
            color = self.theme['success'] if self.theme else "#00cc66"
            self.status_lbl.setStyleSheet(f"color: {color}; margin-top: 5px;")
        else:
            self.status_lbl.setText(f"❌ Error: {message}")
            color = self.theme['error'] if self.theme else "#ff4444"
            self.status_lbl.setStyleSheet(f"color: {color}; margin-top: 5px;")