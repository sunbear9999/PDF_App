"""
Audio is "video without a picture" — VideoTranscriptionService should treat
source_type == "audio" identically to "video" for auto-transcription
trigger/cleanup/reindex, without touching the underlying Whisper pipeline.
"""
from unittest.mock import MagicMock, patch

from core.events.domains.document_events import DocumentEventPayload, SourceEvent
from core.services.document.video_transcription_service import VideoTranscriptionService


def _service():
    pm = MagicMock()
    llm = MagicMock()
    return VideoTranscriptionService(pm, llm)


def test_source_added_triggers_transcription_for_video_and_audio():
    service = _service()
    with patch.object(service, "start_transcription") as mock_start:
        service._on_source_added(SourceEvent.ADDED, DocumentEventPayload(
            path="/tmp/clip.mp4", source_id="s1", source_type="video",
        ))
        service._on_source_added(SourceEvent.ADDED, DocumentEventPayload(
            path="/tmp/clip.mp3", source_id="s2", source_type="audio",
        ))
    assert [c.args for c in mock_start.call_args_list] == [
        ("s1", "/tmp/clip.mp4"),
        ("s2", "/tmp/clip.mp3"),
    ]


def test_source_added_does_not_trigger_for_other_types():
    service = _service()
    with patch.object(service, "start_transcription") as mock_start:
        service._on_source_added(SourceEvent.ADDED, DocumentEventPayload(
            path="/tmp/paper.pdf", source_id="s3", source_type="pdf",
        ))
        service._on_source_added(SourceEvent.ADDED, DocumentEventPayload(
            path="/tmp/notes.txt", source_id="s4", source_type="txt",
        ))
    mock_start.assert_not_called()


def test_source_removed_cleans_up_index_for_audio():
    service = _service()
    service._on_source_removed(SourceEvent.REMOVED, DocumentEventPayload(
        path="/tmp/clip.mp3", source_id="s2", source_type="audio",
    ))
    service.llm.remove_source_from_index.assert_called_once_with("s2", fallback_path="/tmp/clip.mp3")


def test_source_renamed_reindexes_audio_transcript():
    service = _service()
    service.pm.get_video_transcript.return_value = {"segments": [{"text": "hi", "start": 0}]}
    service._on_source_renamed(SourceEvent.RENAMED, DocumentEventPayload(
        old_path="/tmp/old.mp3", new_path="/tmp/new.mp3",
        old_source_id="s2", new_source_id="s2", source_type="audio",
    ))
    service.llm.remove_source_from_index.assert_called_once_with("s2", fallback_path="/tmp/old.mp3")
    service.llm.index_video_transcript.assert_called_once()
