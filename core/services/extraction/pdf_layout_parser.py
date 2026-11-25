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
from core.services.extraction.pdf_paragraphs import BlockShape, group_blocks
from core.services.extraction.native_reference_links import source_links


_EXTRACTOR_NAME = "pdf_layout_parser"
_EXTRACTOR_VERSION = "1.4"   # native reference destinations with exact source spans
_PARSER_PROVIDER = "pymupdf_layout"

_SUPERSCRIPT_THRESHOLD = 0.85   # font < 85% of page median → superscript candidate
_HEADING_SIZE_RATIO = 1.12      # font > 112% of page median → heading candidate
_FOOTNOTE_BOTTOM_RATIO = 0.85   # y-position > 85% of page height → footnote candidate
_MIN_BLOCK_LEN = 1              # skip whitespace-only blocks shorter than this


# ------------------------------------------------------------------ helpers

_SUPERSCRIPT_UNICODE: Dict[str, str] = dict(zip("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹"))
_VISIBLE = re.compile(r"[^\s\u200b\u200c\u200d\ufeff]")
_SUPERSCRIPT_GROUP = re.compile(r"^\d{1,3}(?:[,\-–]\d{1,3})+,?$")


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


def _body_font_size(blocks: List[DocumentBlock]) -> float:
    """Document body size: the font size carrying the most text. Per-page
    medians mislabel body text as headings on reference-heavy pages."""
    weights: Dict[float, int] = {}
    for block in blocks:
        if block.font_size:
            key = round(block.font_size * 2) / 2
            weights[key] = weights.get(key, 0) + len(block.exact_text)
    return max(weights, key=weights.get) if weights else 12.0


def _classify_types(blocks: List[DocumentBlock], sizes: Dict[int, tuple]) -> None:
    body_candidates = [b for b in blocks if b.bbox and b.bbox[1] < 0.7 * sizes.get(b.page_num, (0, 1000))[1]]
    body = _body_font_size(body_candidates or blocks)
    for block in blocks:
        width, height = sizes.get(block.page_num, (0.0, 0.0))
        block.block_type = _classify_block_type_initial(
            block.exact_text, block.font_size or body, body, bool(block.is_bold),
            block.bbox, height,
        )
        if block.block_type == "heading" and (len(block.exact_text.split()) > 35 or (len(block.exact_text.split()) > 6 and block.exact_text.rstrip().endswith("."))):
            block.block_type = "paragraph"
        if block.structure.get("outline_title"):
            block.block_type = "heading"
            block.heading_level = block.structure["outline_level"]
        if block.block_type == "paragraph" and _centered_title(block, width):
            block.block_type = "heading"


_TITLE_END = re.compile(r"[.!?;:,]\s*$")


def _centered_title(block: DocumentBlock, page_width: float) -> bool:
    """A centred single line without closing punctuation is a title
    ("Cross-Racial Solidarity Among Plantation Workers in Hawai'i")."""
    text = block.exact_text.strip()
    if not block.bbox or not page_width or "\n" in text or _TITLE_END.search(text) or len(text) > 160:
        return False
    left, right = block.bbox[0], page_width - block.bbox[2]
    return left > 0.15 * page_width and abs(left - right) < 0.025 * page_width


def doc_page_sizes(pdf_path: str) -> Dict[int, tuple]:
    try:
        with fitz.open(pdf_path) as doc:
            return {number: (page.rect.width, page.rect.height) for number, page in enumerate(doc)}
    except Exception:
        return {}


def _line_shape(raw: Dict, index: int, median_sz: float) -> BlockShape:
    lines = [ln for ln in raw.get("lines", []) if "".join(_span_text(sp) for sp in ln.get("spans", [])).strip()]
    last = lines[-1].get("bbox") if lines else raw.get("bbox")
    text, size, bold, _sup = PDFLayoutParser._process_block(raw, median_sz)
    return BlockShape(index=index, bbox=raw.get("bbox"), last_line_bbox=last,
                      line_height=max(last[3] - last[1], 1.0), text=text, size=size, bold=bold)


