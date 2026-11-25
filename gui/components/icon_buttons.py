"""Compact symbol buttons for Intelligence commands: one symbol, the full name on hover.

Buttons registered with ``bind_artifact_action(..., widget=...)`` get their symbol here by
command id, so plugins that register commands follow the same look (unknown ids keep their
text). The accessible name stays the full label for screen readers and keyboard users.
"""
from __future__ import annotations

from PySide6.QtWidgets import QAbstractButton, QSizePolicy

ICONS = {
    # inspector
    "intelligence.save": "💾", "intelligence.verify": "✔", "intelligence.reject_artifact": "✖",
    "intelligence.undo": "↶", "intelligence.reset": "🗑", "intelligence.show_guide": "📖",
    "intelligence.show_provenance": "📍", "intelligence.show_raw": "{ }", "intelligence.help_focused": "?",
    "intelligence.jump_to_source": "↗", "intelligence.expand_inspector": "⤢", "intelligence.browse_occurrences": "☰",
    "intelligence.entity_review": "🔀",
    # entities
    "intelligence.entity_merge": "⇉", "intelligence.entity_never_merge": "≠", "intelligence.entity_subgroup": "⊂",
    "intelligence.entity_split": "✂",
    # dock
    "intelligence.review_issues": "⚑", "intelligence.review_connections": "🔗", "intelligence.processing": "⚙",
    "intelligence.browsing_guide": "?", "intelligence.reset_view_layout": "↺",
    "intelligence.event.create": "📅", "intelligence.event.edit": "✎",
    # review queue
    "intelligence.review_save_next": "⏭", "intelligence.review_skip": "↷", "intelligence.review_defer": "⏸",
    "intelligence.review_undo": "↶", "intelligence.review_jump": "↗", "intelligence.review_reset_queue": "⟲",
    # decisions
    "intelligence.decision.confirm": "✔", "intelligence.decision.reject": "✖", "intelligence.decision.undo": "↶",
    "intelligence.decision.reload": "⟳", "intelligence.decision.discard": "🗑", "intelligence.decision.jump_source": "⇱",
    "intelligence.decision.jump_target": "⇲", "intelligence.decision.jump_evidence": "❝",
    # connections
    "intelligence.connection.save": "✔", "intelligence.connection.reject": "✖", "intelligence.connection.skip": "↷",
    "intelligence.connection.undo": "↶", "intelligence.connection.jump": "↗",
    # timeline and network filters
    "intelligence.timeline.find_entity": "🔍", "intelligence.timeline.add_entity": "＋", "intelligence.timeline.apply": "✓",
    "intelligence.network.apply": "✓", "intelligence.network.all_entities": "◎", "intelligence.network.expand_source": "⇱",
    "intelligence.network.expand_target": "⇲", "intelligence.network.review": "🔗",
}
SUFFIXES = {".previous_page": "◀", ".next_page": "▶", ".help": "?", ".guide": "?"}
BUTTON_WIDTH = 34


def symbol_for(identity: str) -> str:
    if identity in ICONS:
        return ICONS[identity]
    return next((symbol for suffix, symbol in SUFFIXES.items() if identity.endswith(suffix)), "")


def iconize(widget, symbol: str, name: str, tooltip: str = "") -> None:
    """Show ``symbol``; the full ``name`` stays the accessible name and leads the tooltip."""
    if not symbol or widget is None:
        return
    tip = tooltip or widget.toolTip() or name
    tip = tip if tip.startswith(name) else f"{name} — {tip}" if tip != name else name
    if isinstance(widget, QAbstractButton):
        widget.setText(symbol)
        widget.setAccessibleName(widget.accessibleName() or name)
        widget.setObjectName("iconButton")
        widget.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        widget.setMinimumWidth(BUTTON_WIDTH)
    else:                                   # QAction in a toolbar: the toolbar shows its icon text
        widget.setIconText(symbol)
    widget.setToolTip(tip)
