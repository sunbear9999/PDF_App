"""Shared built-in and extension artifact type vocabulary."""
from typing import Iterable

BUILTIN_TYPES = frozenset({
    "source", "block", "section", "sentence", "transcript_segment",
    "entity", "entity_mention", "document_note", "note_reference", "toc_entry", "date_mention", "citation_entry",
    "in_text_citation", "table", "figure", "annotation", "argument_unit",
    "summary", "analysis_result", "analysis_answer", "schema_extraction",
    "graph_suggestion", "workspace_node_ref", "note",
})

def artifact_type_choices(extensions: Iterable[str] = ()) -> list[str]:
    return sorted(BUILTIN_TYPES | set(extensions))

