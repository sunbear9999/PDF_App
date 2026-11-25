"""Shared models for Papyrus intelligence indexing and retrieval."""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class ProcessingMode(str, Enum):
    MINIMAL = "minimal"
    BALANCED = "balanced"
    DEEP = "deep"
    MANUAL = "manual"


class ProcessingStage(str, Enum):
    SOURCE_REGISTERED = "source_registered"
    PARSED_BLOCKS = "parsed_blocks"
    SECTIONS = "sections"
    TRANSCRIPT = "transcript"
    FULL_TEXT_INDEX = "full_text_index"
    EMBEDDINGS = "embeddings"
    ENTITIES = "entities"
    DATES = "dates"
    CITATIONS = "citations"
    TABLES = "tables"
    FIGURES = "figures"
    SUMMARIES = "summaries"
    CLAIMS = "claims"
    GRAPH_SEEDS = "graph_seeds"
    ENTITY_LINKING = "entity_linking"
    CROSS_REFERENCES = "cross_references"
    ABBREVIATIONS = "abbreviations"


class StageStatus(str, Enum):
    NOT_RUN = "not_run"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
    STALE = "stale"
    DISABLED = "disabled"
    BLOCKED = "blocked"        # waiting for a prerequisite (transcript, model, dependency)
    CANCELLED = "cancelled"


class ArtifactReviewState(str, Enum):
    UNREVIEWED = "unreviewed"
    VERIFIED = "verified"
    CORRECTED = "corrected"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class StageResourceClass(str, Enum):
    DETERMINISTIC = "deterministic"
    ONNX = "onnx"
    GENERATIVE = "generative"


class ModelRole(str, Enum):
    MAIN_ANSWER = "main_answer_model"
    FAST_WORKER = "fast_worker_model"
    EMBEDDING = "embedding_model"
    RERANKER = "reranker_model"
    VISION = "vision_model"
    TRANSCRIPTION = "transcription_model"
    EXTRACTION_REPAIR = "extraction_repair_model"


class GraphDensityProfile(str, Enum):
    """User-facing graph detail presets with stable persisted values."""

    FOCUSED = "focused"
    STANDARD = "standard"
    EXHAUSTIVE = "exhaustive"


@dataclass(frozen=True)
class GraphQualityContract:
    """Typed quality gate shared by built-in, plugin, and saved graph modes."""

    schema_version: int = 1
    density: str = GraphDensityProfile.STANDARD.value
    node_types: List[str] = field(default_factory=lambda: [
        "methodology", "claim", "reasoning", "evidence",
        "counterclaim", "limitation",
    ])
    relation_types: List[str] = field(default_factory=lambda: [
        "supports", "reasons", "refutes", "contradicts",
        "critiques", "part_of",
    ])
    root_roles: List[str] = field(default_factory=lambda: ["methodology", "claim"])
    required_roles: List[str] = field(default_factory=lambda: [
        "claim", "reasoning", "evidence",
    ])
    min_branches: int = 4
    min_sections: int = 4
    min_evidence_per_branch: int = 1
    min_proposition_nodes: int = 20
    max_proposition_nodes: int = 40
    min_evidence_nodes: int = 8
    max_evidence_nodes: int = 24
    max_nodes: int = 64
    max_relations: int = 96
    requires_exact_quotes: bool = True
    require_connected: bool = True
    allow_repair_call: bool = True

    @classmethod
    def from_value(cls, value: Any) -> "GraphQualityContract":
        if isinstance(value, cls):
            return value
        raw = dict(value or {}) if isinstance(value, dict) else {}
        density = str(raw.get("density") or GraphDensityProfile.STANDARD.value)
        defaults = cls.for_density(density)
        aliases = {
            "node_types": raw.get("node_types") or defaults.node_types,
            "relation_types": raw.get("relation_types") or defaults.relation_types,
            "max_nodes": raw.get("max_nodes") or defaults.max_nodes,
            "max_relations": raw.get("max_relations") or defaults.max_relations,
        }
        values = {
            key: raw.get(key, getattr(defaults, key))
            for key in cls.__dataclass_fields__
        }
        values.update(aliases)
        return cls(**values)

    @classmethod
    def for_density(cls, density: str) -> "GraphQualityContract":
        density = str(density or GraphDensityProfile.STANDARD.value).lower()
        if density == GraphDensityProfile.FOCUSED.value:
            return cls(
                density=density, min_branches=3, min_sections=3,
                min_proposition_nodes=12, max_proposition_nodes=24,
                min_evidence_nodes=6, max_evidence_nodes=16,
                max_nodes=40, max_relations=60,
            )
        if density == GraphDensityProfile.EXHAUSTIVE.value:
            return cls(
                density=density, min_branches=6, min_sections=6,
                min_proposition_nodes=40, max_proposition_nodes=80,
                min_evidence_nodes=16, max_evidence_nodes=40,
                max_nodes=120, max_relations=180,
            )
        return cls()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ArtifactRecord:
    artifact_id: str
    source_id: Optional[str] = None
    source_path: Optional[str] = None
    source_type: Optional[str] = None
    artifact_type: str = ""
    storage_ref_type: Optional[str] = None
    storage_ref_id: Optional[str] = None
    parent_artifact_id: Optional[str] = None
    exact_text: Optional[str] = None
    normalized_text: Optional[str] = None
    normalized_key: Optional[str] = None
    locator: Dict[str, Any] = field(default_factory=dict)
    section_path: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    confidence: Optional[float] = None
    created_by: str = "core"
    extractor_name: Optional[str] = None
    extractor_version: Optional[str] = None
    model_role: Optional[str] = None
    model_name: Optional[str] = None
    input_hash: Optional[str] = None
    payload_hash: Optional[str] = None
    status: str = StageStatus.COMPLETE.value
    review_state: str = ArtifactReviewState.UNREVIEWED.value
    revision: int = 1
    supersedes_artifact_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @staticmethod
    def stable_id(
        source_id: str,
        artifact_type: str,
        storage_ref_type: str,
        storage_ref_id: str,
    ) -> str:
        raw = "|".join([
            source_id or "",
            artifact_type or "",
            storage_ref_type or "",
            storage_ref_id or "",
        ])
        return f"artifact:{uuid.uuid5(uuid.NAMESPACE_URL, raw)}"

    def db_values(self) -> Dict[str, Any]:
        data = asdict(self)
        data["locator_json"] = json.dumps(data.pop("locator"), ensure_ascii=False)
        data["section_path_json"] = json.dumps(data.pop("section_path"), ensure_ascii=False)
        data["metadata_json"] = json.dumps(data.pop("metadata"), ensure_ascii=False)
        return data


