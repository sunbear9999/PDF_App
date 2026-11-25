"""Shared artifact-family vocabulary for queries, caching and vector adapters."""
from typing import Any, List

ARTIFACT_FAMILIES = {
    "source": {"source"},
    "structure": {
        "block", "section", "sentence", "transcript_segment", "table",
        "citation_entry", "in_text_citation", "date_mention", "entity",
        "entity_mention", "document_note", "note_reference", "toc_entry",
    },
    "argument": {"argument_unit"},
    "summary": {"summary"},
    "annotation": {"annotation"},
    "relation": set(),
    "embedding": set(),
}


def artifact_family(artifact_type: Any) -> str:
    value = str(artifact_type or "")
    for family, artifact_types in ARTIFACT_FAMILIES.items():
        if value in artifact_types:
            return family
    return "derived"


def artifact_types_for_families(families: List[str]) -> List[str]:
    if not families:
        return []
    values = set()
    for family in families:
        if family in ARTIFACT_FAMILIES:
            values.update(ARTIFACT_FAMILIES[family])
        elif family not in {"relation", "derived"}:
            values.add(family)
    return sorted(values)


