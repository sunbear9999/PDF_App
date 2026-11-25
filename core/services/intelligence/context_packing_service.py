"""
ContextPackingService — Stage D: hierarchical (tiered) context assembly.

Builds a tiered context package under a token/char budget instead of a flat
list of chunks. This is the "extended context" mechanism: broad coverage cheaply
(compact facts + summaries) and fine detail only where it earned its rank.

Fill order protects direct evidence before lossy summaries:
  1. Full sentence/argument/segment text
  2. Compact structured facts
  3. Section summaries
  4. Document summaries

Deterministic budgeting — a greedy algorithm, not a model call.
"""
from __future__ import annotations

from typing import Dict, List

from core.models.intelligence_models import ContextBundle, RetrievalResult
from core.services.intelligence import context_sources

# Artifact types that are "compact facts" — terse, high-value-per-char.
_FACT_TYPES = {"entity", "entity_mention", "date_mention", "table", "citation_entry", "in_text_citation"}
_DOC_SUMMARY = "doc_summary"
_SECTION_SUMMARY = "section_summary"


class ContextPackingService:
    def __init__(self, max_chars: int = 12000):
        self.max_chars = max_chars

    def pack(self, query: str, results: List[RetrievalResult], max_chars: int | None = None,
             first_label: int = 1) -> ContextBundle:
        """Pack under ``max_chars``; each kept item gets a label (``first_label`` onward) and its
        source record goes to ``metadata['sources']`` instead of the text."""
        budget = max(1000, int(max_chars or self.max_chars))
        sources: Dict[str, dict] = {}
        labels = _Labels(first_label)
        facts, doc_summaries, section_summaries, sentences = self._bucket(results)

        pieces: List[str] = []
        kept: List[RetrievalResult] = []
        used = 0
        tier_counts: Dict[str, int] = {"facts": 0, "doc_summaries": 0, "section_summaries": 0, "sentences": 0}

        # Tier 1: direct evidence. Retrieval rank is preserved and a summary
        # can never crowd the source passages out of the prompt.
        for item in sentences:
            piece = self._detail_piece(item, labels.peek())
            if not piece or used + len(piece) > budget:
                continue
            sources[labels.take()] = context_sources.record(item)
            pieces.append(piece)
            kept.append(item)
            used += len(piece)
            tier_counts["sentences"] += 1

        # Tier 2: pack facts individually. A single oversized/low-ranked fact
        # must not cause the entire high-value tier to disappear.
        if facts:
            header = "FACTS"
            if len(header) <= budget:
                pieces.append(header)
                used += len(header)
            for fact in facts:
                line = self._fact_line(fact, labels.peek())
                if not line:
                    continue
                if used + len(line) + 1 > budget:
                    continue
                sources[labels.take()] = context_sources.record(fact)
                pieces.append(line)
                kept.append(fact)
                used += len(line) + 1
                tier_counts["facts"] += 1

        # Tiers 3–4: local summaries before broad document summaries.
        for tier_name, items in (("section_summaries", section_summaries),
                                 ("doc_summaries", doc_summaries)):
            for item in items:
                piece = self._detail_piece(item, labels.peek())
                if not piece:
                    continue
                if used + len(piece) > budget:
                    break
                sources[labels.take()] = context_sources.record(item)
                pieces.append(piece)
                kept.append(item)
                used += len(piece)
                tier_counts[tier_name] += 1

        return ContextBundle(
            query=query,
            items=kept,
            text="\n\n".join(pieces),
            metadata={
                "result_count": len(kept), "max_chars": budget, "sources": sources,
                "estimated_tokens": max(1, used // 4), "tiers": tier_counts,
                "packing_order": [
                    "direct_evidence", "structured_facts",
                    "section_summaries", "document_summaries",
                ],
            },
        )

    # ------------------------------------------------------------------

    def _bucket(self, results: List[RetrievalResult]):
        facts, doc_sum, sec_sum, sentences = [], [], [], []
        for item in results:
            atype = item.artifact_type or ""
            storage = (item.metadata or {}).get("scope") or atype
            if atype in _FACT_TYPES:
                facts.append(item)
            elif atype == "summary" and storage == "document":
                doc_sum.append(item)
            elif atype == "summary":
                sec_sum.append(item)
            else:
                sentences.append(item)
        return facts, doc_sum, sec_sum, sentences

    def _fact_line(self, item: RetrievalResult, tag: str) -> str:
        etype = (item.metadata or {}).get("entity_type") or item.artifact_type
        text = (item.exact_text or "").strip().replace("\n", " ")
        if not text:
            return ""
        return f"{context_sources.header(item, tag, etype or '')}: {text}"

    def _detail_piece(self, item: RetrievalResult, tag: str) -> str:
        """``[S1] Title · p. 6 · Section`` then the passage; routing data stays in ``sources``."""
        text = (item.exact_text or "").strip()
        if not text:
            return ""
        label = (item.metadata or {}).get("class_label")
        return context_sources.header(item, tag, label or "") + "\n" + text


class _Labels:
    def __init__(self, first: int) -> None:
        self.number = max(1, int(first))

    def peek(self) -> str:
        return context_sources.label(self.number)

    def take(self) -> str:
        tag = self.peek()
        self.number += 1
        return tag
