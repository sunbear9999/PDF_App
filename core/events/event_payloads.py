"""
core/events/event_payloads.py

Single-import aggregation for all domain event types.
Import from here instead of from six separate domain files.

Example::

    from core.events.event_payloads import DocumentIntent, DocumentPayload, SIGNAL_CONTRACTS
"""
from __future__ import annotations

from core.events.domains.document_events import (
    DocumentIntent, DocumentPayload,
    DocumentEvent, DocumentEventPayload,
    AnnotationIntent, AnnotationPayload,
)
from core.events.domains.project_events import (
    ProjectIntent, ProjectPayload,
    ProjectEvent, ProjectEventPayload,
)
from core.events.domains.workspace_events import (
    WorkspaceIntent, WorkspacePayload,
    WorkspaceEvent, WorkspaceEventPayload,
)
from core.events.domains.tts_events import (
    TTSIntent, TTSPayload,
    TTSEvent, TTSEventPayload,
    TTSStatus, TTSStatusPayload,
)
from core.events.domains.ocr_events import (
    OCRIntent, OCRPayload,
    OCRStatus, OCRStatusPayload,
)
from core.events.domains.dictionary_events import (
    DictionaryIntent, DictionaryPayload,
    DictionaryEvent, DictionaryEventPayload,
)
from core.events.domains.citation_events import (
    CitationIntent, CitationPayload,
    CitationEvent, CitationEventPayload,
)
from core.events.domains.content_events import (
    EssayIntent, EssayPayload, EssayEvent, EssayEventPayload,
    IndexIntent, IndexPayload, IndexEvent, IndexEventPayload,
)
from core.events.domains.metadata_events import (
    NotesIntent, NotesPayload, NotesEvent, NotesEventPayload,
    TagIntent, TagPayload, TagEvent, TagEventPayload,
    PromptIntent, PromptPayload, PromptEvent, PromptEventPayload,
)

# Maps EventBus signal name → (intent/event enum, payload type)
SIGNAL_CONTRACTS: dict = {
    "document_action_requested": (DocumentIntent, DocumentPayload),
    "document_opened": (DocumentEvent, DocumentEventPayload),
    "annotation_action_requested": (AnnotationIntent, AnnotationPayload),
    "project_action_requested": (ProjectIntent, ProjectPayload),
    "project_loaded": (ProjectEvent, ProjectEventPayload),
    "workspace_action_requested": (WorkspaceIntent, WorkspacePayload),
    "workspace_loaded": (WorkspaceEvent, WorkspaceEventPayload),
    "tts_action_requested": (TTSIntent, TTSPayload),
    "tts_status_updated": (TTSStatus, TTSStatusPayload),
    "ocr_action_requested": (OCRIntent, OCRPayload),
    "ocr_status_updated": (OCRStatus, OCRStatusPayload),
    "dictionary_action_requested": (DictionaryIntent, DictionaryPayload),
    "dictionary_results_ready": (DictionaryEvent, DictionaryEventPayload),
    "citation_action_requested": (CitationIntent, CitationPayload),
    "citation_table_data_ready": (CitationEvent, CitationEventPayload),
    "notes_action_requested": (NotesIntent, NotesPayload),
    "notes_data_ready": (NotesEvent, NotesEventPayload),
    "tag_action_requested": (TagIntent, TagPayload),
    "prompt_action_requested": (PromptIntent, PromptPayload),
}

__all__ = [
    # Document
    "DocumentIntent", "DocumentPayload", "DocumentEvent", "DocumentEventPayload",
    "AnnotationIntent", "AnnotationPayload",
    # Project
    "ProjectIntent", "ProjectPayload", "ProjectEvent", "ProjectEventPayload",
    # Workspace
    "WorkspaceIntent", "WorkspacePayload", "WorkspaceEvent", "WorkspaceEventPayload",
    # TTS
    "TTSIntent", "TTSPayload", "TTSEvent", "TTSEventPayload",
    "TTSStatus", "TTSStatusPayload",
    # OCR
    "OCRIntent", "OCRPayload", "OCRStatus", "OCRStatusPayload",
    # Dictionary
    "DictionaryIntent", "DictionaryPayload", "DictionaryEvent", "DictionaryEventPayload",
    # Citation
    "CitationIntent", "CitationPayload", "CitationEvent", "CitationEventPayload",
    # Content
    "EssayIntent", "EssayPayload", "EssayEvent", "EssayEventPayload",
    "IndexIntent", "IndexPayload", "IndexEvent", "IndexEventPayload",
    # Metadata
    "NotesIntent", "NotesPayload", "NotesEvent", "NotesEventPayload",
    "TagIntent", "TagPayload", "TagEvent", "TagEventPayload",
    "PromptIntent", "PromptPayload", "PromptEvent", "PromptEventPayload",
    # Contracts
    "SIGNAL_CONTRACTS",
]
