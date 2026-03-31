from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QTabWidget, QTextEdit, 
                             QPushButton, QLabel, QHBoxLayout, QLineEdit)
from PyQt6.QtCore import Qt

class ToolsPanel(QWidget):
    def __init__(self, pdf_viewer, parent=None):
        super().__init__(parent)
        self.pdf_viewer = pdf_viewer
        
        # Main layout for the right-hand panel
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        
        # Create the Tab Widget
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("""
            QTabWidget::pane { border: 1px solid #444; background: #2b2b2b; }
            QTabBar::tab { background: #333; padding: 8px 20px; border: 1px solid #444; }
            QTabBar::tab:selected { background: #555; font-weight: bold; }
        """)
        layout.addWidget(self.tabs)

        # Build and add the individual tabs
        self.tabs.addTab(self._build_workspace_tab(), "Workspace")
        self.tabs.addTab(self._build_llm_tab(), "Local LLM")
        self.tabs.addTab(self._build_utilities_tab(), "OCR & TTS")

    def _build_workspace_tab(self):
        """Tab for aggregate notes and highlights."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        
        header = QLabel("Global Project Notes")
        header.setStyleSheet("font-size: 16px; font-weight: bold;")
        layout.addWidget(header)
        
        # A markdown-friendly text editor for notes
        self.notes_editor = QTextEdit()
        self.notes_editor.setPlaceholderText("Your extracted highlights and manual notes will appear here...")
        self.notes_editor.setStyleSheet("background-color: #1e1e1e; border: 1px solid #555;")
        layout.addWidget(self.notes_editor)
        
        return tab

    def _build_llm_tab(self):
        """Tab for chatting with Ollama about the PDF."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        
        # Chat History Area
        self.chat_history = QTextEdit()
        self.chat_history.setReadOnly(True)
        self.chat_history.setStyleSheet("background-color: #1e1e1e; border: 1px solid #555;")
        layout.addWidget(self.chat_history)
        
        # Input Area
        input_layout = QHBoxLayout()
        self.chat_input = QLineEdit()
        self.chat_input.setPlaceholderText("Ask a question about this document...")
        self.chat_input.setStyleSheet("padding: 5px; background: #333; border: 1px solid #555;")
        
        send_btn = QPushButton("Send")
        send_btn.setStyleSheet("background-color: #0078D7; padding: 5px 15px;")
        
        input_layout.addWidget(self.chat_input)
        input_layout.addWidget(send_btn)
        layout.addLayout(input_layout)
        
        return tab

    def _build_utilities_tab(self):
        """Tab for processing tools like OCR and Text-to-Speech."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        
        # OCR Section
        ocr_label = QLabel("Optical Character Recognition")
        ocr_label.setStyleSheet("font-weight: bold; margin-top: 10px;")
        layout.addWidget(ocr_label)
        
        ocr_btn = QPushButton("Run OCR on Current Page")
        ocr_btn.setStyleSheet("padding: 8px; background-color: #444;")
        layout.addWidget(ocr_btn)
        
        # TTS Section
        tts_label = QLabel("Text-to-Speech Generator")
        tts_label.setStyleSheet("font-weight: bold; margin-top: 20px;")
        layout.addWidget(tts_label)
        
        tts_btn = QPushButton("Generate Audio from Selection")
        tts_btn.setStyleSheet("padding: 8px; background-color: #444;")
        layout.addWidget(tts_btn)
        
        layout.addStretch() # Pushes everything to the top
        return tab