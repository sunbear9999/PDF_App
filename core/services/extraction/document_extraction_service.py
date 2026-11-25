"""
DocumentExtractionService — central orchestrator for the deterministic
extraction layer.

All features (timeline, entity discovery, citation dock, data dock, analysis,
RAG) call this service to get extracted content rather than parsing PDFs
independently. Each getter is backed by ExtractionCache so repeated calls
within a session are free.

Phase 0 provides the skeleton with empty implementations; each Phase 1–4
fills in the real extraction logic by injecting sub-services.
"""
from __future__ import annotations

import dataclasses
import os
from core.utils.file_hash import file_sha256
from core.services.document.ocr_engine_setup import tesseract_path
from core.services.extraction.block_roles import is_ocr_text
from core.services.extraction import block_source_snapshot
from core.services.extraction.source_normalization import SourceNormalizationService, prepare_blocks
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from core.events.domains.extraction_events import (
    ExtractionEvent,
    ExtractionIntent,
    ExtractionPayload,
)
from core.models.extraction_models import (
    CitationIndex,
    DocumentBlock,
    ExtractedDate,
    ExtractedEntity,
    ExtractedTable,
    ExtractionProvenance,
    SemanticSection,
    SourceLocation,
)

if TYPE_CHECKING:
    from core.services.extraction.extraction_cache import ExtractionCache


# Cached results are keyed by extractor version: bump the entry here whenever an
# extractor's output changes, and every project recomputes that stage instead of
# serving output from the superseded logic.
_BLOCK_PIPELINE_VERSION = "3.6"   # native contents destinations participate in shared structure

_EXTRACTOR_VERSIONS = {
    "section_structure": "2.2",       # native contents destinations
    "sentence_segmentation_v3": "3.6",  # + endnotes are prose; 3.6: block line geometry not copied per sentence
    "citation_extraction": "2.1",     # hanging indents, note → bibliography links; 2.1 raster note markers
    "entity_extraction": "2.2",       # complete bounded NER coverage, explicit failure
    "date_extraction": "1.8",  # 1.8: numeric dates use the document's day/month convention
    "table_extraction": "1.1",
}


def _citation_version() -> str:
    """Raster marker recovery needs Tesseract; installing it later must refresh cached indexes."""
    return _EXTRACTOR_VERSIONS["citation_extraction"] + ("+raster" if tesseract_path() else "")


