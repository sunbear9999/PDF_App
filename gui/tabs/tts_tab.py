from PyQt6.QtWidgets import QWidget, QVBoxLayout, QTextEdit, QPushButton, QLabel, QComboBox, QHBoxLayout

class TTSTab(QWidget):
    def __init__(self, parent=None, main_window=None):
        super().__init__(parent)
        self.main_window = main_window
        layout = QVBoxLayout(self)
        
        instructions = QLabel("1. Highlight text in the PDF to extract it, or type below:")
        instructions.setStyleSheet("font-weight: bold; margin-bottom: 5px;")
        layout.addWidget(instructions)
        
        # The editable text box for the TTS engine to read
        self.text_editor = QTextEdit()
        self.text_editor.setPlaceholderText("Extracted text will appear here. Edit it before generating audio...")
        self.text_editor.setStyleSheet("background-color: #1e1e1e; border: 1px solid #555; padding: 10px;")
        layout.addWidget(self.text_editor)
        
        # Options row
        opts_layout = QHBoxLayout()
        self.voice_combo = QComboBox()
        self.voice_combo.addItems(["Default Voice", "Alternative Voice 1", "Alternative Voice 2"])
        opts_layout.addWidget(QLabel("Voice:"))
        opts_layout.addWidget(self.voice_combo)
        
        self.speed_combo = QComboBox()
        self.speed_combo.addItems(["1.0x (Normal)", "1.2x (Fast)", "1.5x (Very Fast)"])
        opts_layout.addWidget(QLabel("Speed:"))
        opts_layout.addWidget(self.speed_combo)
        layout.addLayout(opts_layout)
        
        # Action button
        self.btn_generate = QPushButton("▶ Generate & Play Audio")
        self.btn_generate.setStyleSheet("background-color: #0078D7; padding: 12px; font-weight: bold; font-size: 14px; margin-top: 10px;")
        self.btn_generate.clicked.connect(self.generate_audio)
        layout.addWidget(self.btn_generate)
        
    def sync_file(self, filepath):
        self.text_editor.clear()

    def generate_audio(self):
        text_to_read = self.text_editor.toPlainText()
        voice = self.voice_combo.currentText()
        speed = self.speed_combo.currentText()
        # TODO: Paste your TTS backend logic (pyttsx3, edge-tts, etc.) here!
        print(f"Generating audio for: {text_to_read[:20]}... with {voice} at {speed}")