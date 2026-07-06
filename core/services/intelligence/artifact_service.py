"""Artifact reference indexing over existing Papyrus storage."""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, Iterable, List, Optional

from core.models.intelligence_models import (
    ArtifactEdgeRecord,
    ArtifactRecord,
    ProcessingStage,
    StageStatus,
)


class ArtifactService:
    def __init__(self, project_manager=None, document_extraction_service=None):
        self.project_manager = project_manager
        self.document_extraction_service = document_extraction_service
        self._producers: Dict[str, Any] = {}
        self._artifact_types: Dict[str, Dict[str, Any]] = {}
        self._edge_types: Dict[str, Dict[str, Any]] = {}

    @property
    def db(self):
        return getattr(self.project_manager, "db_intelligence", None)

    def ensure_source_artifact(self, source: Dict[str, Any]) -> Optional[str]:
        if not self.db or not source:
            return None
        source_id = source.get("id") or source.get("source_id") or self._source_id_for_path(source.get("path"))
        path = source.get("path") or source.get("source_path")
        artifact_id = ArtifactRecord.stable_id(source_id, "source", "sources", source_id)
        self.db.upsert_artifact(ArtifactRecord(
            artifact_id=artifact_id,
            source_id=source_id,
            source_path=path,
            source_type=source.get("source_type"),
            artifact_type="source",
            storage_ref_type="sources",
            storage_ref_id=source_id,
            exact_text=source.get("title") or os.path.basename(path or ""),
            metadata={"stage": ProcessingStage.SOURCE_REGISTERED.value},
            input_hash=self.source_input_hash(path),
        ))
        return artifact_id

    def sync_source_artifacts(self, source_id: str) -> List[str]:
        source = self._source_by_id(source_id)
        return [self.ensure_source_artifact(source)] if source else []

    def sync_extraction_artifacts(self, source_id: str, stages: Optional[Iterable[str]] = None) -> List[str]:
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
                written.append(self.db.upsert_artifact(ArtifactRecord(
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
                    extractor_name="document_extraction",
                    extractor_version="1.0",
                    input_hash=self.text_input_hash(getattr(block, "exact_text", "") or ""),
                )))
                if source_artifact:
                    self.link_artifacts(artifact_id, source_artifact, "located_in")

        if des and (not stages or ProcessingStage.CITATIONS.value in stages):
            index = des.get_citation_index(source_id) or des.get_citation_index(path)
            if index:
                for entry in getattr(index, "bibliography_entries", []) or []:
                    entry_id = getattr(entry, "entry_id", None) or getattr(entry, "source_id", None) or getattr(entry, "key", None)
                    if not entry_id:
                        continue
                    artifact_id = ArtifactRecord.stable_id(source_id, "citation_entry", "extraction_citation_index", entry_id)
                    written.append(self.db.upsert_artifact(ArtifactRecord(
                        artifact_id=artifact_id,
                        source_id=source_id,
                        source_path=path,
                        source_type=source_type,
                        artifact_type="citation_entry",
                        storage_ref_type="extraction_citation_index",
                        storage_ref_id=entry_id,
                        parent_artifact_id=source_artifact,
                        exact_text=getattr(entry, "raw_text", "") or getattr(entry, "raw", "") or getattr(entry, "title", ""),
                        metadata={"stage": ProcessingStage.CITATIONS.value},
                        confidence=1.0,
                    )))
                for cite in getattr(index, "in_text_citations", []) or []:
                    cite_id = getattr(cite, "citation_id", None)
                    if not cite_id:
                        continue
                    loc = getattr(cite, "location", None)
                    artifact_id = ArtifactRecord.stable_id(source_id, "in_text_citation", "extraction_citation_index", cite_id)
                    written.append(self.db.upsert_artifact(ArtifactRecord(
                        artifact_id=artifact_id,
                        source_id=source_id,
                        source_path=path,
                        source_type=source_type,
                        artifact_type="in_text_citation",
                        storage_ref_type="extraction_citation_index",
                        storage_ref_id=cite_id,
                        parent_artifact_id=source_artifact,
                        exact_text=getattr(cite, "context_text", "") or getattr(cite, "raw_text", ""),
                        locator={"page": getattr(loc, "page_num", None), "block_id": getattr(loc, "block_id", None)},
                        metadata={
                            "stage": ProcessingStage.CITATIONS.value,
                            "raw_text": getattr(cite, "raw_text", ""),
                            "citation_type": getattr(cite, "citation_type", ""),
                            "year": getattr(cite, "year", None),
                        },
                        confidence=getattr(getattr(cite, "provenance", None), "confidence", None),
                    )))

        if des and (not stages or ProcessingStage.ENTITIES.value in stages):
            for entity in des.get_entities(source_id) or des.get_entities(path):
                entity_id = getattr(entity, "entity_id", "")
                if not entity_id:
                    continue
                artifact_id = ArtifactRecord.stable_id(source_id, "entity", "extraction_results", entity_id)
                written.append(self.db.upsert_artifact(ArtifactRecord(
                    artifact_id=artifact_id,
                    source_id=source_id,
                    source_path=path,
                    source_type=source_type,
                    artifact_type="entity",
                    storage_ref_type="extraction_results",
                    storage_ref_id=entity_id,
                    parent_artifact_id=source_artifact,
                    exact_text=getattr(entity, "canonical_text", ""),
                    normalized_text=getattr(entity, "normalized_text", ""),
                    metadata={
                        "stage": ProcessingStage.ENTITIES.value,
                        "entity_type": getattr(entity, "entity_type", ""),
                        "aliases": getattr(entity, "aliases", []),
                    },
                    confidence=1.0,
                )))

        if des and (not stages or ProcessingStage.DATES.value in stages):
            for item in des.get_dates(source_id) or des.get_dates(path):
                date_id = getattr(item, "date_id", "")
                if not date_id:
                    continue
                loc = getattr(item, "location", None)
                artifact_id = ArtifactRecord.stable_id(source_id, "date_mention", "extraction_results", date_id)
                written.append(self.db.upsert_artifact(ArtifactRecord(
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
                    locator={"page": getattr(loc, "page_num", None), "block_id": getattr(loc, "block_id", None)},
                    metadata={
                        "stage": ProcessingStage.DATES.value,
                        "exact_text": getattr(item, "exact_text", ""),
                        "certainty": getattr(item, "certainty", ""),
                    },
                    confidence=getattr(getattr(item, "provenance", None), "confidence", None),
                )))

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
                written.append(self.db.upsert_artifact(ArtifactRecord(
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

    def sync_annotation_artifact(self, highlight_id: str) -> Optional[str]:
        if not self.db or not highlight_id:
            return None
        highlight = self.project_manager.get_highlight(highlight_id)
        if not highlight:
            return None
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
        saved = self.db.upsert_artifact(ArtifactRecord(
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
                "color": highlight.get("color"),
                "section_path": section_path,
                "block_id": getattr(nearest_block, "block_id", None),
            },
            input_hash=self.text_input_hash(json.dumps(highlight, sort_keys=True, ensure_ascii=False)),
        ))
        if source_artifact:
            self.link_artifacts(saved, source_artifact, "located_in")
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
            result.append(self.db.upsert_artifact(ArtifactRecord(
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
            metadata=metadata or {},
        ))

    def get_artifacts(self, source_id: Optional[str] = None, filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        filters = filters or {}
        return self.db.list_artifacts(
            source_id=source_id,
            artifact_type=filters.get("artifact_type"),
            status=filters.get("status"),
            limit=filters.get("limit", 500),
        ) if self.db else []

    def get_related_artifacts(self, artifact_id: str) -> Dict[str, Any]:
        if not self.db:
            return {"artifact": None, "edges": []}
        return {
            "artifact": self.db.get_artifact(artifact_id),
            "edges": self.db.list_edges(artifact_id),
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
        self._artifact_types[artifact_type] = dict(metadata or {})

    def register_edge_type(self, edge_type: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        if not edge_type:
            raise ValueError("Edge type is required.")
        self._edge_types[edge_type] = dict(metadata or {})

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

    @staticmethod
    def text_input_hash(text: str) -> str:
        return hashlib.sha256((text or "").encode("utf-8", errors="replace")).hexdigest()

    @staticmethod
    def source_input_hash(path: Optional[str]) -> str:
        if not path:
            return ""
        try:
            stat = os.stat(path)
            raw = f"{path}|{stat.st_mtime_ns}|{stat.st_size}"
        except OSError:
            raw = path
        return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()

    def _source_by_id(self, source_id: str) -> Optional[Dict[str, Any]]:
        if not self.project_manager:
            return None
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
