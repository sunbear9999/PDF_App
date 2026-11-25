"""Find names established elsewhere in the project inside one document.

Uses the same token matcher as within-document propagation, so offsets,
capitalisation and overlap rules stay in one place. Only names with a single
owner across the project are applied. A one-word name also needs the document
to write it capitalised mid-sentence ("Reading the file" proves nothing), and
no document in the project may write it in lowercase ("court", "states": an
ordinary word one document happened to tag). Occurrences in all-caps lines
(captions, headings) are not counted. Rejected mentions stay rejected.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List

from core.models.intelligence_models import ArtifactRecord, EntityMention, ProcessingStage
from core.models.producer_models import deterministic
from core.services.intelligence.entities import cross_doc_resolution, surfaces
from core.services.intelligence.entities.project_lexicon import ProjectLexicon, build_lexicon, core_words
from core.services.intelligence.entities.publish import MentionPublisher, mention_ref, rebuild_canonical
from core.services.intelligence.entities.recognize import sentence_id
from core.services.intelligence.entities.retirement import retire

METHOD = "project_propagated"
ABBREVIATION_MAX = 4           # "Dist.", "Okla.", "Art.": a short word with a period is an abbreviation
EXTRACTOR_ID = "project_lexicon"
CONFIDENCE = 0.55
STAGE = ProcessingStage.ENTITY_LINKING.value


class ProjectEntityLinker:
    def __init__(self, artifact_service: Any, templates: Callable) -> None:
        self.service = artifact_service
        self.db = artifact_service.db
        self._templates = templates      # EntityExtractionPipeline._templates: one placeholder per sentence

    def link(self, source: Dict[str, Any], cancel_check=lambda: False) -> List[str]:
        source_id = source.get("id") or source.get("source_id") or ""
        rows = self._sentences(source_id)
        lexicon = build_lexicon(self.db.entity_lexicon_rows())
        existing, previous = self._current(source_id)
        added = self._find(source_id, rows, lexicon, existing, cancel_check)
        if added is None:
            return []
        identities = {EXTRACTOR_ID: deterministic(EXTRACTOR_ID, "1.0")}
        result = MentionPublisher(self.service, identities, source).publish(added, previous, cancel_check, stage=STAGE)
        if result.cancelled:
            return result.written
        retire(self.service, previous, result.written, rebuild_canonical)
        for canonical_id, (entity_type, key) in result.rebuild.items():
            rebuild_canonical(self.db, canonical_id, entity_type, key)
        self._propose(lexicon, source_id)
        return [w for w in result.written if w]

    def _sentences(self, source_id: str) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for artifact_type in ("sentence", "transcript_segment"):
            rows.extend(self.service.get_all_artifacts(source_id, {"artifact_type": artifact_type, "status": "complete"}))
        return rows

    def _current(self, source_id: str):
        existing, previous = [], {}
        for row in self.service.get_all_artifacts(source_id, {"artifact_type": "entity_mention", "status": "complete"}):
            meta, locator = row.get("metadata") or {}, row.get("locator") or {}
            if meta.get("stage") == STAGE:
                previous[row["artifact_id"]] = row
            else:
                existing.append(EntityMention(
                    row.get("exact_text") or "", meta.get("entity_type") or "", doc_id=source_id,
                    sentence_id=str(locator.get("sentence_id") or ""), char_start=locator.get("char_start"),
                    char_end=locator.get("char_end"), normalized_key=row.get("normalized_key") or "",
                    confidence=float(row.get("confidence") or 0.0)))
        return existing, previous

    def _find(self, source_id, rows, lexicon: ProjectLexicon, existing, cancel_check):
        known = [EntityMention(o.surface, o.entity_type, normalized_key=o.key, confidence=CONFIDENCE, doc_id=source_id)
                 for o in lexicon.applicable(source_id)]
        if not known or not rows:
            return []
        wanted = {(m.entity_type, m.normalized_key) for m in known}
        templates = self._templates(rows, existing or known)
        merged = surfaces.complete(existing + known, rows, templates, cancel_check)
        if cancel_check():
            return None
        new = [m for m in merged[len(existing) + len(known):] if (m.entity_type, m.normalized_key) in wanted]
        texts = {sentence_id(row): row.get("exact_text") or "" for row in rows}
        return [self._stamp(m) for m in _credible(new, texts, self.db.words_used_lowercase)
                if not self._rejected(source_id, m)]

    def _rejected(self, source_id: str, mention: EntityMention) -> bool:
        row = self.db.get_artifact(ArtifactRecord.stable_id(
            source_id, "entity_mention", "entity_mentions", mention_ref(mention)))
        return bool(row and row.get("review_state") in {"rejected", "superseded"})

    @staticmethod
    def _stamp(mention: EntityMention) -> EntityMention:
        mention.extractor_id = EXTRACTOR_ID
        mention.confidence = CONFIDENCE
        mention.metadata = {**mention.metadata, "method": METHOD, "reason": {
            "rule": "project_lexicon", "matched_surface": mention.metadata.get("matched_surface")}}
        return mention

    def _propose(self, lexicon: ProjectLexicon, source_id: str) -> None:
        blocked = [frozenset((c["source_key"], c["other_key"])) for kind in {o.entity_type for g in lexicon.by_surface.values() for o in g}
                   for c in self.db.entity_grouping_constraints(kind)]
        for proposal in cross_doc_resolution.propose(lexicon, source_id, blocked):
            low, high = sorted((proposal.first.canonical_id, proposal.second.canonical_id))
            evidence = [e for e in (self._evidence(low), self._evidence(high)) if e]
            self.service.link_artifacts(low, high, cross_doc_resolution.EDGE_TYPE, confidence=round(proposal.score, 3),
                                        metadata={"reason": proposal.reason, "rule": proposal.rule},
                                        created_by="project_linking", evidence_artifact_ids=evidence, producer_version="1.0")

    def _evidence(self, canonical_id: str) -> str:
        rows = self.db.list_entity_mentions_for_canonical(canonical_id, limit=1)
        return str((rows[0].get("metadata") or {}).get("evidence_artifact_id") or "") if rows else ""


def _credible(mentions: List[EntityMention], texts: Dict[str, str], used_lowercase) -> List[EntityMention]:
    mentions = [m for m in mentions if not (m.surface_text.isupper() and not m.metadata.get("matched_surface", "").isupper())]
    single = {m.surface_text: core_words(m.surface_text)[0] for m in mentions if len(core_words(m.surface_text)) == 1}
    common = used_lowercase({word.casefold() for word in single.values()})
    mentions = [m for m in mentions if not _abbreviation(m, texts.get(m.sentence_id, ""))]
    inside = {m.surface_text for m in mentions if not _starts_sentence(m, texts.get(m.sentence_id, ""))}
    return [m for m in mentions if m.surface_text not in single
            or (single[m.surface_text].casefold() not in common and m.surface_text in inside)]


def _abbreviation(mention: EntityMention, text: str) -> bool:
    end = mention.char_end or 0
    return " " not in mention.surface_text and len(mention.surface_text) <= ABBREVIATION_MAX and text[end:end + 1] == "."


def _starts_sentence(mention: EntityMention, text: str) -> bool:
    before = text[:mention.char_start or 0].rstrip()
    return not before or before[-1] in ".!?:;\"“‘'([—–-"
