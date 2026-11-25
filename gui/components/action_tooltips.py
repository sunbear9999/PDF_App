"""Hover descriptions for registered Intelligence commands.

One place says what each button does in plain words. Commands not listed here
fall back to their KeySpec description, so plugin commands get a tooltip too.
The current shortcut, when one is bound, is appended.
"""
from __future__ import annotations

from typing import Dict

TIPS: Dict[str, str] = {
    # Artifact inspector
    "intelligence.save": "Save your edits to this record. The original quote and method are kept; Undo restores the previous version.",
    "intelligence.verify": "Mark this record as checked and correct. Verified records survive reprocessing.",
    "intelligence.reject_artifact": "Mark this record as wrong. It is hidden from views and never re-proposed; Undo restores it.",
    "intelligence.undo": "Undo your last review of this record.",
    "intelligence.reset": "Discard unsaved edits in this form.",
    "intelligence.show_guide": "Open the guide explaining every field shown here.",
    "intelligence.show_provenance": "Fix where this record points in the source, when its recorded location is wrong.",
    "intelligence.show_raw": "Show or hide the record's raw stored data (advanced).",
    "intelligence.help_focused": "Explain the field that has keyboard focus.",
    "intelligence.jump_to_source": "Open the source at this record's exact passage and highlight it.",
    "intelligence.expand_inspector": "Open the selected record in a larger, resizable window.",
    "intelligence.browse_occurrences": "List every mention of this entity, or every reference to this bibliography entry, page by page.",
    # Entities
    "intelligence.entity_review": "Review how this entity's mentions are grouped: merge, keep separate or split.",
    "intelligence.entity_merge": "Merge the selected entities into the first one selected: their mentions and spellings move to it. Each merge can be undone.",
    "intelligence.entity_never_merge": "Record that the two selected entities are different, so they are never merged automatically.",
    "intelligence.entity_subgroup": "Put the selected entities under a broader one (e.g. Yucatec Maya under Maya). Each stays its own entity with its own mentions; the broader one lists them.",
    "intelligence.entity_split": "Move the selected mentions out of this entity into a new one you name.",
    # Review queue
    "intelligence.review_issues": "Work through items the system is unsure about, one at a time.",
    "intelligence.review_save_next": "Save your decision on this item and show the next one.",
    "intelligence.review_skip": "Leave this item for now; it comes back when you revisit skipped items.",
    "intelligence.review_defer": "Put this item aside for later without deciding.",
    "intelligence.review_undo": "Undo your last decision in this queue.",
    "intelligence.review_jump": "Open the source at this item's passage.",
    "intelligence.review_reset_queue": "Show items you skipped earlier again.",
    # Connection and grouping decisions
    "intelligence.decision.confirm": "Accept this proposal: the link or grouping becomes confirmed. Undo reverses it.",
    "intelligence.decision.reject": "Reject this proposal: it is hidden and not proposed again. Undo reverses it.",
    "intelligence.decision.undo": "Undo the decision you just saved.",
    "intelligence.decision.reload": "Reload both records, in case they changed elsewhere.",
    "intelligence.decision.discard": "Discard the choices you made here without saving.",
    "intelligence.decision.jump_source": "Open the source at a passage that mentions the first item.",
    "intelligence.decision.jump_target": "Open the source at a passage that mentions the second item.",
    "intelligence.decision.jump_evidence": "Open the source at the sentence this link is based on.",
    "intelligence.decision.guide": "Explain what each decision does.",
    # Connections
    "intelligence.review_connections": "Go through connections between entities one sentence at a time. Filter by kind of connection or entity type.",
    "intelligence.connection.save": "Save this as a confirmed connection of the chosen type. Undo reverses it.",
    "intelligence.connection.reject": "Reject this connection: the sentence does not connect them. Undo reverses it.",
    "intelligence.connection.skip": "Leave this one for now and show the next connection.",
    "intelligence.connection.undo": "Undo your last save or reject.",
    "intelligence.connection.jump": "Open the source at this sentence.",
    # Dock toolbar
    "intelligence.refresh": "Reload this tab from the project.",
    "intelligence.backfill": "Run every processing stage that has not finished for the chosen sources.",
    "intelligence.processing": "See and control processing stages for each source.",
    "intelligence.previous_page": "Show the previous page of results.",
    "intelligence.next_page": "Show the next page of results.",
}


def tooltip(ctx, identity: str, label: str = "") -> str:
    keys = getattr(ctx, "keybinding_registry", None)
    spec = keys.get_spec(identity) if keys is not None and hasattr(keys, "get_spec") else None
    text = TIPS.get(identity) or (spec.description if spec and spec.description and spec.description != label else "")
    text = text or label
    shortcut = keys.get_effective_key(identity) if spec is not None else ""
    return f"{text} ({shortcut})" if shortcut else text
