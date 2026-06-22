from __future__ import annotations

import os
from typing import Any, Dict, List

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from core.events.domains.document_events import (
    DocumentEventPayload,
    SourceEvent,
    SourceIntent,
    SourcePayload,
    VideoEvent,
    VideoEventPayload,
)
from core.events.event_bus import EventBus


class _VideoTranscriptionSignals(QObject):
    progress = Signal(object)
    completed = Signal(object)
    failed = Signal(object)


class _VideoTranscriptionTask(QRunnable):
    def __init__(self, source_id: str, path: str, model_name: str = "small"):
        super().__init__()
        self.source_id = source_id
        self.path = path
        self.model_name = model_name
        self.signals = _VideoTranscriptionSignals()

    @Slot()
    def run(self):
        try:
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
            from faster_whisper import WhisperModel

            try:
                model = WhisperModel(
                    self.model_name,
                    device="auto",
                    compute_type="auto",
                    local_files_only=True,
                )
            except TypeError as exc:
                raise RuntimeError(
                    "This faster-whisper version does not support offline-only model loading. "
                    "Update faster-whisper or configure a local model path before transcribing."
                ) from exc
            transcribe_options = {
                "beam_size": 5,
                "best_of": 5,
                "temperature": 0.0,
                # VAD can incorrectly remove quiet or very short spoken lines.
                # Keep it off by default so repeated transcriptions favor completeness.
                "vad_filter": os.environ.get("PAPYRUS_WHISPER_VAD", "").lower() in {"1", "true", "yes"},
                "condition_on_previous_text": True,
            }
            try:
                segments_iter, info = model.transcribe(self.path, **transcribe_options)
            except TypeError:
                compatible = {
                    key: value
                    for key, value in transcribe_options.items()
                    if key in {"beam_size", "best_of", "temperature", "vad_filter"}
                }
                segments_iter, info = model.transcribe(self.path, **compatible)
            segments: List[Dict[str, Any]] = []
            for idx, segment in enumerate(segments_iter):
                text = (segment.text or "").strip()
                if not text:
                    continue
                segments.append({
                    "index": idx,
                    "start": float(segment.start or 0),
                    "end": float(segment.end or 0),
                    "text": text,
                    "avg_logprob": float(getattr(segment, "avg_logprob", 0.0) or 0.0),
                })
                self.signals.progress.emit({
                    "source_id": self.source_id,
                    "path": self.path,
                    "model": self.model_name,
                    "progress": float(segment.end or 0),
                })
            language = getattr(info, "language", "") or ""
            full_text = " ".join(seg["text"] for seg in segments).strip()
            self.signals.completed.emit({
                "source_id": self.source_id,
                "path": self.path,
                "model": self.model_name,
                "language": language,
                "segments": segments,
                "text": full_text,
            })
        except Exception as exc:
            self.signals.failed.emit({
                "source_id": self.source_id,
                "path": self.path,
                "model": self.model_name,
                "error": str(exc),
            })


