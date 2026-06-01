# gui/docks/unified_research/components/chat_streamer.py
from PySide6.QtWidgets import QWidget, QVBoxLayout, QPushButton, QTextBrowser, QLabel
from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor
from gui.docks.unified_research.components.note_bubble import NoteBubbleWidget

class ChatMessageWidget(QWidget):
    def __init__(self, sender_name, theme=None, is_user=False, parent=None):
        super().__init__(parent)
        self.raw_buffer = ""
        self.theme = theme
        self.is_user = is_user
        
        self.layout = QVBoxLayout(self)
        # Tighten the margins to remove the weird gaps between messages
        self.layout.setContentsMargins(8, 4, 8, 4) 
        
        # 2. Shrink the gap between the sender name and the message text
        self.layout.setSpacing(2)

        lbl_sender = QLabel(f"<b>{sender_name}</b>")
        self.layout.addWidget(lbl_sender)

        if is_user:
            # BUG FIX 1: Use a standard Label for the user. It never fails to size correctly.
            self.user_text = QLabel()
            self.user_text.setWordWrap(True)
            self.user_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.layout.addWidget(self.user_text)
        else:
            # AI Widgets
            self.lbl_status = QLabel("<i>🔄 Initializing AI...</i>")
            self.layout.addWidget(self.lbl_status)
            self.lbl_status.hide()

            self.btn_thought = QPushButton("▶ Show Reasoning")
            self.btn_thought.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            self.btn_thought.clicked.connect(self._toggle_thought)
            self.btn_thought.hide() 
            self.layout.addWidget(self.btn_thought)
            
            self.thought_browser = QTextBrowser()
            self.thought_browser.setOpenExternalLinks(False)
            self.thought_browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.thought_browser.hide()
            self.layout.addWidget(self.thought_browser)

            self.main_browser = QTextBrowser()
            self.main_browser.setOpenExternalLinks(False)
            self.main_browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.layout.addWidget(self.main_browser)

        self.bubbles_container = QWidget()
        self.bubbles_layout = QVBoxLayout(self.bubbles_container)
        self.bubbles_layout.setContentsMargins(0, 4, 0, 0)
        self.layout.addWidget(self.bubbles_container)

        if theme:
            self.update_theme(theme)

    def update_status(self, text):
        if hasattr(self, 'lbl_status'):
            self.lbl_status.setText(f"<i>🔄 {text}</i>")
            if self.theme:
                self.lbl_status.setStyleSheet(f"color: {self.theme.get('accent', '#b366ff')}; font-size: 12px; font-weight: bold;")
            self.lbl_status.show()
        
    def hide_status(self):
        if hasattr(self, 'lbl_status'):
            self.lbl_status.hide()

    def append_chunk(self, chunk):
        self.raw_buffer += chunk

        if self.is_user:
            self.user_text.setText(self.raw_buffer)
            return

        self.hide_status()

        # BUG FIX 2: Rock-solid string splitting instead of fragile Regex
        if "<think>" in self.raw_buffer:
            if self.btn_thought.isHidden():
                self.btn_thought.show()

            parts = self.raw_buffer.split("</think>")
            thought_part = parts[0].replace("<think>", "").strip()
            self.thought_browser.setMarkdown(thought_part)
            self._resize_browser(self.thought_browser)

            # If the LLM has finished thinking and closed the tag, print to the main browser
            if len(parts) > 1:
                main_part = parts[1].strip()
                self.main_browser.setMarkdown(main_part)
                self._resize_browser(self.main_browser)
        else:
            # Standard streaming if no think tags are used
            self.main_browser.setMarkdown(self.raw_buffer.strip())
            self._resize_browser(self.main_browser)

    def add_bubble(self, doc_name, quote, note):
        bubble = NoteBubbleWidget(doc_name, quote, note, self.theme, parent=self)
        if hasattr(self.window(), 'viewer'): 
            bubble.jump_requested.connect(self.window().viewer.jump_to_source)
            bubble.save_requested.connect(lambda: self.window().add_ai_annotation(quote, note, doc_name))
            bubble.search_requested.connect(lambda: self.window().viewer.trigger_find_similar(quote))
        self.bubbles_layout.addWidget(bubble)
        return bubble
    
    def _toggle_thought(self):
        visible = not self.thought_browser.isVisible()
        self.thought_browser.setVisible(visible)
        self.btn_thought.setText("▼ Hide Reasoning" if visible else "▶ Show Reasoning")
        if visible:
            self._resize_browser(self.thought_browser)

    def _resize_browser(self, browser):
        # BUG FIX 3: Ignore resizing if PySide hasn't laid out the UI yet (prevents 0-height collapses)
        doc = browser.document()
        width = browser.viewport().width()
        if width > 0:
            doc.setTextWidth(width)
            
        new_height = int(doc.size().height()) + 8
        if new_height > 15:  
            browser.setMinimumHeight(new_height)
            browser.setMaximumHeight(new_height)

    def update_theme(self, theme):
        self.theme = theme
        text_col = theme.get('text_main', '#fff')
        muted_col = theme.get('text_muted', '#aaa')
        
        self.setStyleSheet(f"color: {text_col};")
        
        if self.is_user:
            self.user_text.setStyleSheet(f"color: {text_col}; font-size: 14px;")
        else:
            self.btn_thought.setStyleSheet(f"""
                QPushButton {{ text-align: left; background: transparent; color: {muted_col}; font-weight: bold; border: none; padding: 2px; }}
                QPushButton:hover {{ color: {text_col}; }}
            """)
            self.thought_browser.setStyleSheet(f"background-color: rgba(0,0,0,0.1); color: {muted_col}; border: 1px solid {theme.get('border', '#444')}; border-radius: 4px;")
            self.main_browser.setStyleSheet(f"background: transparent; color: {text_col}; border: none; font-size: 14px;")
            self.lbl_status.setStyleSheet(f"color: {theme.get('accent', '#b366ff')}; font-size: 12px; font-weight: bold;")
        
        for i in range(self.bubbles_layout.count()):
            widget = self.bubbles_layout.itemAt(i).widget()
            if isinstance(widget, NoteBubbleWidget):
                widget.update_theme(theme)