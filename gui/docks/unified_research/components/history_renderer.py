from PySide6.QtCore import QTimer

from core.engine.ui_payloads import build_ui_payloads, coerce_saved_payloads
from gui.docks.unified_research.components.chat_streamer import ChatMessageWidget


class ChatHistoryRenderer:
    """Replays persisted chat history through the same UI payload interface used live."""

    def __init__(self, tab, *, theme=None):
        self.tab = tab
        self.theme = theme or {}

    def render(self, history):
        for msg in history:
            self.render_message(msg)
        self.scroll_to_bottom()

    def render_message(self, msg):
        role = msg.get("role")
        is_user = role == "user"
        if is_user:
            self._render_text_message("You", msg.get("content", ""), is_user=True)
            return

        payloads = coerce_saved_payloads(msg.get("ui_payload"))
        if not payloads:
            payloads = build_ui_payloads(
                msg.get("ui_format", "live_stream"),
                msg.get("content", ""),
                trace_id=msg.get("trace_id"),
            )

        if payloads:
            for payload in payloads:
                self.tab.receive_ai_payload(dict(payload))
            return

        self._render_text_message("AI Agent", msg.get("content", ""), trace_id=msg.get("trace_id"))

    def scroll_to_bottom(self):
        if hasattr(self.tab, "scroll_area"):
            scrollbar = self.tab.scroll_area.verticalScrollBar()
            QTimer.singleShot(50, lambda: scrollbar.setValue(scrollbar.maximum()))

    def _render_text_message(self, sender, content, *, is_user=False, trace_id=None):
        widget = ChatMessageWidget(sender, theme=self.theme, is_user=is_user)
        widget.append_chunk(content or "")
        if not is_user and trace_id and hasattr(widget, "set_prompt_trace"):
            widget.set_prompt_trace(trace_id)
        if not is_user and hasattr(widget, "hide_status"):
            widget.hide_status()
        self.tab.receive_ai_widget(widget)
