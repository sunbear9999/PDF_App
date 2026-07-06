"""
Shared data models for deterministic extraction outputs.

All features (timeline, entities, citations, tables, analysis, RAG) use these
models so extraction results carry consistent provenance and can be shared
across the pipeline without duplication.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Provenance & Location
# ---------------------------------------------------------------------------

@dataclass
class SourceLocation:
    """Pinpoints where a piece of extracted text lives in the source document."""
    doc_id: str
    page_num: Optional[int] = None
    char_start: Optional[int] = None
    char_end: Optional[int] = None
    bbox: Optional[List[float]] = None        # [x0, y0, x1, y1]
    block_id: Optional[str] = None


@dataclass
class ExtractionProvenance:
    """Records how an item was extracted so the user can audit any result."""
    extractor_name: str
    extractor_version: str
    parser_provider: str
    method: str                                 # "regex","layout","spacy","fuzzy","llm"
    confidence: float = 0.0
    score_breakdown: Dict[str, float] = field(default_factory=dict)
    llm_touched: bool = False


# ---------------------------------------------------------------------------
# Document Structure
# ---------------------------------------------------------------------------

@dataclass
class DocumentBlock:
    """
    A layout-aware unit of document content produced by PDFLayoutParser.

    All downstream extractors (entities, dates, citations, tables, RAG)
    consume DocumentBlocks rather than raw text so provenance is preserved.
    """
    block_id: str               # stable: "{doc_id}_p{page}_b{idx}"
    doc_id: str
    page_num: Optional[int]      # None for reflowable/non-paginated formats (epub, docx, txt, ...)
    block_type: str             # paragraph|heading|list|table|figure|footnote|
                                # bibliography|caption|code|unknown
    exact_text: str
    normalized_text: str
    bbox: Optional[List[float]] = None
    font_size: Optional[float] = None
    is_bold: Optional[bool] = None
    heading_level: Optional[int] = None        # 1–6 for headings
    section_path: List[str] = field(default_factory=list)   # ["Intro","2.1 Methods"]
    provenance: Optional[ExtractionProvenance] = None
    has_superscript: bool = False              # True when span-level superscripts detected


@dataclass
class SemanticSection:
    """A heading-bounded section of a document."""
    section_id: str
    doc_id: str
    heading_text: str
    heading_level: int
    start_block_id: str
    end_block_id: str
    page_range: Tuple[int, int] = (0, 0)
    section_path: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Entity Extraction
# ---------------------------------------------------------------------------

@dataclass
class ExtractedEntity:
    entity_id: str
    doc_id: str
    entity_type: str            # "person","organization","concept", etc.
    canonical_text: str
    normalized_text: str
    aliases: List[str] = field(default_factory=list)
    mentions: List[SourceLocation] = field(default_factory=list)
    properties: Dict[str, Any] = field(default_factory=dict)
    provenance: Optional[ExtractionProvenance] = None


# ---------------------------------------------------------------------------
# Date Extraction
# ---------------------------------------------------------------------------

@dataclass
class ExtractedDate:
    date_id: str
    doc_id: str
    exact_text: str
    normalized_date: str        # ISO 8601
    certainty: str = "confirmed"
    location: Optional[SourceLocation] = None
    context_text: str = ""
    provenance: Optional[ExtractionProvenance] = None


# ---------------------------------------------------------------------------
# Citation Extraction
# ---------------------------------------------------------------------------

@dataclass
class BibliographyEntry:
    """A single entry in the document's reference list."""
    entry_id: str
    doc_id: str
    raw_text: str
    authors: List[str] = field(default_factory=list)
    first_author_surname: str = ""
    year: Optional[str] = None
    title: Optional[str] = None
    source_title: Optional[str] = None
    doi: Optional[str] = None
    url: Optional[str] = None
    reference_number: Optional[str] = None
    citation_style: str = "unknown"             # numbered|author_year|legal|unknown
    location: Optional[SourceLocation] = None
    # Normalized keys for matching: {"surname_year":"smith_2020","doi":"...","numeric":"1"}
    normalized_keys: Dict[str, str] = field(default_factory=dict)
    provenance: Optional[ExtractionProvenance] = None


