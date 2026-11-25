"""Resolve numeric markers from local targets, never typography alone."""
from collections import Counter


def decide(citations, entries, notes):
    numeric = [c for c in citations if c.numeric_keys]
    styles = Counter(c.citation_type for c in citations)
    roles = {c.reference_role for c in citations}
    if "notes" in roles and "numeric_citations" in roles:
        return {"style": "mixed", "confidence": 0.8, "reasons": ["Numbered notes and a reference list coexist"]}
    if "notes" in roles or (notes and not numeric):
        return {"style": "notes", "confidence": 0.9, "reasons": ["Numbered prose in note regions"]}
    if "numeric_citations" in roles:
        return {"style": "numeric_citations", "confidence": 0.9, "reasons": ["Numbered bibliography targets"]}
    if styles["author_year"]:
        return {"style": "author_year", "confidence": 0.9, "reasons": ["Author and year markers"]}
    return {"style": "unresolved", "confidence": 0.0, "reasons": ["Insufficient target evidence"]}


def marker_role(citation, notes, entries, blocks):
    """Local note evidence outranks reference numbers; other regions stay free
    to use numeric citations. Ambiguous endnote scopes do not guess a source.
    """
    if not citation.numeric_keys:
        return "author_year" if citation.citation_type == "author_year" else "unresolved"
    if citation.citation_type == "superscript" and notes:
        if note_targets(citation, notes, blocks):
            return "notes"
        page = getattr(citation.location, "page_num", None)
        local = any(n.normalized_keys.get("scope_kind") == "page" and n.location.page_num == page for n in notes)
        endnotes = any(n.normalized_keys.get("scope_kind") != "page" for n in notes)
        if local or endnotes or not any(e.reference_number for e in entries):
            return "notes"
    if any(e.reference_number in citation.numeric_keys for e in entries):
        return "numeric_citations"
    return "unresolved"


def note_targets(citation, notes, blocks):
    """A duplicated number requires page/chapter evidence; ambiguity abstains."""
    if not citation.location:
        return []
    block = blocks.get(citation.location.block_id)
    structure = getattr(block, "structure", {}) or {}
    candidates = [n for n in notes if n.reference_number in citation.numeric_keys]
    local = [n for n in candidates if n.normalized_keys.get("scope_kind") == "page"
             and n.location.page_num == citation.location.page_num]
    if local:
        return local
    scoped = [n for n in candidates if structure.get("chapter_scope")
              and n.normalized_keys.get("scope") == structure["chapter_scope"]]
    if scoped:
        return scoped
    printed = structure.get("printed_page_label", "")
    if printed.isdigit():
        ranged = [n for n in candidates if n.normalized_keys.get("page_start", "").isdigit()
                  and int(n.normalized_keys["page_start"]) <= int(printed)
                  <= int(n.normalized_keys.get("page_end", "0"))]
        if ranged:
            return ranged
    # A sole endnote scope is useful for chapter extracts; footnotes never
    # cross a page just because the local note is missing.
    scopes = {n.normalized_keys.get("scope") for n in notes
              if n.normalized_keys.get("scope_kind") != "page"}
    if len(scopes) != 1:
        return []
    chapter = structure.get("chapter_scope", "")
    return [n for n in candidates if n.normalized_keys.get("scope_kind") != "page"
            and not (chapter.startswith("chapter:") and str(n.normalized_keys.get("scope", "")).startswith("chapter:")
                     and n.normalized_keys["scope"] != chapter)
            and not (printed.isdigit() and n.normalized_keys.get("page_start"))]
