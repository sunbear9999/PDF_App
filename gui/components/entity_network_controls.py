"""Personal network perspective controls using the shared saved-view component."""
import weakref
from PySide6.QtCore import Signal,Qt
from PySide6.QtWidgets import QWidget,QHBoxLayout,QVBoxLayout,QLabel,QCheckBox,QPushButton
from core.models.network_projection import coerce_network_filter
from gui.components.artifact_actions import bind_artifact_action


class EntityNetworkControls(QWidget):
    changed=Signal()
    kind='network'

    def __init__(self,app_context,parent=None):
        super().__init__(parent)
        self.ctx,self.controller=app_context,None
        self.applied=coerce_network_filter(); self.focus=''; self.excluded=set()
        layout=QVBoxLayout(self)
        self.heading=QLabel('All current entity connections in the selected source scope.',self)
        self.heading.setWordWrap(True); self.heading.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.heading)
        self.candidates=QCheckBox('Show candidate connections and co-occurrences',self); self.candidates.setChecked(True)
        layout.addWidget(self.candidates)
        row=QHBoxLayout(); layout.addLayout(row)
        for label,key,callback in (('Apply','apply',self.apply),('All entities','all_entities',self.all_entities),
            ('Expand source entity','expand_source',self.expand_source),('Expand target entity','expand_target',self.expand_target),
            ('Review connection…','review',self.review)):
            self._button(row,label,key,callback)

    def attach(self,controller):
        self.controller=weakref.ref(controller)

    def _button(self,layout,label,key,callback):
        button=QPushButton(label,self); button.clicked.connect(callback); layout.addWidget(button)
        bind_artifact_action(self.ctx,self,'intelligence.network.'+key,label,callback,'toolbar:intelligence:network',button)
        return button

    def current_filter(self):
        return coerce_network_filter({'focus_entity_id':self.focus,'include_candidates':self.candidates.isChecked(),
            'excluded_assertion_ids':tuple(self.excluded)})

    def set_filter(self,value):
        value=coerce_network_filter(value)
        self.focus=value.focus_entity_id; self.excluded=set(value.excluded_assertion_ids)
        self.candidates.setChecked(value.include_candidates)

    def apply(self):
        self.applied=self.current_filter()
        self.heading.setText('Focused perspective; expand a displayed endpoint or return to All entities.' if self.focus else
            'All current entity connections in the selected source scope.')
        self.changed.emit(); return True

    def all_entities(self):
        self.focus=''; self.apply()

    def focus_entity(self,identity):
        self.focus=identity; self.apply()

    def _selected(self):
        controller=self.controller() if self.controller else None
        rows=controller._selected_rows('network') if controller else []
        return rows[0] if len(rows)==1 else None

    def expand_source(self):
        row=self._selected()
        if row: self.focus_entity(row['source_entity_id'])

    def expand_target(self):
        row=self._selected()
        if row: self.focus_entity(row['target_entity_id'])

    def review(self):
        row=self._selected()
        if row:
            from gui.components.dialogs.connection_review_dialog import open_connection_review
            open_connection_review(self.ctx,self,assertion_id=row['assertion_id'])

    def shutdown(self):
        pass  # Child-owned controls have no backend signal subscriptions or workers.
