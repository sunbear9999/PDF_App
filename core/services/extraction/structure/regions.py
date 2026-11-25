"""One structural interpretation shared by notes, entities and navigation."""
import re

from core.services.extraction.block_roles import content_text, is_furniture
from core.services.extraction.structure.patterns import REGION_PATTERNS
from core.services.extraction.structure.scopes import chapter_scope
from core.services.extraction.structure.page_labels import page_labels
from core.services.extraction.structure.toc import parse_entries, resolve_entries, title_key

_CHAPTER = re.compile(r"(?i)^(?:chapter|chapitre|capítulo|kapitel|part)\s+(\d+|[ivxlcdm]+)\b")
_NOTE_SEQUENCE = re.compile(r"(?m)^\s*\d{1,3}[.)]\s+\S")


def region_heading(text, registry=None):
    first = text.strip().split("\n", 1)[0].strip()
    if len(first) > 100:
        return None
    for kind, fallback in REGION_PATTERNS.items():
        packs = registry.get_packs_for_type("region_" + kind) if registry else []
        patterns = [p for pack in packs for p in pack.compiled] or [re.compile(fallback)]
        if any(p.fullmatch(first) for p in patterns):
            return kind
    return None


def annotate(blocks, registry=None):
    """Assign evidence-backed regions; retain unknown body as body.

    Notes headings require numbered prose nearby. A passing mention of notes
    or a contents entry must never swallow the remainder of a document.
    """
    region, chapter, toc_start = "body", "", None
    prose_pages = {b.page_num for b in blocks if len(content_text(b).split()) >= 45}
    for block in blocks:
        block.structure.pop("toc_entries", None)
    for index, block in enumerate(blocks):
        text = content_text(block).strip()
        if region == "contents" and block.page_num != toc_start and block.page_num in prose_pages:
            region = "body"
        heading = region_heading(text, registry) if not is_furniture(block) else None
        # SectionStructureExtractor owns the validated reference interval.
        # A table's "References" column is not a second bibliography boundary.
        if heading == "references" and block.block_type != "bibliography":
            heading = None
        if region == "references" and block.block_type != "bibliography" and not is_furniture(block):
            region = "body"
        # Endnote page-range running heads are structural evidence even though
        # they are masked out of prose. A chapter extract may start mid-region.
        if any(re.search(r"(?i)notes\s+to\s+pages?\s+\d", line) for line in block.exact_text.splitlines()):
            heading = "notes"
        if region == "contents":
            heading = None
        if heading == "notes":
            following = "\n".join(content_text(b) for b in blocks[index:index + 8])
            if not _NOTE_SEQUENCE.search(following):
                heading = None
        if heading:
            region = heading
            if "\n" not in text and len(text) <= 100:
                block.structure["heading_level"] = 1
            if region == "contents":
                toc_start = block.page_num
        elif region == "contents" and _body_start(block, toc_start):
            region = "body"
        if block.block_type == "bibliography" and region != "contents":
            region = "references"
        match = chapter_scope(text)
        if region == "notes" and match and len(text) < 100:
            block.structure["heading_level"] = 3
        if region == "body" and match and len(text) < 180:
            chapter = match
            block.block_type = "heading"
            block.heading_level = 1
        block.structure.update(region=region, chapter_scope=chapter)
        if region == "references" and not is_furniture(block):
            block.block_type = "bibliography"
        if region in {"notes", "contents", "index", "glossary"} and not is_furniture(block):
            block.block_type = {"notes": "endnote", "contents": "toc"}.get(region, region)
    entries = parse_entries(blocks)
    _promote_toc_headings(blocks, entries)
    labels = page_labels(blocks)
    headings = [b for b in blocks if b.block_type == "heading" or b.structure.get("heading_level")]
    resolve_entries(entries, headings, labels, blocks)
    by_id = {b.block_id: b for b in blocks}
    for entry in entries:
        by_id[entry["block_id"]].structure.setdefault("toc_entries", []).append(entry)
    # Preserve useful scope even for unnumbered chapter titles. Numbered
    # chapter scopes remain preferred for explicit endnote subheadings.
    current = ""
    for block in blocks:
        if block.structure["region"] == "body":
            if block.block_type == "heading" and block.heading_level in (None, 1):
                current = block.structure.get("chapter_scope") or "heading:" + title_key(block.exact_text)
            block.structure["chapter_scope"] = current or block.structure["chapter_scope"]
        if block.page_num in labels:
            block.structure["printed_page_label"] = labels[block.page_num]


def _body_start(block, toc_start):
    if block.page_num is not None and toc_start is not None and block.page_num <= toc_start:
        return False
    text = content_text(block).strip()
    return (len(text.split()) >= 45 or bool(block.structure.get("outline_title"))
            or bool(_CHAPTER.match(text) and not re.search(r"\s\d+\s*$", text)))


def _promote_toc_headings(blocks, entries):
    titles = {title_key(e["title"]) for e in entries}
    for block in blocks:
        if block.structure.get("region") != "body" or is_furniture(block):
            continue
        text = content_text(block).strip()
        if title_key(text) in titles and len(text) <= 160:
            block.block_type, block.heading_level = "heading", 1
