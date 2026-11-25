"""Reading-oriented counts from the dock's existing worker snapshot."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QPlainTextEdit, QToolButton, QVBoxLayout, QWidget

from gui.components.intelligence_view_builders import overview_text
from gui.components.settings_styles import card_style, label_style, input_style, btn_default, theme_value
from gui.theme.theme import ThemeManager


class IntelligenceOverview(QWidget):
    def __init__(self, app_context, parent=None):
        super().__init__(parent)
        self.ctx = app_context
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)
        self.summary = QLabel(self)
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.summary)
        self.cards = QWidget(self)
        grid = QGridLayout(self.cards)
        grid.setContentsMargins(0, 0, 0, 0)
        self.values = {}
        theme = ThemeManager().get_theme()
        self.setObjectName("IntelligenceOverview")
        self.setStyleSheet("QWidget#IntelligenceOverview { background: "+theme_value(theme, "bg_main", "#202020")+"; }")
        self.summary.setStyleSheet(label_style(theme))
        for index, (key, title, detail) in enumerate([
            ("entities", "Entities", "Distinct people, places and concepts with current mentions."),
            ("mentions", "Mentions", "Occurrences linked to an entity throughout the source."),
            ("notes", "Document notes", "Footnote and endnote text; see Notes for their markers."),
            ("references", "References", "Bibliography entries; see Citations for citing passages."),
            ("contents", "Contents links", "Extracted contents entries, including unresolved destinations."),
            ("arguments", "Argument units", "Proposed claims, evidence and reasoning; review their labels."),
        ]):
            card = QFrame(self.cards)
            card.setObjectName("IntelligenceCountCard")
            card.setStyleSheet("QFrame#IntelligenceCountCard {"+card_style(theme)+"}")
            inner = QVBoxLayout(card)
            value = QLabel("—", card)
            value.setStyleSheet(label_style(theme, size=24))
            inner.addWidget(value)
            title_label = QLabel(title, card)
            title_label.setStyleSheet(label_style(theme))
            inner.addWidget(title_label)
            description = QLabel(detail, card)
            description.setWordWrap(True)
            description.setStyleSheet(label_style(theme, muted=True, size=12))
            inner.addWidget(description)
            grid.addWidget(card, index//2, index % 2)
            self.values[key] = value
        layout.addWidget(self.cards)
        self.disclosure = QToolButton(self)
        self.disclosure.setText("Processing details")
        self.disclosure.setStyleSheet(btn_default(theme))
        self.disclosure.setCheckable(True)
        self.disclosure.setArrowType(Qt.ArrowType.RightArrow)
        self.disclosure.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        layout.addWidget(self.disclosure)
        self.details = QPlainTextEdit(self)
        self.details.setReadOnly(True)
        self.details.setStyleSheet(input_style(theme)+label_style(theme))
        self.details.setMaximumHeight(200)
        self.details.hide()
        self.disclosure.toggled.connect(self._toggle_details)
        layout.addWidget(self.details)
        layout.addStretch()

    def _toggle_details(self, checked):
        self.details.setVisible(checked)
        self.disclosure.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)

    def setPlainText(self, text):
        self.summary.setText(text)

    def show_snapshot(self, snapshot):
        counts = snapshot.get("counts") or {}
        kinds = counts.get("current_by_type") or {}
        self.summary.setText(
            f"{counts.get('current_total', 0):,} current observations · {counts.get('total', 0):,} stored records including history. "
            "Observations include text blocks, sentences and repeated mentions; they are not all distinct entities.")
        values = {"entities": counts.get("canonical_entities", 0), "mentions": kinds.get("entity_mention", 0),
                  "notes": kinds.get("document_note", 0), "references": kinds.get("citation_entry", 0),
                  "contents": kinds.get("toc_entry", 0), "arguments": kinds.get("argument_unit", 0)}
        for key, value in values.items():
            self.values[key].setText(f"{value:,}")
        self.details.setPlainText(overview_text(snapshot))
