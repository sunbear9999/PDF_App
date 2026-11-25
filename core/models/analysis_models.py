from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from core.models.extraction_models import (
        CitationIndex,
        DocumentBlock,
        ExtractedDate,
        ExtractedEntity,
        ExtractedTable,
        RankedBlock,
    )


@dataclass
class EvidenceQuote:
    """Atomic evidence item extracted from a single document chunk.

    Exact full text is stored here and in the DB. Later pipeline stages
    receive only quote_id + snippet to keep LLM context windows small.
    """

    quote_id: str           # sha1(run_id + ":" + exact_text)[:16]
    run_id: str
    doc_path: str
    chunk_id: int           # chunk_index from DocumentParser
    exact_text: str         # verbatim text verified against source chunk
    snippet: str            # exact_text[:100] — used in later prompts
    page_num: Optional[int]
    note_text: str          # one-line relevance explanation
    importance_score: float  # 0.0 – 1.0
    confidence_score: float  # 0.0 – 1.0
    suggested_node_type: str  # ontology entity type hint
    quote_role: str         # "supports" | "contradicts" | "context" | "data"
    tags_json: str          # JSON array of tags/topics/entities
    created_at: str         # ISO timestamp


@dataclass
class DeterministicPassInput:
    """Input bundle passed to a DeterministicPassSpec.run_fn.

    blocks is the scope the pass should rank/filter — callers narrow this to
    a single chunk/section's blocks before invoking a pass, so a pass never
    has to know about chunking on its own.
    """

    blocks: "List[DocumentBlock]"
    doc_id: str
    citation_index: "Optional[CitationIndex]" = None
    entity_list: "Optional[List[ExtractedEntity]]" = None
    date_list: "Optional[List[ExtractedDate]]" = None
    table_list: "Optional[List[ExtractedTable]]" = None
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DeterministicPassOutput:
    ranked_blocks: "List[RankedBlock]"
    stats: Dict[str, Any] = field(default_factory=dict)