@dataclass
class InTextCitation:
    """A citation reference found inside the document body."""
    citation_id: str
    doc_id: str
    raw_text: str
    citation_type: str = "unknown"              # numeric|author_year|superscript|footnote
    numeric_keys: List[str] = field(default_factory=list)
    author_keys: List[str] = field(default_factory=list)
    year: Optional[str] = None
    location: Optional[SourceLocation] = None
    context_text: str = ""
    provenance: Optional[ExtractionProvenance] = None


@dataclass
class CitationMatch:
    """Result of matching an in-text citation to a bibliography entry."""
    match_id: str
    in_text_citation_id: str
    bibliography_entry_id: str
    confidence: float
    score_breakdown: Dict[str, float] = field(default_factory=dict)
    is_ambiguous: bool = False
    alternate_entry_ids: List[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class CitationIndex:
    """
    Canonical per-document citation index built by CitationExtractionService.

    Powers: citation dock UI, bibliography expansion, RAG ranking,
    analysis graph evidence linking, and Zotero/plugin integrations.
    """
    doc_id: str
    bibliography_entries: List[BibliographyEntry] = field(default_factory=list)
    in_text_citations: List[InTextCitation] = field(default_factory=list)
    citation_matches: List[CitationMatch] = field(default_factory=list)
    unmatched_bib_entry_ids: List[str] = field(default_factory=list)
    unmatched_in_text_ids: List[str] = field(default_factory=list)
    ambiguous_match_ids: List[str] = field(default_factory=list)
    # entry_id → sorted list of page numbers where it's cited
    entry_page_index: Dict[str, List[int]] = field(default_factory=dict)
    # citation_id → surrounding context snippet
    citation_contexts: Dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Table Extraction
# ---------------------------------------------------------------------------

@dataclass
class ExtractedTableCell:
    row: int
    col: int
    text: str
    bbox: Optional[List[float]] = None
    is_header: bool = False


@dataclass
class ExtractedTable:
    """
    Canonical representation of an extracted table.

    Links back to the data dock via dataset_id so analysis and RAG can
    consume the same object without re-extraction.
    """
    table_id: str
    doc_id: str
    page_num: int
    caption: Optional[str] = None
    headers: List[str] = field(default_factory=list)
    cells: List[List[ExtractedTableCell]] = field(default_factory=list)
    bbox: Optional[List[float]] = None
    extraction_strategy: str = "unknown"
    confidence: float = 0.0
    provenance: Optional[ExtractionProvenance] = None
    dataset_id: Optional[str] = None           # links to data_dock_datasets record


# ---------------------------------------------------------------------------
# Quote Candidates
# ---------------------------------------------------------------------------

@dataclass
class QuoteCandidate:
    """A block passage that is a strong candidate for use as an evidence quote."""
    quote_id: str
    doc_id: str
    exact_text: str
    block_id: str
    location: Optional[SourceLocation] = None
    quote_score: float = 0.0
    in_text_citation_id: Optional[str] = None
    context_before: str = ""
    context_after: str = ""
    provenance: Optional[ExtractionProvenance] = None


# ---------------------------------------------------------------------------
# Ranking Features
# ---------------------------------------------------------------------------

@dataclass
class BlockFeatureSet:
    """
    Named boolean/float features for a DocumentBlock.

    Analysis templates reference these by string key in retrieval_roles.
    RAG uses them as ChromaDB filter metadata.
    """
    block_id: str
    doc_id: str
    features: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RankedBlock:
    block_id: str
    doc_id: str
    score: float
    score_breakdown: Dict[str, float] = field(default_factory=dict)
    block: Optional[DocumentBlock] = None
    features: Optional[BlockFeatureSet] = None


# ---------------------------------------------------------------------------
# Cache Key
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ExtractionCacheKey:
    doc_id: str
    extractor_name: str
    extractor_version: str
    parser_provider: str
