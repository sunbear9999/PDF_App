"""Artifact reference indexing over existing Papyrus storage."""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, Iterable, List, Optional

from core.models.intelligence_models import (
    ArtifactQuery,
    ArtifactEdgeRecord,
    ArtifactRecord,
    SourceScope,
    ProcessingStage,
    StageStatus,
)
from core.models.producer_models import (
    MethodKind, ProducerIdentity, deterministic, human, transcription_identity,
)
from core.models.source_anchor_models import block_span_locator
from core.events.domains.intelligence_events import IntelligenceEvent, ArtifactReviewPayload, KnowledgeReviewPayload
from core.services.intelligence.knowledge_review import preview as preview_decision, COMMANDS as KNOWLEDGE_COMMANDS
from core.services.intelligence.structure_publication import publish as publish_structure
from core.services.intelligence.citation_publication import citation_candidates, publish_citation_links
from core.services.intelligence.note_artifacts import note_author_kind, publish_note
from core.services.intelligence.review_migration import (
    ReviewMigration, citation_key, date_key, source_span_key,
)
from core.utils.bounded_cache import BoundedCache

SOURCE_HASH_CACHE_ENTRIES = 2048

SOURCE_IDENTITY = deterministic("source_registry", "1.0")
HUMAN_IDENTITY = human()

# Producer identity when extraction artifacts are synced outside a stage run.
EXTRACTION_IDENTITIES = {
    ProcessingStage.PARSED_BLOCKS.value: deterministic("pdf_layout_parser", "1.0"),
    ProcessingStage.SECTIONS.value: deterministic("sentence_segmentation", "3.4"),
    ProcessingStage.CITATIONS.value: deterministic("citation_extraction", "3.0"),
    ProcessingStage.DATES.value: deterministic("date_extraction", "1.4"),
    ProcessingStage.TABLES.value: deterministic("table_extraction", "1.0"),
}


