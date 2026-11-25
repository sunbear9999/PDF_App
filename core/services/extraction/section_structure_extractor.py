"""
SectionStructureExtractor — orchestrates block parsing into sections.

Calls SemanticBlockService to assign section_path to every block, detects
the bibliography section boundary, and persists blocks to the extraction
cache. Downstream extractors query DocumentExtractionService.get_blocks()
instead of calling this directly.
"""
from __future__ import annotations

import re

from core.services.extraction.block_roles import content_text, is_furniture
from core.services.extraction.structure.regions import annotate
from core.services.extraction.structure.genre import annotate_genre
from typing import List, Optional, Tuple

from core.models.extraction_models import DocumentBlock, SemanticSection
from core.services.extraction.semantic_block_service import BIBLIOGRAPHY_HEADING_RE
from core.services.extraction.bibliography_extraction_service import has_reference_start

# A real reference list is dense in years, DOIs, URLs and arXiv ids.
_REFERENCE_SIGNAL_RE = re.compile(
    r"\b(?:1[5-9]\d{2}|20[0-2]\d)\b|\b10\.\d{4,}/|https?://|arxiv[:\s]",
    re.I,
)
_MIN_BIBLIOGRAPHY_BLOCKS = 2
_DENSITY_WINDOW = 25
_APPENDIX_RE = re.compile(r"^(?:appendix|table\s+[a-z0-9]+[.:]|figure\s+[a-z0-9]+[.:]|supplementary)", re.I)
_MIN_REFERENCE_DENSITY = 0.5


def _opens_with_heading(text: str) -> bool:
    """Back matter whose heading opens a paragraph block ("About CIGI\nThe
    Centre …"): a short title line — no comma, period or year — followed by
    prose that does not read like a reference."""
    first, _, rest = text.partition("\n")
    first = first.strip()
    words = first.split()
    return (2 <= len(words) <= 6 and len(first) <= 40 and not re.search(r"[,.:;]|\d", first)
            and all(w[:1].isupper() for w in words if len(w) > 3)
            and bool(rest.strip()) and not has_reference_start(rest.strip())
            and not BIBLIOGRAPHY_HEADING_RE.match(first))


def _standalone_title(text: str, following: List[DocumentBlock]) -> bool:
    """A title-like block on its own ("About CIGI") whose next block is prose,
    not a reference: back matter after the list."""
    if "\n" in text or not _opens_with_heading(text + "\nx"):
        return False
    after = next((b.exact_text.strip() for b in following if b.exact_text.strip() and not is_furniture(b)), "")
    return bool(after) and not has_reference_start(after) and not _REFERENCE_SIGNAL_RE.search(after[:120])


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
        self._mark_bibliography_blocks(blocks)
        annotate(blocks, self._svc.pattern_registry)
        annotate_genre(blocks, self._svc.pattern_registry)
        sections, blocks = self._svc.build_section_tree(blocks)
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
        """Own the bibliography boundary for the document, in both directions.

        BlockClassifier types a block as bibliography as soon as *any* preceding
        heading looks like a reference heading, so one false positive — an arXiv
        stamp, a "Notes" section — turned the rest of the document into the
        reference list and left the citation extractor almost no body text. This
        pass is the single authority: it takes the last heading whose following
        region really reads like a reference list, and restores every block
        before that boundary.
        """
        bib_start_idx = self._bibliography_start(blocks)
        bib_end_idx = self._bibliography_end(blocks, bib_start_idx)
        for i, block in enumerate(blocks):
            if is_furniture(block):
                continue
            if bib_start_idx is not None and bib_start_idx <= i < bib_end_idx:
                block.block_type = "bibliography"
            elif block.block_type == "bibliography":
                block.block_type = "paragraph"

    @staticmethod
    def _bibliography_end(blocks: List[DocumentBlock], start: Optional[int]) -> int:
        """The reference list ends at an appendix/table/figure heading or a
        non-reference heading ("Table A. …", "Appendix", "Acknowledgements")."""
        if start is None:
            return len(blocks)
        for index in range(start + 1, len(blocks)):
            block = blocks[index]
            text = content_text(block).strip()
            if is_furniture(block) or not text or not re.search(r"[A-Za-z]{3}", text):
                continue
            if _APPENDIX_RE.match(text) or (
                block.block_type in {"heading", "caption"} and len(text) < 120
                and not _REFERENCE_SIGNAL_RE.search(text)
                and not BIBLIOGRAPHY_HEADING_RE.match(text)
            ) or _opens_with_heading(text) or _standalone_title(text, blocks[index + 1:]):
                return index
        return len(blocks)

    @staticmethod
    def _bibliography_start(blocks: List[DocumentBlock]) -> Optional[int]:
        """Index of the reference-list heading, or None if the document has none."""
        # Underlined "Works Cited" or a small-caps "REFERENCES" is often the
        # body font size, so it parses as a paragraph: any short non-furniture
        # line matching the heading pattern is a candidate. The content check
        # below still has to accept the region that follows.
        # The heading may also open the block that holds the first entries
        # ("BIBLIOGRAPHY\nAkbarzai, Sahar, …"): then the rest of that block is
        # part of the region the content check reads.
        candidates = []
        for i, block in enumerate(blocks):
            text = block.exact_text.strip()
            if is_furniture(block) or not text:
                continue
            first, _, rest = text.partition("\n")
            if (block.block_type in {"heading", "bibliography"} or len(text) <= 40) \
                    and BIBLIOGRAPHY_HEADING_RE.match(text):
                candidates.append((i, ""))
            elif rest.strip() and len(first.strip()) <= 40 and BIBLIOGRAPHY_HEADING_RE.match(first.strip()):
                candidates.append((i, rest))
        # Last match first: it skips the table-of-contents entry for the same
        # heading. Each candidate must still pass the content check.
        for start, rest in reversed(candidates):
            if SectionStructureExtractor._reads_like_references(blocks[start + 1:], rest):
                return start
        return None

    @staticmethod
    def _reads_like_references(region: List[DocumentBlock], lead: str = "") -> bool:
        # The list itself, not appendices after it, decides.
        texts = ([lead] if lead.strip() else []) + [
            b.exact_text for b in region if b.exact_text.strip() and not is_furniture(b)]
        texts = texts[:_DENSITY_WINDOW]
        if len(texts) < _MIN_BIBLIOGRAPHY_BLOCKS:
            return bool(len(texts) == 1 and has_reference_start(texts[0])
                        and _REFERENCE_SIGNAL_RE.search(texts[0]))
        hits = sum(1 for text in texts if _REFERENCE_SIGNAL_RE.search(text))
        return hits / len(texts) >= _MIN_REFERENCE_DENSITY
