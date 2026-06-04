# gui/docks/unified_research/components/universal_search_components.py
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton, QLabel, QFrame
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCursor

class UniversalSearchBar(QFrame):
    """A highly reusable search bar that can accept custom action buttons."""
    search_requested = Signal(str, str) # engine_id, query
    
    def __init__(self, title="🔍 Quick Search:", placeholder="Type query...", buttons=None, theme=None, parent=None):
        super().__init__(parent)
        self.theme = theme or {}
        self.buttons_config = buttons or [("Search", "default")]
        self._build_ui(title, placeholder)

    def _build_ui(self, title, placeholder):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        if title:
            lbl = QLabel(f"<b>{title}</b>")
            layout.addWidget(lbl)
            
        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText(placeholder)
        layout.addWidget(self.input_field)
        
        self.btn_layout = QHBoxLayout()
        self.action_buttons = []
        
        for label, engine_id in self.buttons_config:
            btn = QPushButton(label)
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn.clicked.connect(lambda checked=False, e=engine_id: self.search_requested.emit(e, self.input_field.text().strip()))
            self.btn_layout.addWidget(btn)
            self.action_buttons.append(btn)
            
        layout.addLayout(self.btn_layout)
        self.update_theme(self.theme)

    def update_theme(self, theme):
        self.theme = theme
        input_style = f"background-color: {theme.get('bg_input', '#2b2b2b')}; color: {theme.get('text_main', '#fff')}; border: 1px solid {theme.get('border', '#444')}; padding: 6px; border-radius: 4px;"
        self.input_field.setStyleSheet(input_style)
        
        btn_style = f"background-color: {theme.get('bg_panel', '#333')}; color: {theme.get('text_main', '#fff')}; border: 1px solid {theme.get('border', '#444')}; padding: 4px 8px; border-radius: 4px;"
        for btn in self.action_buttons:
            btn.setStyleSheet(btn_style)


class ReusableCitationCard(QFrame):
    action_requested = Signal(str, dict) # e.g. "jump", {"doc_name": "...", "text": "..."}
    
    def __init__(self, doc_name, text, score=None, metadata=None, theme=None, parent=None):
        super().__init__(parent)
        self.doc_name = doc_name
        self.text = text
        self.metadata = metadata or {}
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        
        score_str = f" <span style='color: {theme.get('success', '#00cc66')};'>({int(score*100)}% Match)</span>" if score is not None else ""
        layout.addWidget(QLabel(f"<b>📄 {doc_name}</b>{score_str}"))
        
        lbl_text = QLabel(f"<i>\"{text}\"</i>")
        lbl_text.setWordWrap(True)
        lbl_text.setStyleSheet(f"color: {theme.get('text_muted', '#aaa')}; margin-top: 4px; margin-bottom: 4px;")
        layout.addWidget(lbl_text)
        
        self.btn_jump = QPushButton("🔗 Jump to Document")
        self.btn_jump.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_jump.clicked.connect(lambda: self.action_requested.emit("jump", {"doc_name": self.doc_name, "text": self.text}))
        layout.addWidget(self.btn_jump)
        
        if theme: self.update_theme(theme)

    def update_theme(self, theme):
        bg_color = theme.get('bg_input', '#2b2b2b')
        border_color = theme.get('success', '#00cc66') 
        self.setStyleSheet(f"QFrame {{ background-color: {bg_color}; border: 1px solid {border_color}; border-radius: 6px; margin-bottom: 8px; }}")
        self.btn_jump.setStyleSheet(f"background-color: {theme.get('bg_panel', '#333')}; border: 1px solid {theme.get('border', '#444')}; padding: 4px; border-radius: 4px; color: {theme.get('text_main', '#fff')};")


class ReusableTermCard(QFrame):
    action_requested = Signal(str, dict) # e.g. "search_external", {"engine": "jstor", "term": "..."}
    
    def __init__(self, term, description, buttons_config=None, theme=None, parent=None):
        super().__init__(parent)
        self.term = term
        self.buttons_config = buttons_config or []
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        
        lbl_term = QLabel(f"<b>{term}</b>")
        lbl_term.setStyleSheet("font-size: 14px;")
        lbl_term.setWordWrap(True)
        layout.addWidget(lbl_term)
        
        lbl_desc = QLabel(f"<i>{description}</i>")
        lbl_desc.setStyleSheet(f"color: {theme.get('text_muted', '#aaa') if theme else '#aaa'};")
        lbl_desc.setWordWrap(True)
        layout.addWidget(lbl_desc)
        
        if self.buttons_config:
            btn_layout = QHBoxLayout()
            self.action_buttons = []
            for label, engine_id in self.buttons_config:
                btn = QPushButton(label)
                btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
                btn.clicked.connect(lambda checked=False, e=engine_id: self.action_requested.emit("search_external", {"engine": e, "term": self.term}))
                btn_layout.addWidget(btn)
                self.action_buttons.append(btn)
            layout.addLayout(btn_layout)
            
        if theme: self.update_theme(theme)

    def update_theme(self, theme):
        bg_color = theme.get('bg_input', '#2b2b2b')
        border_color = theme.get('border', '#444')
        self.setStyleSheet(f"QFrame {{ background-color: {bg_color}; border: 1px solid {border_color}; border-radius: 6px; margin-bottom: 8px; }} QLabel {{ color: {theme.get('text_main', '#fff')}; }}")
        
        if hasattr(self, 'action_buttons'):
            btn_style = f"background-color: {theme.get('bg_panel', '#333')}; border: 1px solid {border_color}; padding: 4px; border-radius: 4px; color: {theme.get('text_main', '#fff')};"
            for btn in self.action_buttons: btn.setStyleSheet(btn_style)