def page_groups(raw_blocks: List[Dict], median_sz: float) -> List[List[int]]:
    """Raw text-block indices grouped into paragraphs (see pdf_paragraphs)."""
    shapes = [_line_shape(raw, i, median_sz) for i, raw in enumerate(raw_blocks)
              if raw.get("type") == 0 and raw.get("bbox")]
    return group_blocks([s for s in shapes if s.text.strip()])


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
            outlines = {}
            for level, title, page_number in doc.get_toc():
                outlines.setdefault(page_number - 1, []).append((level, title))
            explicit_labels = bool(doc.get_page_labels())
            for page_num, page in enumerate(doc):
                links = [link for link in page.get_links()
                         if link.get("kind") == fitz.LINK_GOTO
                         and 0 <= link.get("page", -1) < len(doc)
                         and link.get("to") is not None]
                page_dict = page.get_text("rawdict" if links else "dict")
                median_sz = _page_median_font_size(page_dict)
                raw_blocks = page_dict.get("blocks", [])
                ocr = ocr_text_layer(page)
                for group in page_groups(raw_blocks, median_sz):
                    block = self._group_block(raw_blocks, group, median_sz, doc_id, page_num, ocr, links)
                    if block is not None:
                        if explicit_labels:
                            block.structure["page_label"] = page.get_label()
                        for level, title in outlines.get(page_num, []):
                            if " ".join(title.casefold().split()) == " ".join(block.exact_text.casefold().split()):
                                block.structure.update(outline_title=title, outline_level=level)
                        blocks.append(block)
        finally:
            doc.close()

        _classify_types(blocks, doc_page_sizes(pdf_path))
        return blocks

    def _group_block(self, raw_blocks, group, median_sz, doc_id, page_num, ocr=False, links=()) -> Optional[DocumentBlock]:
        parts, char_boxes = [], []
        for i in group:
            member_boxes = [] if links else None
            parts.append(self._process_block(raw_blocks[i], median_sz, char_boxes=member_boxes, ocr=ocr))
            if char_boxes:
                char_boxes.append(None)
            char_boxes.extend(member_boxes or [])
        full_text = "\n".join(part[0] for part in parts)
        if len(full_text.strip()) < _MIN_BLOCK_LEN:
            return None
        weights = [max(len(part[0]), 1) for part in parts]
        avg_sz = sum(part[1] * w for part, w in zip(parts, weights)) / sum(weights)
        bold = sum(w for part, w in zip(parts, weights) if part[2]) >= sum(weights) / 2
        line_x0 = [round((line.get("bbox") or (0,))[0], 1) for i in group for _k, line in _visible_lines(raw_blocks[i])]
        boxes = [raw_blocks[i].get("bbox", []) for i in group]
        bbox = [min(b[0] for b in boxes), min(b[1] for b in boxes),
                max(b[2] for b in boxes), max(b[3] for b in boxes)] if boxes and all(boxes) else []
        return DocumentBlock(
            block_id=f"{doc_id}_p{page_num}_b{group[0]}",
            doc_id=doc_id, page_num=page_num, block_type="paragraph",
            exact_text=full_text, normalized_text=_normalize_text(full_text),
            bbox=bbox or None, font_size=round(avg_sz, 2) if avg_sz else None,
            is_bold=bold, has_superscript=any(part[3] for part in parts),
            line_x0=line_x0 if len(line_x0) == full_text.count("\n") + 1 else [],
            structure={"line_rects": [list(line.get("bbox") or []) for i in group for _k, line in _visible_lines(raw_blocks[i])],
                       "native_links": source_links(full_text, char_boxes, links)},
            provenance=ExtractionProvenance(
                extractor_name=_EXTRACTOR_NAME, extractor_version=_EXTRACTOR_VERSION,
                parser_provider=_PARSER_PROVIDER, method="ocr_layer" if ocr else "layout",
                confidence=0.8 if ocr else 1.0,
            ),
        )

    @staticmethod
    def _process_block(
        raw: Dict, median_sz: float, char_boxes: Optional[List[Any]] = None, ocr: bool = False
    ):
        """Extract full text, average font size, boldness, and superscript flag.

        When ``char_boxes`` is a list (and ``raw`` came from ``rawdict``), it
        receives one ``(line_index, bbox)`` entry per character of the returned
        text — ``None`` for the inserted line separators — so offsets into the
        stored block text map back to page geometry.
        """
        lines_text: List[str] = []
        line_boxes: List[List[Any]] = []
        sizes: List[float] = []
        bold_spans = 0
        total_spans = 0
        has_superscript = False

        for line_index, line in _visible_lines(raw):
            line_parts: List[str] = []
            boxes: List[Any] = []
            for span in line.get("spans", []):
                sz = span.get("size", 0)
                visible = bool(_VISIBLE.search(_span_text(span)))   # zero-width/blank spans carry no typography
                if sz > 0 and visible:
                    sizes.append(sz)
                if visible:
                    total_spans += 1
                    if span.get("flags", 0) & 0b10000:
                        bold_spans += 1
                # An OCR layer's font sizes are estimates: a small "1" is noise, not a note mark.
                text, replaced = (_span_text(span), False) if ocr else _display_span_text(span, median_sz)
                has_superscript = has_superscript or replaced
                line_parts.append(text)
                if char_boxes is not None:
                    boxes.extend(_span_char_boxes(span, line_index, len(text), replaced))

            line_text = "".join(line_parts)
            if line_text.strip():
                lines_text.append(line_text)
                line_boxes.append(boxes)

        full_text = "\n".join(lines_text)
        if char_boxes is not None:
            for index, boxes in enumerate(line_boxes):
                if index:
                    char_boxes.append(None)
                char_boxes.extend(boxes)
        avg_sz = statistics.mean(sizes) if sizes else 0.0
        is_bold = total_spans > 0 and (bold_spans / total_spans) >= 0.5

        return full_text, avg_sz, is_bold, has_superscript


