"""
SemanticBlockService — refines DocumentBlock types using content patterns.

After PDFLayoutParser assigns initial block types based on font metrics,
SemanticBlockService queries PatternDetectorRegistry to apply content-based
rules: bibliography density, footnote markers, caption patterns, list markers.

BlockClassifier is the stateless rules engine; SemanticBlockService wraps
it with the pattern registry dependency.
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

from core.models.extraction_models import DocumentBlock, SemanticSection


_CAPTION_RE = re.compile(
    r"^(?:Fig(?:ure)?|Table|Chart|Exhibit|Appendix|Plate)\s*\.?\s*\d",
    re.I,
)
_LIST_RE = re.compile(r"^\s*(?:[•·▪▸◦\-–—]|\d+[.)]\s|\([a-z]\))\s")
_CODE_RE = re.compile(r"^\s{4,}|\t{2,}")


class BlockClassifier:
    """
    Stateless rules engine that refines block_type assignments.

    Queries a PatternDetectorRegistry for bibliography heading patterns so
    that plugin-contributed patterns are also applied.
    """

    def __init__(self, pattern_registry=None) -> None:
        self._registry = pattern_registry

    # ------------------------------------------------------------------

    def classify(self, block: DocumentBlock, all_blocks: List[DocumentBlock]) -> str:
        """Return a refined block_type string for the given block."""
        text = block.exact_text.strip()
        if not text:
            return "unknown"

        # Already classified as heading — honour it unless it's a bib heading
        if block.block_type == "heading":
            if self._is_bibliography_heading(text):
                return "bibliography"
            return "heading"

        # Caption
        if _CAPTION_RE.match(text):
            return "caption"

        # List items
        if _LIST_RE.match(text):
            return "list"

        # Code / preformatted (4-space or double-tab indent)
        if _CODE_RE.match(text):
            return "code"

        # Footnote refinement (already tagged by layout parser; keep)
        if block.block_type == "footnote":
            return "footnote"

        # Bibliography section: check if a prior heading was a bib heading
        if block.block_type not in ("heading", "caption", "bibliography"):
            if self._in_bibliography_section(block, all_blocks):
                return "bibliography"

        return block.block_type or "paragraph"

    def _is_bibliography_heading(self, text: str) -> bool:
        lowered = text.lower().strip()
        if self._registry:
            for pack in self._registry.get_packs_for_type("bibliography"):
                for pattern in pack.compiled:
                    if pattern.match(lowered):
                        return True
        # Hard-coded fallback
        return bool(re.match(
            r"^(?:references?|works?\s+cited|bibliography|sources?|"
            r"literature\s+cited|notes?|endnotes?|footnotes?|"
            r"further\s+reading)\s*$",
            lowered,
        ))

    def _in_bibliography_section(
        self, block: DocumentBlock, all_blocks: List[DocumentBlock]
    ) -> bool:
        """True if any preceding heading in the same doc is a bibliography heading."""
        for other in all_blocks:
            if other.block_id == block.block_id:
                break
            if other.doc_id != block.doc_id:
                continue
            # page_num is None for reflowable formats (epub/docx/txt/...) — in
            # that case block order alone (the loop already walks in document
            # order) determines precedence, so skip the page comparison.
            same_or_before = (
                other.page_num is None or block.page_num is None
                or other.page_num <= block.page_num
            )
            if other.block_type in ("heading", "bibliography") and same_or_before:
                if self._is_bibliography_heading(other.exact_text):
                    return True
        return False


class SemanticBlockService:
    """
    Refines block types and assembles section paths for all blocks in a doc.
    """

    def __init__(self, pattern_registry=None) -> None:
        self._classifier = BlockClassifier(pattern_registry)

    # ------------------------------------------------------------------

    def classify_blocks(self, blocks: List[DocumentBlock]) -> List[DocumentBlock]:
        """Refine block_type on every block using content-based rules."""
        for block in blocks:
            block.block_type = self._classifier.classify(block, blocks)
        return blocks

    def build_section_tree(
        self, blocks: List[DocumentBlock]
    ) -> Tuple[List[SemanticSection], List[DocumentBlock]]:
        """
        Walk blocks in order to build the heading hierarchy and assign
        section_path to every block. Returns (sections, updated_blocks).
        """
        sections: List[SemanticSection] = []
        heading_stack: List[Tuple[int, str]] = []  # (level, heading_text)
        current_section_id: Optional[str] = None
        section_counter = 0

        prev_heading_block_id: Optional[str] = None

        for i, block in enumerate(blocks):
            if block.block_type == "heading":
                level = block.heading_level or self._infer_heading_level(
                    block, blocks
                )
                block.heading_level = level

                # Pop stack to current level
                while heading_stack and heading_stack[-1][0] >= level:
                    heading_stack.pop()

                heading_stack.append((level, block.exact_text.strip()))
                block.section_path = [h for _, h in heading_stack]

                # Close previous section and open a new one
                if prev_heading_block_id is not None and sections:
                    sections[-1].end_block_id = blocks[i - 1].block_id if i > 0 else block.block_id

                section_counter += 1
                sec_id = f"{block.doc_id}_sec{section_counter}"
                current_section_id = sec_id
                prev_heading_block_id = block.block_id

                sections.append(SemanticSection(
                    section_id=sec_id,
                    doc_id=block.doc_id,
                    heading_text=block.exact_text.strip(),
                    heading_level=level,
                    start_block_id=block.block_id,
                    end_block_id=block.block_id,   # updated as we advance
                    page_range=(block.page_num, block.page_num),
                    section_path=list(block.section_path),
                ))
            else:
                # Inherit current section path
                block.section_path = [h for _, h in heading_stack]
                if sections:
                    # Extend last section's end and page range
                    sec = sections[-1]
                    sec.end_block_id = block.block_id
                    sec.page_range = (sec.page_range[0], block.page_num)

        return sections, blocks

    @staticmethod
    def _infer_heading_level(
        block: DocumentBlock, all_blocks: List[DocumentBlock]
    ) -> int:
        """Estimate heading level from font size relative to other headings."""
        heading_sizes = sorted(
            {b.font_size for b in all_blocks
             if b.block_type == "heading" and b.font_size},
            reverse=True,
        )
        if not heading_sizes or not block.font_size:
            return 1
        try:
            rank = heading_sizes.index(block.font_size)
            return min(rank + 1, 6)
        except ValueError:
            return 2
