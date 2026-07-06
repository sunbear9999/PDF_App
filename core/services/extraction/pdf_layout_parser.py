"""
PDFLayoutParser — converts a PDF file into a list of DocumentBlock objects.

Uses PyMuPDF's page.get_text("dict") to obtain block/line/span hierarchies
with font-size and bounding-box metadata. This is the same call already used
in DeterministicExtractorService for superscript detection; we centralise it
here so all downstream extractors share the same parse.

DocumentParserRegistry allows plugins to register alternative parsers
(e.g. PyMuPDF4LLM) behind the same interface.
"""
from __future__ import annotations

import re
import statistics
import unicodedata
from typing import Any, Callable, Dict, List, Optional

import fitz  # PyMuPDF

from core.models.extraction_models import DocumentBlock, ExtractionProvenance


_EXTRACTOR_NAME = "pdf_layout_parser"
_EXTRACTOR_VERSION = "1.0"
_PARSER_PROVIDER = "pymupdf_layout"

_SUPERSCRIPT_THRESHOLD = 0.85   # font < 85% of page median → superscript candidate
_HEADING_SIZE_RATIO = 1.12      # font > 112% of page median → heading candidate
_FOOTNOTE_BOTTOM_RATIO = 0.85   # y-position > 85% of page height → footnote candidate
_MIN_BLOCK_LEN = 2              # skip whitespace-only blocks shorter than this


# ------------------------------------------------------------------ helpers

_SUPERSCRIPT_UNICODE: Dict[str, str] = dict(zip("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹"))


def _to_superscript(text: str) -> str:
    return "".join(_SUPERSCRIPT_UNICODE.get(c, c) for c in text)


def _normalize_text(text: str) -> str:
    nfc = unicodedata.normalize("NFC", text)
    return re.sub(r"\s+", " ", nfc).strip().lower()


def _page_median_font_size(page_dict: Dict) -> float:
    sizes = []
    for block in page_dict.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                s = span.get("size", 0)
                if s > 0:
                    sizes.append(s)
    if not sizes:
        return 12.0
    return statistics.median(sizes)


def _classify_block_type_initial(
    text: str,
    avg_size: float,
    median_size: float,
    is_bold: bool,
    block_bbox: Optional[List[float]],
    page_height: float,
) -> str:
    stripped = text.strip()
    if not stripped:
        return "unknown"

    # Heading: large font or bold+short
    if avg_size > median_size * _HEADING_SIZE_RATIO:
        return "heading"
    if is_bold and len(stripped) < 120 and "\n" not in stripped[:80]:
        return "heading"

    # Footnote: small + near bottom of page
    if block_bbox and page_height > 0:
        y1 = block_bbox[3]
        if y1 / page_height > _FOOTNOTE_BOTTOM_RATIO and avg_size < median_size * 0.9:
            return "footnote"

    # Caption: starts with Figure/Table N
    if re.match(r"^(?:Fig(?:ure)?|Table|Chart|Exhibit)\s*\.?\s*\d", stripped, re.I):
        return "caption"

    return "paragraph"


# ------------------------------------------------------------------ parser

