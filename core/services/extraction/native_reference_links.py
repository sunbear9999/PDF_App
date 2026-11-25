"""Native PDF destinations as explicit, bounded reference evidence.

Geometry is read by the parser once. Resolution uses cached line geometry;
it never opens a viewer document or guesses the nearest entry on a page.
"""
import re
import uuid

from core.models.extraction_models import InTextCitation, SourceLocation, ExtractionProvenance

_NUMERIC = re.compile(r"\[?\d{1,3}(?:\s*[,–-]\s*\d{1,3})*\]?")
_PLAIN = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻", "0123456789-")


def source_links(text, char_boxes, links):
    """Native link rectangles → exact source spans and destination points."""
    result = []
    for link in links:
        rect = link["from"]
        offsets = [i for i, item in enumerate(char_boxes) if item and
                   rect.x0 <= (item[1][0] + item[1][2]) / 2 <= rect.x1 and
                   rect.y0 <= (item[1][1] + item[1][3]) / 2 <= rect.y1]
        if not offsets:
            continue
        start, end = min(offsets), max(offsets) + 1
        if end > len(text):
            continue
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end-1].isspace():
            end -= 1
        if start == end:
            continue
        point = link["to"]
        result.append({"char_start": start, "char_end": end, "target_page": link["page"],
                       "target_point": [point.x, point.y]})
    return result


def numeric_native_markers(blocks, citations, doc_id):
    """Linked ordinary digits are usable even when superscript OCR was lost."""
    occupied = {}
    for citation in citations:
        if citation.location:
            occupied.setdefault(citation.location.block_id, []).append(
                (citation.location.char_start or 0, citation.location.char_end or 0))
    result = []
    for block in blocks:
        if block.structure.get("region", "body") not in {"body", "appendix"}:
            continue
        spans = occupied.setdefault(block.block_id, [])
        for link in block.structure.get("native_links", []):
            start, end = link["char_start"], link["char_end"]
            if any(a < end and start < b for a, b in spans):
                continue
            raw = block.exact_text[start:end]
            plain = raw.translate(_PLAIN)
            if not _NUMERIC.fullmatch(plain):
                continue
            # Key expansion and range guards are shared with other markers.
            from core.services.extraction.in_text_citation_extractor import _expand_numeric_keys
            keys = _expand_numeric_keys(plain.strip("[]"))
            if not keys:
                continue
            spans.append((start, end))
            result.append(InTextCitation(
                citation_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{doc_id}|native|{block.block_id}|{start}|{end}")),
                doc_id=doc_id, raw_text=raw, citation_type="native_numeric", numeric_keys=keys,
                location=SourceLocation(doc_id, block.page_num, start, end, block_id=block.block_id),
                context_text=block.exact_text[max(0, start-80):end+80],
                provenance=ExtractionProvenance("citation_extraction", "2.0", "pymupdf_layout", "native_link", 0.99),
            ))
    return result


def target_index(entries, blocks):
    """Entry spans indexed by page and occupied text lines."""
    by_page = {}
    for entry in entries:
        loc = entry.location
        spans = entry.source_spans or ([{"block_id": loc.block_id, "page": loc.page_num,
                                        "char_start": loc.char_start, "char_end": loc.char_end}] if loc else [])
        for span in spans:
            block = blocks.get(span["block_id"])
            if block is None or span["char_start"] is None or span["char_end"] is None:
                continue
            rects = block.structure.get("line_rects", [])
            offset = 0
            for line, rect in zip(block.exact_text.split("\n"), rects):
                end = offset + len(line)
                if len(rect) == 4 and offset < span["char_end"] and span["char_start"] < end:
                    by_page.setdefault(span["page"], []).append((entry, rect))
                offset = end + 1
    return by_page


def native_targets(citation, block, index):
    """None = no native evidence; [] = native evidence without a unique target.

    A link's source must be contained in this occurrence, not just in the
    same paragraph. Destination points must lie on an entry's text line.
    Points at the top of a page are deliberately insufficient.
    """
    if block is None or citation.location is None:
        return None
    loc = citation.location
    links = [link for link in block.structure.get("native_links", [])
             if loc.char_start <= link["char_start"] < link["char_end"] <= loc.char_end]
    if not links:
        return None
    targets = {}
    for link in links:
        candidates = {entry.entry_id: entry for entry in point_hits(index, link)}
        if len(candidates) != 1:
            return []
        targets.update(candidates)
    return list(targets.values())


def point_hits(index, link):
    """Objects whose occupied text line contains a native destination."""
    x, y = link["target_point"]
    return [item for item, rect in index.get(link["target_page"], [])
            if rect[0]-2 <= x <= rect[2]+2 and rect[1]-2 <= y <= rect[3]+2]
