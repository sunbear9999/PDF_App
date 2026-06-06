from PySide6.QtWidgets import QFrame, QVBoxLayout, QPushButton, QLabel, QScrollArea, QWidget
from PySide6.QtCore import Qt

class UniversalInternalOverlay(QFrame):
    """A frameless overlay that darkens the app and displays dynamic AI results natively."""
    def __init__(self, main_window, theme):
        super().__init__(main_window)
        self.main_window = main_window
        self.theme = theme
        self.setObjectName("UniversalInternalOverlay")
        
        # Dim the rest of the application
        self.setStyleSheet("QFrame#UniversalInternalOverlay { background-color: rgba(0, 0, 0, 180); }")
        self.hide()

        # Center Panel
        self.panel = QFrame(self)
        self.panel.setStyleSheet(f"background-color: {theme['bg_main']}; border-radius: 8px; border: 1px solid {theme['border']};")
        self.panel_layout = QVBoxLayout(self.panel)

        self.lbl_title = QLabel("AI Result")
        self.lbl_title.setStyleSheet(f"color: {theme['text_main']}; font-size: 18px; font-weight: bold; border: none;")
        self.panel_layout.addWidget(self.lbl_title)

        # Dynamic Content Area (Scrollable)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.content_container = QWidget()
        self.content_layout = QVBoxLayout(self.content_container)
        self.content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(self.content_container)
        self.panel_layout.addWidget(self.scroll)

        # Close Button
        self.btn_close = QPushButton("Close")
        self.btn_close.setStyleSheet(f"background-color: {theme['bg_panel']}; color: {theme['text_main']}; padding: 8px; border-radius: 4px;")
        self.btn_close.clicked.connect(self.hide)
        self.panel_layout.addWidget(self.btn_close, alignment=Qt.AlignmentFlag.AlignRight)

    def clear_content(self):
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()

    def receive_ai_widget(self, widget):
        self.content_layout.addWidget(widget)
        self.show()
        self.raise_()

    def receive_ai_payload(self, payload: dict):
        payload_type = payload.get("type")
        if payload_type in {"status", "hide_status"}:
            return

        if payload_type == "outline":
            from gui.docks.unified_research.components.dynamic_outlines import UniversalOutlineWidget
            self.lbl_title.setText(payload.get("title", "AI Result"))
            annot_manager = self.main_window.viewer.annot_manager if hasattr(self.main_window, "viewer") else None
            widget = UniversalOutlineWidget(payload.get("title", "AI Result"), payload.get("content", ""), self.theme, annot_manager)
            widget._raw_ai_data = payload.get("raw_ai_data", payload.get("content", ""))
        elif payload_type == "data_table":
            from gui.docks.unified_research.components.dynamic_data_table import DynamicDataTableWidget
            widget = DynamicDataTableWidget(payload.get("content", ""), self.theme)
        elif payload_type == "card_grid":
            from gui.docks.unified_research.components.dynamic_card_grid import DynamicCardGridWidget
            widget = DynamicCardGridWidget(payload.get("content", ""), self.theme)
        elif payload_type == "citation_cards":
            from gui.docks.unified_research.components.chat_streamer import ChatMessageWidget
            widget = ChatMessageWidget("AI Agent", theme=self.theme)
            for item in payload.get("items", []):
                if isinstance(item, dict):
                    widget.add_bubble(
                        doc_name=item.get("doc_name", "Unknown Document"),
                        quote=item.get("quote", item.get("text", "")),
                        note=item.get("note", item.get("reason", ""))
                    )
        elif payload_type == "results_dialog":
            from gui.components.dialogs.tag_relatives_dialog import AIResultsDialog
            dlg = AIResultsDialog(payload.get("title", "AI Results"), payload.get("items", []), self.main_window, self.main_window)
            dlg.show()
            return
        elif payload_type == "error":
            self.lbl_title.setText("Pipeline Error")
            widget = QLabel(payload.get("message", "Unknown error"))
        else:
            return

        self.clear_content()
        self.receive_ai_widget(widget)

    def resizeEvent(self, event):
        # Always stretch to fill the main window exactly
        self.resize(self.main_window.size())
        # Keep panel centered, occupying 60% of width, 80% of height
        pw, ph = int(self.width() * 0.6), int(self.height() * 0.8)
        self.panel.setFixedSize(pw, ph)
        self.panel.move((self.width() - pw) // 2, (self.height() - ph) // 2)