class VideoTranscriptionService(QObject):
    def __init__(self, project_manager, llm_manager=None, parent=None):
        super().__init__(parent)
        self.pm = project_manager
        self.llm = llm_manager
        self.model_name = os.environ.get("PAPYRUS_WHISPER_MODEL", "small")
        self.bus = EventBus.get_instance()
        self._signals = []
        self.bus.source_added.connect(self._on_source_added)
        self.bus.source_removed.connect(self._on_source_removed)
        self.bus.source_renamed.connect(self._on_source_renamed)
        self.bus.source_action_requested.connect(self._on_source_intent)

    def _on_source_added(self, event: SourceEvent, payload: DocumentEventPayload):
        if event == SourceEvent.ADDED and payload.source_type == "video":
            self.start_transcription(payload.source_id, payload.path)

    def _on_source_removed(self, event: SourceEvent, payload: DocumentEventPayload):
        if event == SourceEvent.REMOVED and payload.source_type == "video" and self.llm:
            self.llm.remove_source_from_index(payload.source_id, fallback_path=payload.path)

    def _on_source_renamed(self, event: SourceEvent, payload: DocumentEventPayload):
        if event != SourceEvent.RENAMED or payload.source_type != "video" or not self.llm:
            return
        source_id = payload.new_source_id or payload.old_source_id
        self.llm.remove_source_from_index(source_id, fallback_path=payload.old_path)
        transcript = self.pm.get_video_transcript(source_id) if source_id else {}
        if transcript.get("segments"):
            try:
                self.llm.index_video_transcript(payload.new_path, source_id, transcript.get("segments"))
            except Exception as exc:
                print(f"[VideoTranscriptionService] Reindex after video rename failed: {exc}")

    def _on_source_intent(self, intent: SourceIntent, payload: SourcePayload):
        if intent == SourceIntent.TRANSCRIBE:
            path = payload.path or (self.pm.get_source_path(payload.source_id) if payload.source_id else "")
            source_id = payload.source_id
            if not source_id and path and hasattr(self.pm, "get_source_entity_by_path"):
                source = self.pm.get_source_entity_by_path(path)
                source_id = source.id if source else None
            self.start_transcription(source_id, path)

    def start_transcription(self, source_id: str, path: str):
        if not source_id or not path or not os.path.exists(path):
            return
        self.pm.save_video_transcript_status(source_id, path, "running", model=self.model_name)
        self.bus.video_status_updated.emit(
            VideoEvent.TRANSCRIPTION_STARTED,
            VideoEventPayload(path=path, source_id=source_id, status="running", model=self.model_name),
        )
        task = _VideoTranscriptionTask(source_id, path, self.model_name)
        self._signals.append(task.signals)
        task.signals.progress.connect(self._on_progress)
        task.signals.completed.connect(self._on_completed)
        task.signals.failed.connect(self._on_failed)
        QThreadPool.globalInstance().start(task)

    def _on_progress(self, result: dict):
        self.bus.video_status_updated.emit(
            VideoEvent.TRANSCRIPTION_PROGRESS,
            VideoEventPayload(
                path=result.get("path"),
                source_id=result.get("source_id"),
                status="running",
                model=result.get("model", "small"),
                progress=result.get("progress", 0),
            ),
        )

    def _on_completed(self, result: dict):
        source_id = result.get("source_id")
        path = result.get("path")
        segments = result.get("segments") or []
        text = result.get("text") or ""
        self.pm.save_video_transcript(
            source_id,
            path,
            segments,
            text,
            model=result.get("model", "small"),
            language=result.get("language", ""),
        )
        if self.llm and getattr(self.llm, "ai_enabled", False):
            try:
                self.llm.index_video_transcript(path, source_id, segments)
            except Exception as exc:
                print(f"[VideoTranscriptionService] Video transcript indexing failed: {exc}")
        self.bus.video_status_updated.emit(
            VideoEvent.TRANSCRIPTION_COMPLETED,
            VideoEventPayload(
                path=path,
                source_id=source_id,
                status="completed",
                model=result.get("model", "small"),
                language=result.get("language", ""),
                segments=segments,
                text=text,
            ),
        )
        self.bus.status_message_requested.emit(f"Finished transcribing '{os.path.basename(path)}'.", 5000)

    def _on_failed(self, result: dict):
        source_id = result.get("source_id")
        path = result.get("path")
        error = result.get("error") or "Unknown transcription error"
        lower_error = error.lower()
        if (
            "local" in lower_error
            or "hugging" in lower_error
            or "hf" in lower_error
            or "unable to open file" in lower_error
            or "model.bin" in lower_error
        ):
            error = (
                "Whisper model is not available locally. Papyrus will not download it automatically; "
                "install/cache the faster-whisper 'small' model locally or configure a local model path."
            )
        self.pm.save_video_transcript_status(source_id, path, "failed", model=result.get("model", "small"), error_text=error)
        self.bus.video_status_updated.emit(
            VideoEvent.TRANSCRIPTION_FAILED,
            VideoEventPayload(path=path, source_id=source_id, status="failed", model=result.get("model", "small"), error=error),
        )
        self.bus.status_message_requested.emit(f"Video transcription failed: {error}", 8000)
