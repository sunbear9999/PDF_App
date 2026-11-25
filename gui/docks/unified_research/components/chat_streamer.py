# gui/docks/unified_research/components/chat_streamer.py
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTextBrowser, QLabel
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCursor
from gui.docks.unified_research.components.note_bubble import NoteBubbleWidget

class ChatMessageWidget(QWidget):
    def __init__(self, sender_name, theme=None, is_user=False, app_context=None, parent=None):
        super().__init__(parent)
        self._app_context = app_context
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
        self.rag_context_button = None
        self._rag_context_audit = {}
        self._rag_context_dialog = None
        if not is_user:
            from gui.components.prompt_trace_button import PromptTraceButton
            self.trace_button = PromptTraceButton(theme=self.theme, app_context=app_context, parent=self)
            self.trace_button.hide()
            header_layout.addWidget(self.trace_button)
            self.rag_context_button = QPushButton("📚 View full RAG context")
            self.rag_context_button.setToolTip(
                "Inspect raw ranked chunks sent to the model and the next-best chunks omitted by the context limit"
            )
            self.rag_context_button.clicked.connect(self._show_rag_context)
            self.rag_context_button.hide()
            header_layout.addWidget(self.rag_context_button)
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

    def set_rag_context(self, audit):
        if not isinstance(audit, dict) or not audit.get("candidates"):
            return
        self._rag_context_audit = audit
        sent = int(audit.get("sent_count") or 0)
        total = int(
            audit.get("candidate_count")
            or len(audit.get("candidates") or [])
        )
        if self.rag_context_button:
            self.rag_context_button.setText(
                f"📚 View full RAG context ({sent}/{total} sent)"
            )
            self.rag_context_button.show()
        if self._rag_context_dialog is not None:
            try:
                self._rag_context_dialog.set_audit(audit)
            except RuntimeError:
                self._rag_context_dialog = None

    def apply_rag_context_highlights(self, audit):
        self.set_rag_context(audit)

    def _show_rag_context(self):
        if not self._rag_context_audit:
            return
        if self._rag_context_dialog is not None:
            try:
                self._rag_context_dialog.show()
                self._rag_context_dialog.raise_()
                self._rag_context_dialog.activateWindow()
                return
            except RuntimeError:
                self._rag_context_dialog = None
        from gui.docks.unified_research.components.rag_context_viewer import (
            RagContextViewerDialog,
        )
        dialog = RagContextViewerDialog(
            self._rag_context_audit,
            self.theme,
            app_context=self._app_context,
            parent=self,
        )
        self._rag_context_dialog = dialog
        dialog.destroyed.connect(
            lambda *_: setattr(self, "_rag_context_dialog", None)
        )
        dialog.show()

    def update_status(self, text):
        if hasattr(self, 'lbl_status'):
            self.lbl_status.setText(f"<i>🔄 {text}</i>")
            if self.theme:
                self.lbl_status.setStyleSheet(f"color: {self.theme.get('accent', '#b366ff')}; font-size: 12px; font-weight: bold;")
            self.lbl_status.show()
        
    def hide_status(self):
        if hasattr(self, 'lbl_status'):
            self.lbl_status.hide()

    def set_error_text(self, text):
        self.raw_buffer = text or "Unknown AI error."
        if self.is_user:
            self.user_text.setText(self.raw_buffer)
            return
        self.hide_status()
        if hasattr(self, "main_browser"):
            self.main_browser.show()
            self.main_browser.setMarkdown(f"**Error:** {self.raw_buffer}")
            if self.theme:
                self.main_browser.setStyleSheet(
                    f"color: {self.theme.get('error', '#ff6666')}; "
                    "background: transparent; border: none;"
                )
            self._resize_browser(self.main_browser)

    def append_chunk(self, chunk):
        self.raw_buffer += chunk

        if self.is_user:
            self.user_text.setText(self.raw_buffer)
            return

        self.hide_status()

        thought_part, main_part, has_thought = self._split_thought(self.raw_buffer)
        if has_thought:
            self.btn_thought.show()
            self.thought_browser.setMarkdown(thought_part.strip())
            self._resize_browser(self.thought_browser)
            self.main_browser.setMarkdown(main_part.strip())
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

    def add_manifest_status(self, status, text, detail=""):
        from gui.docks.unified_research.components.manifest_bubble import ManifestUpdateStatusWidget

        status_widget = ManifestUpdateStatusWidget(
            status,
            text,
            detail,
            self.theme,
            parent=self,
        )
        if hasattr(self, "main_browser") and not self.raw_buffer.strip():
            self.main_browser.hide()
        self.bubbles_layout.addWidget(status_widget)
        return status_widget

    def set_citations(self, items):
        small = getattr(self, '_small_citations', [])
        if len(small)+len(items) <= 4 and not hasattr(self, '_citation_collection'):
            for item in items:
                bubble = self.add_bubble(item['doc_name'], item['quote'], item['note'], item['source_meta'])
                small.append((item, bubble))
            self._small_citations = small
            return
        from gui.components.citation_collection import CitationCollection
        if not hasattr(self, '_citation_collection'):
            retained = []
            for item, bubble in small:
                item['draft'] = bubble.note_edit.toPlainText()
                retained.append(item)
                self.bubbles_layout.removeWidget(bubble)
                bubble.hide()
                bubble.deleteLater()
            self._small_citations = []
            self._citation_collection = CitationCollection(self._app_context, self)
            self.bubbles_layout.addWidget(self._citation_collection)
            self._citation_collection.set_items(retained+items)
        else:
            self._citation_collection._retain_draft()
            self._citation_collection.set_items(self._citation_collection.items+items)

    def add_bubble(self, doc_name, quote, note, source_meta=None):
        bubble = self._create_bubble(doc_name, quote, note, source_meta)
        self.bubbles_layout.addWidget(bubble)
        return bubble

    def _create_bubble(self, doc_name, quote, note, source_meta=None):
        app_context = self._app_context or getattr(self.window(), "app_context", None)
        bubble = NoteBubbleWidget(doc_name, quote, note, self.theme, parent=self, source_meta=source_meta,
                                  app_context=app_context)
        viewer = getattr(self.window(), "viewer", None)
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
        bubble.save_requested.connect(lambda q, n, d, meta=source_meta, b=bubble: self._save_bubble_note(q, n, d,
            {**meta, "suggested_text": b.current_note}))
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
            annot_manager = getattr(viewer, "annot_manager", None)
            if annot_manager and hasattr(annot_manager, "trigger_similar_context"):
                bubble.search_requested.connect(
                    lambda selected_quote: annot_manager.trigger_similar_context(selected_quote)
                )
        if hasattr(self, "main_browser") and not self.raw_buffer.strip():
            self.main_browser.hide()
        return bubble

    def _save_bubble_note(self, quote, note, doc_name, source_meta=None):
        from gui.components.dialogs.reference_note_dialog import open_reference_note
        meta = dict(source_meta or {})
        ctx = self._app_context or getattr(self.window(), "app_context", None)
        trace = getattr(self, "trace_button", None)
        open_reference_note(ctx, {**meta, "quote": quote, "doc_name": doc_name}, note,
            origin={"kind": "assistant", "trace_id": getattr(trace, "trace_id", None),
                    "step_id": meta.get("producer_step_id") or meta.get("step_id"),
                    "suggested_text": meta.get("suggested_text", note)}, parent=self)
    
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
        thought, answer, has_thought = self._split_thought(text)
        if has_thought:
            self.btn_thought.show()
            self.thought_browser.setMarkdown(thought.strip())
            self._resize_browser(self.thought_browser)
            self.main_browser.setMarkdown(answer.strip())
        else:
            self.main_browser.setMarkdown(text)
        self._resize_browser(self.main_browser)

    @staticmethod
    def _split_thought(text):
        raw = text or ""
        lower = raw.lower()
        start = lower.find("<think>")
        if start < 0:
            return "", raw, False
        content_start = start + len("<think>")
        end = lower.find("</think>", content_start)
        if end < 0:
            thought = raw[content_start:]
            main = raw[:start].strip()
            return thought, main, True
        thought = raw[content_start:end]
        main = (raw[:start] + raw[end + len("</think>"):]).strip()
        return thought, main, True

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
            if self.rag_context_button:
                self.rag_context_button.setStyleSheet(
                    f"QPushButton {{ background: transparent; color: {theme.get('accent', '#b366ff')}; "
                    f"border: 1px solid {theme.get('border', '#444')}; padding: 3px 7px; border-radius: 4px; }}"
                    f"QPushButton:hover {{ background: {theme.get('bg_panel', '#292a2d')}; }}"
                )
        
        for i in range(self.bubbles_layout.count()):
            widget = self.bubbles_layout.itemAt(i).widget()
            if isinstance(widget, NoteBubbleWidget):
                widget.update_theme(theme)
            elif hasattr(widget, "update_theme"):
                widget.update_theme(theme)
