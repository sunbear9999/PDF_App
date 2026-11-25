"""Show a resolved source anchor in the PDF viewer without text search.

Feeds the anchor's precomputed rectangles into the viewer's existing hit
highlighting and cross-document "pending jump" machinery, so the right PDF
opens (by path from the resolved source ID), scrolls to the page, and the
exact occurrence is highlighted. The search bar is neither opened nor driven.
"""
from __future__ import annotations

from typing import Any, Dict, List

import fitz


def show_pdf_anchor(pdf_viewer: Any, path: str, spans: List[Dict[str, Any]]) -> bool:
    hits = [
        {"pdf": path, "page": span["page_index"], "rect": fitz.Rect(*rect)}
        for span in spans if span.get("page_index") is not None
        for rect in span.get("rects") or []
    ]
    if not hits:
        page = next(
            (span["page_index"] for span in spans if span.get("page_index") is not None),
            None,
        )
        return _show_page(pdf_viewer, path, page)
    pdf_viewer.search_hits = hits
    pdf_viewer.search_phrase_hits = [{
        "pdf": path,
        "page": hits[0]["page"],
        "hit_indices": list(range(len(hits))),
        "first_rect": hits[0]["rect"],
    }]
    pdf_viewer.current_hit_index = 0
    # A later manual search for any text must not be deduplicated against
    # this anchor, which never went through the search bar.
    pdf_viewer.current_search_text = ""
    pdf_viewer.navigate_to_current_hit()
    return True


def _show_page(pdf_viewer: Any, path: str, page: Any) -> bool:
    if page is None:
        return False
    # Zero-size hit: reuses the open-then-jump path to reach the page
    # without drawing a misleading highlight.
    pdf_viewer.search_hits = []
    pdf_viewer.search_phrase_hits = [{
        "pdf": path, "page": page, "hit_indices": [], "first_rect": fitz.Rect(0, 0, 0, 0),
    }]
    pdf_viewer.current_hit_index = 0
    pdf_viewer.current_search_text = ""
    pdf_viewer.navigate_to_current_hit()
    return True
