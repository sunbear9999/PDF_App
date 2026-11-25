"""Reusable browser panel layout and registered expansion/occurrence actions."""
import weakref
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QToolBar, QSplitter,QToolButton,QHBoxLayout,QLabel,QScrollArea, QAbstractItemView

from gui.components.artifact_inspector import ArtifactInspector
from gui.components.artifact_tree import ArtifactTreeView
from gui.components.artifact_actions import bind_artifact_action
from gui.components.intelligence_filter_bar import IntelligenceFilterBar
from gui.components.icon_buttons import iconize
from core.events.domains.help_events import HelpIntent, HelpPayload

TREE_DEFAULT_HEIGHT, INSPECTOR_DEFAULT_HEIGHT = 360, 400
TIMELINE_FILTER_MIN_HEIGHT = 120
TREE_MIN_HEIGHT, INSPECTOR_MIN_HEIGHT = 120, 220   # the inspector stays usable
PANEL_ICONS = {"Verify": "✔", "Reject": "✖", "Verify date": "✔", "Reject date": "✖", "History": "🕘",
               "Jump to source": "↗", "Rerun stage": "⟳", "Cancel source": "⏹"}
PANEL_TIPS = {
    "_set_review": "Verify or reject the selected rows. Verified rows survive reprocessing; rejected rows are hidden. Undo from the inspector.",
    "_show_history": "Show earlier versions of the selected record and who changed it.",
    "_jump_to_source": "Open the source at the selected record's exact passage.",
    "_rerun_selected_stage": "Run the selected processing stage again for its source.",
    "_cancel_selected_source": "Stop queued and running processing for the selected sources.",
}