class DocumentExtractionService:
    """
    Stable facade over the extraction pipeline.

    Sub-services (PDFLayoutParser, CitationExtractionService, etc.) are
    injected after construction so Phase 0 can wire into PapyrusCore before
    any sub-service exists.
    """

    def __init__(self, project_manager, bus, extraction_cache: "ExtractionCache") -> None:
        self._normalizer = SourceNormalizationService()
        self._pm = project_manager
        self._bus = bus
        self._cache = extraction_cache

        # Sub-services injected in later phases
        self._layout_parser = None
        self._semantic_block_service = None
        self._section_structure_extractor = None
        self._entity_extraction_service = None
        self._date_extraction_service = None
        self._citation_extraction_service = None
        self._table_extraction_service = None
        self._sentence_segmentation_service = None
        self._parser_registry = None           # DocumentParserRegistry — per-format parser dispatch
        self._source_format_registry = None    # SourceFormatRegistry — resolves a doc_id's format

        bus.extraction_action_requested.connect(self._on_intent)

    # ------------------------------------------------------------------
    # Sub-service injection (called by PapyrusCore as phases are wired in)
    # ------------------------------------------------------------------

    def set_source_normalizer(self, normalizer) -> None:
        self._normalizer = normalizer

    def set_layout_parser(self, parser) -> None:
        self._layout_parser = parser

    def set_semantic_block_service(self, svc) -> None:
        self._semantic_block_service = svc

    def set_section_structure_extractor(self, svc) -> None:
        self._section_structure_extractor = svc

    def set_entity_extraction_service(self, svc) -> None:
        self._entity_extraction_service = svc

    def set_date_extraction_service(self, svc) -> None:
        self._date_extraction_service = svc

    def set_citation_extraction_service(self, svc) -> None:
        self._citation_extraction_service = svc

    def set_table_extraction_service(self, svc) -> None:
        self._table_extraction_service = svc

    def set_sentence_segmentation_service(self, svc) -> None:
        self._sentence_segmentation_service = svc

    def set_parser_registry(self, registry) -> None:
        self._parser_registry = registry

    def set_source_format_registry(self, registry) -> None:
        self._source_format_registry = registry

    # ------------------------------------------------------------------
    # Public getters — primary API surface
    # ------------------------------------------------------------------

    def get_blocks(self, doc_id: str) -> List[DocumentBlock]:
        """Return cached DocumentBlocks or trigger parsing. Thread-safe read."""
        cached = self._cache.get_blocks(doc_id, _BLOCK_PIPELINE_VERSION)
        if cached is not None:
            snapshot = self._cache.get(doc_id, block_source_snapshot.SNAPSHOT_NAME,
                                      'snapshot', block_source_snapshot.SNAPSHOT_VERSION) or {}
            producers = snapshot.get('producers') or {}
            return [self._dict_to_block({**b, **snapshot.get('layout', {}).get(b['block_id'], {}), 'provenance': b.get('provenance') or producers.get(b['block_id'])}) for b in cached]
        if self._layout_parser is None and self._parser_registry is None:
            return []
        return self._parse_and_cache(doc_id)

    def get_cached_block_snapshot(self, doc_id: str) -> Dict[str, Any]:
        """Cache-only verified read; never parse or start OCR/model work."""
        rows = self._cache.get_blocks(doc_id, _BLOCK_PIPELINE_VERSION) or []
        snapshot = block_source_snapshot.read(self._cache, doc_id, rows)
        path, _ = self._resolve_source(doc_id)
        if not path or file_sha256(path) != snapshot['source_hash']:
            raise ValueError('The source changed after extraction. Reprocess it before selecting a passage.')
        return {**snapshot, 'blocks': [self._dict_to_block({**row, **snapshot.get('layout', {}).get(row['block_id'], {}), 'provenance':
            snapshot.get('producers', {}).get(row['block_id'])}) for row in rows]}

    def refresh_blocks(self, doc_id: str) -> List[DocumentBlock]:
        """Explicit processing-stage entry; upgrade unbound or changed caches."""
        try:
            return self.get_cached_block_snapshot(doc_id)['blocks']
        except (ValueError, OSError):
            if self._layout_parser is None and self._parser_registry is None:
                raise ValueError('A source parser is required to refresh these extracted blocks.')
            self.invalidate(doc_id)
            return self._parse_and_cache(doc_id)

    def import_blocks(self, doc_id: str, blocks: List[Dict[str, Any]]) -> None:
        """Persist external OCR through the canonical extraction cache."""
        if not doc_id:
            raise ValueError("doc_id is required")
        path, _ = self._resolve_source(doc_id)
        source_hash = file_sha256(path) if path else ''
        normalized = []
        for block in blocks or []:
            item = dict(block)
            item["doc_id"] = doc_id
            if not item.get("block_id") or not item.get("exact_text"):
                raise ValueError("Imported OCR blocks need block_id and exact_text.")
            item.setdefault("normalized_text", " ".join(str(item["exact_text"]).split()))
            item.setdefault("block_type", "paragraph")
            item.setdefault("section_path", [])
            item["extractor_version"] = _BLOCK_PIPELINE_VERSION
            normalized.append(item)
        normalized = self._normalize_imported(normalized)
        if normalized:
            self._cache.set_blocks(doc_id, normalized, _BLOCK_PIPELINE_VERSION)
            block_source_snapshot.store(self._cache, doc_id, normalized, source_hash)

    def _normalize_imported(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Imported OCR/text gets the same furniture normalization as parsed sources."""
        if not rows or self._normalizer is None:
            return rows
        blocks = self._normalizer.normalize([self._dict_to_block(row) for row in rows])
        for row, block in zip(rows, blocks):
            row["block_type"] = block.block_type
            row["furniture_spans"] = block.furniture_spans
        return rows

    def get_sections(self, doc_id: str) -> List[SemanticSection]:
        """Return the heading-derived section tree for a document.

        Sections are cached as a side effect of _parse_and_cache(); if blocks
        were already cached from before this getter existed, they are
        recomputed here from the cached blocks rather than re-parsing the PDF.
        """
        cached = self._cache.get(doc_id, "section_structure", "sections",
                                 _EXTRACTOR_VERSIONS["section_structure"])
        if cached is not None:
            return [self._dict_to_section(s) for s in cached]
        if self._section_structure_extractor is None:
            return []
        blocks = self.get_blocks(doc_id)
        if not blocks:
            return []
        cached = self._cache.get(doc_id, "section_structure", "sections",
                                 _EXTRACTOR_VERSIONS["section_structure"])
        if cached is not None:
            return [self._dict_to_section(s) for s in cached]
        sections, _ = self._section_structure_extractor.extract(blocks)
        self._cache.set(
            doc_id, "section_structure", _EXTRACTOR_VERSIONS["section_structure"], "sections",
            [self._section_to_dict(s) for s in sections],
        )
        return sections

    def get_sentences(self, doc_id: str) -> List[Dict[str, Any]]:
        """Return ordered sentence records (dicts) with block/page/char provenance.

        Cached like every other getter. Sentences are the finest-grained
        provenance unit; every downstream stage (classification, entity
        mentions, retrieval) keys against ``sentence_id``.
        """
        extractor = "sentence_segmentation_v3"
        cached = self._cache.get(doc_id, extractor, "sentences",
                                 _EXTRACTOR_VERSIONS[extractor])
        if cached is not None:
            return cached
        if self._sentence_segmentation_service is None:
            return []
        blocks = self.get_blocks(doc_id)
        if not blocks:
            return []
        sentences = self._sentence_segmentation_service.segment(blocks, doc_id)
        self._cache.set(
            doc_id, extractor, _EXTRACTOR_VERSIONS[extractor], "sentences", sentences,
        )
        return sentences

    def get_citation_index(self, doc_id: str) -> Optional[CitationIndex]:
        """Return the citation index for a document, building it if needed."""
        cached = self._cache.get(
            doc_id, "citation_extraction", "citation_index", _citation_version(),
        )
        if cached is not None:
            return self._dict_to_citation_index(cached, doc_id)
        if self._citation_extraction_service is None:
            return None
        blocks = self.get_blocks(doc_id)
        if not blocks:
            return None
        return self._extract_and_cache_citations(doc_id, blocks)

    def _marker_images(self, doc_id: str, blocks: List[DocumentBlock]):
        """Page images for raster note-marker recovery: scanned PDFs, Tesseract installed."""
        path, _source_type = self._resolve_source(doc_id)
        if not path or not str(path).lower().endswith(".pdf") or not tesseract_path():
            return None
        if not any(is_ocr_text(block) for block in blocks):
            return None
        from core.services.extraction.raster_markers import PdfMarkerImages
        return PdfMarkerImages(path)

    def get_entities(self, doc_id: str) -> List[ExtractedEntity]:
        """Return extracted entities for a document."""
        cached = self._cache.get(doc_id, "entity_extraction", "entities",
                                 _EXTRACTOR_VERSIONS["entity_extraction"])
        if cached is not None:
            return [self._dict_to_entity(e) for e in cached]
        if self._entity_extraction_service is None:
            return []
        blocks = self.get_blocks(doc_id)
        if not blocks:
            return []
        return self._extract_and_cache_entities(doc_id, blocks)

    def get_cached_entities(self, doc_id: str) -> List[ExtractedEntity]:
        """Return cached extracted entities without running extraction."""
        cached = self._cache.get(doc_id, "entity_extraction", "entities",
                                 _EXTRACTOR_VERSIONS["entity_extraction"])
        if cached is None:
            return []
        return [self._dict_to_entity(e) for e in cached]

    def get_dates(self, doc_id: str) -> List[ExtractedDate]:
        """Return extracted dates for a document."""
        cached = self._cache.get(doc_id, "date_extraction", "dates",
                                 _EXTRACTOR_VERSIONS["date_extraction"])
        if cached is not None:
            return [self._dict_to_date(d) for d in cached]
        if self._date_extraction_service is None:
            return []
        blocks = self.get_blocks(doc_id)
        if not blocks:
            return []
        return self._extract_and_cache_dates(doc_id, blocks)

    def get_tables(self, doc_id: str) -> List[ExtractedTable]:
        """Return extracted tables for a document."""
        cached = self._cache.get(doc_id, "table_extraction", "tables",
                                 _EXTRACTOR_VERSIONS["table_extraction"])
        if cached is not None:
            return [self._dict_to_table(t) for t in cached]
        if self._table_extraction_service is None:
            return []
        blocks = self.get_blocks(doc_id)
        if not blocks:
            return []
        return self._extract_and_cache_tables(doc_id, blocks)

    def invalidate(self, doc_id: str) -> None:
        """Discard all cached extraction data for a document (e.g. on re-import)."""
        self._cache.invalidate(doc_id)

    def resolve_pdf_path(self, doc_id: str) -> Optional[str]:
        """Resolve the on-disk path for a doc_id via ProjectManager. Kept as a
        thin back-compat wrapper — prefer _resolve_source() for new code,
        which also returns the source_type needed for parser dispatch."""
        path, _source_type = self._resolve_source(doc_id)
        return path

    def _resolve_source(self, doc_id: str) -> Tuple[Optional[str], Optional[str]]:
        """Return (path, source_type) for doc_id.

        Prefers the persisted `source_type` column from ProjectManager's
        sources table; falls back to SourceFormatRegistry detection when the
        row has none (or doc_id isn't a known source row, e.g. a bare path
        passed directly).
        """
        path = None
        source_type = None
        lookup = getattr(self._pm, 'get_source_record', None)
        source = lookup(doc_id) if lookup else None
        if not source:
            by_path = getattr(self._pm, 'get_source_record_by_path', None)
            source = by_path(doc_id) if by_path else None
        if source:
            return source.get('path'), source.get('source_type')
        try:
            sources = self._pm.get_sources()
            for src in sources:
                if src.get("path") == doc_id or src.get("id") == doc_id:
                    path = src.get("path")
                    source_type = src.get("source_type")
                    break
        except Exception:
            pass
        if path is None and os.path.isfile(doc_id):
            path = doc_id
        if path and not source_type and self._source_format_registry is not None:
            source_type = self._source_format_registry.detect_source_type(path)
        return path, source_type

    # ------------------------------------------------------------------
    # Intent handler
    # ------------------------------------------------------------------

    def _on_intent(self, intent: ExtractionIntent, payload: ExtractionPayload) -> None:
        if intent == ExtractionIntent.PARSE_DOCUMENT:
            self.get_blocks(payload.doc_id)
        elif intent == ExtractionIntent.EXTRACT_CITATIONS:
            self.get_citation_index(payload.doc_id)
        elif intent == ExtractionIntent.EXTRACT_ENTITIES:
            self.get_entities(payload.doc_id)
        elif intent == ExtractionIntent.EXTRACT_DATES:
            self.get_dates(payload.doc_id)
        elif intent == ExtractionIntent.EXTRACT_TABLES:
            self.get_tables(payload.doc_id)
        elif intent == ExtractionIntent.INVALIDATE_CACHE:
            self.invalidate(payload.doc_id)

    # ------------------------------------------------------------------
    # Internal parsing helpers (filled in by Phase 1)
    # ------------------------------------------------------------------

    def _parse_and_cache(self, doc_id: str) -> List[DocumentBlock]:
        path, source_type = self._resolve_source(doc_id)
        if not path:
            return []
        parser = None
        if self._parser_registry is not None:
            provider_id = None
            if self._source_format_registry is not None and source_type:
                spec = self._source_format_registry.get(source_type)
                if spec and spec.parser_provider_id:
                    provider_id = spec.parser_provider_id
            parser = self._parser_registry.get_parser(provider_id)
        elif self._layout_parser is not None:
            parser = self._layout_parser.parse
        if parser is None:
            return []
        try:
            source_hash = file_sha256(path)
            blocks = parser(path, doc_id)
            sections, blocks = prepare_blocks(
                blocks, self._normalizer, self._semantic_block_service,
                self._section_structure_extractor,
            )
            if file_sha256(path) != source_hash:
                raise ValueError('The source changed during extraction. Run extraction again.')
            rows = [self._block_to_dict(b) for b in blocks]
            self._cache.set_blocks(
                doc_id, rows,
                _BLOCK_PIPELINE_VERSION,
            )
            block_source_snapshot.store(self._cache, doc_id, rows, source_hash)
            if sections:
                self._cache.set(
                    doc_id, "section_structure", _EXTRACTOR_VERSIONS["section_structure"], "sections",
                    [self._section_to_dict(s) for s in sections],
                )
            self._bus.extraction_state_changed.emit(
                ExtractionEvent.BLOCKS_READY,
                ExtractionPayload(doc_id=doc_id),
            )
            return blocks
        except Exception as exc:
            self._bus.extraction_state_changed.emit(
                ExtractionEvent.EXTRACTION_FAILED,
                ExtractionPayload(doc_id=doc_id, error=str(exc)),
            )
            return []

    def _extract_and_cache_citations(
        self, doc_id: str, blocks: List[DocumentBlock]
    ) -> Optional[CitationIndex]:
        images = self._marker_images(doc_id, blocks)
        try:
            index = self._citation_extraction_service.build_citation_index(
                doc_id, blocks, marker_images=images, tesseract=tesseract_path() if images else "",
            )
            self._cache.set(
                doc_id, "citation_extraction", _citation_version(),
                "citation_index", self._citation_index_to_dict(index),
            )
            self._bus.extraction_state_changed.emit(
                ExtractionEvent.CITATIONS_READY,
                ExtractionPayload(doc_id=doc_id),
            )
            return index
        except Exception as exc:
            self._bus.extraction_state_changed.emit(
                ExtractionEvent.EXTRACTION_FAILED,
                ExtractionPayload(doc_id=doc_id, error=str(exc)),
            )
            return None
        finally:
            if images is not None:
                images.close()

    def _extract_and_cache_entities(
        self, doc_id: str, blocks: List[DocumentBlock]
    ) -> List[ExtractedEntity]:
        try:
            entities = self._entity_extraction_service.extract_from_blocks(
                blocks, doc_id
            )
            self._cache.set(
                doc_id, "entity_extraction", _EXTRACTOR_VERSIONS["entity_extraction"],
                "entities", [self._entity_to_dict(e) for e in entities],
            )
            self._bus.extraction_state_changed.emit(
                ExtractionEvent.ENTITIES_READY,
                ExtractionPayload(doc_id=doc_id),
            )
            return entities
        except Exception as exc:
            self._bus.extraction_state_changed.emit(
                ExtractionEvent.EXTRACTION_FAILED,
                ExtractionPayload(doc_id=doc_id, error=str(exc)),
            )
            return []

    def _extract_and_cache_dates(
        self, doc_id: str, blocks: List[DocumentBlock]
    ) -> List[ExtractedDate]:
        try:
            dates = self._date_extraction_service.extract_dates(blocks, doc_id)
            self._cache.set(
                doc_id, "date_extraction", _EXTRACTOR_VERSIONS["date_extraction"],
                "dates", [self._date_to_dict(d) for d in dates],
            )
            self._bus.extraction_state_changed.emit(
                ExtractionEvent.DATES_READY,
                ExtractionPayload(doc_id=doc_id),
            )
            return dates
        except Exception as exc:
            self._bus.extraction_state_changed.emit(
                ExtractionEvent.EXTRACTION_FAILED,
                ExtractionPayload(doc_id=doc_id, error=str(exc)),
            )
            return []

    def _extract_and_cache_tables(
        self, doc_id: str, blocks: List[DocumentBlock]
    ) -> List[ExtractedTable]:
        try:
            # The path is what lets TableExtractionService use the real PDF table
            # extractor instead of the block-text fallback.
            pdf_path, _source_type = self._resolve_source(doc_id)
            tables = self._table_extraction_service.extract_tables(doc_id, blocks, pdf_path)
            self._cache.set(
                doc_id, "table_extraction", _EXTRACTOR_VERSIONS["table_extraction"],
                "tables", [self._table_to_dict(t) for t in tables],
            )
            self._bus.extraction_state_changed.emit(
                ExtractionEvent.TABLES_READY,
                ExtractionPayload(doc_id=doc_id),
            )
            return tables
        except Exception as exc:
            self._bus.extraction_state_changed.emit(
                ExtractionEvent.EXTRACTION_FAILED,
                ExtractionPayload(doc_id=doc_id, error=str(exc)),
            )
            return []

    # ------------------------------------------------------------------
    # Serialisation helpers (minimal round-trip for cache storage)
    # ------------------------------------------------------------------

    @staticmethod
    def _block_to_dict(b: DocumentBlock) -> Dict[str, Any]:
        return {
            "block_id": b.block_id, "doc_id": b.doc_id, "page_num": b.page_num,
            "block_type": b.block_type, "exact_text": b.exact_text,
            "normalized_text": b.normalized_text, "bbox": b.bbox,
            "font_size": b.font_size, "is_bold": b.is_bold,
            "heading_level": b.heading_level, "section_path": b.section_path,
            "has_superscript": b.has_superscript,
            "furniture_spans": b.furniture_spans,
            "line_x0": b.line_x0,
            "structure": b.structure,
            "extractor_version": _BLOCK_PIPELINE_VERSION,
            "provenance": dataclasses.asdict(b.provenance) if b.provenance else None,
        }

    @staticmethod
    def _dict_to_block(d: Dict[str, Any]) -> DocumentBlock:
        from core.models.extraction_models import DocumentBlock as DB
        return DB(
            block_id=d["block_id"], doc_id=d["doc_id"],
            page_num=d.get("page_num"),
            block_type=d.get("block_type", "unknown"),
            exact_text=d.get("exact_text", ""),
            normalized_text=d.get("normalized_text", ""),
            bbox=d.get("bbox"),
            font_size=d.get("font_size"),
            is_bold=d.get("is_bold"),
            heading_level=d.get("heading_level"),
            section_path=d.get("section_path", []),
            has_superscript=d.get("has_superscript", False),
            furniture_spans=d.get("furniture_spans") or [],
            line_x0=d.get("line_x0") or [],
            structure=d.get("structure") or {},
            provenance=ExtractionProvenance(**d['provenance']) if d.get('provenance') else None,
        )

    @staticmethod
    def _entity_to_dict(e: ExtractedEntity) -> Dict[str, Any]:
        return {
            "entity_id": e.entity_id, "doc_id": e.doc_id,
            "entity_type": e.entity_type, "canonical_text": e.canonical_text,
            "normalized_text": e.normalized_text, "aliases": e.aliases,
            "properties": e.properties,
        }

    @staticmethod
    def _dict_to_entity(d: Dict[str, Any]) -> ExtractedEntity:
        from core.models.extraction_models import ExtractedEntity as EE
        return EE(
            entity_id=d["entity_id"], doc_id=d["doc_id"],
            entity_type=d.get("entity_type", ""),
            canonical_text=d.get("canonical_text", ""),
            normalized_text=d.get("normalized_text", ""),
            aliases=d.get("aliases", []),
            properties=d.get("properties", {}),
        )

    @staticmethod
    def _section_to_dict(s: SemanticSection) -> Dict[str, Any]:
        return {
            "section_id": s.section_id, "doc_id": s.doc_id,
            "heading_text": s.heading_text, "heading_level": s.heading_level,
            "start_block_id": s.start_block_id, "end_block_id": s.end_block_id,
            "page_range": list(s.page_range), "section_path": list(s.section_path),
        }

    @staticmethod
    def _dict_to_section(d: Dict[str, Any]) -> SemanticSection:
        from core.models.extraction_models import SemanticSection as SS
        page_range = d.get("page_range") or [0, 0]
        return SS(
            section_id=d["section_id"], doc_id=d["doc_id"],
            heading_text=d.get("heading_text", ""),
            heading_level=d.get("heading_level", 0),
            start_block_id=d.get("start_block_id", ""),
            end_block_id=d.get("end_block_id", ""),
            page_range=tuple(page_range[:2]) if len(page_range) >= 2 else (0, 0),
            section_path=d.get("section_path", []),
        )

    @staticmethod
    def _date_to_dict(d: ExtractedDate) -> Dict[str, Any]:
        return {
            "date_id": d.date_id, "doc_id": d.doc_id,
            "exact_text": d.exact_text, "normalized_date": d.normalized_date,
            "certainty": d.certainty, "context_text": d.context_text,
            "location": _plain(d.location),
            "provenance": _plain(d.provenance),
            "metadata": d.metadata,
        }

    @staticmethod
    def _dict_to_date(d: Dict[str, Any]) -> ExtractedDate:
        return ExtractedDate(
            date_id=d["date_id"], doc_id=d["doc_id"],
            exact_text=d.get("exact_text", ""),
            normalized_date=d.get("normalized_date", ""),
            certainty=d.get("certainty", "confirmed"),
            context_text=d.get("context_text", ""),
            location=_location(d.get("location")),
            provenance=_provenance(d.get("provenance")),
            metadata=d.get("metadata") or {},
        )

    @staticmethod
    def _table_to_dict(t: ExtractedTable) -> Dict[str, Any]:
        return {
            "table_id": t.table_id, "doc_id": t.doc_id, "page_num": t.page_num,
            "caption": t.caption, "headers": t.headers,
            "bbox": t.bbox, "extraction_strategy": t.extraction_strategy,
            "confidence": t.confidence, "dataset_id": t.dataset_id,
        }

    @staticmethod
    def _dict_to_table(d: Dict[str, Any]) -> ExtractedTable:
        from core.models.extraction_models import ExtractedTable as ET
        return ET(
            table_id=d["table_id"], doc_id=d["doc_id"],
            page_num=d.get("page_num", 0),
            caption=d.get("caption"),
            headers=d.get("headers", []),
            bbox=d.get("bbox"),
            extraction_strategy=d.get("extraction_strategy", "unknown"),
            confidence=d.get("confidence", 0.0),
            dataset_id=d.get("dataset_id"),
        )

    @staticmethod
    def _citation_index_to_dict(idx: CitationIndex) -> Dict[str, Any]:
        import dataclasses
        return dataclasses.asdict(idx)

    @staticmethod
    def _dict_to_citation_index(d: Dict[str, Any], doc_id: str) -> CitationIndex:
        from core.models.extraction_models import (
            BibliographyEntry, CitationIndex as CI, CitationMatch, InTextCitation,
        )
        try:
            return CI(
                doc_id=d.get("doc_id", doc_id),
                bibliography_entries=[
                    BibliographyEntry(**_typed_refs(e))
                    for e in d.get("bibliography_entries", [])
                ],
                in_text_citations=[
                    InTextCitation(**_typed_refs(c))
                    for c in d.get("in_text_citations", [])
                ],
                citation_matches=[
                    CitationMatch(**m) for m in d.get("citation_matches", [])
                ],
                unmatched_bib_entry_ids=d.get("unmatched_bib_entry_ids", []),
                unmatched_in_text_ids=d.get("unmatched_in_text_ids", []),
                ambiguous_match_ids=d.get("ambiguous_match_ids", []),
                entry_page_index=d.get("entry_page_index", {}),
                citation_contexts=d.get("citation_contexts", {}),
                reference_style=d.get("reference_style", {}),
            )
        except Exception:
            return CI(doc_id=doc_id)


# Cached results are JSON; nested location/provenance must come back as their
# dataclasses or consumers reading ``.page_num``/``.char_start`` silently get
# None and every artifact synced from cache loses its source anchor.

def _plain(value: Any) -> Optional[Dict[str, Any]]:
    return dataclasses.asdict(value) if dataclasses.is_dataclass(value) else value


def _location(value: Any) -> Optional[SourceLocation]:
    return SourceLocation(**value) if isinstance(value, dict) else value


def _provenance(value: Any) -> Optional[ExtractionProvenance]:
    return ExtractionProvenance(**value) if isinstance(value, dict) else value


def _typed_refs(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        **item,
        "location": _location(item.get("location")),
        "provenance": _provenance(item.get("provenance")),
    }
