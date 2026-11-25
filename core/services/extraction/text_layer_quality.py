"""Decide whether a PDF has a usable text layer before intelligence runs on it.

A scanned book parses into zero blocks, and every stage downstream then
"completes" with nothing. This check samples pages and reports pages that are
images without text, pages whose text is garbled (broken font encodings, bad
OCR), and an overall verdict the parse stage gates on. It reads text only — no
rendering — so it stays cheap on large files.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List

import fitz  # PyMuPDF

NEEDS_OCR_REASON = "needs_ocr"

# Below this many visible characters a page counts as having no text.
_MIN_PAGE_CHARS = 25
# A document needing OCR on this share of its content pages is gated.
_BAD_PAGE_SHARE = 0.25
# Pages sampled from long documents (spread evenly, first pages included).
_SAMPLE_PAGES = 40
_TOKEN = re.compile(r"\S+")


@dataclass
class TextLayerReport:
    path: str
    page_count: int = 0
    pages_sampled: int = 0
    image_only_pages: List[int] = field(default_factory=list)
    garbled_pages: List[int] = field(default_factory=list)
    blank_pages: int = 0
    error: str = ""

    @property
    def content_pages(self) -> int:
        return max(0, self.pages_sampled - self.blank_pages)

    @property
    def bad_pages(self) -> int:
        return len(self.image_only_pages) + len(self.garbled_pages)

    @property
    def needs_ocr(self) -> bool:
        if self.error or not self.pages_sampled:
            return False
        if not self.content_pages:
            # Nothing extractable at all (e.g. text drawn as vector outlines).
            return True
        return self.bad_pages / self.content_pages >= _BAD_PAGE_SHARE

    def summary(self) -> str:
        if self.error:
            return f"The PDF could not be read: {self.error}"
        sampled = (f"{self.pages_sampled} sampled pages" if self.pages_sampled < self.page_count
                   else f"{self.page_count} pages")
        parts = []
        if self.image_only_pages:
            parts.append(f"{len(self.image_only_pages)} of {sampled} are scanned images with no text")
        if self.garbled_pages:
            parts.append(f"{len(self.garbled_pages)} of {sampled} have unreadable (garbled) text")
        if not parts and not self.content_pages:
            parts.append(f"none of the {sampled} contain extractable text")
        return "; ".join(parts) or "The text layer looks usable."

    def to_metadata(self) -> Dict[str, Any]:
        return {
            "reason": NEEDS_OCR_REASON if self.needs_ocr else "",
            "path": self.path, "page_count": self.page_count,
            "pages_sampled": self.pages_sampled,
            "image_only_pages": [page + 1 for page in self.image_only_pages[:50]],
            "garbled_pages": [page + 1 for page in self.garbled_pages[:50]],
            "summary": self.summary(),
        }


def sample_page_indexes(page_count: int, limit: int = _SAMPLE_PAGES) -> List[int]:
    if page_count <= limit:
        return list(range(page_count))
    head = list(range(min(5, page_count)))
    step = (page_count - 1) / (limit - len(head) - 1)
    spread = {round(index * step) for index in range(limit - len(head))}
    return sorted(set(head) | spread)


def text_is_garbled(text: str) -> bool:
    """Text that extracts but is not language: mojibake, private-use glyphs,
    replacement characters or symbol soup from a bad OCR pass."""
    visible = [ch for ch in text if not ch.isspace()]
    if len(visible) < _MIN_PAGE_CHARS:
        return False
    junk = sum(1 for ch in visible if ch == "�" or 0xE000 <= ord(ch) <= 0xF8FF
               or (ord(ch) < 32))
    if junk / len(visible) > 0.1:
        return True
    tokens = _TOKEN.findall(text)
    wordlike = sum(1 for token in tokens if _is_wordlike(token))
    letters = sum(1 for ch in visible if ch.isalpha())
    return letters / len(visible) < 0.45 or (len(tokens) >= 8 and wordlike / len(tokens) < 0.4)


def _is_wordlike(token: str) -> bool:
    core = token.strip(".,;:!?\"'()[]{}«»“”‘’-—–")
    if len(core) < 2:
        return core.isalnum()
    letters = sum(1 for ch in core if ch.isalpha())
    return letters / len(core) >= 0.7 or core.replace(",", "").replace(".", "").isdigit()


def assess_pdf_text_layer(path: str, sample_limit: int = _SAMPLE_PAGES) -> TextLayerReport:
    report = TextLayerReport(path=path)
    try:
        doc = fitz.open(path)
    except Exception as exc:
        report.error = str(exc)
        return report
    try:
        if doc.needs_pass:
            report.error = "the file is password protected"
            return report
        report.page_count = doc.page_count
        for index in sample_page_indexes(doc.page_count, sample_limit):
            page = doc.load_page(index)
            text = page.get_text("text") or ""
            report.pages_sampled += 1
            if len(text.strip()) >= _MIN_PAGE_CHARS:
                if text_is_garbled(text):
                    report.garbled_pages.append(index)
            elif page.get_images(full=False):
                report.image_only_pages.append(index)
            else:
                report.blank_pages += 1
    except Exception as exc:
        report.error = str(exc)
    finally:
        doc.close()
    return report
