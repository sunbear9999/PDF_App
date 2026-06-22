from __future__ import annotations

import os
import html

from PySide6.QtCore import QElapsedTimer, QEvent, QTimer, QUrl, Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.events.domains.document_events import DocumentEvent, DocumentEventPayload, SourceEvent, SourceIntent, SourcePayload, VideoEvent
from core.events.event_bus import EventBus

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PySide6.QtMultimediaWidgets import QVideoWidget
except Exception:  # pragma: no cover - depends on platform multimedia packages
    QAudioOutput = None
    QMediaPlayer = None
    QVideoWidget = None


class VideoPlayer(QWidget):
    def __init__(self, app_context=None, parent=None):
        super().__init__(parent)
        self._ctx = app_context
        self.bus = EventBus.get_instance()
        self.video_path = ""
        self.source_id = ""
        self.segments = []
        self._duration = 0
        self._scrubbing = False
        self._controls_hover = False
        self._captions_enabled = True
        self._timestamps_visible = True
        self._active_segment_index = -1
        self._transcription_timer = QElapsedTimer()
        self._shortcuts_registered = False
        self._build_ui()
        self.bus.source_opened.connect(self._on_source_opened)
        self.bus.video_status_updated.connect(self._on_video_status)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        if not QMediaPlayer or not QVideoWidget:
            self.player = None
            self.video_widget = QLabel("Qt multimedia support is not available.")
            self.video_widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.video_widget.setMinimumHeight(120)
            layout.addWidget(self.video_widget, 0)
        else:
            self.player = QMediaPlayer(self)
            self.audio = QAudioOutput(self)
            self.player.setAudioOutput(self.audio)
            self.video_widget = QVideoWidget(self)
            self.player.setVideoOutput(self.video_widget)
            self.player.positionChanged.connect(self._on_position_changed)
            self.player.durationChanged.connect(self._on_duration_changed)
            self.video_widget.setMinimumHeight(120)
            layout.addWidget(self.video_widget, 0)

        self.video_widget.setMouseTracking(True)
        self.video_widget.installEventFilter(self)

        self.scrub_frame = QFrame(self)
        self.scrub_frame.setObjectName("VideoScrubFrame")
        self.scrub_frame.setMouseTracking(True)
        self.scrub_frame.installEventFilter(self)
        scrub_layout = QHBoxLayout(self.scrub_frame)
        scrub_layout.setContentsMargins(10, 4, 10, 6)
        scrub_layout.setSpacing(8)
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setRange(0, 0)
        self.seek.setTracking(True)
        self.time_lbl = QLabel("00:00 / 00:00")
        self.time_lbl.setMinimumWidth(104)
        self.time_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        scrub_layout.addWidget(self.seek, 1)
        scrub_layout.addWidget(self.time_lbl)
        self.scrub_frame.setStyleSheet(
            "QFrame#VideoScrubFrame { background: rgba(15, 23, 32, 210); }"
            "QSlider::groove:horizontal { height: 5px; background: #3b4754; border-radius: 2px; }"
            "QSlider::handle:horizontal { width: 14px; margin: -5px 0; border-radius: 7px; background: #64b5f6; }"
            "QLabel { color: #f5f5f5; }"
        )
        self.scrub_frame.hide()
        layout.addWidget(self.scrub_frame)

        self.caption_label = QLabel("")
        self.caption_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.caption_label.setWordWrap(True)
        self.caption_label.setStyleSheet("background: rgba(0,0,0,180); color: white; padding: 8px;")
        layout.addWidget(self.caption_label)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("background: #263238; color: #f5f5f5; padding: 6px 10px;")
        self.status_label.hide()
        layout.addWidget(self.status_label)

        controls = QFrame(self)
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(8, 6, 8, 6)
        controls_layout.setSpacing(6)

        self.play_btn = QPushButton("Play")
        self.back_btn = QPushButton("-10s")
        self.forward_btn = QPushButton("+10s")
        self.transcript_btn = QPushButton("Transcript")
        self.transcript_btn.setCheckable(True)
        self.timestamps_btn = QPushButton("Timestamps")
        self.timestamps_btn.setCheckable(True)
        self.timestamps_btn.setChecked(True)
        self.highlight_btn = QPushButton("Highlight")
        self.caption_btn = QPushButton("CC On")
        self.caption_btn.setCheckable(True)
        self.caption_btn.setChecked(True)
        self.speed_combo = QComboBox()
        for label, rate in [("0.5x", 0.5), ("0.75x", 0.75), ("1x", 1.0), ("1.25x", 1.25), ("1.5x", 1.5), ("2x", 2.0)]:
            self.speed_combo.addItem(label, rate)
        self.speed_combo.setCurrentIndex(2)
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(80)
        self.volume.setFixedWidth(90)

        controls_layout.addWidget(self.play_btn)
        controls_layout.addWidget(self.back_btn)
        controls_layout.addWidget(self.forward_btn)
        controls_layout.addWidget(self.transcript_btn)
        controls_layout.addWidget(self.timestamps_btn)
        controls_layout.addWidget(self.highlight_btn)
        controls_layout.addWidget(self.caption_btn)
        controls_layout.addWidget(self.speed_combo)
        controls_layout.addWidget(self.volume)
        self._inject_plugin_buttons(controls_layout)
        layout.addWidget(controls)

        self.transcript_view = QTextEdit(self)
        self.transcript_view.setReadOnly(True)
        self.transcript_view.setAcceptRichText(True)
        self.transcript_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.transcript_view.customContextMenuRequested.connect(self._show_transcript_context_menu)
        self.transcript_view.setMinimumHeight(80)
        self.transcript_view.setVisible(False)
        self.transcript_view.setStyleSheet("QTextEdit { background: #111; color: #f5f5f5; border: none; padding: 8px; }")
        layout.addWidget(self.transcript_view)
        layout.addStretch(1)

        self.play_btn.clicked.connect(self.toggle_playback)
        self.back_btn.clicked.connect(lambda: self.seek_relative(-10000))
        self.forward_btn.clicked.connect(lambda: self.seek_relative(10000))
        self.transcript_btn.toggled.connect(self._toggle_transcript)
        self.timestamps_btn.toggled.connect(self._toggle_timestamps)
        self.highlight_btn.clicked.connect(self.save_note_at_current_time)
        self.seek.sliderPressed.connect(self._seek_pressed)
        self.seek.sliderMoved.connect(self._preview_seek)
        self.seek.sliderReleased.connect(self._seek_released)
        self.caption_btn.toggled.connect(self._set_captions)
        self.speed_combo.currentIndexChanged.connect(self._set_speed)
        self.volume.valueChanged.connect(self._set_volume)

    def _inject_plugin_buttons(self, layout):
        registry = getattr(self._ctx, "action_registry", None) if self._ctx else None
        if not registry:
            return
        for spec in registry.iter_mount("toolbar:video_player"):
            label = f"{spec.icon} {spec.label}".strip() if spec.icon else spec.label
            btn = QPushButton(label)
            if spec.tooltip:
                btn.setToolTip(spec.tooltip)
            if spec.callback:
                btn.clicked.connect(lambda checked=False, cb=spec.callback: cb({"source_id": self.source_id, "path": self.video_path}))
            layout.addWidget(btn)

    def eventFilter(self, watched, event):
        if watched is getattr(self, "video_widget", None) or watched is getattr(self, "scrub_frame", None):
            if event.type() in {QEvent.Type.Enter, QEvent.Type.MouseMove}:
                self._set_scrub_visible(True)
            elif event.type() == QEvent.Type.Leave and not self._scrubbing:
                QTimer.singleShot(220, self._hide_scrub_if_unhovered)
        return super().eventFilter(watched, event)

    def _set_scrub_visible(self, visible: bool):
        if hasattr(self, "scrub_frame"):
            self.scrub_frame.setVisible(bool(visible))

    def _hide_scrub_if_unhovered(self):
        if self._scrubbing or not hasattr(self, "scrub_frame"):
            return
        cursor_pos = self.mapFromGlobal(QCursor.pos())
        if self.video_widget.geometry().contains(cursor_pos) or self.scrub_frame.geometry().contains(cursor_pos):
            return
        self.scrub_frame.hide()

    def register_shortcuts(self):
        """Bind video actions to the app-wide shortcut registry."""
        if self._shortcuts_registered:
            return
        registry = getattr(self._ctx, "keybinding_registry", None) if self._ctx else None
        if not registry:
            return

        bindings = {
            "video.play_pause": self._shortcut_toggle_playback,
            "video.back_10s": lambda: self._shortcut_seek_relative(-10000),
            "video.forward_10s": lambda: self._shortcut_seek_relative(10000),
            "video.toggle_captions": self._shortcut_toggle_captions,
            "video.toggle_transcript": self._shortcut_toggle_transcript,
            "video.toggle_timestamps": self._shortcut_toggle_timestamps,
            "video.save_note": self._shortcut_save_note,
        }
        for action_id, callback in bindings.items():
            registry.bind(action_id, callback, self, Qt.ShortcutContext.WindowShortcut)
        self._shortcuts_registered = True

    def _shortcuts_active(self):
        return self.isVisible() and bool(self.video_path)

    def _shortcut_toggle_playback(self):
        if self._shortcuts_active():
            self.toggle_playback()

    def _shortcut_seek_relative(self, delta_ms):
        if self._shortcuts_active():
            self.seek_relative(delta_ms)

    def _shortcut_toggle_captions(self):
        if self._shortcuts_active():
            self.caption_btn.setChecked(not self.caption_btn.isChecked())

    def _shortcut_toggle_transcript(self):
        if self._shortcuts_active():
            self.transcript_btn.setChecked(not self.transcript_btn.isChecked())

    def _shortcut_toggle_timestamps(self):
        if self._shortcuts_active():
            self.timestamps_btn.setChecked(not self.timestamps_btn.isChecked())

    def _shortcut_save_note(self):
        if self._shortcuts_active():
            self.save_note_at_current_time()

    def _on_source_opened(self, event: SourceEvent, payload: DocumentEventPayload):
        if event == SourceEvent.OPENED and payload.source_type == "video":
            context = payload.context or {}
            self.load_video(
                payload.path,
                payload.source_id,
                timestamp=payload.timestamp,
                transcript=context.get("transcript", {}),
                autoplay=bool(context.get("autoplay")),
            )

    def _on_video_status(self, event: VideoEvent, payload):
        if payload.source_id != self.source_id:
            return
        if event == VideoEvent.TRANSCRIPTION_STARTED:
            self._transcription_timer.restart()
            self._set_status(self._estimate_status())
        elif event == VideoEvent.TRANSCRIPTION_PROGRESS:
            self._set_status(self._estimate_status(progress_seconds=payload.progress))
        elif event == VideoEvent.TRANSCRIPTION_COMPLETED:
            self.segments = payload.segments or []
            self.caption_label.setText("")
            self._render_transcript()
            self._set_status("Transcription complete. Captions and video search are ready.", transient=False)
        elif event == VideoEvent.TRANSCRIPTION_FAILED:
            self._set_status(f"Captions unavailable: {payload.error or 'transcription failed'}", error=True)

    def load_video(self, path, source_id=None, timestamp=None, transcript=None, autoplay=False):
        self.video_path = path or ""
        self.source_id = source_id or ""
        transcript = transcript or {}
        self.segments = transcript.get("segments") or []
        self._active_segment_index = -1
        self._render_transcript()
        status = transcript.get("status", "")
        if status == "running":
            self._set_status(self._estimate_status())
        elif status == "failed":
            self._set_status(f"Captions unavailable: {transcript.get('error') or 'transcription failed'}", error=True)
        elif status == "completed":
            self._set_status("Transcription complete. Captions and video search are ready.", transient=False)
        else:
            self._set_status("")
        if self.player and path:
            self.player.setSource(QUrl.fromLocalFile(path))
            self._set_volume(self.volume.value())
            if timestamp is not None:
                self.player.setPosition(int(float(timestamp) * 1000))
            if autoplay:
                QTimer.singleShot(0, self.player.play)
                self.play_btn.setText("Pause")
            else:
                self.play_btn.setText("Play")
        elif path:
            self.caption_label.setText(os.path.basename(path))

    def toggle_playback(self):
        if not self.player:
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.play_btn.setText("Play")
        else:
            self.player.play()
            self.play_btn.setText("Pause")

    def seek_relative(self, delta_ms):
        if self.player:
            self.player.setPosition(max(0, self.player.position() + delta_ms))

    def seek_to_timestamp(self, seconds):
        if self.player:
            self.player.setPosition(max(0, int(float(seconds or 0) * 1000)))
            self.player.play()
            self.play_btn.setText("Pause")

    def _seek_pressed(self):
        self._scrubbing = True
        self._set_scrub_visible(True)

    def _preview_seek(self, position):
        self._update_time_label(position)
        self._update_caption(position / 1000.0)
        if self.player:
            self.player.setPosition(position)

    def _seek_released(self):
        if self.player:
            self.player.setPosition(self.seek.value())
        self._scrubbing = False
        QTimer.singleShot(300, self._hide_scrub_if_unhovered)

    def _set_captions(self, enabled):
        self._captions_enabled = bool(enabled)
        self.caption_btn.setText("CC On" if enabled else "CC Off")
        if not enabled:
            self.caption_label.setText("")

    def _set_speed(self):
        if self.player:
            self.player.setPlaybackRate(float(self.speed_combo.currentData() or 1.0))

    def _set_volume(self, value):
        if getattr(self, "audio", None):
            self.audio.setVolume(max(0.0, min(1.0, value / 100.0)))

    def _on_duration_changed(self, duration):
        self._duration = duration
        self.seek.setRange(0, duration)
        self._update_time_label(self.player.position() if self.player else 0)
        if self.status_label.isVisible() and "Transcribing" in self.status_label.text():
            self._set_status(self._estimate_status())
        self._resize_video_surface()

    def _on_position_changed(self, position):
        if not self._scrubbing:
            self.seek.setValue(position)
        self._update_time_label(position)
        self._update_caption(position / 1000.0)

    def _update_caption(self, seconds):
        if not self._captions_enabled:
            return
        active = None
        active_idx = -1
        for idx, seg in enumerate(self.segments):
            if float(seg.get("start", 0)) <= seconds <= float(seg.get("end", 0)):
                active = seg
                active_idx = idx
                break
        self.caption_label.setText(active.get("text", "") if active else "")
        if active_idx != self._active_segment_index:
            self._active_segment_index = active_idx
            if self.transcript_view.isVisible():
                self._render_transcript()

    def _update_time_label(self, position):
        self.time_lbl.setText(f"{self._fmt(position)} / {self._fmt(self._duration)}")

    def _fmt(self, ms):
        seconds = int((ms or 0) / 1000)
        return f"{seconds // 60:02d}:{seconds % 60:02d}"

    def _toggle_transcript(self, visible):
        self.transcript_view.setVisible(bool(visible))
        if visible:
            self._render_transcript()

    def _toggle_timestamps(self, visible):
        self._timestamps_visible = bool(visible)
        self._render_transcript()

    def _show_transcript_context_menu(self, pos):
        menu = self.transcript_view.createStandardContextMenu()
        if self.transcript_view.textCursor().hasSelection():
            menu.addSeparator()
            action = menu.addAction("Highlight Selected Transcript Text")
            action.triggered.connect(self.save_note_at_current_time)
        menu.exec(self.transcript_view.mapToGlobal(pos))

    def _render_transcript(self):
        if not hasattr(self, "transcript_view"):
            return
        if not self.segments:
            self.transcript_view.setHtml("<i>No transcript is available yet.</i>")
            return
        parts = []
        for idx, seg in enumerate(self.segments):
            text = html.escape(str(seg.get("text", "")).strip())
            stamp = f"{self._fmt_seconds(float(seg.get('start', 0)))} " if self._timestamps_visible else ""
            if idx == self._active_segment_index:
                parts.append(f"<p style='background:#31424a; margin:2px 0; padding:4px;'><b>{html.escape(stamp)}</b>{text}</p>")
            else:
                parts.append(f"<p style='margin:2px 0; padding:4px;'><span style='color:#9fb0b8;'>{html.escape(stamp)}</span>{text}</p>")
        scrollbar = self.transcript_view.verticalScrollBar()
        old_value = scrollbar.value()
        self.transcript_view.setHtml("".join(parts))
        if self._active_segment_index >= 0:
            cursor = self.transcript_view.document().find(str(self.segments[self._active_segment_index].get("text", "")).strip()[:40])
            if not cursor.isNull():
                self.transcript_view.setTextCursor(cursor)
                self.transcript_view.ensureCursorVisible()
        elif old_value:
            scrollbar.setValue(old_value)

    def _current_segment(self):
        seconds = (self.player.position() / 1000.0) if self.player else 0
        return next((seg for seg in self.segments if float(seg.get("start", 0)) <= seconds <= float(seg.get("end", 0))), None)

    def save_note_at_current_time(self):
        selected = self.transcript_view.textCursor().selectedText().strip()
        selected = " ".join(selected.split())
        segment = self._current_segment()
        quote = selected or (segment or {}).get("text", "").strip()
        if not quote:
            self._set_status("No transcript text is available to save at this timestamp.", error=True)
            return
        timestamp = float((segment or {}).get("start", 0))
        if self.player:
            timestamp = self.player.position() / 1000.0
        self.save_quote_note(quote, "", timestamp=timestamp)

    def save_quote_note(self, quote, note="", timestamp=0, path="", source_id=""):
        quote = (quote or "").strip()
        if not quote:
            self._set_status("No transcript text is available to save.", error=True)
            return
        path = path or self.video_path
        source_id = source_id or self.source_id
        timestamp = float(timestamp or 0)
        annot_id = f"VideoNote|{source_id}|{int(timestamp * 1000)}"
        self.bus.highlight_created.emit(
            DocumentEvent.HIGHLIGHT_CREATED,
            DocumentEventPayload(highlight_data={
                "id": annot_id,
                "subject": quote,
                "content": note or "",
                "pdf_path": path,
                "page_num": int(timestamp),
                "rect_coords": f"timestamp:{timestamp:.3f}",
                "color": "#b366ff",
            }),
        )
        self._set_status(f"Saved video note at {self._fmt_seconds(timestamp)}.", transient=True)

    def _set_status(self, text, error=False, transient=False):
        if not text:
            self.status_label.hide()
            return
        color = "#5b1f1f" if error else "#263238"
        self.status_label.setStyleSheet(f"background: {color}; color: #f5f5f5; padding: 6px 10px;")
        self.status_label.setText(text)
        self.status_label.show()

    def _estimate_status(self, progress_seconds=None):
        duration_seconds = self._duration / 1000.0 if self._duration else 0
        if progress_seconds and duration_seconds and progress_seconds > 1:
            elapsed = max(1.0, self._transcription_timer.elapsed() / 1000.0)
            ratio = min(0.98, max(0.01, float(progress_seconds) / duration_seconds))
            remaining = int(max(0, elapsed * (1 - ratio) / ratio))
            return f"Transcribing captions... about {self._fmt_seconds(remaining)} remaining."
        if duration_seconds:
            rough = int(max(30, duration_seconds * 0.8))
            return f"Transcribing captions... rough estimate {self._fmt_seconds(rough)}."
        return "Transcribing captions..."

    def _fmt_seconds(self, seconds):
        seconds = int(max(0, seconds))
        if seconds < 60:
            return f"{seconds}s"
        return f"{seconds // 60}m {seconds % 60:02d}s"

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._resize_video_surface()

    def _resize_video_surface(self):
        if not hasattr(self, "video_widget") or self.width() <= 0:
            return
        available = max(120, self.height() - 130)
        target = int(self.width() * 9 / 16)
        self.video_widget.setFixedHeight(max(120, min(target, available)))

    def jump_to_source(self, doc_name, quote):
        if self.video_path:
            self.bus.source_action_requested.emit(
                SourceIntent.OPEN,
                SourcePayload(path=self.video_path, source_id=self.source_id, source_type="video"),
            )
