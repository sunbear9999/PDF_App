"""Compact layout rules and the generic list renderer for the Intelligence dock."""
from __future__ import annotations

from core.models.entity_categories import type_label

# Scoped to the dock by object name, so the rest of the app keeps its spacing.
COMPACT_STYLE = """
#intelligenceDock QTabBar::tab { padding: 4px 9px; }
#intelligenceDock QPushButton { padding: 3px 8px; }
#intelligenceDock QToolButton { padding: 2px 6px; }
#intelligenceDock QToolBar { spacing: 2px; padding: 0; }
#intelligenceDock QGroupBox { margin-top: 8px; }
#intelligenceDock QComboBox { padding: 2px 4px; }
#intelligenceDock QLabel#intelligenceTabDescription { padding: 0 2px; }
"""

KIND_LABELS = {"document_note": "Note", "note_reference": "Note marker", "toc_entry": "Contents entry",
               "section": "Heading", "in_text_citation": "In-text citation", "citation_entry": "Bibliography entry",
               "entity_mention": "Mention", "argument_unit": "Argument unit", "date_mention": "Date"}


def kind_label(row) -> str:
    kind = row.get("artifact_type") or ""
    if kind == "entity":
        return type_label((row.get("metadata") or {}).get("entity_type"))
    return KIND_LABELS.get(kind, kind.replace("_", " ").capitalize())


def generic_columns(row):
    meta = row.get("metadata") or {}
    number = meta.get("reference_number") or meta.get("note_number")
    text = (row.get("exact_text") or row.get("normalized_text") or "").strip()
    page = (row.get("locator") or {}).get("page")
    detail = f"p. {int(page) + 1}" if isinstance(page, int) else ""
    label = meta.get("display_kind") or (kind_label(row) + (f" {number}" if number else ""))
    text = meta.get("display_text") or text
    return label, text, row.get("source_id") or "", " · ".join(v for v in (detail, row.get("review_state") or "") if v)


def fill_generic(dock, view_id, spec, rows):
    """Rows of the view's declared artifact types, newest page first."""
    wanted = set(spec.required_artifact_types or [])
    dock._fill(view_id, [row for row in rows if not wanted or row.get("artifact_type") in wanted], generic_columns)
