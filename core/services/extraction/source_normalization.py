"""SourceNormalizationService — the one preparation step every source passes.

Every format parser (PDF layout parser with paragraph reconstruction, EPUB,
DOCX, HTML, RTF, plain text, image OCR) and every imported block set (archive
OCR, external text) goes through :func:`prepare_blocks` before any
intelligence runs. Normalization is additive and offset-safe:

1. **Page furniture** — running heads, page numbers, copyright/download
   stamps, cover pages and banners are marked (``page_furniture``), at line
   level so heads glued into paragraphs are caught too.
2. **Block roles** — furniture keeps its own block types, so semantic
   classification, sections and extractors never treat it as content.
3. **Semantic classification and sections** — headings, lists, captions,
   bibliography boundaries (existing services).

Text-level repairs (soft hyphens, line-break hyphenation, ligatures and
font-encoded ligature glyphs, whitespace) are applied by the offset-mapped
normalizer that sentence segmentation uses, so ``exact_text`` always remains
the verbatim source and every reported offset is a raw offset.
"""
from __future__ import annotations

from typing import Any, List, Optional, Tuple

from core.services.extraction import page_furniture

NORMALIZATION_VERSION = "1.0"


class SourceNormalizationService:
    """Format-independent cleanup applied to parsed blocks."""

    version = NORMALIZATION_VERSION

    def normalize(self, blocks: List[Any]) -> List[Any]:
        page_furniture.detect(blocks)
        return blocks


def prepare_blocks(
    blocks: List[Any],
    normalizer: Optional[SourceNormalizationService],
    semantic_block_service: Any = None,
    section_structure_extractor: Any = None,
) -> Tuple[List[Any], List[Any]]:
    """Parsed blocks → (sections, normalized, classified blocks).

    The single sequence used by DocumentExtractionService and by the
    evaluation harness, so measured behaviour is the shipped behaviour.
    """
    if normalizer is not None:
        blocks = normalizer.normalize(blocks)
    if semantic_block_service is not None:
        blocks = semantic_block_service.classify_blocks(blocks)
    sections: List[Any] = []
    if section_structure_extractor is not None:
        sections, blocks = section_structure_extractor.extract(blocks)
    return sections, blocks
