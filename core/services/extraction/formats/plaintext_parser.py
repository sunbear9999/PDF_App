"""
PlainTextParser — converts a .txt or .md file into a list of DocumentBlock
objects. Stdlib only, always available.

Paragraphs are split on blank lines. Markdown ATX headings ("#".."######")
are detected with a simple regex — a full Markdown AST is not needed since
only heading_level/section_path matter downstream (section_extract_step.py
groups by section_path, not by rendered markup).
"""
from __future__ import annotations

import re
from typing import List

from core.models.extraction_models import DocumentBlock
from core.services.extraction.formats._shared import (
    is_long_enough,
    make_provenance,
    normalize_text,
    split_text_blocks,
)

_EXTRACTOR_NAME = "plaintext_parser"
_EXTRACTOR_VERSION = "1.0"
_PARSER_PROVIDER = "plaintext_parser"

_ATX_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


class PlainTextParser:
    provider_id = _PARSER_PROVIDER

    @staticmethod
    def is_available() -> bool:
        return True

    def parse(self, path: str, doc_id: str) -> List[DocumentBlock]:
        try:
            with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
                raw = fh.read()
        except OSError:
            return []

        provenance = make_provenance(_EXTRACTOR_NAME, _EXTRACTOR_VERSION, _PARSER_PROVIDER)
        blocks: List[DocumentBlock] = []
        for idx, para in enumerate(split_text_blocks(raw)):
            text = para
            block_type = "paragraph"
            heading_level = None
            match = _ATX_HEADING_RE.match(text)
            if match:
                heading_level = len(match.group(1))
                text = match.group(2).strip()
                block_type = "heading"
            if not is_long_enough(text):
                continue
            blocks.append(DocumentBlock(
                block_id=f"{doc_id}_b{idx}",
                doc_id=doc_id,
                page_num=None,
                block_type=block_type,
                exact_text=text,
                normalized_text=normalize_text(text),
                heading_level=heading_level,
                provenance=provenance,
            ))
        return blocks