class PDFLayoutParser:
    """
    Parses a PDF into a flat list of DocumentBlock objects.

    Each block maps to a PyMuPDF text block and carries:
    - exact_text / normalized_text
    - page_num, bbox, font_size, is_bold
    - has_superscript (True when a span looks like a citation superscript)
    - block_type (initial classification; refined by SemanticBlockService)
    - section_path (populated later by SectionStructureExtractor)
    """

    def parse(self, pdf_path: str, doc_id: str) -> List[DocumentBlock]:
        blocks: List[DocumentBlock] = []
        try:
            doc = fitz.open(pdf_path)
        except Exception:
            return blocks

        try:
            for page_num, page in enumerate(doc):
                page_dict = page.get_text("dict")
                page_height = page.rect.height
                median_sz = _page_median_font_size(page_dict)
                raw_blocks = page_dict.get("blocks", [])

                for block_idx, raw in enumerate(raw_blocks):
                    if raw.get("type") != 0:
                        # type 1 = image block; skip
                        continue

                    block_id = f"{doc_id}_p{page_num}_b{block_idx}"
                    full_text, avg_sz, is_bold, has_sup = self._process_block(
                        raw, median_sz
                    )
                    if len(full_text.strip()) < _MIN_BLOCK_LEN:
                        continue

                    bbox = list(raw.get("bbox", []))
                    block_type = _classify_block_type_initial(
                        full_text, avg_sz, median_sz, is_bold, bbox, page_height
                    )

                    provenance = ExtractionProvenance(
                        extractor_name=_EXTRACTOR_NAME,
                        extractor_version=_EXTRACTOR_VERSION,
                        parser_provider=_PARSER_PROVIDER,
                        method="layout",
                        confidence=1.0,
                    )

                    blocks.append(DocumentBlock(
                        block_id=block_id,
                        doc_id=doc_id,
                        page_num=page_num,
                        block_type=block_type,
                        exact_text=full_text,
                        normalized_text=_normalize_text(full_text),
                        bbox=bbox or None,
                        font_size=round(avg_sz, 2) if avg_sz else None,
                        is_bold=is_bold,
                        has_superscript=has_sup,
                        provenance=provenance,
                    ))
        finally:
            doc.close()

        return blocks

    @staticmethod
    def _process_block(
        raw: Dict, median_sz: float
    ):
        """Extract full text, average font size, boldness, and superscript flag."""
        lines_text: List[str] = []
        sizes: List[float] = []
        bold_spans = 0
        total_spans = 0
        has_superscript = False

        for line in raw.get("lines", []):
            line_parts: List[str] = []
            for span in line.get("spans", []):
                sz = span.get("size", 0)
                text = span.get("text", "")
                flags = span.get("flags", 0)
                is_span_bold = bool(flags & 0b10000)

                if sz > 0:
                    sizes.append(sz)
                total_spans += 1
                if is_span_bold:
                    bold_spans += 1

                # Superscript detection: small font + digits-only
                if sz > 0 and sz < median_sz * _SUPERSCRIPT_THRESHOLD:
                    digits = re.sub(r"\s+", "", text)
                    if digits and digits.isdigit():
                        has_superscript = True
                        text = _to_superscript(digits)

                line_parts.append(text)

            line_text = "".join(line_parts)
            if line_text.strip():
                lines_text.append(line_text)

        full_text = "\n".join(lines_text)
        avg_sz = statistics.mean(sizes) if sizes else 0.0
        is_bold = total_spans > 0 and (bold_spans / total_spans) >= 0.5

        return full_text, avg_sz, is_bold, has_superscript


# ------------------------------------------------------------------ registry

class DocumentParserRegistry:
    """
    Allows plugins to register alternative PDF parsers behind the same
    interface. The default provider is 'pymupdf_layout'.

    All parsers must accept (pdf_path: str, doc_id: str) and return
    List[DocumentBlock].
    """

    def __init__(self) -> None:
        self._parsers: Dict[str, Callable] = {}
        default = PDFLayoutParser()
        self._parsers[_PARSER_PROVIDER] = default.parse
        self._default_id = _PARSER_PROVIDER

    def register(self, provider_id: str, parse_fn: Callable) -> None:
        self._parsers[provider_id] = parse_fn

    def get_parser(self, provider_id: Optional[str] = None) -> Callable:
        pid = provider_id or self._default_id
        return self._parsers.get(pid, self._parsers[self._default_id])

    def set_default(self, provider_id: str) -> None:
        if provider_id in self._parsers:
            self._default_id = provider_id

    def available(self) -> List[str]:
        return list(self._parsers.keys())

    def remove_by_plugin(self, plugin_id: str) -> None:
        pass  # placeholder; plugin parsers can tag themselves for cleanup
