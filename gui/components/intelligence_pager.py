"""Bounded database paging shared by the Intelligence dock's artifact views."""
from dataclasses import asdict
import json

from PySide6.QtCore import QTimer
from gui.components.cursor_pager import CursorPager

from core.db.intelligence_artifact_queries import coerce_query
from core.models.intelligence_models import SourceScope


ARTIFACT_PAGE_SIZE = 200
ENTITY_PAGE_SIZE = 50
SEARCH_DEBOUNCE_MS = 200


class IntelligencePager(CursorPager):

    def __init__(self, app_context, parent=None):
        super().__init__(app_context, parent)
        self.ctx = app_context
        self.scope = self.tabs = self.search = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(SEARCH_DEBOUNCE_MS)
        self.timer.timeout.connect(self.requested)
        self.register_actions("intelligence", "toolbar:intelligence:paging")

    def attach(self, scope, tabs, search):
        self.scope, self.tabs, self.search = scope, tabs, search
        scope.currentIndexChanged.connect(self.reset)
        scope.sources_invalidated.connect(self.reset)
        tabs.currentChanged.connect(self.reset)
        search.textChanged.connect(self.search_changed)


    def query(self):
        widget = self.tabs.currentWidget()
        view_id = widget.property("intelligence_view_id") if widget else "overview"
        registry = getattr(self.ctx, "intelligence_view_registry", None)
        spec = registry.get(view_id) if registry else None
        values = dict(spec.query or {}) if spec else {}
        values.setdefault("artifact_types", list(spec.required_artifact_types) if spec else [])
        bar = getattr(widget, "filter_bar", None)
        if bar is not None:
            values.update(bar.query_values())
        values.update(source_scope=SourceScope(source_ids=[self.scope.currentData()] if self.scope.currentData() else []),
                      text=self.search.text(), cursor=self.cursor, limit=ENTITY_PAGE_SIZE if view_id == "entities" else ARTIFACT_PAGE_SIZE)
        if view_id != "artifacts":
            values.update(statuses=["complete"], review_states=["unreviewed", "verified", "corrected"], current_only=True)
        if view_id == "timeline":
            values.update(order_by="date", timeline_only=True)
        return view_id, coerce_query(values)

    def facet_fields(self):
        """Filter menus on the current tab that want per-value counts."""
        bar = getattr(self.tabs.currentWidget(), "filter_bar", None) if self.tabs else None
        return list(bar.facet_fields()) if bar is not None and not self.cursor else []

    def apply(self, snapshot):
        super().apply(snapshot)
        self.status.setToolTip("Search covers every matching artifact or processing run, including later pages.")

    def request_key(self):
        view_id, query = self.query()
        return json.dumps([view_id, asdict(query),self.projection()], sort_keys=True)

    def projection(self):
        widget=self.tabs.currentWidget()
        controls=getattr(widget,'timeline_filters',None) or getattr(widget,'network_filters',None)
        return asdict(controls.applied) if controls is not None else None


    def search_changed(self, *_):
        self.cursor, self.next_cursor, self.history = "", "", []
        self._update_buttons()
        self.timer.start()





    def shutdown(self):
        self.timer.stop()
        for signal, slot in ((self.scope.currentIndexChanged, self.reset),
                             (self.scope.sources_invalidated, self.reset),
                             (self.tabs.currentChanged, self.reset),
                             (self.search.textChanged, self.search_changed)):
            signal.disconnect(slot)
