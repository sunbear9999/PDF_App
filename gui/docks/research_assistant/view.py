# gui/docks/research_assistant/view.py
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, 
                             QPushButton, QLabel, QCheckBox, QScrollArea, QFrame, QInputDialog, QComboBox, QLineEdit)
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QCursor

class CitationWidget(QFrame):
    """A card specifically for displaying verified academic citations."""
    jump_requested = Signal(str, str)

    def __init__(self, doc_name, text, score, theme, parent=None):
        super().__init__(parent)
        self.doc_name = doc_name
        self.text = text
        
        bg_color = theme.get('bg_input', '#2b2b2b')
        border_color = theme.get('success', '#00cc66') # Use success color to denote verified extraction
        self.setStyleSheet(f"""
            CitationWidget {{
                background-color: {bg_color};
                border: 1px solid {border_color};
                border-radius: 6px;
                margin-bottom: 8px;
            }}
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)

        lbl_doc = QLabel(f"<b>📄 {doc_name}</b> <span style='color: {border_color};'>({int(score*100)}% Match)</span>")
        layout.addWidget(lbl_doc)

        lbl_text = QLabel(f"<i>\"{text}\"</i>")
        lbl_text.setWordWrap(True)
        lbl_text.setStyleSheet(f"color: {theme.get('text_muted', '#aaa')}; margin-top: 4px; margin-bottom: 4px;")
        layout.addWidget(lbl_text)

        self.btn_jump = QPushButton("🔗 Jump to Document")
        self.btn_jump.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_jump.setStyleSheet(f"background-color: {theme.get('bg_panel', '#333')}; border: 1px solid {theme.get('border', '#444')}; padding: 4px; border-radius: 4px;")
        self.btn_jump.clicked.connect(lambda: self.jump_requested.emit(self.doc_name, self.text))
        layout.addWidget(self.btn_jump)
class TermWidget(QFrame):
    """A visually pleasing card for a single search term."""
    open_jstor = Signal(str)
    open_scholar = Signal(str)
    open_custom = Signal(str)

    def __init__(self, term, reason, theme, parent=None):
        super().__init__(parent)
        self.term = term
        
        bg_color = theme.get('bg_input', '#2b2b2b')
        border_color = theme.get('border', '#444')
        self.setStyleSheet(f"""
            TermWidget {{
                background-color: {bg_color};
                border: 1px solid {border_color};
                border-radius: 6px;
                margin-bottom: 8px;
            }}
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)

        lbl_term = QLabel(f"<b>{term}</b>")
        lbl_term.setStyleSheet("font-size: 14px;")
        lbl_term.setWordWrap(True)
        layout.addWidget(lbl_term)

        lbl_reason = QLabel(f"<i>{reason}</i>")
        lbl_reason.setStyleSheet(f"color: {theme.get('text_muted', '#aaa')};")
        lbl_reason.setWordWrap(True)
        layout.addWidget(lbl_reason)

        btn_layout = QHBoxLayout()
        
        self.btn_jstor = QPushButton("🏛️ JSTOR")
        self.btn_scholar = QPushButton("🎓 Scholar")
        self.btn_custom = QPushButton("🔗 Custom")
        
        for btn in [self.btn_jstor, self.btn_scholar, self.btn_custom]:
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn.setStyleSheet(f"background-color: {theme.get('bg_panel', '#333')}; border: 1px solid {border_color}; padding: 4px; border-radius: 4px;")
            btn_layout.addWidget(btn)

        layout.addLayout(btn_layout)

        self.btn_jstor.clicked.connect(lambda: self.open_jstor.emit(self.term))
        self.btn_scholar.clicked.connect(lambda: self.open_scholar.emit(self.term))
        self.btn_custom.clicked.connect(lambda: self.open_custom.emit(self.term))


