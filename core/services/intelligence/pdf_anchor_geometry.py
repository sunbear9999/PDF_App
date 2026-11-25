"""PDF highlight geometry for resolved anchor spans.

Rebuilds the parser's block text from ``rawdict`` character boxes and only
trusts the rectangles when that text equals the cached block text. A parser
change, OCR-imported block or rewritten file therefore degrades honestly to a
block-level (coarse) highlight instead of drawing over the wrong characters.
"""
from __future__ import annotations

from typing import Any, List, Optional, Tuple

import fitz  # PyMuPDF

from core.models.source_anchor_models import AnchorSpan, NavigationPrecision
from core.services.extraction.pdf_layout_parser import block_char_rects, parse_block_id


class PdfAnchorGeometry:
    def rects_for_span(
        self,
        path: str,
        span: AnchorSpan,
        block: Any,
        precision: NavigationPrecision,
    ) -> Tuple[List[List[float]], NavigationPrecision]:
        block_rect = list(getattr(block, "bbox", None) or [])
        if span.page_index is None:
            return [], precision
        if span.char_start is None or span.char_end is None or block is None:
            return ([block_rect] if block_rect else []), NavigationPrecision.COARSE
        rects = self._char_rects(path, span, block)
        if rects:
            return rects, precision
        # Offsets are right but the page geometry no longer reproduces the
        # block: highlight the whole block and say so.
        return ([block_rect] if block_rect else []), NavigationPrecision.COARSE

    @staticmethod
    def _char_rects(path: str, span: AnchorSpan, block: Any) -> Optional[List[List[float]]]:
        parsed = parse_block_id(span.block_id or "")
        if not parsed or parsed[0] != span.page_index:
            return None
        try:
            with fitz.open(path) as doc:
                if not 0 <= span.page_index < len(doc):
                    return None
                result = block_char_rects(
                    doc.load_page(span.page_index), parsed[1], span.char_start, span.char_end,
                )
        except Exception:
            return None
        if not result or result[0] != getattr(block, "exact_text", None):
            return None
        return result[1] or None