def ocr_text_layer(page) -> bool:
    """A scanned page with a hidden OCR text layer: the text is invisible
    (render mode 3) and drawn over an image that covers most of the page."""
    try:
        trace = page.get_texttrace()[:200]
        if not trace or sum(1 for t in trace if t.get("type") == 3) < 0.8 * len(trace):
            return False
        area = page.rect.width * page.rect.height or 1.0
        covered = sum((i["bbox"][2] - i["bbox"][0]) * (i["bbox"][3] - i["bbox"][1]) for i in page.get_image_info())
        return covered >= 0.6 * area
    except Exception:
        return False


def _visible_lines(raw: Dict) -> List[tuple]:
    """(original index, line) for lines that carry text, without overprints."""
    kept, previous = [], None
    for index, line in enumerate(raw.get("lines", [])):
        if not "".join(_span_text(sp) for sp in line.get("spans", [])).strip() or _overprint(line, previous):
            continue        # blank, or the same line drawn twice (fake bold / shadow)
        previous = line
        kept.append((index, line))
    return kept


def _overprint(line: Dict, previous: Optional[Dict]) -> bool:
    """A line repeating the previous one at (almost) the same position."""
    if previous is None:
        return False
    text = "".join(_span_text(sp) for sp in line.get("spans", [])).strip()
    if not text or text != "".join(_span_text(sp) for sp in previous.get("spans", [])).strip():
        return False
    a, b = line.get("bbox") or (0, 0, 0, 0), previous.get("bbox") or (0, 0, 0, 0)
    return abs(a[0] - b[0]) <= 3 and abs(a[1] - b[1]) <= 2


def _display_span_text(span: Dict, median_sz: float) -> tuple:
    """Span text as stored in the block; small digit-only spans become
    superscript characters. Returns ``(text, was_superscript)``."""
    text = _span_text(span)
    sz = span.get("size", 0)
    if sz > 0 and sz < median_sz * _SUPERSCRIPT_THRESHOLD:
        digits = re.sub(r"\s+", "", text)
        if digits and digits.isdigit():
            return _to_superscript(digits), True
        # Citation groups and ranges set small: "1-3", "5,6", "8, 14".
        if _SUPERSCRIPT_GROUP.match(digits):
            return _to_superscript(digits.replace("-", "⁻").replace("–", "⁻")), True
    return text, False


