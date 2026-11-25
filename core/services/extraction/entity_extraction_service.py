"""
EntityExtractionService — shared person/organisation extraction.

Replaces the duplicated PersonOrgExtractor logic that existed separately in
deterministic.py and DocumentIngestionService. Both now delegate here.

Strategy:
  1. spaCy NER via LanguageFeatureService (if available)
  2. PatternDetectorRegistry author_name pack (regex fallback)
  3. Dedup by fuzzy similarity (≥88 % with difflib.SequenceMatcher)
"""
from __future__ import annotations

import re
import uuid
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Set

from core.models.extraction_models import (
    DocumentBlock,
    ExtractedEntity,
    ExtractionProvenance,
    SourceLocation,
)

_EXTRACTOR_VERSION = "1.0"
_DEDUP_THRESHOLD = 0.88

_NOISE_WORDS: Set[str] = {
    "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "introduction", "conclusion", "abstract", "references", "appendix",
    "figure", "table", "chapter", "section", "part", "volume",
    "university", "institute", "department", "press",
    "new", "york", "london", "paris", "berlin",
}


class EntityExtractionService:
    def __init__(self, language_feature_service, pattern_registry=None) -> None:
        self._lang = language_feature_service
        self._registry = pattern_registry

    # ------------------------------------------------------------------

    def extract_persons_orgs(
        self, blocks: List[DocumentBlock], doc_id: str
    ) -> List[ExtractedEntity]:
        """Extract people and organisations from document blocks."""
        return self.extract_from_blocks(blocks, doc_id, entity_type="entity.person_org")

    def extract_from_blocks(
        self,
        blocks: List[DocumentBlock],
        doc_id: str,
        entity_type: str = "entity.person_org",
    ) -> List[ExtractedEntity]:
        """General extraction entry point — dispatches by entity_type."""
        if entity_type == "entity.person_org":
            return self._extract_people_orgs(blocks, doc_id)
        return []

    # ------------------------------------------------------------------

    def _extract_people_orgs(
        self, blocks: List[DocumentBlock], doc_id: str
    ) -> List[ExtractedEntity]:
        raw: List[Dict[str, Any]] = []

        # Skip bibliography blocks to reduce false positives from author lists
        body_blocks = [b for b in blocks if b.block_type != "bibliography"]

        # Phase A: spaCy NER
        if self._lang and self._lang.is_available():
            raw.extend(self._extract_via_spacy(body_blocks, doc_id))

        # Phase B: regex from PatternDetectorRegistry (always run as supplement)
        raw.extend(self._extract_via_regex(body_blocks, doc_id))

        return self._dedup_and_build(raw, doc_id)

    # ------------------------------------------------------------------

    def _extract_via_spacy(
        self, blocks: List[DocumentBlock], doc_id: str
    ) -> List[Dict[str, Any]]:
        hits: List[Dict[str, Any]] = []
        for block in blocks:
            text = block.exact_text
            if not text.strip():
                continue
            entities = self._lang.get_entities(text)
            people_orgs = self._lang.filter_person_org_entities(entities)
            for ent_text, label, start, end in people_orgs:
                clean = ent_text.strip()
                if len(clean) < 3 or clean.lower() in _NOISE_WORDS:
                    continue
                hits.append({
                    "text": clean,
                    "method": "spacy",
                    "block_id": block.block_id,
                    "page_num": block.page_num,
                    "char_start": start,
                    "char_end": end,
                    "doc_id": doc_id,
                })
        return hits

    def _extract_via_regex(
        self, blocks: List[DocumentBlock], doc_id: str
    ) -> List[Dict[str, Any]]:
        hits: List[Dict[str, Any]] = []
        patterns = []
        if self._registry:
            for pack in self._registry.get_packs_for_type("person"):
                patterns.extend(pack.compiled)
        if not patterns:
            import re as _re
            _default = _re.compile(
                r"\b(?:Dr\.?|Prof\.?|Mr\.?|Mrs\.?|Ms\.?)?\s*"
                r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3}\b"
            )
            patterns = [_default]

        for block in blocks:
            text = block.exact_text
            for pat in patterns:
                for m in pat.finditer(text):
                    clean = m.group(0).strip()
                    words = clean.split()
                    if len(words) < 2 or clean.lower() in _NOISE_WORDS:
                        continue
                    if any(w.lower() in _NOISE_WORDS for w in words):
                        continue
                    hits.append({
                        "text": clean,
                        "method": "regex",
                        "block_id": block.block_id,
                        "page_num": block.page_num,
                        "char_start": m.start(),
                        "char_end": m.end(),
                        "doc_id": doc_id,
                    })
        return hits

    def _dedup_and_build(
        self, raw: List[Dict[str, Any]], doc_id: str
    ) -> List[ExtractedEntity]:
        """Cluster near-duplicate names and produce canonical ExtractedEntity objects."""
        clusters: List[List[Dict]] = []

        for hit in raw:
            text = hit["text"]
            merged = False
            for cluster in clusters:
                rep = cluster[0]["text"]
                ratio = SequenceMatcher(None, text.lower(), rep.lower()).ratio()
                if ratio >= _DEDUP_THRESHOLD:
                    cluster.append(hit)
                    merged = True
                    break
            if not merged:
                clusters.append([hit])

        entities: List[ExtractedEntity] = []
        for cluster in clusters:
            canonical = max(cluster, key=lambda h: len(h["text"]))["text"]
            mentions = [
                SourceLocation(
                    doc_id=doc_id,
                    page_num=h["page_num"],
                    block_id=h["block_id"],
                    char_start=h.get("char_start"),
                    char_end=h.get("char_end"),
                )
                for h in cluster
            ]
            method = "spacy" if any(h["method"] == "spacy" for h in cluster) else "regex"
            provenance = ExtractionProvenance(
                extractor_name="entity_extraction_service",
                extractor_version=_EXTRACTOR_VERSION,
                parser_provider="pymupdf_layout",
                method=method,
                confidence=0.85 if method == "spacy" else 0.65,
            )
            entities.append(ExtractedEntity(
                entity_id=str(uuid.uuid4()),
                doc_id=doc_id,
                entity_type="entity.person_org",
                canonical_text=canonical,
                normalized_text=canonical.lower(),
                aliases=list({h["text"] for h in cluster} - {canonical}),
                mentions=mentions,
                provenance=provenance,
            ))
        return entities
