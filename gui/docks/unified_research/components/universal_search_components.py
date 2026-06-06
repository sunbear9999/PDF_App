from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCursor
from gui.components.base import BaseCard, BaseSearchBar


class UniversalSearchBar(BaseSearchBar):
    """A highly reusable search bar that can accept custom action buttons."""

    def __init__(self, title="🔍 Quick Search:", placeholder="Type query...", buttons=None, theme=None, parent=None):
        super().__init__(title=title, placeholder=placeholder, buttons=buttons, theme=theme, parent=parent)


class ReusableCitationCard(BaseCard):
    action_requested = Signal(str, dict) # e.g. "jump", {"doc_name": "...", "text": "..."}

    def __init__(self, doc_name, text, score=None, metadata=None, theme=None, parent=None):
        super().__init__(theme=theme, accent_color=(theme or {}).get("success", "#00cc66"), parent=parent)
        self.doc_name = doc_name
        self.text = text
        self.metadata = metadata or {}

        score_str = f" <span style='color: {(theme or {}).get('success', '#00cc66')};'>({int(score*100)}% Match)</span>" if score is not None else ""
        self.add_title(f"<b>📄 {doc_name}</b>{score_str}")
        self.lbl_text = self.add_body_text(f"<i>\"{text}\"</i>", muted=True)

        self.btn_jump = self.add_action_button("🔗 Jump to Document", "jump", {"doc_name": self.doc_name, "text": self.text})
        self.btn_jump.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))


class ReusableTermCard(BaseCard):
    action_requested = Signal(str, dict) # e.g. "search_external", {"engine": "jstor", "term": "..."}

    def __init__(self, term, description, buttons_config=None, theme=None, parent=None):
        super().__init__(theme=theme, parent=parent)
        self.term = term
        self.buttons_config = buttons_config or []

        self.lbl_term = self.add_title(f"<b>{term}</b>")
        self.lbl_term.setStyleSheet("font-size: 14px;")
        self.lbl_desc = self.add_body_text(f"<i>{description}</i>", muted=True)

        if self.buttons_config:
            btn_layout = QHBoxLayout()
            self.action_buttons = []
            for label, engine_id in self.buttons_config:
                btn = QPushButton(label)
                btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
                btn.clicked.connect(lambda checked=False, e=engine_id: self.action_requested.emit("search_external", {"engine": e, "term": self.term}))
                btn_layout.addWidget(btn)
                self.action_buttons.append(btn)
            self.body_layout.addLayout(btn_layout)

    def update_theme(self, theme):
        super().update_theme(theme)
        if hasattr(self, 'action_buttons'):
            btn_style = self.button_style()
            for btn in self.action_buttons: btn.setStyleSheet(btn_style)
