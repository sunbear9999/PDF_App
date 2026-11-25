"""One definition of what counts as document *content*.

Page furniture (running heads, page numbers, copyright/download stamps,
watermarks, cover pages) is marked during source normalization. It stays in
``DocumentBlock.exact_text`` so source anchors remain exact, but extractors
read :func:`content_text`, which blanks furniture at equal length — every
offset an extractor reports is still a raw ``exact_text`` offset.
"""
from __future__ import annotations

from typing import Any, Iterable, List

FURNITURE_TYPES = frozenset({"header", "footer", "page_number", "watermark", "boilerplate"})


def is_furniture(block: Any) -> bool:
    return (getattr(block, "block_type", "") or "") in FURNITURE_TYPES


def is_content_block(block: Any) -> bool:
    return not is_furniture(block) and bool((getattr(block, "exact_text", "") or "").strip())


def content_text(block: Any) -> str:
    """``exact_text`` with furniture replaced by spaces (newlines kept)."""
    text = getattr(block, "exact_text", "") or ""
    if is_furniture(block):
        return _blank(text)
    spans = getattr(block, "furniture_spans", None) or []
    if not spans:
        return text
    chars = list(text)
    for start, end, *_label in spans:
        for index in range(max(0, int(start)), min(len(chars), int(end))):
            if chars[index] != "\n":
                chars[index] = " "
    return "".join(chars)


def content_blocks(blocks: Iterable[Any]) -> List[Any]:
    return [block for block in blocks or [] if is_content_block(block)]


def _blank(text: str) -> str:
    return "".join("\n" if char == "\n" else " " for char in text)


def is_ocr_text(block: Any) -> bool:
    """Text recognised from an image (a scan's OCR layer, an OCR'd image):
    its spelling and typography are estimates, not the author's."""
    provenance = getattr(block, "provenance", None)
    method = str(getattr(provenance, "method", "") or "")
    provider = str(getattr(provenance, "parser_provider", "") or "")
    return method in {"ocr", "ocr_layer"} or "ocr" in provider