@dataclass(frozen=True)
class ArtifactEdgeRecord:
    edge_id: str
    source_artifact_id: str
    target_artifact_id: str
    edge_type: str
    confidence: Optional[float] = None
    created_by: str = "core"
    metadata: Dict[str, Any] = field(default_factory=dict)
    evidence_artifact_ids: List[str] = field(default_factory=list)
    producer_version: Optional[str] = None
    created_at: Optional[str] = None

    @staticmethod
    def stable_id(source_artifact_id: str, target_artifact_id: str, edge_type: str) -> str:
        raw = "|".join([source_artifact_id or "", target_artifact_id or "", edge_type or ""])
        return f"artifact_edge:{uuid.uuid5(uuid.NAMESPACE_URL, raw)}"


@dataclass
class EntityMention:
    """A single raw occurrence of an entity in the text.

    Produced by an EntityExtractor layer; the resolution layer clusters
    mentions that share a ``normalized_key`` into one canonical entity. Offsets
    are relative to the block text so a mention traces back to an exact span.
    """
    surface_text: str
    entity_type: str
    doc_id: str = ""
    block_id: str = ""
    sentence_id: str = ""
    page_num: Optional[int] = None
    char_start: Optional[int] = None
    char_end: Optional[int] = None
    section_path: List[str] = field(default_factory=list)
    normalized_key: str = ""
    extractor_id: str = "unknown"
    confidence: float = 0.7
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RetrievalResult:
    artifact_id: str
    artifact_type: str
    source_id: Optional[str] = None
    source_title: str = ""
    locator: Dict[str, Any] = field(default_factory=dict)
    section_path: List[str] = field(default_factory=list)
    exact_text: str = ""
    score: float = 0.0
    score_breakdown: Dict[str, float] = field(default_factory=dict)
    retrieval_reason: str = ""
    confidence: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceScope:
    """Canonical source restriction used by every retrieval consumer.

    ``source_ids`` is the database identity. Paths and display names are kept
    separately for adapters (notably Chroma) that still filter by document
    metadata instead of source ids.
    """

    source_ids: List[str] = field(default_factory=list)
    source_paths: List[str] = field(default_factory=list)
    display_names: List[str] = field(default_factory=list)
    unresolved_refs: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class RetrievalPlan:
    query: str
    allowed_source_ids: List[str] = field(default_factory=list)
    source_scope: SourceScope = field(default_factory=SourceScope)
    artifact_types: List[str] = field(default_factory=list)
    use_structured: bool = True
    use_keyword: bool = True
    use_embeddings: bool = True
    use_reranker: bool = True
    reranker_policy: str = "auto"  # auto | on | off
    graph_expansion: int = 1
    freshness_policy: str = "current_only"
    review_states: List[str] = field(default_factory=lambda: [
        ArtifactReviewState.UNREVIEWED.value,
        ArtifactReviewState.VERIFIED.value,
        ArtifactReviewState.CORRECTED.value,
    ])
    query_intent: str = "general"
    preferred_artifact_types: List[str] = field(default_factory=list)
    section_paths: List[List[str]] = field(default_factory=list)
    argument_roles: List[str] = field(default_factory=list)
    artifact_families: List[str] = field(default_factory=list)
    coverage_strategy: str = "relevance"
    per_section_limit: int = 0
    min_sections: int = 0
    include_stale: bool = False
    rerank_top_k: int = 20
    max_results: int = 10
    tag_filters: List[str] = field(default_factory=list)
    tag_logic: str = "AND"
    has_citation: Optional[bool] = None
    has_table: Optional[bool] = None
    block_types: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class ContextBundle:
    query: str
    items: List[RetrievalResult] = field(default_factory=list)
    text: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProcessingSelection:
    preset: str = ProcessingMode.BALANCED.value
    stage_overrides: Dict[str, bool] = field(default_factory=dict)
    transcription_policy: str = "ask"
    generative_policy: str = "on_demand"
    remember_for_project: bool = False


