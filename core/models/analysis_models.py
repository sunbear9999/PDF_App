from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


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
