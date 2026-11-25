"""Small typed notifications for universal artifact review changes."""
from dataclasses import dataclass, field
from enum import Enum


class IntelligenceEvent(Enum):
    ARTIFACT_REVIEWED = "artifact_reviewed"
    KNOWLEDGE_REVIEWED = "knowledge_reviewed"
    PROCESSING_POLICY_CHANGED = "processing_policy_changed"
    MODEL_DEPENDENCY_CHANGED = "model_dependency_changed"
    LINKED_NOTE_CHANGED = "linked_note_changed"
    ARTIFACT_SCHEMA_CHANGED = 'artifact_schema_changed'
    PROJECTION_VIEW_CHANGED = 'projection_view_changed'


@dataclass(frozen=True)
class ProjectionViewPayload:
    view_id: str


@dataclass(frozen=True)
class ArtifactSchemaPayload:
    artifact_type: str
    generation: int


@dataclass(frozen=True)
class LinkedNotePayload:
    annotation_id: str
    source_id: str = ""


@dataclass(frozen=True)
class ProcessingStatePayload:
    source_id: str = ""
    dependency_id: str = ""


@dataclass(frozen=True)
class ArtifactReviewPayload:
    artifact_id: str
    source_id: str = ""
    revision: int = 0


class IntelligenceIntent(Enum):
    REVIEW_DECISION = "review_decision"


@dataclass(frozen=True)
class ReviewDecisionPayload:
    artifact_id: str
    command: str = "entity_merge"
    values: dict = field(default_factory=dict)


@dataclass(frozen=True)
class KnowledgeReviewPayload:
    resource_id: str
    source_id: str = ""
    decision_id: str = ""
