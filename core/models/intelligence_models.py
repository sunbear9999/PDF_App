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


class StageStatus(str, Enum):
    NOT_RUN = "not_run"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
    STALE = "stale"
    DISABLED = "disabled"


class ModelRole(str, Enum):
    MAIN_ANSWER = "main_answer_model"
    FAST_WORKER = "fast_worker_model"
    EMBEDDING = "embedding_model"
    RERANKER = "reranker_model"
    VISION = "vision_model"
    TRANSCRIPTION = "transcription_model"
    EXTRACTION_REPAIR = "extraction_repair_model"


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
    status: str = StageStatus.COMPLETE.value
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
    created_at: Optional[str] = None

    @staticmethod
    def stable_id(source_artifact_id: str, target_artifact_id: str, edge_type: str) -> str:
        raw = "|".join([source_artifact_id or "", target_artifact_id or "", edge_type or ""])
        return f"artifact_edge:{uuid.uuid5(uuid.NAMESPACE_URL, raw)}"


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
class RetrievalPlan:
    query: str
    allowed_source_ids: List[str] = field(default_factory=list)
    artifact_types: List[str] = field(default_factory=list)
    use_structured: bool = True
    use_keyword: bool = True
    use_embeddings: bool = True
    use_reranker: bool = False
    query_intent: str = "general"
    preferred_artifact_types: List[str] = field(default_factory=list)
    include_stale: bool = False
    rerank_top_k: int = 20
    max_results: int = 10


@dataclass(frozen=True)
class ContextBundle:
    query: str
    items: List[RetrievalResult] = field(default_factory=list)
    text: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
