"""
HtmlParser — converts a standalone .html/.htm file into a list of
DocumentBlock objects using BeautifulSoup (already a dependency for
EpubParser — reused here rather than adding a second HTML parsing path).
"""
from __future__ import annotations

import importlib.util
from typing import List

from core.models.extraction_models import DocumentBlock
from core.services.extraction.formats._shared import (
    is_long_enough,
    make_provenance,
    normalize_text,
)

_EXTRACTOR_NAME = "html_parser"
_EXTRACTOR_VERSION = "1.0"
_PARSER_PROVIDER = "html_parser"

_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_TEXT_TAGS = _HEADING_TAGS | {"p", "li"}


class HtmlParser:
    provider_id = _PARSER_PROVIDER

    @staticmethod
    def is_available() -> bool:
        return importlib.util.find_spec("bs4") is not None

    def parse(self, path: str, doc_id: str) -> List[DocumentBlock]:
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            return []
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                raw = fh.read()
        except OSError:
            return []
        try:
            soup = BeautifulSoup(raw, "html.parser")
        except Exception:
            return []

        provenance = make_provenance(_EXTRACTOR_NAME, _EXTRACTOR_VERSION, _PARSER_PROVIDER)
        blocks: List[DocumentBlock] = []
        idx = 0
        for tag in soup.find_all(_TEXT_TAGS):
            text = tag.get_text(" ", strip=True)
            if not text or not is_long_enough(text):
                continue
            is_heading = tag.name in _HEADING_TAGS
            blocks.append(DocumentBlock(
                block_id=f"{doc_id}_b{idx}",
                doc_id=doc_id,
                page_num=None,
                block_type="heading" if is_heading else "paragraph",
                exact_text=text,
                normalized_text=normalize_text(text),
                heading_level=int(tag.name[1]) if is_heading else None,
                provenance=provenance,
            ))
            idx += 1

        return blocks
