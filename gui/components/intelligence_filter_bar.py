"""Per-tab sort and filter controls for Intelligence views.

A view spec declares which sorts and filters it offers (``IntelligenceViewSpec.sorts``
and ``.filters``); this bar renders them and turns the choices into
``ArtifactQuery`` fields. Menus list the values that occur in the current scope,
with counts, so a choice never silently leads to an empty page.
"""
from __future__ import annotations

from typing import Dict, Iterable, List

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QMenu, QToolButton, QWidget

from core.models.entity_categories import MEASURE_TYPES, type_label
from core.registries.intelligence_view_registry import FILTER_LABELS, SORT_LABELS

REVIEW_LABELS = {"unreviewed": "Not reviewed", "verified": "Verified", "corrected": "Corrected",
                 "rejected": "Rejected", "superseded": "Superseded"}
ROLE_LABELS = {"claim": "Claim", "evidence": "Evidence", "reasoning": "Reasoning", "counterclaim": "Counterclaim",
               "methodology": "Methodology", "limitation": "Limitation", "": "Unclassified"}
FIELD_QUERY = {"entity_type": "entity_types", "artifact_type": "artifact_types",
               "review_state": "review_states", "argument_role": "argument_roles",
               "tag": "tag_filters", "source_tag": "source_tags"}
VERBATIM_FIELDS = {"tag", "source_tag"}      # user-made names, shown as written
DEFAULT_CHOICES = {"review_state": ["unreviewed", "verified", "corrected"]}


def _label(field: str, value: str) -> str:
    if field in VERBATIM_FIELDS:
        return value
    if field == "entity_type":
        return type_label(value)
    if field == "review_state":
        return REVIEW_LABELS.get(value, value)
    if field == "argument_role":
        return ROLE_LABELS.get(value, value.capitalize())
    return value.rsplit(".", 1)[-1].replace("_", " ").capitalize()     # "locallaws.holding" -> "Holding"


class ChoiceMenu(QToolButton):
    """A multi-select menu: nothing ticked means no restriction."""
    changed = Signal()

    def __init__(self, field: str, parent=None, offer_measures=True, labels=None):
        super().__init__(parent)
        self.field, self.selected, self.counts, self.labels = field, set(), {}, dict(labels or {})
        self.offer_measures = offer_measures and field == "entity_type"
        self.hide_measures = self.offer_measures
        self.require_all = False            # tags: every ticked tag (narrowing) instead of any
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setMenu(QMenu(self))
        self.menu().setToolTipsVisible(True)
        self.setAccessibleName(f"Filter by {FILTER_LABELS.get(field, field).lower()}")
        self.setToolTip(f"Show only rows of the ticked {FILTER_LABELS.get(field, field).lower()} values. "
                        "Nothing ticked shows everything.")
        self._rebuild()

    def _name(self, value: str) -> str:
        return self.labels.get(value) or _label(self.field, value)

    def set_counts(self, counts: Dict[str, int]) -> None:
        self.counts = dict(counts or {})
        self._rebuild()

    def _values(self) -> List[str]:
        values = list(self.counts) or list(DEFAULT_CHOICES.get(self.field, []))
        return sorted(set(values) | self.selected, key=lambda v: (-(self.counts.get(v) or 0), self._name(v)))

    def _rebuild(self) -> None:
        menu = self.menu()
        menu.clear()
        menu.addAction("Show all", self._clear).setToolTip("Remove this filter.")
        if self.offer_measures:
            names = menu.addAction("Hide dates, numbers and measures")
            names.setCheckable(True)
            names.setChecked(self.hide_measures)
            names.setToolTip("Dates, quantities, money and measured values are not names; hide them from the list.")
            names.toggled.connect(self._toggle_measures)
        if self.field == "tag":
            every = menu.addAction("Require all ticked tags")
            every.setCheckable(True)
            every.setChecked(self.require_all)
            every.setToolTip("On: rows must carry every ticked tag (narrows). Off: any ticked tag.")
            every.toggled.connect(self._toggle_require_all)
        menu.addSeparator()
        for value in self._values():
            count = self.counts.get(value)
            action = menu.addAction(self._name(value) + (f"  ({count:,})" if count is not None else ""))
            action.setCheckable(True)
            action.setChecked(value in self.selected)
            action.toggled.connect(lambda checked, v=value: self._toggle(v, checked))
        self._caption()

    def _caption(self) -> None:
        name = FILTER_LABELS.get(self.field, self.field)
        if self.selected:
            shown = ", ".join(self._name(v) for v in sorted(self.selected))
            self.setText(f"{name}: {shown[:40]}{'…' if len(shown) > 40 else ''}")
        else:
            self.setText(f"{name}: all" + (" names" if self.hide_measures else ""))

    def _toggle(self, value: str, checked: bool) -> None:
        (self.selected.add if checked else self.selected.discard)(value)
        self._caption()
        self.changed.emit()

    def _toggle_measures(self, checked: bool) -> None:
        self.hide_measures = checked
        self._caption()
        self.changed.emit()

    def _toggle_require_all(self, checked: bool) -> None:
        self.require_all = checked
        self._caption()
        if self.selected:
            self.changed.emit()

    def _clear(self) -> None:
        self.selected.clear()
        self.hide_measures = False
        self._rebuild()
        self.changed.emit()

    def query_values(self) -> Dict[str, list]:
        values = {FIELD_QUERY[self.field]: sorted(self.selected)} if self.selected else {}
        if self.field == "tag" and self.selected:
            values["tag_logic"] = "AND" if self.require_all else "OR"
        if self.field == "entity_type" and self.hide_measures and not self.selected:
            values["exclude_entity_types"] = sorted(MEASURE_TYPES)
        return values

    def state(self) -> dict:
        return {"selected": sorted(self.selected), "hide_measures": self.hide_measures, "require_all": self.require_all}

    def restore(self, state: dict) -> None:
        self.selected = set(state.get("selected") or [])
        self.hide_measures = bool(state.get("hide_measures", self.hide_measures))
        self.require_all = bool(state.get("require_all", False))
        self._rebuild()


