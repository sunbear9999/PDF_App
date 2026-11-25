"""
RankingFeatureService — deterministic block feature computation and ranking.

Computes named boolean/float features for each DocumentBlock from shared
extraction results (citations, entities, dates, tables).  Analysis templates
reference features by string key in retrieval_roles; RAG uses them as
ChromaDB filter metadata.

No LLM calls.  All scoring is deterministic.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from core.models.extraction_models import (
    BlockFeatureSet,
    CitationIndex,
    DocumentBlock,
    ExtractedDate,
    ExtractedEntity,
    ExtractedTable,
    RankedBlock,
)

_EXTRACTOR_VERSION = "1.0"

# ── Pattern packs used for feature computation ───────────────────────────────

_CAUSAL_RE = re.compile(
    r"\b(?:therefore|because|thus|consequently|as\s+a\s+result|hence|"
    r"it\s+follows|due\s+to|caused\s+by|leads?\s+to|results?\s+in)\b",
    re.I,
)
_ARGUMENT_RE = re.compile(
    r"\b(?:however|furthermore|moreover|nevertheless|in\s+contrast|"
    r"on\s+the\s+other\s+hand|in\s+addition|additionally|conversely|"
    r"despite|although|while|whereas|yet|still|nonetheless)\b",
    re.I,
)
_STATISTICAL_RE = re.compile(
    r"\b(?:p\s*[<=>]\s*[\d.]+|n\s*=\s*\d+|mean|median|SD|SE|CI|"
    r"confidence\s+interval|OR\s*=|RR\s*=|HR\s*=|β\s*=|r\s*=|"
    r"\d+(?:\.\d+)?\s*%|per\s+cent)\b",
    re.I,
)


class DeterministicFeatureService:
    """Compute a BlockFeatureSet for a single DocumentBlock."""

    def compute_block_features(
        self,
        block: DocumentBlock,
        citation_index: Optional[CitationIndex] = None,
        entity_list: Optional[List[ExtractedEntity]] = None,
        date_list: Optional[List[ExtractedDate]] = None,
        table_list: Optional[List[ExtractedTable]] = None,
        total_pages: int = 1,
    ) -> BlockFeatureSet:
        text = block.exact_text
        text_lower = text.lower()
        word_count = max(len(text.split()), 1)

        features: Dict[str, Any] = {}

        # ── Block type / structure ──────────────────────────────────────
        features["block_type"] = block.block_type
        features["heading_level"] = block.heading_level
        features["is_heading"] = block.block_type == "heading"
        features["is_paragraph"] = block.block_type == "paragraph"
        features["is_footnote"] = block.block_type == "footnote"
        features["is_bibliography"] = block.block_type == "bibliography"
        features["is_caption"] = block.block_type == "caption"
        features["section_type"] = block.block_type
        features["section_path"] = " > ".join(block.section_path) if block.section_path else ""

        # ── Page position ───────────────────────────────────────────────
        # page_num is None for reflowable formats (epub/docx/txt/...) that
        # have no fixed pagination — position ratio is meaningless there.
        features["page_position_ratio"] = (
            round(block.page_num / max(total_pages, 1), 3)
            if block.page_num is not None else 0.0
        )

        # ── Citation features ───────────────────────────────────────────
        in_text_count = 0
        if citation_index:
            for cit in citation_index.in_text_citations:
                if cit.location and cit.location.block_id == block.block_id:
                    in_text_count += 1
        features["contains_in_text_citation"] = in_text_count > 0
        features["citation_density"] = round(in_text_count / word_count, 4)
        features["contains_bibliography_entry"] = block.block_type == "bibliography"

        # ── Entity features ─────────────────────────────────────────────
        entity_count = 0
        contains_person = False
        contains_org = False
        if entity_list:
            for ent in entity_list:
                for mention in ent.mentions:
                    if mention.block_id == block.block_id:
                        entity_count += 1
                        if "person" in ent.entity_type.lower():
                            contains_person = True
                        if "org" in ent.entity_type.lower():
                            contains_org = True
        features["contains_person"] = contains_person
        features["contains_organization"] = contains_org
        features["entity_density"] = round(entity_count / word_count, 4)

        # ── Date features ───────────────────────────────────────────────
        date_count = 0
        if date_list:
            for d in date_list:
                if d.location and d.location.block_id == block.block_id:
                    date_count += 1
        features["contains_date"] = date_count > 0
        features["date_density"] = round(date_count / word_count, 4)

        # ── Table features ──────────────────────────────────────────────
        contains_table = block.block_type == "table"
        if not contains_table and table_list:
            for t in table_list:
                if t.page_num == block.page_num:
                    contains_table = True
                    break
        features["contains_table"] = contains_table
        features["contains_data_point"] = bool(_STATISTICAL_RE.search(text))

        # ── Discourse / argument markers ────────────────────────────────
        causal_hits = len(_CAUSAL_RE.findall(text))
        argument_hits = len(_ARGUMENT_RE.findall(text))
        stat_hits = len(_STATISTICAL_RE.findall(text))
        features["causal_marker_density"] = round(causal_hits / word_count, 4)
        features["argument_marker_density"] = round(argument_hits / word_count, 4)
        features["data_statistical_marker"] = stat_hits > 0

        # ── Quote candidate score (heuristic) ──────────────────────────
        quote_score = 0.0
        if in_text_count > 0:
            quote_score += 0.4
        if contains_person or contains_org:
            quote_score += 0.2
        if date_count > 0:
            quote_score += 0.1
        if causal_hits > 0:
            quote_score += 0.1
        if argument_hits > 0:
            quote_score += 0.1
        if block.block_type == "paragraph":
            quote_score += 0.1
        features["quote_candidate_score"] = round(min(quote_score, 1.0), 3)

        return BlockFeatureSet(
            block_id=block.block_id,
            doc_id=block.doc_id,
            features=features,
        )

    def compute_all(
        self,
        blocks: List[DocumentBlock],
        citation_index: Optional[CitationIndex] = None,
        entity_list: Optional[List[ExtractedEntity]] = None,
        date_list: Optional[List[ExtractedDate]] = None,
        table_list: Optional[List[ExtractedTable]] = None,
    ) -> List[BlockFeatureSet]:
        total_pages = max((b.page_num for b in blocks if b.page_num is not None), default=1)
        return [
            self.compute_block_features(
                block, citation_index, entity_list, date_list, table_list, total_pages
            )
            for block in blocks
        ]


class RankingFeatureService:
    """
    Rank DocumentBlocks using precomputed BlockFeatureSets.

    Scoring:
      - Base score: sum of weights for preferred_features that are truthy
      - Block-type bonus: preferred_block_types get +0.3
      - Pattern boost: regex matches in block text via boost_patterns
    """

    def __init__(self) -> None:
        self._feature_svc = DeterministicFeatureService()

    # ------------------------------------------------------------------

    def rank_blocks(
        self,
        blocks: List[DocumentBlock],
        features: List[BlockFeatureSet],
        preferred_features: Optional[List[str]] = None,
        preferred_block_types: Optional[List[str]] = None,
        boost_patterns: Optional[List[str]] = None,
        top_n: Optional[int] = None,
    ) -> List[RankedBlock]:
        """
        Score and rank blocks.

        preferred_features: feature keys that increase score when truthy
        preferred_block_types: block type strings that receive a +0.3 bonus
        boost_patterns: feature key names that are pattern-category flags
                        (causal_marker_density, argument_marker_density, etc.)
        top_n: return only the top N blocks (all if None)
        """
        preferred_features = preferred_features or []
        preferred_block_types = preferred_block_types or []
        boost_patterns = boost_patterns or []

        feature_map: Dict[str, BlockFeatureSet] = {
            fs.block_id: fs for fs in features
        }

        ranked: List[RankedBlock] = []

        for block in blocks:
            fs = feature_map.get(block.block_id)
            if fs is None:
                ranked.append(RankedBlock(
                    block_id=block.block_id,
                    doc_id=block.doc_id,
                    score=0.0,
                    block=block,
                    features=None,
                ))
                continue

            score, breakdown = self._score_block(
                block, fs, preferred_features, preferred_block_types, boost_patterns
            )
            ranked.append(RankedBlock(
                block_id=block.block_id,
                doc_id=block.doc_id,
                score=score,
                score_breakdown=breakdown,
                block=block,
                features=fs,
            ))

        ranked.sort(key=lambda rb: rb.score, reverse=True)

        if top_n is not None:
            return ranked[:top_n]
        return ranked

    def rank_with_compute(
        self,
        blocks: List[DocumentBlock],
        preferred_features: Optional[List[str]] = None,
        preferred_block_types: Optional[List[str]] = None,
        boost_patterns: Optional[List[str]] = None,
        top_n: Optional[int] = None,
        citation_index: Optional[CitationIndex] = None,
        entity_list: Optional[List[ExtractedEntity]] = None,
        date_list: Optional[List[ExtractedDate]] = None,
        table_list: Optional[List[ExtractedTable]] = None,
    ) -> List[RankedBlock]:
        """Compute features then rank — convenience method for single-call use."""
        features = self._feature_svc.compute_all(
            blocks, citation_index, entity_list, date_list, table_list
        )
        return self.rank_blocks(
            blocks, features, preferred_features, preferred_block_types, boost_patterns, top_n
        )

    # ------------------------------------------------------------------

    @staticmethod
    def _score_block(
        block: DocumentBlock,
        fs: BlockFeatureSet,
        preferred_features: List[str],
        preferred_block_types: List[str],
        boost_patterns: List[str],
    ) -> tuple[float, Dict[str, float]]:
        breakdown: Dict[str, float] = {}
        score = 0.0
        feat = fs.features

        # Feature weights
        weight_per_feature = 1.0 / max(len(preferred_features), 1)
        for fname in preferred_features:
            val = feat.get(fname)
            if val is None:
                continue
            if isinstance(val, bool):
                if val:
                    breakdown[fname] = weight_per_feature
                    score += weight_per_feature
            elif isinstance(val, (int, float)):
                contribution = min(float(val), 1.0) * weight_per_feature
                if contribution > 0:
                    breakdown[fname] = round(contribution, 4)
                    score += contribution

        # Block type bonus
        if block.block_type in preferred_block_types:
            breakdown["block_type_bonus"] = 0.3
            score += 0.3

        # Pattern boost (density features)
        for pattern_key in boost_patterns:
            density = feat.get(f"{pattern_key}_density", 0.0)
            if isinstance(density, float) and density > 0:
                contribution = min(density * 2.0, 0.5)
                breakdown[f"pattern_{pattern_key}"] = round(contribution, 4)
                score += contribution

        return round(score, 4), breakdown
