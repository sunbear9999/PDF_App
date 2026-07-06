"""
core/services/extraction/formats/_shared.py

Small helpers shared by every non-PDF format parser in this package, so each
parser file only contains the format-specific parsing logic. All parsers here
implement the same DocumentParserRegistry contract as PDFLayoutParser:
parse(path: str, doc_id: str) -> List[DocumentBlock], with page_num=None
(reflowable formats have no fixed pagination).
"""
from __future__ import annotations

import re
import unicodedata

from core.models.extraction_models import ExtractionProvenance

_MIN_BLOCK_LEN = 2


def normalize_text(text: str) -> str:
    nfc = unicodedata.normalize("NFC", text)
    return re.sub(r"\s+", " ", nfc).strip().lower()


def make_provenance(extractor_name: str, extractor_version: str, parser_provider: str) -> ExtractionProvenance:
    return ExtractionProvenance(
        extractor_name=extractor_name,
        extractor_version=extractor_version,
        parser_provider=parser_provider,
        method="layout",
        confidence=1.0,
    )


def split_paragraphs(text: str) -> list:
    """Blank-line paragraph split, used by plaintext/RTF parsers."""
    return [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]


def split_text_blocks(text: str) -> list:
    """
    Blank-line paragraph split with a single-newline fallback.

    Plain prose/markdown files usually separate paragraphs with a blank
    line. But text with no blank lines at all — e.g. output from
    `soffice --convert-to txt` (used by LegacyDocParser) or from
    striprtf — uses one newline per logical line/paragraph instead. If the
    blank-line split collapses everything into a single chunk despite the
    text having multiple non-blank lines, fall back to per-line splitting
    so structure isn't lost.
    """
    paragraphs = split_paragraphs(text)
    if len(paragraphs) <= 1:
        lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
        if len(lines) > 1:
            return lines
    return paragraphs


def is_long_enough(text: str) -> bool:
    return len(text.strip()) >= _MIN_BLOCK_LEN