class IntelligenceFilterBar(QWidget):
    changed = Signal()

    def __init__(self, spec, parent=None):
        super().__init__(parent)
        self.spec = spec
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.sort = QComboBox(self)
        self.sort.setAccessibleName("Sort this tab")
        self.sort.setToolTip("Choose the order of rows. Paging follows this order across the whole result.")
        for key in spec.sorts:
            self.sort.addItem(SORT_LABELS.get(key, key), key)
        self.sort.setCurrentIndex(max(0, self.sort.findData(spec.default_sort or (spec.sorts or [""])[0])))
        self.sort.currentIndexChanged.connect(self.changed)
        if spec.sorts:
            layout.addWidget(QLabel("Sort:", self))
            layout.addWidget(self.sort)
        self.menus = {}
        for field in spec.filters:
            menu = ChoiceMenu(field, self)
            menu.hide_measures = field == "entity_type" and spec.hide_measures
            menu._caption()
            menu.changed.connect(self.changed)
            self.menus[field] = menu
            layout.addWidget(menu)
        layout.addStretch(1)
        self.setVisible(bool(spec.sorts or spec.filters))

    def facet_fields(self) -> Iterable[str]:
        return list(self.menus)

    def set_facets(self, facets: Dict[str, Dict[str, int]]) -> None:
        for field, menu in self.menus.items():
            if field in (facets or {}):
                menu.set_counts(facets[field])

    def query_values(self) -> dict:
        values = {"order_by": self.sort.currentData()} if self.spec.sorts else {}
        for menu in self.menus.values():
            values.update(menu.query_values())
        return values

    def state(self) -> dict:
        return {"sort": self.sort.currentData(), "menus": {k: m.state() for k, m in self.menus.items()}}

    def restore(self, state: dict) -> None:
        if not state:
            return
        index = self.sort.findData(state.get("sort"))
        if index >= 0:
            self.sort.setCurrentIndex(index)
        for field, menu_state in (state.get("menus") or {}).items():
            if field in self.menus:
                self.menus[field].restore(menu_state)
