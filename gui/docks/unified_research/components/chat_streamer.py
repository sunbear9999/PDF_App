# gui/docks/unified_research/components/chat_streamer.py
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTextBrowser, QLabel
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCursor
from gui.docks.unified_research.components.note_bubble import NoteBubbleWidget

class ChatMessageWidget(QWidget):
    def __init__(self, sender_name, theme=None, is_user=False, app_context=None, parent=None):
        super().__init__(parent)
        self.raw_buffer = ""
        self.theme = theme
        self.is_user = is_user

        self.layout = QVBoxLayout(self)
        # Tighten the margins to remove the weird gaps between messages
        self.layout.setContentsMargins(8, 4, 8, 4)

        # 2. Shrink the gap between the sender name and the message text
        self.layout.setSpacing(2)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        lbl_sender = QLabel(f"<b>{sender_name}</b>")
        header_layout.addWidget(lbl_sender)
        header_layout.addStretch()
        self.trace_button = None
        if not is_user:
            from gui.components.prompt_trace_button import PromptTraceButton
            self.trace_button = PromptTraceButton(theme=self.theme, app_context=app_context, parent=self)
            self.trace_button.hide()
            header_layout.addWidget(self.trace_button)
        self.layout.addLayout(header_layout)

        if is_user:
            # BUG FIX 1: Use a standard Label for the user. It never fails to size correctly.
            self.user_text = QLabel()
            self.user_text.setWordWrap(True)
            self.user_text.setTextFormat(Qt.TextFormat.PlainText)
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
            self.thought_browser.setOpenExternalLinks(True)
            self.thought_browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.thought_browser.hide()
            self.layout.addWidget(self.thought_browser)

            self.main_browser = QTextBrowser()
            self.main_browser.setOpenExternalLinks(True)
            self.main_browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.layout.addWidget(self.main_browser)

        self.bubbles_container = QWidget()
        self.bubbles_layout = QVBoxLayout(self.bubbles_container)
        self.bubbles_layout.setContentsMargins(0, 4, 0, 0)
        self.layout.addWidget(self.bubbles_container)

        if theme:
            self.update_theme(theme)

    def set_prompt_trace(self, trace_id):
        if self.trace_button:
            self.trace_button.theme = self.theme or {}
            self.trace_button.set_trace_id(trace_id)

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

    def set_final_text(self, text):
        self.raw_buffer = text or ""
        if self.is_user:
            self.user_text.setText(self.raw_buffer)
            return
        self.hide_status()
        self._render_ai_text()

    def add_notice(self, text):
        """Show a compact system acknowledgement outside the answer body."""
        notice = QLabel(f"✓ {text}")
        notice.setStyleSheet(
            f"color: {(self.theme or {}).get('text_muted', '#aaa')}; "
            "font-size: 11px; padding: 3px 0;"
        )
        self.bubbles_layout.addWidget(notice)

    def add_manifest_update(self, changes, open_callback=None):
        from gui.docks.unified_research.components.manifest_bubble import ManifestUpdateWidget

        update_widget = ManifestUpdateWidget(changes or {}, self.theme, parent=self)
        if open_callback:
            update_widget.btn_open.clicked.connect(open_callback)
        if hasattr(self, "main_browser") and not self.raw_buffer.strip():
            self.main_browser.hide()
        self.bubbles_layout.addWidget(update_widget)
        return update_widget

    def add_bubble(self, doc_name, quote, note, source_meta=None):
        bubble = NoteBubbleWidget(doc_name, quote, note, self.theme, parent=self, source_meta=source_meta)
        viewer = getattr(self.window(), "viewer", None)
        app_context = getattr(self.window(), "app_context", None)
        source_meta = dict(source_meta or {})
        source_meta.setdefault("doc_name", doc_name)
        source_meta.setdefault("quote", quote)
        registry = getattr(app_context, "plugin_extension_registry", None)
        handler = registry.get_citation_source_handler(source_meta.get("source_type", "")) if registry else None
        if handler:
            bubble.setProperty("citation_handler_connected", True)
            bubble.citation_jump_requested.connect(
                lambda citation, spec=handler, ctx=app_context: spec.callback(citation, ctx)
            )
        if viewer:
            if not handler and hasattr(viewer, "jump_to_source"):
                bubble.jump_requested.connect(viewer.jump_to_source)
            if hasattr(viewer, "jump_to_video"):
                bubble.source_jump_requested.connect(
                    lambda meta: viewer.jump_to_video(
                        source_id=meta.get("source_id"),
                        timestamp=meta.get("start", 0),
                    )
                )
            bubble.save_requested.connect(lambda q, n, d, meta=source_meta: self._save_bubble_note(q, n, d, meta))
            annot_manager = getattr(viewer, "annot_manager", None)
            if annot_manager and hasattr(annot_manager, "trigger_similar_context"):
                bubble.search_requested.connect(
                    lambda selected_quote: annot_manager.trigger_similar_context(selected_quote)
                )
        if hasattr(self, "main_browser") and not self.raw_buffer.strip():
            self.main_browser.hide()
        self.bubbles_layout.addWidget(bubble)
        return bubble

    def _save_bubble_note(self, quote, note, doc_name, source_meta=None):
        source_meta = source_meta or {}
        if source_meta.get("source_type") == "video" and hasattr(self.window(), "viewer"):
            viewer = self.window().viewer
            path = ""
            pm = getattr(getattr(self.window(), "app_context", None), "project_manager", None)
            if pm and source_meta.get("source_id") and hasattr(pm, "get_source_path"):
                path = pm.get_source_path(source_meta.get("source_id")) or ""
            if hasattr(viewer, "video_player"):
                player = viewer.video_player
                if path:
                    player.video_path = path
                player.source_id = source_meta.get("source_id", "")
                player.save_quote_note(
                    quote,
                    note,
                    timestamp=source_meta.get("start", 0),
                    path=path,
                    source_id=source_meta.get("source_id", ""),
                )
            return
        app_context = getattr(self.window(), "app_context", None)
        annotation_service = getattr(app_context, "workspace_annotation_service", None)
        if annotation_service and hasattr(annotation_service, "add_ai_annotation"):
            annotation_service.add_ai_annotation(quote, note, doc_name)
    
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

    def _render_ai_text(self):
        text = self.raw_buffer.strip()
        if not text:
            self.main_browser.hide()
            return
        self.main_browser.show()
        if "<think>" in text:
            self.btn_thought.show()
            thought, separator, answer = text.partition("</think>")
            self.thought_browser.setMarkdown(thought.replace("<think>", "").strip())
            self._resize_browser(self.thought_browser)
            self.main_browser.setMarkdown(answer.strip() if separator else "")
        else:
            self.main_browser.setMarkdown(text)
        self._resize_browser(self.main_browser)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self.is_user:
            QTimer.singleShot(0, self._resize_rendered_content)

    def _resize_rendered_content(self):
        if self.is_user:
            return
        if self.main_browser.isVisible():
            self._resize_browser(self.main_browser)
        if self.thought_browser.isVisible():
            self._resize_browser(self.thought_browser)

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
            if self.trace_button:
                self.trace_button.theme = theme
                self.trace_button.apply_theme()
        
        for i in range(self.bubbles_layout.count()):
            widget = self.bubbles_layout.itemAt(i).widget()
            if isinstance(widget, NoteBubbleWidget):
                widget.update_theme(theme)
            elif hasattr(widget, "update_theme"):
                widget.update_theme(theme)
