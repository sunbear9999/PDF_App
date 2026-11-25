"""
RtfParser — converts a .rtf file into a list of DocumentBlock objects using
striprtf. striprtf has no structural heading concept, so every block is
block_type="paragraph" — no heading_level, unlike the other text parsers.
"""
from __future__ import annotations

import importlib.util
from typing import List

from core.models.extraction_models import DocumentBlock
from core.services.extraction.formats._shared import (
    is_long_enough,
    make_provenance,
    normalize_text,
    split_text_blocks,
)

_EXTRACTOR_NAME = "rtf_parser"
_EXTRACTOR_VERSION = "1.0"
_PARSER_PROVIDER = "rtf_parser"


class RtfParser:
    provider_id = _PARSER_PROVIDER

    @staticmethod
    def is_available() -> bool:
        return importlib.util.find_spec("striprtf") is not None

    def parse(self, path: str, doc_id: str) -> List[DocumentBlock]:
        try:
            from striprtf.striprtf import rtf_to_text
        except ImportError:
            return []
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                raw = fh.read()
            text = rtf_to_text(raw)
        except Exception:
            return []

        provenance = make_provenance(_EXTRACTOR_NAME, _EXTRACTOR_VERSION, _PARSER_PROVIDER)
        blocks: List[DocumentBlock] = []
        for idx, para in enumerate(split_text_blocks(text)):
            if not is_long_enough(para):
                continue
            blocks.append(DocumentBlock(
                block_id=f"{doc_id}_b{idx}",
                doc_id=doc_id,
                page_num=None,
                block_type="paragraph",
                exact_text=para,
                normalized_text=normalize_text(para),
                provenance=provenance,
            ))
        return blocks
