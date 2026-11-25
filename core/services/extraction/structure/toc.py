"""Contents entries with source spans and conservative heading resolution."""
import re
import unicodedata
from difflib import SequenceMatcher

from core.services.extraction.block_roles import content_text, is_furniture
from core.services.extraction.structure.page_labels import target_pages
from core.services.extraction.native_reference_links import point_hits

_ENTRY = re.compile(r"^\s*(.+?)\s*(?:\.{2,}|\s{2,}|\s)\s*(\d{1,4}|[ivxlcdm]+)\s*$", re.I)
_NUMBER = re.compile(r"^(?:\d{1,4}|[ivxlcdm]+)$", re.I)
MIN_SIMILARITY = 0.86


def title_key(text):
    text = unicodedata.normalize("NFKD", text.casefold())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"^(?:(?:chapter|part)\s+)?(?:\d+|[ivxlcdm]+)\s*[.:\-]?\s+", "", text)
    return " ".join(re.findall(r"[^\W_]+", text))


def parse_entries(blocks):
    geometric, geometric_pages = _column_entries(blocks)
    result, pending = list(geometric), []
    for block in blocks:
        if block.structure.get("region") != "contents" or is_furniture(block):
            continue
        offset = 0
        for line in content_text(block).split("\n"):
            text = line.strip()
            start = offset + len(line) - len(line.lstrip())
            match = _ENTRY.fullmatch(line)
            if match and len(title_key(match[1])) >= 3:
                result.append(_entry(block, start, start + len(text), match[1].strip(" ."), match[2]))
            elif block.page_num in geometric_pages:
                pass
            elif _NUMBER.fullmatch(text) and pending:
                candidate = pending.pop(0)
                candidate["page_label"] = text
                result.append(candidate)
            elif 3 <= len(text) <= 160 and re.search(r"[^\W\d_]", text) and text.lower() not in {"contents", "table of contents"}:
                pending.append(_entry(block, start, start + len(text), text, ""))
            offset += len(line) + 1
    return result


def _column_entries(blocks):
    by_page = {}
    for block in blocks:
        if block.structure.get("region") != "contents":
            continue
        lines = block.exact_text.split("\n")
        rects = block.structure.get("line_rects") or []
        if len(lines) != len(rects):
            continue
        offset = 0
        for line, rect in zip(lines, rects):
            if len(rect) == 4 and line.strip():
                by_page.setdefault(block.page_num, []).append((block, offset, line, rect))
            offset += len(line) + 1
    result, pages = [], set()
    for page, lines in by_page.items():
        numbers = [row for row in lines if _NUMBER.fullmatch(row[2].strip())]
        titles = [row for row in lines if re.search(r"[^\W\d_]{3}", row[2]) and not _ENTRY.fullmatch(row[2])]
        used = set()
        for block, start, line, rect in titles:
            candidates = [(abs((rect[1]+rect[3]) - (other[3][1]+other[3][3])), index, other)
                          for index, other in enumerate(numbers) if index not in used
                          and other[3][0] > rect[2] and abs(rect[1]-other[3][1]) < max(rect[3]-rect[1], other[3][3]-other[3][1])]
            if not candidates:
                clean = line.strip()
                if clean[:1].isupper() and len(title_key(clean)) >= 4 and clean.casefold() not in {"contents", "table of contents"}:
                    result.append(_entry(block, start, start+len(line.rstrip()), clean, ""))
                continue
            _, index, number = min(candidates, key=lambda item: item[0])
            used.add(index)
            lead = len(line) - len(line.lstrip())
            entry = _entry(block, start+lead, start+len(line.rstrip()), line.strip(), number[2].strip())
            # Some PDFs attach the destination only to the separate page-number column.
            number_block, number_start, number_text, _ = number
            number_lead = len(number_text) - len(number_text.lstrip())
            entry["page_label_span"] = {"block_id": number_block.block_id,
                                        "char_start": number_start + number_lead,
                                        "char_end": number_start + len(number_text.rstrip())}
            result.append(entry)
        if numbers:
            pages.add(page)
    return result, pages


def _entry(block, start, end, title, label):
    return {"block_id": block.block_id, "page": block.page_num, "char_start": start,
            "char_end": end, "title": title, "page_label": label, "target_block_id": None}


def resolve_entries(entries, headings, labels, blocks=()):
    by_id = {b.block_id: b for b in blocks}
    target_index = {}
    for heading in headings:
        if heading.structure.get("region") not in {None, "body", "appendix"}:
            continue
        for rect in heading.structure.get("line_rects", []):
            if len(rect) == 4:
                target_index.setdefault(heading.page_num, []).append((heading, rect))
    for entry in entries:
        native = _native_target(entry, by_id, target_index)
        if native is not None:
            if native:
                entry.update(target_block_id=native[0].block_id,
                             confidence=0.99, reason="native heading destination")
            # Native evidence that does not identify a unique heading must not
            # fall back to an unrelated title elsewhere in the document.
            continue
        expected = target_pages(entry["page_label"], labels)
        key = title_key(entry["title"])
        scored = []
        for heading in headings:
            similarity = SequenceMatcher(None, key, title_key(heading.exact_text)).ratio()
            native = heading.structure.get("outline_title")
            if native:
                similarity = max(similarity, SequenceMatcher(None, key, title_key(native)).ratio())
            if similarity < MIN_SIMILARITY:
                continue
            if expected and heading.page_num not in expected:
                continue
            scored.append((similarity, heading))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        if scored and (len(scored) == 1 or scored[0][0] - scored[1][0] > 0.08):
            entry.update(target_block_id=scored[0][1].block_id,
                         confidence=scored[0][0], reason="title and page label" if expected else "unique title")


def _native_target(entry, blocks, index):
    links = []
    for span in [entry, entry.get("page_label_span")]:
        if not span or span["block_id"] not in blocks:
            continue
        links.extend(link for link in blocks[span["block_id"]].structure.get("native_links", [])
                     if span["char_start"] <= link["char_start"] < link["char_end"] <= span["char_end"])
    if not links:
        return None
    targets = {}
    for link in links:
        candidates = {heading.block_id: heading for heading in point_hits(index, link)}
        if len(candidates) != 1:
            return []
        targets.update(candidates)
    return list(targets.values()) if len(targets) == 1 else []
