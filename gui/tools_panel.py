from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QTabWidget, QTextEdit, 
                             QPushButton, QLabel, QHBoxLayout, QLineEdit)
from PyQt6.QtCore import Qt

class ToolsPanel(QWidget):
    def __init__(self, pdf_viewer, parent=None):
        super().__init__(parent)
        self.pdf_viewer = pdf_viewer
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        
        self.tabs = QTabWidget(self)
        layout.addWidget(self.tabs)

        self.tabs.addTab(self._build_workspace_tab(), "Workspace")
        self.tabs.addTab(self._build_llm_tab(), "Local LLM")
        self.tabs.addTab(self._build_utilities_tab(), "OCR & TTS")

    def _build_workspace_tab(self):
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        
        header = QLabel("Global Project Notes", tab)
        header.setStyleSheet("font-size: 16px; font-weight: bold;")
        layout.addWidget(header)
        
        self.notes_editor = QTextEdit(tab)
        self.notes_editor.setPlaceholderText("Your extracted highlights and manual notes will appear here...")
        layout.addWidget(self.notes_editor)
        
        return tab

    def _build_llm_tab(self):
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        
        self.chat_history = QTextEdit(tab)
        self.chat_history.setReadOnly(True)
        layout.addWidget(self.chat_history)
        
        input_layout = QHBoxLayout()
        self.chat_input = QLineEdit(tab)
        self.chat_input.setPlaceholderText("Ask a question about this document...")
        
        send_btn = QPushButton("Send", tab)
        
        input_layout.addWidget(self.chat_input)
        input_layout.addWidget(send_btn)
        layout.addLayout(input_layout)
        
        return tab

    def _build_utilities_tab(self):
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        
        ocr_label = QLabel("Optical Character Recognition", tab)
        ocr_label.setStyleSheet("font-weight: bold; margin-top: 10px;")
        layout.addWidget(ocr_label)
        
        ocr_btn = QPushButton("Run OCR on Current Page", tab)
        layout.addWidget(ocr_btn)
        
        tts_label = QLabel("Text-to-Speech Generator", tab)
        tts_label.setStyleSheet("font-weight: bold; margin-top: 20px;")
        layout.addWidget(tts_label)
        
        tts_btn = QPushButton("Generate Audio from Selection", tab)
        layout.addWidget(tts_btn)
        
        layout.addStretch()
        return tab