class ArtifactService:
    def __init__(self, project_manager=None, document_extraction_service=None):
        self.project_manager = project_manager
        self.document_extraction_service = document_extraction_service
        from core.registries.intelligence_review_registry import IntelligenceReviewRegistry, builtin_review_issues
        self.review_registry = IntelligenceReviewRegistry()
        for issue in builtin_review_issues():
            self.review_registry.register(issue)
        self._producers: Dict[str, Any] = {}
        self._artifact_types: Dict[str, Dict[str, Any]] = {}
        self.schema_generation = 0
        self._edge_types: Dict[str, Dict[str, Any]] = {}
        # path → (mtime, size, sha256); bounded so long sessions do not grow it forever.
        self._source_hash_cache = BoundedCache(SOURCE_HASH_CACHE_ENTRIES)

    @property
    def db(self):
        return getattr(self.project_manager, "db_intelligence", None)

    def ensure_source_artifact(self, source: Dict[str, Any], *, compute_hash: bool = True) -> Optional[str]:
        if not self.db or not source:
            return None
        source_id = source.get("id") or source.get("source_id") or self._source_id_for_path(source.get("path"))
        path = source.get("path") or source.get("source_path")
        artifact_id = ArtifactRecord.stable_id(source_id, "source", "sources", source_id)
        self._publish(SOURCE_IDENTITY, ArtifactRecord(
            artifact_id=artifact_id,
            source_id=source_id,
            source_path=path,
            source_type=source.get("source_type"),
            artifact_type="source",
            storage_ref_type="sources",
            storage_ref_id=source_id,
            exact_text=source.get("title") or os.path.basename(path or ""),
            metadata={"stage": ProcessingStage.SOURCE_REGISTERED.value},
            input_hash=self.source_input_hash(path, compute=compute_hash),
        ))
        return artifact_id

    def sync_source_artifacts(self, source_id: str) -> List[str]:
        source = self._source_by_id(source_id)
        return [self.ensure_source_artifact(source)] if source else []

    def sync_extraction_artifacts(
        self, source_id: str, stages: Optional[Iterable[str]] = None,
        identity: Optional[ProducerIdentity] = None,
    ) -> List[str]:
        """Publish cached extraction results as artifacts.

        ``identity`` names the producer run; without one each record carries
        an explicit deterministic identity instead of a blank model name.
        """
        if not self.db:
            return []
        stages = set(stages or [])
        source = self._source_by_id(source_id)
        if not source:
            return []
        source_id = source.get("id") or source_id
        path = source.get("path") or source_id
        source_type = source.get("source_type", "")
        written: List[str] = []
        source_artifact = self.ensure_source_artifact(source)
        des = self.document_extraction_service

        if des and (not stages or ProcessingStage.PARSED_BLOCKS.value in stages or ProcessingStage.SECTIONS.value in stages):
            blocks = des.get_blocks(source_id) or des.get_blocks(path)
            for block in blocks:
                section_path = list(getattr(block, "section_path", []) or [])
                artifact_id = ArtifactRecord.stable_id(source_id, "block", "extraction_blocks", block.block_id)
                written.append(self._publish(identity, ArtifactRecord(
                    artifact_id=artifact_id,
                    source_id=source_id,
                    source_path=path,
                    source_type=source_type,
                    artifact_type="block",
                    storage_ref_type="extraction_blocks",
                    storage_ref_id=block.block_id,
                    parent_artifact_id=source_artifact,
                    exact_text=getattr(block, "exact_text", "") or None,
                    normalized_text=getattr(block, "normalized_text", "") or None,
                    locator={"page": getattr(block, "page_num", None), "bbox": getattr(block, "bbox", None)},
                    section_path=section_path,
                    metadata={
                        "stage": ProcessingStage.PARSED_BLOCKS.value,
                        "block_type": getattr(block, "block_type", ""),
                        "heading_level": getattr(block, "heading_level", None),
                    },
                    extractor_name=getattr(block.provenance, "extractor_name", "document_extraction"),
                    extractor_version=getattr(block.provenance, "extractor_version", "unknown"),
                    input_hash=self.text_input_hash(getattr(block, "exact_text", "") or ""),
                )))
                if source_artifact:
                    self.link_artifacts(artifact_id, source_artifact, "located_in")

        if des and (not stages or ProcessingStage.SECTIONS.value in stages) and hasattr(des, "get_sentences"):
            written.extend(publish_structure(self, source, blocks))
            sentences = des.get_sentences(source_id) or des.get_sentences(path)
            current_sentence_ids = set()
            for sent in sentences or []:
                sentence_id = sent.get("sentence_id")
                if not sentence_id:
                    continue
                text = sent.get("text") or ""
                artifact_id = ArtifactRecord.stable_id(source_id, "sentence", "extraction_sentences", sentence_id)
                current_sentence_ids.add(artifact_id)
                block_id = sent.get("block_id")
                parent_block_artifact = (
                    ArtifactRecord.stable_id(source_id, "block", "extraction_blocks", block_id)
                    if block_id else source_artifact
                )
                written.append(self._publish(identity, ArtifactRecord(
                    artifact_id=artifact_id,
                    source_id=source_id,
                    source_path=path,
                    source_type=source_type,
                    artifact_type="sentence",
                    storage_ref_type="extraction_sentences",
                    storage_ref_id=sentence_id,
                    parent_artifact_id=parent_block_artifact,
                    exact_text=text or None,
                    normalized_text=text.strip().lower() or None,
                    locator={
                        "page": sent.get("page_num"),
                        "block_id": block_id,
                        "char_start": sent.get("char_start"),
                        "char_end": sent.get("char_end"),
                        "source_spans": sent.get("source_spans") or [],
                    },
                    section_path=list(sent.get("section_path") or []),
                    metadata={
                        "stage": ProcessingStage.SECTIONS.value,
                        "block_type": sent.get("block_type", ""),
                        "is_fragment": bool(sent.get("is_fragment")),
                        "text_source": sent.get("text_source", ""),
                        "structure": sent.get("structure") or {},
                    },
                    extractor_name="sentence_segmentation",
                    extractor_version="3.4",
                    input_hash=self.text_input_hash(text),
                )))
                if block_id and self.db.get_artifact(parent_block_artifact):
                    self.link_artifacts(artifact_id, parent_block_artifact, "part_of")
            previous_sentence_ids = {
                str(item.get("artifact_id")) for item in self.db.list_artifacts(
                    source_id=source_id, artifact_type="sentence", limit=100000
                ) if item.get("artifact_id")
            }
            retiring = previous_sentence_ids - current_sentence_ids
            retiring -= ReviewMigration(self).carry_forward(retiring, current_sentence_ids, source_span_key)
            self.db.mark_artifact_ids_status(sorted(retiring), "superseded")

        if des and (not stages or ProcessingStage.CITATIONS.value in stages):
            index = des.get_citation_index(source_id) or des.get_citation_index(path)
            if index:
                entry_artifact_ids = {}
                citation_artifact_ids = {}
                unmatched_entries = set(
                    getattr(index, "unmatched_bib_entry_ids", []) or []
                )
                unmatched_citations = set(
                    getattr(index, "unmatched_in_text_ids", []) or []
                )
                current_citation_artifacts = set()
                for entry in getattr(index, "bibliography_entries", []) or []:
                    entry_id = getattr(entry, "entry_id", None) or getattr(entry, "source_id", None) or getattr(entry, "key", None)
                    if not entry_id:
                        continue
                    kind = "document_note" if entry.citation_style == "footnote" else "citation_entry"
                    artifact_id = ArtifactRecord.stable_id(source_id, kind, "extraction_citation_index", entry_id)
                    entry_artifact_ids[entry_id] = artifact_id
                    current_citation_artifacts.add(artifact_id)
                    entry_location = getattr(entry, "location", None)
                    written.append(self._publish(identity, ArtifactRecord(
                        artifact_id=artifact_id,
                        source_id=source_id,
                        source_path=path,
                        source_type=source_type,
                        artifact_type=kind,
                        storage_ref_type="extraction_citation_index",
                        storage_ref_id=entry_id,
                        parent_artifact_id=source_artifact,
                        exact_text=getattr(entry, "raw_text", "") or getattr(entry, "raw", "") or getattr(entry, "title", ""),
                        locator={**block_span_locator(entry_location), "source_spans": entry.source_spans},
                        extractor_name=getattr(entry.provenance, "extractor_name", "citation_extraction"),
                        extractor_version=getattr(entry.provenance, "extractor_version", "2.0"),
                        metadata={
                            "stage": ProcessingStage.CITATIONS.value,
                            "authors": getattr(entry, "authors", []) or [],
                            "year": getattr(entry, "year", None),
                            "title": getattr(entry, "title", None),
                            "doi": getattr(entry, "doi", None),
                            "url": getattr(entry, "url", None),
                            "citation_style": getattr(entry, "citation_style", "unknown"),
                            "reference_number": getattr(entry, "reference_number", None),
                            "scope": entry.normalized_keys.get("scope"),
                            "reference_style": index.reference_style,
                            # Authority key groups the same case or statute
                            # across documents, where raw text varies by pin cite.
                            "authority": (
                                getattr(entry, "normalized_keys", None) or {}
                            ).get("authority"),
                            "unresolved": entry_id in unmatched_entries,
                        },
                        confidence=1.0,
                    )))
                note_ids = {e.entry_id for e in index.bibliography_entries if e.citation_style == "footnote"}
                note_markers = {m.in_text_citation_id for m in index.citation_matches if m.bibliography_entry_id in note_ids}
                candidates = citation_candidates(index, entry_artifact_ids)
                for cite in getattr(index, "in_text_citations", []) or []:
                    cite_id = getattr(cite, "citation_id", None)
                    if not cite_id:
                        continue
                    loc = getattr(cite, "location", None)
                    is_note = cite_id in note_markers or cite.reference_role == "notes"
                    kind = "note_reference" if is_note else "in_text_citation"
                    artifact_id = ArtifactRecord.stable_id(source_id, kind, "extraction_citation_index", cite_id)
                    citation_artifact_ids[cite_id] = artifact_id
                    current_citation_artifacts.add(artifact_id)
                    written.append(self._publish(identity, ArtifactRecord(
                        artifact_id=artifact_id,
                        source_id=source_id,
                        source_path=path,
                        source_type=source_type,
                        artifact_type=kind,
                        storage_ref_type="extraction_citation_index",
                        storage_ref_id=cite_id,
                        parent_artifact_id=source_artifact,
                        exact_text=getattr(cite, "raw_text", ""),
                        locator=block_span_locator(loc),
                        extractor_name=getattr(cite.provenance, "extractor_name", "citation_extraction"),
                        extractor_version=getattr(cite.provenance, "extractor_version", "2.0"),
                        metadata={
                            "stage": ProcessingStage.CITATIONS.value,
                            "raw_text": getattr(cite, "raw_text", ""),
                            "context_text": getattr(cite, "context_text", ""),
                            "citation_type": getattr(cite, "citation_type", ""),
                            "reference_role": cite.reference_role,
                            "resolution_method": getattr(cite, "resolution_method", ""),
                            "resolved_authority_key": getattr(cite, "resolved_authority_key", None),
                            "unmatched_numeric_keys": getattr(cite, "unmatched_numeric_keys", []),
                            "matching_candidates": candidates.get(cite_id, []),
                            "reference_style": index.reference_style,
                            "ambiguous": bool(candidates.get(cite_id)),
                            "year": getattr(cite, "year", None),
                            "unresolved": cite_id in unmatched_citations,
                        },
                        confidence=getattr(getattr(cite, "provenance", None), "confidence", None),
                    )))
                publish_citation_links(self, index, citation_artifact_ids, entry_artifact_ids)
                # Entry and citation ids are regenerated on every run, so each
                # rebuild wrote a fresh set of artifacts. Without retiring the
                # previous set the Discovery list showed one card per run of the
                # same reference.
                previous_citation_artifacts = {
                    str(item.get("artifact_id"))
                    for artifact_type in ("citation_entry", "in_text_citation", "document_note", "note_reference")
                    for item in self.db.list_artifacts(
                        source_id=source_id, artifact_type=artifact_type, limit=100000
                    )
                    if item.get("artifact_id")
                }
                retiring = previous_citation_artifacts - current_citation_artifacts
                retiring -= ReviewMigration(self).carry_forward(
                    retiring, current_citation_artifacts, citation_key)
                self.db.mark_artifact_ids_status(sorted(retiring), "superseded")

        # NOTE: entity artifacts are produced by EntityExtractionPipeline (the
        # layered NER→pattern→resolution→relation extractor), not here — the
        # ENTITIES stage runs that pipeline directly. This keeps a single
        # entity path and avoids the person/org-conflated legacy output.

        if des and (not stages or ProcessingStage.DATES.value in stages):
            current_date_artifacts = set()
            for item in des.get_dates(source_id) or des.get_dates(path):
                date_id = getattr(item, "date_id", "")
                if not date_id:
                    continue
                loc = getattr(item, "location", None)
                artifact_id = ArtifactRecord.stable_id(source_id, "date_mention", "extraction_results", date_id)
                current_date_artifacts.add(artifact_id)
                written.append(self._publish(identity, ArtifactRecord(
                    artifact_id=artifact_id,
                    source_id=source_id,
                    source_path=path,
                    source_type=source_type,
                    artifact_type="date_mention",
                    storage_ref_type="extraction_results",
                    storage_ref_id=date_id,
                    parent_artifact_id=source_artifact,
                    exact_text=getattr(item, "context_text", "") or getattr(item, "exact_text", ""),
                    normalized_text=getattr(item, "normalized_date", ""),
                    locator=block_span_locator(loc),
                    metadata={
                        "stage": ProcessingStage.DATES.value,
                        "exact_text": getattr(item, "exact_text", ""),
                        "normalized_date": getattr(item, "normalized_date", "") or "",
                        "certainty": getattr(item, "certainty", ""),
                        **(getattr(item, "metadata", None) or {}),
                    },
                    confidence=getattr(getattr(item, "provenance", None), "confidence", None),
                )))
            previous_date_artifacts = {
                str(item.get("artifact_id")) for item in self.db.list_artifacts(
                    source_id=source_id, artifact_type="date_mention", limit=100000
                ) if item.get("storage_ref_type") == "extraction_results"
            }
            retiring = previous_date_artifacts - current_date_artifacts
            retiring -= ReviewMigration(self).carry_forward(
                retiring, current_date_artifacts, date_key)
            self.db.mark_artifact_ids_status(sorted(retiring), "superseded")

        if des and (not stages or ProcessingStage.TABLES.value in stages):
            for table in des.get_tables(source_id) or des.get_tables(path):
                table_id = getattr(table, "table_id", "")
                if not table_id:
                    continue
                rows = []
                for row in getattr(table, "cells", []) or []:
                    rows.append(" | ".join(getattr(cell, "text", "") for cell in row))
                text = "\n".join([getattr(table, "caption", "") or "", *rows]).strip()
                artifact_id = ArtifactRecord.stable_id(source_id, "table", "extraction_results", table_id)
                written.append(self._publish(identity, ArtifactRecord(
                    artifact_id=artifact_id,
                    source_id=source_id,
                    source_path=path,
                    source_type=source_type,
                    artifact_type="table",
                    storage_ref_type="extraction_results",
                    storage_ref_id=table_id,
                    parent_artifact_id=source_artifact,
                    exact_text=text,
                    locator={"page": getattr(table, "page_num", None), "bbox": getattr(table, "bbox", None)},
                    metadata={
                        "stage": ProcessingStage.TABLES.value,
                        "caption": getattr(table, "caption", ""),
                        "headers": getattr(table, "headers", []),
                        "dataset_id": getattr(table, "dataset_id", None),
                    },
                    confidence=getattr(table, "confidence", None),
                )))

        return [item for item in written if item]

    def _publish(self, identity: Optional[ProducerIdentity], record: ArtifactRecord) -> str:
        stage = (record.metadata or {}).get("stage") or record.artifact_type
        recorded = (deterministic(record.extractor_name, record.extractor_version)
                    if record.extractor_name and record.extractor_version else None)
        chosen = identity or recorded or EXTRACTION_IDENTITIES.get(stage) or deterministic(
            record.extractor_name or "document_extraction"
        )
        return self.db.upsert_artifact(chosen.stamp(record))

    def sync_annotation_artifact(self, highlight_id: str) -> Optional[str]:
        if not self.db or not highlight_id:
            return None
        highlight = self.project_manager.get_highlight(highlight_id)
        if not highlight:
            return None
        from core.services.intelligence.reference_note_records import anchor_of, ids, PREFIX
        if anchor := anchor_of(highlight):
            return ids(anchor["source_id"], highlight_id[len(PREFIX):])["annotation_id"]
        doc_id = highlight.get("doc_id") or ""
        source = self._source_by_path(doc_id) or self._source_by_id(doc_id) or {
            "id": self._source_id_for_path(doc_id),
            "path": doc_id,
            "source_type": self.project_manager.get_source_type(doc_id) if self.project_manager else "",
        }
        source_id = source.get("id") or self._source_id_for_path(doc_id)
        source_artifact = self.ensure_source_artifact(source)
        nearest_block = self._nearest_block_for_highlight(source_id, source.get("path") or doc_id, highlight)
        section_path = list(getattr(nearest_block, "section_path", []) or []) if nearest_block else []
        artifact_id = ArtifactRecord.stable_id(source_id, "annotation", "highlights", highlight_id)
        saved = self._publish(HUMAN_IDENTITY, ArtifactRecord(
            artifact_id=artifact_id,
            source_id=source_id,
            source_path=source.get("path") or doc_id,
            source_type=source.get("source_type"),
            artifact_type="annotation",
            storage_ref_type="highlights",
            storage_ref_id=highlight_id,
            parent_artifact_id=source_artifact,
            exact_text=highlight.get("text_content") or "",
            normalized_text=(highlight.get("text_content") or "").strip().lower(),
            locator={
                "page": highlight.get("page_num"),
                "rects": highlight.get("rect_coords"),
                "block_id": getattr(nearest_block, "block_id", None),
            },
            section_path=section_path,
            metadata={
                "stage": "annotation",
                "note": highlight.get("note_content") or highlight.get("content") or "",
                "note_authored_by": note_author_kind(highlight_id),
                "color": highlight.get("color"),
                "section_path": section_path,
                "block_id": getattr(nearest_block, "block_id", None),
            },
            input_hash=self.text_input_hash(json.dumps(highlight, sort_keys=True, ensure_ascii=False)),
        ))
        if source_artifact:
            self.link_artifacts(saved, source_artifact, "located_in")
        publish_note(self, saved, highlight, source_id)
        if nearest_block:
            block_artifact_id = ArtifactRecord.stable_id(
                source_id,
                "block",
                "extraction_blocks",
                getattr(nearest_block, "block_id", ""),
            )
            if self.db.get_artifact(block_artifact_id):
                self.link_artifacts(saved, block_artifact_id, "annotates")
        return saved

    def sync_all_annotation_artifacts(self, source_id: Optional[str] = None) -> List[str]:
        if not self.project_manager:
            return []
        highlights = self.project_manager.get_highlights() or {}
        result = []
        for highlight in highlights.values():
            if source_id:
                source = self._source_by_id(source_id)
                if source and highlight.get("doc_id") != source.get("path"):
                    continue
            artifact_id = self.sync_annotation_artifact(highlight.get("id"))
            if artifact_id:
                result.append(artifact_id)
        return result

    def sync_transcript_artifacts(self, source_id: str) -> List[str]:
        if not self.db or not self.project_manager:
            return []
        source = self._source_by_id(source_id)
        if not source:
            return []
        source_id = source.get("id") or source_id
        transcript = self.project_manager.get_video_transcript(source_id) or {}
        segments = transcript.get("segments") or []
        source_artifact = self.ensure_source_artifact(source)
        result = []
        for seg in segments:
            idx = str(seg.get("index", len(result)))
            storage_id = f"{source_id}:segment:{idx}"
            artifact_id = ArtifactRecord.stable_id(source_id, "transcript_segment", "video_transcripts", storage_id)
            result.append(self._publish(transcription_identity(transcript), ArtifactRecord(
                artifact_id=artifact_id,
                source_id=source_id,
                source_path=source.get("path"),
                source_type=source.get("source_type"),
                artifact_type="transcript_segment",
                storage_ref_type="video_transcripts",
                storage_ref_id=storage_id,
                parent_artifact_id=source_artifact,
                exact_text=seg.get("text") or "",
                normalized_text=(seg.get("text") or "").strip().lower(),
                locator={"start": seg.get("start"), "end": seg.get("end"), "segment_index": seg.get("index")},
                metadata={"stage": ProcessingStage.TRANSCRIPT.value, "model": transcript.get("model")},
                confidence=float(seg.get("avg_logprob", 0.0) or 0.0),
                model_role="transcription_model",
                model_name=transcript.get("model"),
                input_hash=self.text_input_hash(seg.get("text") or ""),
            )))
        return result

    def link_artifacts(
        self,
        source_artifact_id: str,
        target_artifact_id: str,
        edge_type: str,
        confidence: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
        *,
        created_by: str = "core",
        evidence_artifact_ids: Optional[List[str]] = None,
        producer_version: Optional[str] = None,
    ) -> Optional[str]:
        if not self.db or not source_artifact_id or not target_artifact_id:
            return None
        edge_id = ArtifactEdgeRecord.stable_id(source_artifact_id, target_artifact_id, edge_type)
        return self.db.upsert_edge(ArtifactEdgeRecord(
            edge_id=edge_id,
            source_artifact_id=source_artifact_id,
            target_artifact_id=target_artifact_id,
            edge_type=edge_type,
            confidence=confidence,
            created_by=created_by,
            metadata=metadata or {},
            evidence_artifact_ids=list(evidence_artifact_ids or []),
            producer_version=producer_version,
        ))

    def get_artifacts(self, source_id: Optional[str] = None, filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Compatibility list API; streaming/paged consumers use the typed APIs."""
        from itertools import islice
        filters = dict(filters or {})
        limit = max(0, int(filters.pop("limit", 500)))
        return list(islice(self.iter_artifacts(_list_filters(source_id, filters)), limit))

    def get_all_artifacts(self, source_id: Optional[str], filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Every matching artifact, streamed without a row cap.

        Producers must see the whole source: a capped list silently drops the
        tail of long books (a 20,000-row cap lost sentences past ~800 pages).
        """
        filters = dict(filters or {})
        filters.pop("limit", None)
        return list(self.iter_artifacts(_list_filters(source_id, filters)))

    def query_artifacts(self, query: ArtifactQuery | Dict[str, Any]) -> Dict[str, Any]:
        from core.db.intelligence_artifact_queries import coerce_query
        return self.db.query_artifacts(coerce_query(query)) if self.db else {"items": [], "next_cursor": ""}

    def iter_artifacts(self, query: ArtifactQuery | Dict[str, Any]):
        from core.db.intelligence_artifact_queries import coerce_query
        return self.db.iter_artifacts(coerce_query(query)) if self.db else iter(())

    def query_entities(self, query: ArtifactQuery | Dict[str, Any]) -> Dict[str, Any]:
        from core.db.intelligence_artifact_queries import coerce_query
        return self.db.query_entities(coerce_query(query)) if self.db else {"items": [], "next_cursor": ""}

    def iter_entities(self, query: ArtifactQuery | Dict[str, Any]):
        from core.db.intelligence_artifact_queries import coerce_query
        return self.db.iter_entities(coerce_query(query)) if self.db else iter(())

    def revise_artifact(
        self,
        artifact_id: str,
        *,
        exact_text: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        review_state: str = "corrected",
        created_by: str = "user",
    ) -> Optional[Dict[str, Any]]:
        return self.db.save_artifact_revision(
            artifact_id,
            exact_text=exact_text,
            metadata=metadata,
            review_state=review_state,
            created_by=created_by,
        ) if self.db else None

    def revise_artifact_fields(
        self, artifact_id: str, changes: Dict[str, Any], *,
        provenance_confirmed: bool = False,
        created_by: str = "user",
    ) -> Optional[Dict[str, Any]]:
        """Apply a typed, reviewable edit through the shared revision ledger."""
        if not self.db:
            return None
        if any(key in changes for key in ("source_id", "source_path", "source_type")):
            current = self.db.get_artifact(artifact_id) or {}
            source_id = changes.get("source_id", current.get("source_id"))
            sources = self.project_manager.list_sources(include_removed=True) if (
                self.project_manager and hasattr(self.project_manager, "list_sources")
            ) else []
            source = next((item for item in sources if item.get("id") == source_id), None)
            if source_id and not source:
                raise ValueError("Source ID must name a source in this project.")
            if not source_id and any(field in changes for field in ("source_path", "source_type")):
                raise ValueError("Choose a registered source before changing its path or type.")
            if source and "source_id" in changes:
                changes = {
                    **changes,
                    "source_path": source.get("path"),
                    "source_type": source.get("source_type"),
                }
            for field, source_key in (("source_path", "path"), ("source_type", "source_type")):
                if source and field in changes and changes[field] != source.get(source_key):
                    raise ValueError(f"{field} must match the registered source.")
        return self.db.save_artifact_revision(
            artifact_id, field_changes=changes,
            provenance_confirmed=provenance_confirmed,
            extra_artifact_types=list(self._artifact_types),
            created_by=created_by,
        )

    def assign_citation(
        self, citation_id: str, entry_id: Optional[str], *, created_by: str = "user"
    ) -> Dict[str, Any]:
        """Record a reviewed bibliography match, or explicitly leave it unmatched."""
        if not self.db:
            raise ValueError("Artifact storage is unavailable.")
        citation = self.db.get_artifact(citation_id)
        entry = self.db.get_artifact(entry_id) if entry_id else None
        if not citation or citation.get("artifact_type") != "in_text_citation":
            raise ValueError("Select an in-text citation.")
        if entry_id and (not entry or entry.get("artifact_type") != "citation_entry"
                         or entry.get("source_id") != citation.get("source_id")
                         or entry.get("status") != "complete" or entry.get("review_state") in {"rejected", "superseded"}):
            raise ValueError("Select an active bibliography entry from the same source.")
        db = self.db
        with db._write_lock:
            db._conn.execute("SAVEPOINT intelligence_citation_assignment")
            try:
                for edge in db.list_edges(citation_id):
                    if edge.get("edge_type") != "cites" or edge.get("source_artifact_id") != citation_id:
                        continue
                    for assertion in edge.get("assertions") or []:
                        if assertion.get("review_state") != "rejected":
                            db.set_edge_assertion_review_state(assertion["assertion_id"], "rejected")
                if entry_id:
                    edge_id = self.link_artifacts(
                        citation_id, entry_id, "cites", confidence=1.0,
                        created_by=created_by, evidence_artifact_ids=[citation_id],
                        producer_version="manual-v1", metadata={"manual": True},
                    )
                    for assertion in db.list_edge_assertions(edge_id):
                        if assertion.get("created_by") == created_by:
                            db.set_edge_assertion_review_state(assertion["assertion_id"], "verified")
                metadata = {
                    **(citation.get("metadata") or {}),
                    "assigned_bibliography_artifact_id": entry_id or "",
                    "unresolved": not bool(entry_id),
                }
                result = self.revise_artifact_fields(
                    citation_id, {"metadata": metadata}, created_by=created_by
                )
                db._conn.execute("RELEASE SAVEPOINT intelligence_citation_assignment")
                return result
            except Exception:
                db._conn.execute("ROLLBACK TO SAVEPOINT intelligence_citation_assignment")
                db._conn.execute("RELEASE SAVEPOINT intelligence_citation_assignment")
                raise

    def review_artifacts(self, rows, command, *, cancelled=None):
        receipts = []
        project = (id(self.db), id(getattr(self.db, "_conn", None)))
        for row in rows:
            if cancelled and cancelled():
                break
            receipts.append(self.execute_review_command(row["artifact_id"], command,
                            expected_revision=row.get("revision"), expected_project=project))
        return {"count": len(receipts)}

    def get_review_fields(self, row):
        from core.models.artifact_fields import ArtifactField, semantic_fields
        fields = semantic_fields(row)
        known = {spec.path for spec in fields}
        for definition in (self._artifact_types.get(row.get("artifact_type")) or {}).get("review_fields") or []:
            spec = ArtifactField(**definition)
            if spec.path not in known:
                fields.append(spec)
                known.add(spec.path)
        return fields

    def query_occurrences(self, artifact_id, query=None):
        from core.services.intelligence.artifact_browsing import occurrences
        return occurrences(self, artifact_id, query)

    def query_stage_runs(self, query):
        return self.db.query_stage_runs(query)

    def get_browser_preferences(self):
        from core.services.intelligence.artifact_browsing import get_preferences
        return get_preferences(self)

    def save_browser_preferences(self, state, expected_project=None):
        from core.services.intelligence.artifact_browsing import save_preferences
        return save_preferences(self, state, expected_project)

    def inspect_artifact(self, artifact_id, *, history=False, search=""):
        row = self.db.get_artifact(artifact_id) if self.db else None
        if not row:
            raise ValueError("Artifact is no longer available.")
        result = {"row": row, "editor_context": self.get_editor_context(row, search=search)}
        if history:
            result['history_page'] = self.query_artifact_history(artifact_id)
        return result

    def query_artifact_history(self, artifact_id, cursor='', limit=25):
        from core.services.intelligence.artifact_history import query
        return query(self, artifact_id, cursor, limit)

    def query_timeline(self, query=None, filters=None, *, cancelled=lambda:False):
        """Project event candidates from current evidence, without extraction/inference."""
        from core.services.intelligence.event_projection import query_timeline
        return query_timeline(self,query,filters,cancelled=cancelled)

    def get_projection_views(self):
        from core.services.intelligence.projection_views import read
        return read(self)

    def query_entity_network(self, query=None, filters=None, *, cancelled=lambda:False):
        from core.services.intelligence.entity_network_projection import query_network
        return query_network(self, query, filters, cancelled=cancelled)

    def save_projection_view(self, **arguments):
        from core.services.intelligence.projection_views import write
        return write(self,**arguments)

    def check_projection_ids(self, filters):
        from core.services.intelligence.projection_views import check_ids
        return check_ids(self, filters)

    def resolve_reference(self, reference):
        from core.services.intelligence.artifact_references import resolve_reference
        return resolve_reference(self, reference)

    def resolve_references(self, references, *, compact=False):
        from core.services.intelligence.reference_hydration import resolve_references
        return resolve_references(self, references, compact=compact)

    def execute_review_command(self, artifact_id, command, **kwargs):
        from core.services.intelligence.artifact_review_commands import execute_command
        result = execute_command(self, artifact_id, command, **kwargs)
        row = result.get("row") or {}
        if row.get("artifact_type") == "note" and (row.get("metadata") or {}).get("reference_note"):
            from core.events.domains.intelligence_events import LinkedNotePayload
            signal = getattr(getattr(self.project_manager, "bus", None), "intelligence_state_changed", None)
            if signal is not None:
                signal.emit(IntelligenceEvent.LINKED_NOTE_CHANGED, LinkedNotePayload(row.get("parent_artifact_id") or artifact_id, row.get("source_id") or ""))
        signal = getattr(getattr(self.project_manager, "bus", None), "intelligence_state_changed", None)
        if signal is not None:
            row = result.get("row") or {}
            if command in KNOWLEDGE_COMMANDS or (kwargs.get("receipt") or {}).get("kind") == "knowledge":
                signal.emit(IntelligenceEvent.KNOWLEDGE_REVIEWED, KnowledgeReviewPayload(artifact_id,row.get("source_id") or "",(result.get("receipt") or {}).get("decision_id") or ""))
            else:
                signal.emit(IntelligenceEvent.ARTIFACT_REVIEWED, ArtifactReviewPayload(artifact_id, row.get("source_id") or "", row.get("revision", 0)))
        return result

    def count_review_queue(self, issue_id, source_id="", entity_types=()):
        """How many items a queue still has (counted separately so the next item shows first)."""
        return {"remaining": self.get_review_queue(issue_id, source_id, entity_types=entity_types).get("remaining")}

    def get_review_queue(self, issue_id, source_id="", **kwargs):
        from core.services.intelligence.artifact_review_queries import review_queue
        return review_queue(self, self.review_registry, issue_id, source_id, **kwargs)

    def get_editor_context(self, row, **kwargs):
        from core.services.intelligence.artifact_review_queries import editor_context
        return editor_context(self, row, **kwargs)

    def apply_artifact_edit(
        self, artifact_id: str, changes: Dict[str, Any], *,
        provenance_confirmed: bool = False,
        citation_changed: bool = False,
        citation_target: Optional[str] = None,
        created_by: str = "user",
    ) -> Optional[Dict[str, Any]]:
        """Publish a correction and optional citation reassignment atomically."""
        if not self.db:
            raise ValueError("Artifact storage is unavailable.")
        with self.db._write_lock:
            self.db._conn.execute("SAVEPOINT intelligence_artifact_edit")
            try:
                result = self.revise_artifact_fields(
                    artifact_id, changes,
                    provenance_confirmed=provenance_confirmed,
                    created_by=created_by,
                ) if changes else self.db.get_artifact(artifact_id)
                if citation_changed and (self.db.get_artifact(artifact_id) or {}).get("artifact_type") == "entity":
                    from core.services.intelligence.entity_citations import set_cited_entry
                    result = set_cited_entry(self, artifact_id, citation_target)["row"]
                elif citation_changed:
                    result = self.assign_citation(
                        artifact_id, citation_target, created_by=created_by
                    )
                self.db._conn.execute("RELEASE SAVEPOINT intelligence_artifact_edit")
                return result
            except Exception:
                self.db._conn.execute("ROLLBACK TO SAVEPOINT intelligence_artifact_edit")
                self.db._conn.execute("RELEASE SAVEPOINT intelligence_artifact_edit")
                raise

    def set_review_state(
        self, artifact_id: str, review_state: str, *, created_by: str = "user"
    ) -> Optional[Dict[str, Any]]:
        return self.db.set_artifact_review_state(
            artifact_id, review_state, created_by=created_by
        ) if self.db else None

    def get_revisions(self, artifact_id: str) -> List[Dict[str, Any]]:
        return self.db.list_artifact_revisions(artifact_id) if self.db else []

    def get_reprocessing_proposals(self, artifact_id: str) -> List[Dict[str, Any]]:
        return self.db.list_artifact_proposals(artifact_id) if self.db else []

    def citation_assignments(self, source_id: Optional[str] = None) -> Dict[str, Optional[str]]:
        return self.db.citation_assignments(source_id) if self.db else {}

    def rename_entity(self, artifact_id: str, display_name: str) -> Optional[Dict[str, Any]]:
        entity = self.db.get_artifact(artifact_id) if self.db else None
        if not entity or entity.get("artifact_type") != "entity":
            return None
        metadata = {
            **(entity.get("metadata") or {}),
            "canonical_label": display_name,
            "user_named": True,
        }
        return self.revise_artifact(
            artifact_id, exact_text=display_name, metadata=metadata,
            review_state="corrected", created_by="user",
        )

    def prepare_review_decision(self, artifact_id, command, payload=None):
        return preview_decision(self, artifact_id, command, payload)

    def query_decision_candidates(self, artifact_id, command, **kwargs):
        from core.services.intelligence.decision_queries import candidates
        return candidates(self, artifact_id, command, **kwargs)

    def merge_entities(self, source_artifact_id, target_artifact_id):
        return self.execute_review_command(source_artifact_id, "entity_merge", payload={"target_id":target_artifact_id})

    def merge_entity_group(self, source_artifact_ids, target_artifact_id):
        """Merge several entities into one as one undoable decision (all or nothing)."""
        from core.services.intelligence.knowledge_review import merge_group
        result = merge_group(self, source_artifact_ids, target_artifact_id)
        self._knowledge_changed(target_artifact_id, result)
        return {**result, "target_artifact_id": target_artifact_id}

    def undo_entity_merges(self, target_artifact_id, receipt):
        """Undo a group merge; refused if any merged entity changed since."""
        return self.execute_review_command(target_artifact_id, "undo", receipt=receipt)

    def _knowledge_changed(self, artifact_id, result):
        signal = getattr(getattr(self.project_manager, "bus", None), "intelligence_state_changed", None)
        if signal is not None:
            row = result.get("row") or {}
            signal.emit(IntelligenceEvent.KNOWLEDGE_REVIEWED, KnowledgeReviewPayload(
                artifact_id, row.get("source_id") or "", (result.get("receipt") or {}).get("decision_id") or ""))

    def next_connection(self, **filters):
        """Next unreviewed entity connection (sentence, both entities, reason) under the filters."""
        from core.services.intelligence.connection_review import next_connection
        return next_connection(self, **filters)

    def connection_card(self, assertion_id):
        from core.services.intelligence.connection_review import card
        return {"card": card(self, assertion_id)}

    def save_connection(self, assertion_id, edge_type):
        from core.services.intelligence.connection_review import save_connection
        return save_connection(self, assertion_id, edge_type)

    def reject_connection(self, assertion_id):
        return self.execute_review_command(assertion_id, "relation_reject")

    def undo_connection(self, assertion_id, receipt):
        return self.execute_review_command(assertion_id, "undo", receipt=receipt)

    def bibliography_choices(self, text="", source_id=""):
        from core.services.intelligence.entity_citations import entry_choices
        return {"items": entry_choices(self, text, source_id)}

    def set_entity_citation(self, entity_id, entry_id=None):
        """The entity's mentions also cite ``entry_id`` (None clears it); one undoable decision."""
        from core.services.intelligence.entity_citations import set_cited_entry
        result = set_cited_entry(self, entity_id, entry_id)
        self._knowledge_changed(entity_id, result)
        return result

    def merge_suggestions(self, artifact_id):
        """Same-type entities with similar names, best first, each with its reason."""
        from core.services.intelligence.merge_suggestions import suggest
        return {"items": [{"artifact_id": item["row"]["artifact_id"], "name": item["row"].get("exact_text") or "",
                           "mentions": (item["row"].get("metadata") or {}).get("mention_count") or 0,
                           "reason": item["reason"], "score": item["score"]} for item in suggest(self, artifact_id)]}

    def set_entity_parent(self, artifact_id, parent_id=None):
        """Put an entity under a broader one (it stays its own entity); None removes it. Undoable."""
        from core.services.intelligence.entity_hierarchy import set_parent
        result = set_parent(self, artifact_id, parent_id)
        self._knowledge_changed(artifact_id, result)
        return result

    def set_entity_parents(self, artifact_ids, parent_id):
        """Put several entities under one broader entity; each is its own undoable decision."""
        results = [self.set_entity_parent(identity, parent_id) for identity in artifact_ids]
        return {"count": len(results), "receipts": [r["receipt"] for r in results]}

    def connect_entities(self, artifact_id, target_id, edge_type):
        """Record a confirmed typed connection between two different entities. Undoable."""
        from core.services.intelligence.entity_hierarchy import connect
        result = connect(self, artifact_id, target_id, edge_type)
        self._knowledge_changed(artifact_id, result)
        return result

    def entity_pair_preview(self, artifact_id, target_id):
        """Both entities with an example sentence each, for subgroup / connect decisions."""
        from core.services.intelligence.knowledge_review import representative_context
        rows = [self.db.get_artifact(artifact_id), self.db.get_artifact(target_id)]
        if any(not row or row.get("artifact_type") != "entity" for row in rows):
            raise ValueError("Choose two current entities.")
        return {"source": rows[0], "target": rows[1], "can_confirm": artifact_id != target_id,
                "source_context": representative_context(self, rows[0]),
                "target_context": representative_context(self, rows[1]),
                "project_identity": [id(self.db), id(self.db._conn)]}

    def split_entity(self, source_artifact_id, mention_ids, display_name):
        return self.execute_review_command(source_artifact_id, "entity_split", payload={"mention_ids":mention_ids,"display_name":display_name})

    def never_merge_entities(self, first_artifact_id, second_artifact_id):
        return self.execute_review_command(first_artifact_id, "entity_never_merge", payload={"target_id":second_artifact_id})

    def set_edge_assertion_review_state(
        self, assertion_id: str, review_state: str
    ) -> Optional[Dict[str, Any]]:
        return (
            self.db.set_edge_assertion_review_state(assertion_id, review_state)
            if self.db else None
        )

    def create_artifact(
        self,
        *,
        source_id: Optional[str],
        artifact_type: str,
        storage_ref_type: str,
        storage_ref_id: str,
        exact_text: str = "",
        normalized_text: Optional[str] = None,
        source_path: Optional[str] = None,
        source_type: str = "",
        parent_artifact_id: Optional[str] = None,
        locator: Optional[Dict[str, Any]] = None,
        section_path: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        confidence: Optional[float] = None,
        created_by: str = "model",
        extractor_name: Optional[str] = None,
        model_role: Optional[str] = None,
        model_name: Optional[str] = None,
        identity: Optional[ProducerIdentity] = None,
    ) -> Optional[str]:
        """Persist a derived artifact (deterministic or model-produced).

        The single rules-compliant write path used by analysis-mode steps —
        idempotent via ``stable_id`` and provenance-stamped (``created_by``,
        ``confidence``, ``model_role``/``model_name``, ``input_hash``).
        """
        if not self.db or not artifact_type or not storage_ref_id:
            return None
        artifact_id = ArtifactRecord.stable_id(source_id or "", artifact_type, storage_ref_type, storage_ref_id)
        identity = identity or ProducerIdentity(
            producer_id=extractor_name or created_by or "artifact_writer",
            method_kind=(
                MethodKind.GENERATIVE.value if model_name else MethodKind.DETERMINISTIC.value
            ),
            model_id=model_name or "",
        )
        self._publish(identity, ArtifactRecord(
            artifact_id=artifact_id,
            source_id=source_id,
            source_path=source_path,
            source_type=source_type,
            artifact_type=artifact_type,
            storage_ref_type=storage_ref_type,
            storage_ref_id=storage_ref_id,
            parent_artifact_id=parent_artifact_id,
            exact_text=exact_text or None,
            normalized_text=(
                normalized_text
                if normalized_text is not None
                else (exact_text or "").strip().lower() or None
            ),
            locator=locator or {},
            section_path=list(section_path or []),
            metadata=metadata or {},
            confidence=confidence,
            created_by=created_by,
            extractor_name=extractor_name,
            extractor_version="1.0",
            model_role=model_role,
            model_name=model_name,
            input_hash=self.text_input_hash(exact_text or ""),
        ))
        return artifact_id

    def create_edge(
        self,
        source_artifact_id: str,
        target_artifact_id: str,
        edge_type: str,
        confidence: Optional[float] = None,
        created_by: str = "model",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """Persist a derived edge with provenance (thin wrapper over link_artifacts)."""
        if not self.db or not source_artifact_id or not target_artifact_id:
            return None
        from core.models.intelligence_models import ArtifactEdgeRecord
        edge_id = ArtifactEdgeRecord.stable_id(source_artifact_id, target_artifact_id, edge_type)
        return self.db.upsert_edge(ArtifactEdgeRecord(
            edge_id=edge_id,
            source_artifact_id=source_artifact_id,
            target_artifact_id=target_artifact_id,
            edge_type=edge_type,
            confidence=confidence,
            created_by=created_by,
            metadata=metadata or {},
            evidence_artifact_ids=list((metadata or {}).get("evidence_artifact_ids") or []),
            producer_version=str((metadata or {}).get("producer_version") or "") or None,
        ))

    def get_related_artifacts(self, artifact_id: str) -> Dict[str, Any]:
        if not self.db:
            return {"artifact": None, "edges": []}
        from core.services.intelligence.artifact_eligibility import active_assertions
        edges = []
        for edge in self.db.list_edges(artifact_id):
            assertions = active_assertions(edge)
            if assertions:
                edges.append({**edge, "assertions": assertions})
        return {
            "artifact": self.db.get_artifact(artifact_id),
            "edges": edges,
        }

    def register_producer(self, producer_id: str, callback) -> None:
        if not producer_id or not callable(callback):
            raise ValueError("Artifact producer requires a stable id and callable.")
        self._producers[producer_id] = callback

    def unregister_producer(self, producer_id: str) -> None:
        self._producers.pop(producer_id, None)

    def register_artifact_type(self, artifact_type: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        if not artifact_type:
            raise ValueError("Artifact type is required.")
        from core.models.artifact_fields import ArtifactField
        definition = dict(metadata or {})
        for field in definition.get("review_fields") or []:
            spec = ArtifactField(**field)
            parts = spec.path.split(".")
            if len(parts) != 2 or parts[0] != "metadata" or parts[1] in {"producer", "stage", "class_scores", "source_revision", "migration_conflict", "model_name"}:
                raise ValueError("Extension review fields must name editable metadata, not identity or diagnostics.")
            if spec.kind not in {"text", "choice", "list", "boolean", "multiline"}:
                raise ValueError("Unknown semantic field control type.")
            if spec.kind == "choice" and not spec.choices:
                raise ValueError("Choice fields require explicit valid values.")
        self._artifact_types[artifact_type] = definition
        self._schema_changed(artifact_type)

    def register_edge_type(self, edge_type: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        if not edge_type:
            raise ValueError("Edge type is required.")
        self._edge_types[edge_type] = dict(metadata or {})

    def unregister_artifact_type(self, artifact_type, *, plugin_id):
        if (self._artifact_types.get(artifact_type) or {}).get("plugin_id") == plugin_id:
            self._artifact_types.pop(artifact_type, None)
            self._schema_changed(artifact_type)

    def _schema_changed(self, artifact_type):
        from core.events.domains.intelligence_events import IntelligenceEvent, ArtifactSchemaPayload
        self.schema_generation += 1
        bus = getattr(self.project_manager, 'bus', None)
        if bus is not None:
            bus.intelligence_state_changed.emit(IntelligenceEvent.ARTIFACT_SCHEMA_CHANGED,
                ArtifactSchemaPayload(artifact_type, self.schema_generation))

    def list_registered_extensions(self) -> Dict[str, Any]:
        return {
            "producers": sorted(self._producers),
            "artifact_types": dict(self._artifact_types),
            "edge_types": dict(self._edge_types),
        }

    def mark_source_stale(self, source_id: str) -> int:
        if not self.db:
            return 0
        count = self.db.mark_source_artifacts_status(source_id, StageStatus.STALE.value)
        try:
            self.db.mark_artifact_embeddings_status(source_id, StageStatus.STALE.value)
        except Exception:
            pass
        return count

    def delete_derived(self, source_id: str, stage: Optional[str] = None) -> int:
        return self.db.delete_derived_artifacts(source_id, stage=stage) if self.db else 0

    def archive_derived(self, source_id: str, stage: Optional[str] = None) -> int:
        return self.db.archive_derived_artifacts(source_id, stage=stage) if self.db else 0

    @staticmethod
    def text_input_hash(text: str) -> str:
        return hashlib.sha256((text or "").encode("utf-8", errors="replace")).hexdigest()

    def source_input_hash(self, path: Optional[str], *, compute: bool = True) -> str:
        """sha256 of the file, cached by mtime and size.

        ``compute=False`` returns only a cached value ("" otherwise): the Qt
        thread uses it so a large file is never hashed there (rules.md §1.3);
        the stage worker computes the real hash before the stage runs.
        """
        if not path:
            return ""
        try:
            stat = os.stat(path)
            cached = self._source_hash_cache.get(path)
            if cached and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
                return cached[2]
            if not compute:
                return ""
            from core.utils.file_hash import file_sha256
            value = file_sha256(path)
            self._source_hash_cache[path] = (stat.st_mtime_ns, stat.st_size, value)
            return value
        except OSError:
            return hashlib.sha256(path.encode("utf-8", errors="replace")).hexdigest()

    def _source_by_id(self, source_id: str) -> Optional[Dict[str, Any]]:
        if not self.project_manager:
            return None
        lookup = getattr(self.project_manager, "get_source_record", None)
        if lookup:
            return lookup(source_id) or self.project_manager.get_source_record_by_path(source_id)
        for source in self.project_manager.list_sources(include_removed=True):
            if source.get("id") == source_id or source.get("path") == source_id:
                return source
        return None

    def _source_by_path(self, path: str) -> Optional[Dict[str, Any]]:
        if not self.project_manager:
            return None
        return self.project_manager.get_source_record_by_path(path)

    def _nearest_block_for_highlight(self, source_id: str, path: str, highlight: Dict[str, Any]):
        des = self.document_extraction_service
        if not des:
            return None
        try:
            blocks = des.get_blocks(source_id) or des.get_blocks(path) or []
        except Exception:
            return None
        page = highlight.get("page_num")
        try:
            page = int(page) if page is not None else None
        except Exception:
            page = None
        text = (highlight.get("text_content") or "").strip().lower()
        page_blocks = [block for block in blocks if page is None or getattr(block, "page_num", None) == page]
        if text:
            for block in page_blocks:
                block_text = (getattr(block, "exact_text", "") or "").lower()
                if text and text[:80] in block_text:
                    return block
        return page_blocks[0] if page_blocks else None

    @staticmethod
    def _source_id_for_path(path: Optional[str]) -> str:
        import uuid
        return f"source:{uuid.uuid5(uuid.NAMESPACE_URL, path or '')}"


def _list_filters(source_id: Optional[str], filters: Dict[str, Any]) -> Dict[str, Any]:
    """Translate the compatibility list filters into an ArtifactQuery mapping."""
    for singular, plural in (("artifact_type", "artifact_types"), ("status", "statuses")):
        if singular in filters:
            value = filters.pop(singular)
            if value:
                filters.setdefault(plural, [value])
    filters.setdefault("source_scope", SourceScope(source_ids=[source_id] if source_id else []))
    return filters