@dataclass(frozen=True)
class StageSpec:
    id: str
    dependencies: List[str] = field(default_factory=list)
    source_categories: List[str] = field(default_factory=lambda: [
        "document", "image", "audio", "video",
    ])
    resource_class: str = StageResourceClass.DETERMINISTIC.value
    output_artifact_types: List[str] = field(default_factory=list)
    producer_version: str = "1.0"
    handler_id: str = ""
    enabled: bool = True
    # User-facing description, shared by the import dialog and plan previews.
    label: str = ""
    description: str = ""
    cost_hint: str = ""
    method_kind: str = "deterministic"
    # Project settings that change this stage's output; only these enter its
    # fingerprint, so unrelated setting edits never force a rerun.
    settings_keys: List[str] = field(default_factory=list)
    # ModelDependencyService ids that can enhance this stage when installed.
    model_dependencies: List[str] = field(default_factory=list)
    help_topic: str = ""
    model_role: str = ""
    # Project-wide inputs (names registered with register_project_input): when one
    # changes, this stage goes stale in every source, not only the one that changed.
    project_inputs: List[str] = field(default_factory=list)
    # Presets ("balanced", "deep") that include this stage automatically; how a plugin
    # stage joins ordinary processing without editing the built-in preset lists.
    default_presets: List[str] = field(default_factory=list)

    @property
    def display_label(self) -> str:
        return self.label or self.id.replace("_", " ").capitalize()


@dataclass(frozen=True)
class ArtifactQuery:
    source_scope: SourceScope = field(default_factory=SourceScope)
    artifact_types: List[str] = field(default_factory=list)
    statuses: List[str] = field(default_factory=list)
    review_states: List[str] = field(default_factory=list)
    text: str = ""
    entity_types: List[str] = field(default_factory=list)
    created_by: List[str] = field(default_factory=list)
    model_names: List[str] = field(default_factory=list)
    stage: str = ""
    min_confidence: Optional[float] = None
    max_confidence: Optional[float] = None
    date_from: str = ""
    date_to: str = ""
    cursor: str = ""
    limit: int = 100
    argument_roles: List[str] = field(default_factory=list)
    section_paths: List[List[str]] = field(default_factory=list)
    artifact_families: List[str] = field(default_factory=list)
    parent_artifact_ids: List[str] = field(default_factory=list)
    current_only: bool = False
    order_by: str = "recent"
    mention_limit: int = 50
    timeline_only: bool = False
    issue_kind: str = ""
    exclude_artifact_ids: List[str] = field(default_factory=list)
    bibliography_ids: List[str] = field(default_factory=list)
    tag_filters: List[str] = field(default_factory=list)
    tag_logic: str = "AND"
    has_citation: Optional[bool] = None
    has_table: Optional[bool] = None
    block_types: List[str] = field(default_factory=list)
    exclude_entity_types: List[str] = field(default_factory=list)
    source_tags: List[str] = field(default_factory=list)   # project document tags (by name), any of them


@dataclass(frozen=True)
class StageRunQuery:
    source_scope: SourceScope = field(default_factory=SourceScope)
    stages: List[str] = field(default_factory=list)
    statuses: List[str] = field(default_factory=list)
    model_names: List[str] = field(default_factory=list)
    text: str = ""
    cursor: str = ""
    limit: int = 100


@dataclass(frozen=True)
class ModelCallBudget:
    allowed_roles: List[str] = field(default_factory=list)
    max_calls: int = 0
    max_input_tokens: int = 0
    max_output_tokens: int = 0
    allow_repair_calls: bool = False


@dataclass(frozen=True)
class AnalysisResultEnvelope:
    run_id: str
    mode_id: str
    result_kind: str
    output_artifact_ids: List[str] = field(default_factory=list)
    output: Any = None
    provenance: Dict[str, Any] = field(default_factory=dict)
    diagnostics: Dict[str, Any] = field(default_factory=dict)
    model_usage: Dict[str, Any] = field(default_factory=dict)
    cache_status: str = "miss"
    view_state: Dict[str, Any] = field(default_factory=dict)
    quality: Dict[str, Any] = field(default_factory=dict)
