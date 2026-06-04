import os
import re
import time
import threading
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QTextEdit, QPushButton, QLabel, 
                             QComboBox, QHBoxLayout, QCheckBox, QSpinBox, QMessageBox,
                             QScrollArea, QFrame)
from PySide6.QtCore import Signal

from core.pdf_utils import extract_filtered_blocks
from core.utils.text_utils import sanitize_extracted_text
from core.tts_engine import generate_audio

class TTSTab(QWidget):
    status_updated = Signal(str)
    generation_complete = Signal(bool, str)

    def __init__(self, parent=None, main_window=None):
        super().__init__(parent)
        self.main_window = main_window
        self.theme = None
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        self.tab_scroll_area = QScrollArea(self)
        self.tab_scroll_area.setWidgetResizable(True)
        self.tab_scroll_area.setFrameShape(QFrame.Shape.NoFrame)

        self.content_widget = QWidget()
        layout = QVBoxLayout(self.content_widget)
        
        self.instructions = QLabel("1. Extract Text from PDF:")
        layout.addWidget(self.instructions)
        
        # --- Extraction Controls ---
        extract_layout = QVBoxLayout()
        extract_row_1 = QHBoxLayout()
        extract_row_2 = QHBoxLayout()

        extract_row_1.addWidget(QLabel("Pages:"))
        self.spin_start = QSpinBox()
        self.spin_start.setMinimum(1)
        
        self.spin_end = QSpinBox()
        self.spin_end.setMinimum(1)
        self.spin_end.setMaximum(9999)
        
        extract_row_1.addWidget(self.spin_start)
        extract_row_1.addWidget(QLabel("to"))
        extract_row_1.addWidget(self.spin_end)

        self.chk_ignore = QCheckBox("Ignore Headers/Footers")
        self.chk_ignore.setChecked(True)
        extract_row_2.addWidget(self.chk_ignore)
        extract_row_2.addStretch()
        
        self.btn_fetch = QPushButton("⬇️ Pull Text")
        self.btn_fetch.clicked.connect(self.pull_text)
        extract_row_1.addWidget(self.btn_fetch)
        
        extract_row_1.addStretch()

        extract_layout.addLayout(extract_row_1)
        extract_layout.addLayout(extract_row_2)
        layout.addLayout(extract_layout)
        
        # --- Text Editor ---
        self.text_editor = QTextEdit()
        self.text_editor.setPlaceholderText("Extracted text will appear here. Edit it before generating audio...")
        layout.addWidget(self.text_editor)
        
        # --- TTS Options ---
        opts_layout = QVBoxLayout()
        opts_row_1 = QHBoxLayout()
        opts_row_2 = QHBoxLayout()
        
        self.voice_combo = QComboBox()
        self.voice_mapping = {} 
        self._load_voices()
        
        opts_row_1.addWidget(QLabel("Voice:"))
        opts_row_1.addWidget(self.voice_combo)
        opts_row_1.addStretch()
        
        self.speed_combo = QComboBox()
        self.speed_combo.addItems(["1.0x (Normal)", "1.2x (Fast)", "1.5x (Very Fast)"])
        opts_row_2.addWidget(QLabel("Speed:"))
        opts_row_2.addWidget(self.speed_combo)
        opts_row_2.addStretch()

        opts_layout.addLayout(opts_row_1)
        opts_layout.addLayout(opts_row_2)
        layout.addLayout(opts_layout)
        
        # --- Status & Actions ---
        self.status_lbl = QLabel("Ready")
        layout.addWidget(self.status_lbl)
        
        self.btn_generate = QPushButton("▶ Generate & Save Audio")
        self.btn_generate.clicked.connect(self.start_generation_thread)
        layout.addWidget(self.btn_generate)

        self.status_updated.connect(self._handle_status_update)
        self.generation_complete.connect(self._on_generation_complete)

        self.tab_scroll_area.setWidget(self.content_widget)
        outer_layout.addWidget(self.tab_scroll_area)

    def update_theme(self, theme):
        self.theme = theme
        
        # Ensure base colors are correct before styling internals
        self.setStyleSheet(f"TTSTab {{ background-color: {theme['bg_main']}; color: {theme['text_main']}; }}")
        
        # Explicitly theme the Scroll Area components
        self.tab_scroll_area.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.tab_scroll_area.viewport().setStyleSheet("background: transparent;")
        self.content_widget.setStyleSheet(f"QWidget {{ background-color: {theme['bg_main']}; color: {theme['text_main']}; }}")
        
        # Theme Inputs
        input_style = f"background-color: {theme['bg_input']}; color: {theme['text_main']}; border: 1px solid {theme['border']}; border-radius: 4px; padding: 4px;"
        self.text_editor.setStyleSheet(input_style)
        self.spin_start.setStyleSheet(input_style)
        self.spin_end.setStyleSheet(input_style)
        self.voice_combo.setStyleSheet(input_style)
        self.speed_combo.setStyleSheet(input_style)
        
        # Theme basic labels
        self.instructions.setStyleSheet(f"color: {theme['text_main']}; font-weight: bold; background: transparent;")
        self.chk_ignore.setStyleSheet(f"color: {theme['text_main']}; font-weight: bold; background: transparent;")
        
        # Theme Buttons
        self.btn_fetch.setStyleSheet(f"""
            QPushButton {{ background-color: {theme['bg_panel']}; color: {theme['text_main']}; padding: 6px; border: 1px solid {theme['border']}; border-radius: 4px; font-weight: bold; }}
            QPushButton:hover {{ background-color: {theme['accent_hover']}; color: white; border: none; }}
        """)
        
        self.btn_generate.setStyleSheet(f"""
            QPushButton {{ background-color: {theme['accent']}; padding: 10px; font-weight: bold; font-size: 14px; margin-top: 5px; color: #ffffff; border-radius: 4px; border: none; }}
            QPushButton:hover {{ background-color: {theme['accent_hover']}; }}
            QPushButton:disabled {{ background-color: {theme['bg_input']}; color: {theme['text_muted']}; }}
        """)
        
        if self.status_lbl.text() == "Ready":
            self.status_lbl.setStyleSheet(f"color: {theme['text_muted']}; margin-top: 5px; font-weight: bold; background: transparent;")
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
        text = self.clean_text_for_tts(text)
        self.text_editor.setPlainText(text)
        
        self.status_lbl.setText(f"Extracted {len(text)} characters.")
        scolor = self.theme['success'] if self.theme else "#00cc66"
        self.status_lbl.setStyleSheet(f"color: {scolor}; margin-top: 5px;")

    def clean_text_for_tts(self, raw_text):
        """Scrubs hidden PDF annotation anchors and broken unicode before TTS."""
        return sanitize_extracted_text(raw_text, collapse_whitespace=True)

    def start_generation_thread(self):
        text_to_read = self.text_editor.toPlainText().strip()
        text_to_read = self.clean_text_for_tts(text_to_read)
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