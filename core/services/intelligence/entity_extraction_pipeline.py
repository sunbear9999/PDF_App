"""Layered entity recognition, document refinement, resolution and publication.

All occurrences remain source-backed artifacts; explicit user grouping rules
take precedence over automated canonical grouping. Each step lives in
``core/services/intelligence/entities``; this class orders them.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from core.models.intelligence_models import EntityMention, ProcessingStage
from core.models.producer_models import ProducerIdentity, deterministic
from core.services.intelligence import entity_mention_completion
from core.services.extraction.language_id import LanguageIdentifier
from core.services.intelligence.entities import surfaces, context_types, names, stream_filter, language_gate
from core.services.intelligence.entities.acronyms import apply_acronym_aliases
from core.services.intelligence.entities.coreference import link_nominals
from core.services.intelligence.entities.finalize import finalize
from core.services.intelligence.entities.publish import (
    RESOLUTION_IDENTITY, MentionPublisher, canonical_entity_id, rebuild_canonical,
)
from core.services.intelligence.entities.recognize import recognize, sentence_id
from core.services.intelligence.entities.relations import write_relations
from core.services.intelligence.entities import abbreviation_names, roles
from core.services.intelligence.entities.type_resolution import absorb_stragglers, resolve_types
from core.services.intelligence.entities.retirement import retire
from core.services.intelligence.entity_refinement import EntityMentionRefiner, defined_acronyms

GLOSSARY_METADATA_KEY = "entity_glossary"
__all__ = ["EntityExtractionPipeline", "GLOSSARY_METADATA_KEY", "RESOLUTION_IDENTITY",
           "extractor_identity", "canonical_entity_id"]


def extractor_identity(extractor: Any) -> ProducerIdentity:
    """Identity of one entity extractor layer (learned NER or rules)."""
    provider = getattr(extractor, "producer_identity", None)
    if callable(provider):
        try:
            return provider()
        except Exception:
            pass
    return deterministic(
        getattr(extractor, "id", "entity_extractor"),
        str(getattr(extractor, "version", "1.0")),
    )


class EntityExtractionPipeline:
    def __init__(self, project_manager=None, artifact_service=None, extractor_registry=None,
                 document_extraction_service=None, pattern_registry=None, evidence_registry=None,
                 language_model=None) -> None:
        self.project_manager = project_manager
        self.artifact_service = artifact_service
        self.registry = extractor_registry
        self.document_extraction_service = document_extraction_service
        self.refiner = EntityMentionRefiner()
        self.pattern_registry = pattern_registry
        self.language_identifier = LanguageIdentifier(pattern_registry)
        self.evidence_registry = evidence_registry
        self.language_model = language_model     # optional GlotLID runtime (AI Setup)
        self._settings: Dict[str, Any] = {}

    def configuration_identity(self, settings: Dict[str, Any]) -> str:
        """Optional resources that change results: active evidence sources and the language model."""
        parts = [self.evidence_registry.identity(settings) if self.evidence_registry else ""]
        # Plugin extractors change what is found: loading or removing one makes Entities stale.
        plugin_layers = sorted(f"{getattr(e, 'id', '')}@{getattr(e, 'version', '')}" for e in (self.registry.all() if self.registry else [])
                               if "." in str(getattr(e, "id", "")))
        parts.extend(plugin_layers)
        if self._language_model_on(settings):
            parts.append("glotlid")
        return "+".join(p for p in parts if p)

    def _language_model_on(self, settings: Dict[str, Any]) -> bool:
        return bool(settings.get("use_language_model") and self.language_model is not None
                    and self.language_model.is_available())

    def run(self, source: Dict[str, Any], cancel_check=None, settings: Dict[str, Any] = None) -> List[str]:
        db = getattr(self.artifact_service, "db", None)
        if db is None or not self.registry or not self.artifact_service:
            return []
        self._settings = dict(settings or {})
        self.language_identifier.model = self.language_model if self._language_model_on(self._settings) else None
        cancel_check = cancel_check or (lambda: False)
        self._identities = {getattr(e, "id", ""): extractor_identity(e) for e in self.registry.all()}
        source_id = source.get("id") or source.get("source_id") or ""
        all_records = self._input_records(source)
        records = [row for row in all_records if surfaces.eligible(row)]
        # DATE mentions belong to the Dates stage; this stage retires only its own.
        previous = {row["artifact_id"]: row for row in self.artifact_service.get_all_artifacts(
            source_id, {"artifact_type": "entity_mention", "status": "complete"})
            if (row.get("metadata") or {}).get("stage", ProcessingStage.ENTITIES.value) == ProcessingStage.ENTITIES.value}
        extractors = [*self.registry.all(), *self._key_extractor(source_id)]
        mentions = recognize(extractors, records, source_id, self._load_glossary(), cancel_check) if records else []
        if mentions is None:
            return []
        if not mentions:
            self._retire(previous, [])
            return []
        mentions = self._resolve_in_document(mentions, records, all_records, db, cancel_check)
        if mentions is None:
            return []
        result = MentionPublisher(self.artifact_service, self._identities, source).publish(mentions, previous, cancel_check)
        if result.cancelled:
            return result.written
        self._retire(previous, result.written)
        for canonical_id, (entity_type, key) in result.rebuild.items():
            self._rebuild_canonical(db, canonical_id, entity_type, key)
        self._write_relations(db, mentions, result.canonical_ids, all_records)
        return [w for w in result.written if w]

    def _resolve_in_document(self, mentions, records, all_records, db, cancel_check):
        """Document-level cleanup, typing, name resolution and propagation."""
        texts = {sentence_id(row): row.get("exact_text") or "" for row in records}
        mentions, keyed = abbreviation_names.split(mentions)     # the key decides these; filters skip them
        mentions = language_gate.gate(mentions, records, self.language_identifier, self._model_language(),
                                      self._known_word())
        # Refinement may retype a span first ("1868 Treaty": DATE -> LAW).
        mentions = stream_filter.keep_names(self._refine(mentions, records), self.pattern_registry, texts)
        mentions = roles.apply(mentions, self.pattern_registry)   # "Justice" is a title, not a person
        stream_filter.merge_ocr_confusions(mentions, (
            sentence_id(row) for row in records if (row.get("metadata") or {}).get("text_source") == "ocr"))
        if self.evidence_registry is not None:     # optional gazetteer / GLiNER / plugin votes
            self.evidence_registry.apply(mentions, texts, self._settings)
        # One type per name from document-wide usage rates (length-invariant).
        resolve_types(mentions, texts, self.pattern_registry)
        apply_acronym_aliases(mentions, records)
        context_types.apply(mentions, texts, self.pattern_registry)
        mentions = finalize(mentions, texts)
        absorb_stragglers(mentions)
        mentions = abbreviation_names.merge(mentions, keyed)
        names.resolve(mentions, self.pattern_registry, db.entity_grouping_constraints("PERSON"))
        templates = self._templates(all_records, mentions)
        mentions = surfaces.complete(mentions, all_records, templates, cancel_check)
        mentions += link_nominals(mentions, [row for row in all_records if surfaces.eligible(row, discover=False)],
                                  templates, self.pattern_registry)
        return None if cancel_check() else mentions

    def _key_extractor(self, source_id: str):
        """The document's abbreviation key as an extractor (empty when it has none)."""
        rows = self.artifact_service.get_all_artifacts(source_id, {"artifact_type": "abbreviation", "status": "complete"})
        extractor = abbreviation_names.KeyExtractor(rows, self.pattern_registry) if rows else None
        return [extractor] if extractor and extractor.keys else []

    def _retire(self, previous, current):
        retire(self.artifact_service, previous, current, self._rebuild_canonical)

    def _rebuild_canonical(self, db, canonical_id: str, entity_type: str, key: str) -> None:
        rebuild_canonical(db, canonical_id, entity_type, key)

    def _write_relations(self, db, mentions, canonical_ids, records) -> None:
        write_relations(self.artifact_service, mentions, canonical_ids, records)

    def _input_records(self, source: Dict[str, Any]) -> List[Dict[str, Any]]:
        source_id = source.get("id") or source.get("source_id") or ""
        records: List[Dict[str, Any]] = []
        for artifact_type in ("sentence", "transcript_segment"):
            records.extend(self.artifact_service.get_all_artifacts(
                source_id,
                {
                    "artifact_type": artifact_type,
                    "status": "complete",
                },
            ))
        if records:
            return records
        category = source.get("source_type") or ""
        if category in {"audio", "video"}:
            self.artifact_service.sync_transcript_artifacts(source_id)
        else:
            self.artifact_service.sync_extraction_artifacts(
                source_id, stages=[ProcessingStage.SECTIONS.value]
            )
        for artifact_type in ("sentence", "transcript_segment"):
            records.extend(self.artifact_service.get_all_artifacts(
                source_id,
                {
                    "artifact_type": artifact_type,
                    "status": "complete",
                },
            ))
        return records

    def _refine(self, mentions: List[EntityMention], records: List[Dict[str, Any]]) -> List[EntityMention]:
        """Document-level cleanup and retyping (see entity_refinement)."""
        texts, captions, ocr = {}, set(), set()
        for row in records:
            sentence_id = str(row.get("storage_ref_id") or row.get("artifact_id") or "")
            texts[sentence_id] = row.get("exact_text") or ""
            locator = row.get("locator") or {}
            if locator.get("page") == 0 and (row.get("metadata") or {}).get("block_type") == "heading":
                captions.add(sentence_id)   # the document's own caption names its parties
            if (row.get("metadata") or {}).get("text_source") == "ocr":
                ocr.add(sentence_id)
        refined = self.refiner.refine(mentions, texts, captions, known_word=self._known_word(), ocr_sentences=ocr)
        return entity_mention_completion.complete(
            refined, texts, defined_acronyms(texts.values()), self._templates(records, mentions))

    def _model_language(self) -> str:
        for extractor in self.registry.all() if self.registry else []:
            lang = getattr(extractor, "_lang", None)
            if lang is not None and hasattr(lang, "model_language"):
                return lang.model_language()
        return ""

    def _known_word(self):
        """The NER model's vocabulary lookup, when a spaCy model is loaded."""
        for extractor in self.registry.all() if self.registry else []:
            lang = getattr(extractor, "_lang", None)
            if lang is not None and hasattr(lang, "is_known_word") and lang.is_known_word("the") is not None:
                return lang.is_known_word
        return None

    @staticmethod
    def _templates(records, mentions) -> Dict[str, EntityMention]:
        """A placeholder mention per sentence (its block, page, section)."""
        doc_id = mentions[0].doc_id if mentions else ""
        templates = {}
        for row in records:
            sentence_id = str(row.get("storage_ref_id") or row.get("artifact_id") or "")
            locator = row.get("locator") or {}
            templates[sentence_id] = EntityMention(
                surface_text="", entity_type="CONCEPT", doc_id=doc_id,
                block_id=str(locator.get("block_id") or ""), sentence_id=sentence_id,
                page_num=locator.get("page"), section_path=list(row.get("section_path") or []),
                metadata={"evidence_artifact_id": row.get("artifact_id"), "region": surfaces.region(row)})
        return templates

    def _load_glossary(self) -> Dict[str, List[str]]:
        pm = self.project_manager
        if not pm:
            return {}
        try:
            raw = pm.get_metadata(GLOSSARY_METADATA_KEY, "{}")
            data = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(data, dict):
                return {str(k): list(v) for k, v in data.items() if isinstance(v, (list, tuple))}
        except Exception:
            pass
        return {}
