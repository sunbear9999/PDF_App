"""Text-only compatibility adapters over the canonical citation services.

Discovery/old ontology ingestion supply page text instead of layout blocks.
They reuse the same bibliography segmentation, citation syntax and matching;
only their output representation differs from canonical artifact records.
"""
import re

from core.models.extraction_models import DocumentBlock
from core.services.extraction.bibliography_extraction_service import BibliographyExtractionService
from core.services.extraction.citation_extraction_service import CitationExtractionService
from core.services.extraction.citation_matcher import CitationMatcher
from core.services.extraction.section_structure_extractor import SectionStructureExtractor
from core.services.extraction.semantic_block_service import BIBLIOGRAPHY_HEADING_RE, SemanticBlockService


def citation_index_from_pages(page_texts, doc_id="legacy", *, bibliography_service=None, matcher=None):
    blocks = []
    for page, text in page_texts:
        start = 0
        parts = []
        for line in re.finditer(r"(?m)^.*$", text):
            if not BIBLIOGRAPHY_HEADING_RE.fullmatch(line[0].strip()):
                continue
            parts.extend([(text[start:line.start()], "paragraph"), (line[0], "heading")])
            start = line.end()
        parts.append((text[start:], "paragraph"))
        for index, (part, kind) in enumerate(parts):
            if part.strip():
                blocks.append(DocumentBlock(f"{doc_id}_p{page}_b{index}", doc_id, page,
                                            kind, part, part.casefold()))
    SectionStructureExtractor(SemanticBlockService()).extract(blocks)
    service = CitationExtractionService(bibliography_service or BibliographyExtractionService(), matcher or CitationMatcher())
    return service.build_citation_index(doc_id, blocks)


def bibliography_entries_from_text(text, doc_id="legacy", *, service=None):
    service = service or BibliographyExtractionService()
    block = DocumentBlock(f"{doc_id}_bib", doc_id, 0, "bibliography", text, text.casefold())
    return service.segment_entries([block], doc_id)
