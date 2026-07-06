"""Pack artifact retrieval results into compact LLM context."""
from __future__ import annotations

from typing import List

from core.models.intelligence_models import ContextBundle, RetrievalResult


class ContextPackingService:
    def __init__(self, max_chars: int = 12000):
        self.max_chars = max_chars

    def pack(self, query: str, results: List[RetrievalResult], max_chars: int | None = None) -> ContextBundle:
        pieces = []
        used = 0
        kept: List[RetrievalResult] = []
        budget = max(1000, int(max_chars or self.max_chars))
        for item in results:
            locator = _format_locator(item)
            section = " > ".join(item.section_path) if item.section_path else ""
            text = (item.exact_text or "").strip()
            if not text:
                continue
            piece = (
                f"--- ARTIFACT: {item.artifact_id} | {item.artifact_type} ---\n"
                f"SOURCE: {item.source_title}\n"
                f"LOCATOR: {locator}\n"
                f"SECTION: {section}\n"
                f"REASON: {item.retrieval_reason}\n"
                f"{text}"
            )
            if used + len(piece) > budget:
                break
            pieces.append(piece)
            kept.append(item)
            used += len(piece)
        return ContextBundle(
            query=query,
            items=kept,
            text="\n\n".join(pieces),
            metadata={"result_count": len(kept), "max_chars": budget},
        )


def _format_locator(item: RetrievalResult) -> str:
    locator = item.locator or {}
    parts = []
    for key in ("page", "start", "end", "segment_index"):
        if locator.get(key) is not None:
            parts.append(f"{key}={locator[key]}")
    return ", ".join(parts)
