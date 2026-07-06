"""
gui/components/audio_player.py

Audio is "video without a picture" — QMediaPlayer already plays audio-only
files identically to video (same decode/seek/transcript-timestamp machinery),
so AudioPlayer subclasses VideoPlayer rather than reimplementing playback.
The only difference: the QVideoWidget surface is hidden (nothing to render)
and replaced with a simple placeholder label, and source_opened events are
filtered on source_type == "audio" instead of "video".
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

from core.events.domains.document_events import DocumentEventPayload, SourceEvent
from gui.components.video_player import VideoPlayer


class AudioPlayer(VideoPlayer):
    def __init__(self, app_context=None, parent=None):
        super().__init__(app_context=app_context, parent=parent)
        self.video_widget.hide()
        placeholder = QLabel("🎧 Audio")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder.setMinimumHeight(120)
        placeholder.setStyleSheet("font-size: 24px; color: #9aa5b1;")
        layout = self.layout()
        layout.insertWidget(layout.indexOf(self.video_widget), placeholder)

    def _on_source_opened(self, event: SourceEvent, payload: DocumentEventPayload):
        if event == SourceEvent.OPENED and payload.source_type == "audio":
            context = payload.context or {}
            self.load_video(
                payload.path,
                payload.source_id,
                timestamp=payload.timestamp,
                transcript=context.get("transcript", {}),
                autoplay=bool(context.get("autoplay")),
            )