class IntelligenceViewPanel(QWidget):
    def __init__(self, app_context, parent=None):
        super().__init__(parent)
        self.ctx = app_context

    def configure(self, view_id, controller):
        self.view_id, self._controller = view_id, weakref.ref(controller)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 0)
        layout.setSpacing(3)
        actions = QToolBar(self)
        actions.setMovable(False)
        actions.setFloatable(False)
        actions.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        actions.setAccessibleName("Intelligence view actions")
        if view_id != "runs":
            definitions = [("Verify date" if view_id=='timeline' else "Verify", "_set_review", (view_id, "verified")),
                           ("Reject date" if view_id=='timeline' else "Reject", "_set_review", (view_id, "rejected")),
                           ("History", "_show_history", (view_id,)),
                           ("Jump to source", "_jump_to_source", (view_id,))]
        else:
            definitions = [("Rerun stage", "_rerun_selected_stage", ()), ("Cancel source", "_cancel_selected_source", ())]
        for label, method, arguments in definitions:
            action = actions.addAction(label, lambda *_, m=method, a=arguments: self._call(m, *a))
            iconize(action, PANEL_ICONS.get(label, ""), label, PANEL_TIPS.get(method, label))
            action.setToolTip(action.toolTip() or PANEL_TIPS.get(method, label))
        if view_id in {"entities", "artifacts"}:
            for key,label in (("merge","Merge selected…"),("never_merge","Keep separate…"),("subgroup","Group under…"),("split","Split mentions…")):
                self._action(actions,label,"intelligence.entity_"+key,getattr(self,"entity_"+key))
        self._action(actions, "Expand inspector…", "intelligence.expand_inspector", self.expand_inspector)
        if view_id in {"entities", "citations"}:
            self._action(actions, "Browse all occurrences…", "intelligence.browse_occurrences", self.browse_occurrences)
        self._action(actions, "Browsing help", "intelligence.browsing_guide", self.show_guide)
        self._action(actions, "Reset view layout", "intelligence.reset_view_layout", self.reset_layout)
        if view_id in {'timeline','artifacts'}:
            self._action(actions, 'Create event from passage…', 'intelligence.event.create', self.create_event)
        if view_id == 'timeline':
            self._action(actions, 'Edit event / participants…', 'intelligence.event.edit', self.edit_event)
        layout.addWidget(actions)
        registry = getattr(self.ctx, "intelligence_view_registry", None)
        spec = registry.get(view_id) if registry else None
        self.filter_bar = IntelligenceFilterBar(spec, self) if spec else None
        if self.filter_bar is not None:
            self.filter_bar.changed.connect(lambda: controller.pager.reset())
            layout.addWidget(self.filter_bar)
        if view_id == 'timeline':
            self._timeline_controls(layout,controller)
        if view_id == 'network':
            self._network_controls(layout,controller)
        self.splitter = QSplitter(Qt.Orientation.Vertical, self)
        self.splitter.setChildrenCollapsible(False)
        self.tree = self._artifact_tree(view_id, self.splitter)
        if view_id == 'timeline':
            self._timeline_tree()
        self.inspector = ArtifactInspector(self.splitter, app_context=self.ctx)
        self.inspector.saved.connect(controller.refresh)
        # The list and its page buttons stay together; the dock moves its pager into this slot.
        self.tree_box = QWidget(self.splitter)
        box = QVBoxLayout(self.tree_box)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(2)
        box.addWidget(self.tree, 1)
        self.pager_slot = box
        self.splitter.addWidget(self.tree_box)
        self.splitter.addWidget(self.inspector)
        # The list gets most of the height; the inspector shows the selected record below it.
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)
        self.splitter.setSizes([TREE_DEFAULT_HEIGHT, INSPECTOR_DEFAULT_HEIGHT])
        self.inspector.setMinimumHeight(INSPECTOR_MIN_HEIGHT)
        self.tree.setMinimumHeight(TREE_MIN_HEIGHT)
        if view_id == 'timeline':
            outer = QSplitter(Qt.Orientation.Vertical, self)
            outer.setChildrenCollapsible(False)
            outer.addWidget(self.timeline_top)
            outer.addWidget(self.splitter)
            outer.setStretchFactor(0, 1)
            outer.setStretchFactor(1, 3)
            self.timeline_splitter = outer
            layout.addWidget(outer, 1)
        else:
            layout.addWidget(self.splitter, 1)
        self._register_view_help(view_id)

    def _register_view_help(self,view_id):
        registry = getattr(getattr(self.ctx, "help_service", None), "ui_target_registry", None)
        if registry:
            identity = f"intelligence.browser.{view_id}.{id(self)}"
            registry.register_widget(identity, self, {'timeline':'core.intelligence.events','network':'core.intelligence.networks'}.get(view_id,'core.intelligence.browsing'))
            self.destroyed.connect(lambda *_, r=registry, target=identity: r.unregister(target))

    def _network_controls(self,layout,controller):
        from gui.components.entity_network_controls import EntityNetworkControls
        from gui.components.entity_network_view import EntityNetworkView
        from gui.components.projection_view_controls import ProjectionViewControls
        self.network_filters=EntityNetworkControls(self.ctx,self)
        self.network_filters.attach(controller)
        self.network_filters.changed.connect(lambda:controller.pager.reset())
        layout.addWidget(self.network_filters)
        self.perspectives=ProjectionViewControls(self.ctx,self)
        self.perspectives.attach(self.network_filters,controller)
        self.network_view_toggle=QToolButton(self)
        self.network_view_toggle.setText('Saved network perspectives'); self.network_view_toggle.setCheckable(True)
        self.network_view_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.network_view_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        layout.addWidget(self.network_view_toggle)
        self.network_saved=QScrollArea(self); self.network_saved.setWidgetResizable(True)
        self.network_saved.setMinimumHeight(220); self.network_saved.setMaximumHeight(220)
        self.network_saved.setWidget(self.perspectives); self.network_saved.hide()
        self.network_view_toggle.toggled.connect(self._show_network_views)
        bind_artifact_action(self.ctx,self,'intelligence.network.toggle_views','Expand Saved Network Perspectives',self.toggle_network_views,'toolbar:intelligence:network')
        layout.addWidget(self.network_saved)
        self.network_diagram=EntityNetworkView(self.ctx,self)
        self.network_diagram.entity_selected.connect(self.network_filters.focus_entity)
        layout.addWidget(self.network_diagram)
        # The shared tree is created after these controls; headers follow in show_network.

    def show_network(self, snapshot):
        self.tree.setHeaderLabels(['Connection','Entities / explanation','Evidence source','Assessment'])
        self.network_diagram.set_network(snapshot)

    def toggle_network_views(self):
        self.network_view_toggle.setChecked(not self.network_view_toggle.isChecked())

    def _show_network_views(self,shown):
        self.network_saved.setVisible(shown)
        self.network_view_toggle.setArrowType(Qt.ArrowType.DownArrow if shown else Qt.ArrowType.RightArrow)

    def _timeline_controls(self,layout,controller):
        from gui.components.event_timeline_controls import EventTimelineControls
        from gui.components.projection_view_controls import ProjectionViewControls
        header=QHBoxLayout()
        self.filter_toggle=QToolButton(self)
        self.filter_toggle.setText('Filters && saved views')   # && shows a literal ampersand
        self.filter_toggle.setToolTip('Show or hide the timeline filters: names to look for, date range, how names must relate to dates, and saved filter sets.')
        self.filter_toggle.setCheckable(True)
        self.filter_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.filter_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.filter_toggle.setAccessibleName('Expand timeline filters and saved views')
        self.timeline_summary=QLabel('Event candidates use stored dates and sentence evidence.',self)
        self.timeline_summary.setWordWrap(True)
        header.addWidget(self.filter_toggle); header.addWidget(self.timeline_summary,1)
        layout.addLayout(header)
        # Filters and chart sit in their own splitter pane above the list, so they can be resized.
        self.timeline_top=QWidget(self); top=QVBoxLayout(self.timeline_top); top.setContentsMargins(0,0,0,0)
        self.timeline_body=QScrollArea(self.timeline_top); self.timeline_body.setWidgetResizable(True)
        self.timeline_body.setMinimumHeight(TIMELINE_FILTER_MIN_HEIGHT)
        content=QWidget(self.timeline_body); body=QVBoxLayout(content)
        self.timeline_filters=EventTimelineControls(self.ctx,content)
        self.timeline_filters.attach(controller)
        self.timeline_filters.changed.connect(lambda:(self.timeline_summary.setText(self.timeline_filters.status.text()),
                                                      controller.pager.reset()))
        self.perspectives=ProjectionViewControls(self.ctx,content)
        self.perspectives.attach(self.timeline_filters,controller)
        body.addWidget(self.timeline_filters); body.addWidget(self.perspectives)
        self.timeline_body.setWidget(content); self.timeline_body.hide()
        top.addWidget(self.timeline_body,1)
        from gui.components.event_interval_chart import EventIntervalChart
        self.interval_chart = EventIntervalChart(self.ctx, self)
        self.interval_chart.selected.connect(self._select_event)
        top.addWidget(self.interval_chart)
        self.filter_toggle.toggled.connect(self._toggle_timeline)
        bind_artifact_action(self.ctx,self,'intelligence.timeline.toggle_filters','Expand Timeline Filters',self.toggle_timeline,'toolbar:intelligence:timeline')

    def toggle_timeline(self):
        self.filter_toggle.setChecked(not self.filter_toggle.isChecked())

    def _select_event(self, index):
        model = self.tree.model()
        rows=self.interval_chart.canvas.rows
        if index < len(rows):
            for position in range(model.rowCount()):
                row=model.index(position,0).data(Qt.ItemDataRole.UserRole) or {}
                if row.get('artifact_id')==rows[index]['artifact_id']:
                    self.tree.setCurrentIndex(model.index(position,0)); break

    def show_events(self, snapshot):
        self.interval_chart.set_events(snapshot.get('timeline') or [], self.timeline_filters.applied.zoom)

    def _toggle_timeline(self,shown):
        self.timeline_body.setVisible(shown)
        self.filter_toggle.setArrowType(Qt.ArrowType.DownArrow if shown else Qt.ArrowType.RightArrow)
        self.filter_toggle.setAccessibleName(('Collapse' if shown else 'Expand')+' timeline filters and saved views')

    def _timeline_tree(self):
        from gui.components.event_timeline_view import TimelineDateDelegate
        self.tree.setHeaderLabels(['Date / precision','Event candidate and co-mentioned entities','Source','Assessment'])
        delegate=TimelineDateDelegate(self.tree)
        self.tree.setItemDelegate(delegate)
        self.tree.setItemDelegateForColumn(0,delegate)
        self.tree.setUniformRowHeights(True)
        self.tree.setWordWrap(False)   # one line per event; the full sentence is in the tooltip and inspector
        self.tree.setAccessibleDescription('Chronological event candidates on a vertical date rail. Select a row to inspect the canonical date and jump to its recorded source.')

    def create_event(self):
        self._event_dialog(True)

    def edit_event(self):
        self._event_dialog(False)

    def _event_dialog(self, create):
        from gui.components.dialogs.event_details_dialog import open_event_details
        controller = self._controller()
        rows = controller._selected_rows(self.view_id) if controller else []
        if len(rows) != 1:
            if controller: controller.nav_status.setText('Select one source passage or timeline event first.')
            return
        row = rows[0]
        if create and row.get('context_artifact_id'):
            row = next((item for item in controller._last_snapshot.get('artifacts', [])
                        if item['artifact_id'] == row['context_artifact_id']), row)
        dialog = open_event_details(self.ctx, row, create=create, parent=self)
        if dialog:
            dialog.saved.connect(controller.refresh)

    def _artifact_tree(self, view_id, parent):
        tree = ArtifactTreeView(parent)
        tree.setObjectName(f"IntelligenceView_{view_id}")
        tree.setHeaderLabels(["Type", "Value", "Source", "State"])
        tree.setAlternatingRowColors(True)
        tree.setSortingEnabled(False)
        tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        tree.currentItemChanged.connect(lambda item, previous: self._call("_show_raw", item, previous))
        return tree

    def _call(self, name, *arguments):
        controller = self._controller()
        if controller is not None:
            getattr(controller, name)(*arguments)

    def _action(self, toolbar, label, identity, callback):
        bind_artifact_action(self.ctx, self, identity, label, callback, widget=toolbar.addAction(label, callback))

    def expand_inspector(self):
        self._call("_open_inspector", self.view_id)

    def browse_occurrences(self):
        self._call("_browse_occurrences", self.view_id)

    def show_guide(self):
        bus = getattr(self.ctx, "bus", None)
        if bus is not None:
            bus.help_action_requested.emit(HelpIntent.SHOW_TOPIC, HelpPayload(topic_id={'timeline':'core.intelligence.events','network':'core.intelligence.networks'}.get(self.view_id,'core.intelligence.browsing')))

    def reset_layout(self):
        self._call("_reset_view_layout", self.view_id)

    def entity_merge(self):
        self._call("_merge_entities")

    def entity_never_merge(self):
        self._call("_never_merge_entities")

    def entity_subgroup(self):
        self._call("_subgroup_entities")

    def entity_split(self):
        self._call("_split_entity_mentions")