def _span_text(span: Dict) -> str:
    if "text" in span:
        return span.get("text", "")
    return "".join(char.get("c", "") for char in span.get("chars", []))


def _span_char_boxes(span: Dict, line_index: int, length: int, replaced: bool) -> List[Any]:
    chars = span.get("chars") or []
    if replaced or len(chars) != length:
        # Superscript rewriting changes the character count; fall back to the
        # whole-span box so geometry stays on the right glyph run.
        return [(line_index, tuple(span.get("bbox") or ()))] * length
    return [(line_index, tuple(char.get("bbox") or ())) for char in chars]


_BLOCK_ID_RE = re.compile(r"_p(\d+)_b(\d+)$")


def parse_block_id(block_id: str) -> Optional[tuple]:
    """Return ``(page_index, raw_block_index)`` for parser-issued block IDs."""
    match = _BLOCK_ID_RE.search(block_id or "")
    return (int(match.group(1)), int(match.group(2))) if match else None


def block_char_rects(
    page: Any, block_index: int, char_start: int, char_end: int
) -> Optional[tuple]:
    """Map a raw char range in a parser block to per-line rectangles.

    Returns ``(block_text, rects)`` where ``block_text`` is reassembled with
    the same rules as :meth:`PDFLayoutParser.parse`; callers must compare it
    with the stored block text before trusting the rectangles.
    """
    found = block_char_boxes(page, block_index)
    if found is None:
        return None
    text, boxes = found
    return text, merge_line_rects(boxes[max(0, char_start):max(0, char_end)])


def page_layout(page: Any, page_dict: Optional[Dict] = None) -> Dict[str, Any]:
    """The parser's per-page grouping inputs, computed once and reusable for every block of the page."""
    page_dict = page_dict if page_dict is not None else page.get_text("rawdict")
    raw_blocks = page_dict.get("blocks", [])
    median = _page_median_font_size(page_dict)
    return {"blocks": raw_blocks, "median": median, "ocr": ocr_text_layer(page),
            "groups": {group[0]: group for group in page_groups(raw_blocks, median)}}


def block_char_boxes(page: Any, block_index: int, layout: Optional[Dict] = None) -> Optional[tuple]:
    """``(block_text, per-character (line, bbox) boxes)`` for one parser block.
    Pass ``layout`` (:func:`page_layout`) to reuse one page's grouping across its blocks."""
    layout = layout if layout is not None else page_layout(page)
    raw_blocks, median, ocr = layout["blocks"], layout["median"], layout["ocr"]
    if not 0 <= block_index < len(raw_blocks):
        return None
    if raw_blocks[block_index].get("type") != 0:
        return None
    group = layout["groups"].get(block_index, [block_index])
    boxes: List[Any] = []
    texts: List[str] = []
    line_offset = 0
    for member in group:
        member_boxes: List[Any] = []
        text, *_rest = PDFLayoutParser._process_block(raw_blocks[member], median, char_boxes=member_boxes, ocr=ocr)
        if texts:
            boxes.append(None)
        texts.append(text)
        boxes.extend(None if box is None else (box[0] + line_offset, box[1]) for box in member_boxes)
        line_offset += len(raw_blocks[member].get("lines", [])) + 1
    return "\n".join(texts), boxes


def merge_line_rects(boxes: List[Any]) -> List[List[float]]:
    merged: Dict[int, List[float]] = {}
    order: List[int] = []
    for item in boxes:
        if not item or len(item[1]) < 4:
            continue
        line_index, (x0, y0, x1, y1) = item[0], item[1][:4]
        if line_index not in merged:
            merged[line_index] = [x0, y0, x1, y1]
            order.append(line_index)
            continue
        rect = merged[line_index]
        rect[:] = [min(rect[0], x0), min(rect[1], y0), max(rect[2], x1), max(rect[3], y1)]
    return [merged[index] for index in order]


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
