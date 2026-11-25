"""Turn document tags in a retrieval request into the documents that carry them.

The research assistant's tag filter lists project (document) tags. Passages do not carry
those tags reliably: vectors keep the tags a document had when it was embedded, and other
artifacts carry none. So a document tag is resolved to source ids when the search is
planned, and only tags that are not document tags stay as passage metadata filters.
"""
from __future__ import annotations

from dataclasses import replace
from typing import List, Tuple

from core.models.intelligence_models import SourceScope

NO_TAGGED_DOCUMENTS = "no selected document has the tag(s): {}"


def apply_document_tags(pm, scope: SourceScope, tags: List[str], logic: str) -> Tuple[List[str], SourceScope]:
    """Return (remaining passage tag filters, scope narrowed to documents with the document tags)."""
    if not tags or pm is None or not hasattr(pm, "get_doc_tag_map") or not hasattr(pm, "list_sources"):
        return tags, scope
    try:
        assignments = pm.get_doc_tag_map() or {}
        known = {tag.get("name") for tag in (pm.get_all_tags() or [])} if hasattr(pm, "get_all_tags") else set()
    except Exception:
        return tags, scope
    document_tags = [tag for tag in tags if tag in known]
    others = [tag for tag in tags if tag not in known]
    if not document_tags or (others and str(logic).upper() == "OR"):
        return tags, scope                      # "either a document tag or a passage tag": keep as filters
    wanted = set(document_tags)
    match = all if str(logic).upper() != "OR" else any
    tagged = []
    for source in pm.list_sources():
        names = {tag.get("name") for tag in assignments.get(source.get("path"), [])}
        if match(tag in names for tag in wanted):
            tagged.append(source)
    allowed = set(scope.source_ids)
    chosen = [s for s in tagged if not allowed or str(s.get("id")) in allowed]
    if not chosen:
        return others, replace(scope, unresolved_refs=[*scope.unresolved_refs,
                                                       NO_TAGGED_DOCUMENTS.format(", ".join(document_tags))])
    return others, SourceScope(
        source_ids=[str(s.get("id")) for s in chosen],
        source_paths=[str(s.get("path") or "") for s in chosen if s.get("path")],
        display_names=[str(s.get("title") or "") for s in chosen if s.get("title")],
        unresolved_refs=list(scope.unresolved_refs),
    )
