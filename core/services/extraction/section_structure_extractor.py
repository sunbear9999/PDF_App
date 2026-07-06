"""
SectionStructureExtractor — orchestrates block parsing into sections.

Calls SemanticBlockService to assign section_path to every block, detects
the bibliography section boundary, and persists blocks to the extraction
cache. Downstream extractors query DocumentExtractionService.get_blocks()
instead of calling this directly.
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

from core.models.extraction_models import DocumentBlock, SemanticSection


_BIB_HEADING_RE = re.compile(
    r"^(?:references?|works?\s+cited|bibliography|bibliograph[yi]|"
    r"sources?|literature\s+cited|notes?|endnotes?|footnotes?|"
    r"further\s+reading)\s*$",
    re.I,
)


class SectionStructureExtractor:
    """
    Wraps SemanticBlockService to produce the final section tree and
    to tag bibliography blocks accurately before the citation extractor runs.
    """

    def __init__(self, semantic_block_service) -> None:
        self._svc = semantic_block_service

    # ------------------------------------------------------------------

    def extract(
        self, blocks: List[DocumentBlock]
    ) -> Tuple[List[SemanticSection], List[DocumentBlock]]:
        """
        Build the section hierarchy, assign section_path to all blocks,
        and mark the bibliography section for downstream citation extraction.

        Returns (sections, updated_blocks).
        """
        sections, blocks = self._svc.build_section_tree(blocks)
        self._mark_bibliography_blocks(blocks)
        return sections, blocks

    # ------------------------------------------------------------------

    def get_bibliography_blocks(
        self, blocks: List[DocumentBlock]
    ) -> List[DocumentBlock]:
        """Return blocks classified as bibliography content."""
        return [b for b in blocks if b.block_type == "bibliography"]

    def get_body_blocks(self, blocks: List[DocumentBlock]) -> List[DocumentBlock]:
        """Return non-bibliography blocks (safe for in-text citation search)."""
        return [b for b in blocks if b.block_type != "bibliography"]

    # ------------------------------------------------------------------

    def _mark_bibliography_blocks(self, blocks: List[DocumentBlock]) -> None:
        """
        After section_path assignment, identify the bibliography section
        and ensure all its member blocks are typed 'bibliography'.

        Strategy: use the LAST bibliography heading found (to skip TOC entries)
        and mark all following blocks as bibliography.
        """
        bib_start_idx: Optional[int] = None

        for i, block in enumerate(blocks):
            if block.block_type == "heading" and \
               _BIB_HEADING_RE.match(block.exact_text.strip()):
                bib_start_idx = i   # keep updating → last match wins

        if bib_start_idx is None:
            return

        # Also accept density-based detection for documents without headings
        for i in range(bib_start_idx, len(blocks)):
            blocks[i].block_type = "bibliography"
