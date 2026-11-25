"""Conservative OCR marker candidates with exact raw offsets."""
import re
import uuid

from core.models.extraction_models import InTextCitation, SourceLocation, ExtractionProvenance
from core.services.extraction.block_roles import content_text, is_ocr_text

_GLUED = re.compile(r"(?<=[.!?;”’)])(\d{1,3})(?=\s+(?:[A-ZÀ-ÖØ-Þ]|$)|$)")
_SUPERSCRIPT_SEARCH_MAP = str.maketrans("0123456789-–", "⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁻")


def superscript_search_text(text, block):
    """An equal-length search view of formatted digits, never an anchor edit.

    HTML/EPUB and DOCX preserve ASCII digits plus typography spans. Reusing
    the same detector as Unicode/PDF markers keeps exponent, year and label
    exclusions consistent across formats.
    """
    spans = block.structure.get("superscript_spans", [])
    if not spans:
        return text
    chars = list(text)
    for start, end in spans:
        if not (0 <= start < end <= len(text)):
            continue
        chars[start:end] = text[start:end].translate(_SUPERSCRIPT_SEARCH_MAP)
    return "".join(chars)


def ocr_markers(blocks, doc_id):
    result = []
    for block in blocks:
        if not is_ocr_text(block) or block.structure.get("region", "body") != "body":
            continue
        text = content_text(block)
        for match in _GLUED.finditer(text):
            if int(match[1]) == 0:
                continue
            if match.start() >= 2 and text[match.start()-2].isdigit():
                continue  # decimal values, section/table numbers and versions
            result.append(InTextCitation(
                citation_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{doc_id}|ocr-marker|{block.block_id}|{match.start()}")),
                doc_id=doc_id, raw_text=match[1], citation_type="superscript",
                numeric_keys=[match[1]], context_text=text[max(0, match.start()-80):match.end()+80],
                location=SourceLocation(doc_id, block.page_num, match.start(), match.end(), block_id=block.block_id),
                provenance=ExtractionProvenance("citation_extraction", "2.0", "ocr_layer", "marker_candidate", 0.65),
            ))
    return result
