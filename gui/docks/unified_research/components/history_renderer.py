from PySide6.QtCore import QTimer
import shiboken6

from core.engine.ui_payloads import build_ui_payloads, coerce_saved_payloads
from core.utils.json_utils import extract_json_from_tags, strip_tagged_block
from gui.docks.unified_research.components.chat_streamer import ChatMessageWidget


class ChatHistoryRenderer:
    """Replays persisted chat history through the same UI payload interface used live."""

    def __init__(self, tab, *, theme=None, app_context=None):
        self.tab = tab
        self.theme = theme or {}
        self.app_context = app_context

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
        payloads = self._sanitize_saved_payloads(payloads)
        if not payloads:
            payloads = build_ui_payloads(
                msg.get("ui_format", "live_stream"),
                msg.get("content", ""),
                trace_id=msg.get("trace_id"),
            )

        if payloads:
            if self._is_chat_message(payloads):
                self._render_chat_payloads(payloads)
            else:
                for payload in payloads:
                    self.tab.receive_ai_payload(dict(payload))
            return

        self._render_text_message("AI Agent", msg.get("content", ""), trace_id=msg.get("trace_id"))

    @staticmethod
    def _sanitize_saved_payloads(payloads):
        cleaned = []
        manifest_changes = None
        for payload in payloads:
            item = dict(payload)
            if item.get("type") in {"replace_stream_text", "stream_chunk"}:
                key = "text" if item.get("type") == "replace_stream_text" else "chunk"
                raw = item.get(key, "")
                success, changes = extract_json_from_tags(raw, "UPDATE_MANIFEST")
                if success and isinstance(changes, dict):
                    manifest_changes = changes
                item[key] = strip_tagged_block(raw, "UPDATE_MANIFEST")
            cleaned.append(item)
        if manifest_changes and not any(item.get("type") == "manifest_updated" for item in cleaned):
            manifest_payload = {
                "type": "manifest_updated",
                "text": "Project brief updated",
                "changes": manifest_changes,
            }
            hide_index = next(
                (index for index, item in enumerate(cleaned) if item.get("type") == "hide_status"),
                len(cleaned),
            )
            cleaned.insert(hide_index, manifest_payload)
        return cleaned

    @staticmethod
    def _is_chat_message(payloads):
        chat_types = {
            "replace_stream_text", "citation_cards", "prompt_trace_available",
            "hide_status", "manifest_updated", "status", "stream_chunk",
        }
        return any(payload.get("type") in {"replace_stream_text", "citation_cards", "stream_chunk", "manifest_updated"}
                   for payload in payloads) and all(payload.get("type") in chat_types for payload in payloads)

    def _render_chat_payloads(self, payloads):
        """Keep text, trace controls, notices, and note bubbles in one turn widget."""
        widget = ChatMessageWidget("AI Agent", theme=self.theme, app_context=self.app_context)
        self.tab.receive_ai_widget(widget)
        self.tab._active_stream_widget = widget
        for payload in payloads:
            self.tab.receive_ai_payload(dict(payload))
        if hasattr(widget, "hide_status"):
            widget.hide_status()
        self.tab._active_stream_widget = None

    def scroll_to_bottom(self):
        if hasattr(self.tab, "scroll_area"):
            QTimer.singleShot(50, self._scroll_if_alive)

    def _scroll_if_alive(self):
        try:
            if not shiboken6.isValid(self.tab) or not shiboken6.isValid(self.tab.scroll_area):
                return
            scrollbar = self.tab.scroll_area.verticalScrollBar()
            if shiboken6.isValid(scrollbar):
                scrollbar.setValue(scrollbar.maximum())
        except (AttributeError, RuntimeError):
            return

    def _render_text_message(self, sender, content, *, is_user=False, trace_id=None):
        widget = ChatMessageWidget(sender, theme=self.theme, is_user=is_user, app_context=self.app_context)
        widget.append_chunk(content or "")
        if not is_user and trace_id and hasattr(widget, "set_prompt_trace"):
            widget.set_prompt_trace(trace_id)
        if not is_user and hasattr(widget, "hide_status"):
            widget.hide_status()
        self.tab.receive_ai_widget(widget)