class ResearchView(QWidget):
    generate_requested = Signal(str, str, bool) # goal, model, is_advanced
    change_custom_url_requested = Signal()
    
    # --- NEW: Manual Search Signals ---
    manual_search_jstor = Signal(str)
    manual_search_scholar = Signal(str)
    manual_search_custom = Signal(str)
    manual_search_google = Signal(str)
    manual_search_rag = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.theme = {}
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # ==========================================
        # NEW: Manual Quick Search Area
        # ==========================================
        manual_layout = QVBoxLayout()
        self.lbl_manual = QLabel("🔍 Quick Manual Search:")
        self.lbl_manual.setStyleSheet("font-weight: bold;")
        manual_layout.addWidget(self.lbl_manual)

        self.input_manual = QLineEdit()
        self.input_manual.setPlaceholderText("Type custom keywords or a boolean query here...")
        manual_layout.addWidget(self.input_manual)

        manual_btn_layout = QHBoxLayout()
        self.btn_man_jstor = QPushButton("🏛️ JSTOR")
        self.btn_man_scholar = QPushButton("🎓 Scholar")
        self.btn_man_custom = QPushButton("🔗 Custom")
        self.btn_man_google = QPushButton("🌐 Google")
        self.btn_man_rag = QPushButton("📄 RAG Search")
        
        for btn in [self.btn_man_jstor, self.btn_man_scholar, self.btn_man_custom, self.btn_man_google, self.btn_man_rag]:
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            manual_btn_layout.addWidget(btn)

        manual_layout.addLayout(manual_btn_layout)
        layout.addLayout(manual_layout)
        
        # Connect buttons to helper function
        self.btn_man_jstor.clicked.connect(lambda: self._on_manual_search("jstor"))
        self.btn_man_scholar.clicked.connect(lambda: self._on_manual_search("scholar"))
        self.btn_man_custom.clicked.connect(lambda: self._on_manual_search("custom"))
        self.btn_man_google.clicked.connect(lambda: self._on_manual_search("google"))
        self.btn_man_rag.clicked.connect(lambda: self._on_manual_search("rag"))

        self.sep = QFrame()
        self.sep.setFrameShape(QFrame.Shape.HLine)
        self.sep.setFixedHeight(1)
        layout.addWidget(self.sep)

        # ==========================================
        # Existing: AI Generator Area
        # ==========================================
        header_layout = QHBoxLayout()
        self.lbl_title = QLabel("🤖 AI Query Generator:")
        self.lbl_title.setStyleSheet("font-weight: bold;")
        header_layout.addWidget(self.lbl_title)
        header_layout.addStretch()
        
        self.btn_settings = QPushButton("⚙️ Custom Link")
        self.btn_settings.clicked.connect(self.change_custom_url_requested.emit)
        header_layout.addWidget(self.btn_settings)
        layout.addLayout(header_layout)

        self.input_goal = QTextEdit()
        self.input_goal.setPlaceholderText("Explain what you want to research or argue here. Be as specific as possible...")
        self.input_goal.setMaximumHeight(80)
        layout.addWidget(self.input_goal)

        options_layout = QHBoxLayout()
        options_layout.addWidget(QLabel("Model:"))
        self.model_combo = QComboBox()
        options_layout.addWidget(self.model_combo, 1)

        self.chk_advanced = QCheckBox("Advanced (Scan Citations)")
        options_layout.addWidget(self.chk_advanced)
        options_layout.addStretch()
        
        self.btn_generate = QPushButton("Generate Search Terms")
        self.btn_generate.clicked.connect(self._on_generate_clicked)
        options_layout.addWidget(self.btn_generate)
        layout.addLayout(options_layout)

        # Output Area
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        
        self.results_container = QWidget()
        self.results_layout = QVBoxLayout(self.results_container)
        self.results_layout.setContentsMargins(0, 0, 0, 0)
        self.results_layout.addStretch() 
        self.scroll_area.setWidget(self.results_container)
        
        layout.addWidget(self.scroll_area)
        
        self.status_lbl = QLabel("")
        layout.addWidget(self.status_lbl)
        
    def _on_manual_search(self, target):
        term = self.input_manual.text().strip()
        if not term: return
        
        if target == "jstor": self.manual_search_jstor.emit(term)
        elif target == "scholar": self.manual_search_scholar.emit(term)
        elif target == "custom": self.manual_search_custom.emit(term)
        elif target == "google": self.manual_search_google.emit(term)
        elif target == "rag": self.manual_search_rag.emit(term)

    def _on_generate_clicked(self):
        goal = self.input_goal.toPlainText().strip()
        model = self.model_combo.currentText()
        if goal and model:
            self.generate_requested.emit(goal, model, self.chk_advanced.isChecked())

    def set_loading_state(self, is_loading):
        self.btn_generate.setEnabled(not is_loading)
        self.input_goal.setEnabled(not is_loading)
        if is_loading:
            self.clear_results()
            self.set_status("⏳ Analyzing research goal...", is_error=False)

    def set_status(self, message, is_error=False):
        color = self.theme.get('error', '#ff4444') if is_error else self.theme.get('text_main', '#fff')
        self.status_lbl.setStyleSheet(f"color: {color};")
        self.status_lbl.setText(message)

    def clear_results(self):
        while self.results_layout.count() > 1:
            item = self.results_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def add_term_card(self, term, reason):
        card = TermWidget(term, reason, self.theme)
        self.results_layout.insertWidget(self.results_layout.count() - 1, card)
        return card
    def add_citation_card(self, doc_name, text, score):
        card = CitationWidget(doc_name, text, score, self.theme)
        self.results_layout.insertWidget(self.results_layout.count() - 1, card)
        return card
    def prompt_for_custom_url(self, current_url):
        text, ok = QInputDialog.getText(
            self, 
            "Custom Search URL", 
            "Enter search URL template (use '{term}' where the search query should go):",
            text=current_url
        )
        return text if ok else None

    def update_theme(self, theme):
        self.theme = theme
        self.setStyleSheet(f"background-color: {theme['bg_main']}; color: {theme['text_main']};")
        self.input_goal.setStyleSheet(f"background-color: {theme['bg_input']}; border: 1px solid {theme['border']};")
        self.model_combo.setStyleSheet(f"background-color: {theme['bg_input']}; border: 1px solid {theme['border']}; padding: 4px;")
        self.btn_generate.setStyleSheet(f"background-color: {theme['accent']}; font-weight: bold; color: white; padding: 6px;")
        self.btn_settings.setStyleSheet(f"background-color: transparent; color: {theme['text_muted']}; border: none;")
        
        # --- NEW STYLES ---
        self.sep.setStyleSheet(f"background-color: {theme.get('border', '#444')};")
        self.input_manual.setStyleSheet(f"background-color: {theme['bg_input']}; border: 1px solid {theme['border']}; padding: 4px; border-radius: 4px;")
        
        btn_style = f"background-color: {theme.get('bg_panel', '#333')}; border: 1px solid {theme.get('border', '#444')}; padding: 4px; border-radius: 4px;"
        for btn in [self.btn_man_jstor, self.btn_man_scholar, self.btn_man_custom, self.btn_man_google]:
            btn.setStyleSheet(btn_style)