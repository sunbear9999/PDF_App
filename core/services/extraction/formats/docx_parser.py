"""
DocxParser — converts a .docx file into a list of DocumentBlock objects
using python-docx. Paragraph styles named "Heading N" / "Title" become
heading_level; tables become block_type="table".
"""
from __future__ import annotations

import importlib.util
import re
from typing import List

from core.models.extraction_models import DocumentBlock
from core.services.extraction.formats._shared import (
    is_long_enough,
    make_provenance,
    normalize_text,
)

_EXTRACTOR_NAME = "docx_parser"
_EXTRACTOR_VERSION = "1.1"
_PARSER_PROVIDER = "docx_parser"

_HEADING_STYLE_RE = re.compile(r"heading\s*(\d+)", re.I)


class DocxParser:
    provider_id = _PARSER_PROVIDER

    @staticmethod
    def is_available() -> bool:
        return importlib.util.find_spec("docx") is not None

    def parse(self, path: str, doc_id: str) -> List[DocumentBlock]:
        try:
            import docx
        except ImportError:
            return []
        try:
            document = docx.Document(path)
        except Exception:
            return []

        provenance = make_provenance(_EXTRACTOR_NAME, _EXTRACTOR_VERSION, _PARSER_PROVIDER)
        blocks: List[DocumentBlock] = []
        idx = 0

        # Paragraphs and tables must remain interleaved: moving a table to
        # the end can put its content in the references or notes region.
        from docx.table import Table
        from docx.text.paragraph import Paragraph
        for child in document.element.body.iterchildren():
            if child.tag.endswith("}tbl"):
                table = Table(child, document)
                row_lines = [" | ".join((cell.text or "").strip() for cell in row.cells)
                             for row in table.rows]
                text = "\n".join(line for line in row_lines if line.strip())
                if text and is_long_enough(text):
                    blocks.append(DocumentBlock(
                        block_id=f"{doc_id}_b{idx}", doc_id=doc_id, page_num=None,
                        block_type="table", exact_text=text,
                        normalized_text=normalize_text(text), provenance=provenance,
                    ))
                    idx += 1
                continue
            if not child.tag.endswith("}p"):
                continue
            para = Paragraph(child, document)
            text, superscripts = _paragraph_content(para)
            if not text or not is_long_enough(text):
                continue
            style_name = (para.style.name if para.style else "") or ""
            block_type = "paragraph"
            heading_level = None
            heading_match = _HEADING_STYLE_RE.search(style_name)
            if heading_match:
                block_type = "heading"
                heading_level = min(int(heading_match.group(1)), 6)
            elif style_name.strip().lower() == "title":
                block_type = "heading"
                heading_level = 1
            blocks.append(DocumentBlock(
                block_id=f"{doc_id}_b{idx}",
                doc_id=doc_id,
                page_num=None,
                block_type=block_type,
                exact_text=text,
                normalized_text=normalize_text(text),
                heading_level=heading_level,
                provenance=provenance,
                has_superscript=bool(superscripts),
                structure={"superscript_spans": superscripts},
            ))
            idx += 1

        return blocks


def _paragraph_content(paragraph):
    """Preserve run typography, including runs inside a hyperlink."""
    from docx.text.run import Run
    parts, spans, offset = [], [], 0
    for element in paragraph._p.xpath("./w:r | ./w:hyperlink/w:r"):
        run = Run(element, paragraph)
        text = run.text or ""
        superscript = run.font.superscript
        style = run.style
        seen = set()
        while superscript is None and style is not None and style.style_id not in seen:
            seen.add(style.style_id)
            superscript = style.font.superscript
            style = style.base_style
        if superscript and text:
            spans.append([offset, offset + len(text)])
        parts.append(text)
        offset += len(text)
    raw = "".join(parts)
    left = len(raw) - len(raw.lstrip())
    text = raw.strip()
    spans = [[max(0, a-left), min(len(text), b-left)] for a, b in spans
             if b > left and a < left+len(text)]
    return text, spans
