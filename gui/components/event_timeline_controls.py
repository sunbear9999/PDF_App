"""Typed temporal filters and entity selection, shared by timeline consumers."""
import json
import weakref

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel,QLineEdit,QComboBox,QSpinBox,QCheckBox,QPushButton
from core.models.event_projection import coerce_filter,MAX_WORD_WINDOW
from core.models.intelligence_models import ArtifactQuery,SourceScope
from core.events.domains.help_events import HelpIntent,HelpPayload
from gui.components.artifact_tasks import ArtifactTaskRunner
from gui.components.artifact_actions import bind_artifact_action
from gui.components.cursor_pager import CursorPager
from gui.utils.layout_helpers import make_shrinkable


class EventTimelineControls(QWidget):
    changed=Signal()

    def __init__(self,app_context,parent=None):
        super().__init__(parent)
        self.ctx,self.controller=app_context,None
        self.applied=coerce_filter()
        self.compiled_expression,self.compiled_text=None,''
        self._entity_query=None
        self.bindings={}
        self.excluded=set()
        self.included=set()
        self._build()
        self.finder=ArtifactTaskRunner(app_context,self)
        self.finder.completed.connect(self._entities_ready)
        self.finder.failed.connect(self.status.setText)
        self.destroyed.connect(self.finder.shutdown)
        bus=getattr(app_context,'bus',None)
        self._clear_signal=getattr(bus,'project_clearing_started',None)
        if self._clear_signal is not None:
            self._clear_signal.connect(self.clear)
        self._help_target()

    def attach(self,controller):
        self.controller=weakref.ref(controller)

    def _build(self):
        layout=QVBoxLayout(self)
        self.expression=QLineEdit(self)
        self.expression.setPlaceholderText('Alice AND ("Bob Smith" OR Carol); NOT excludes a co-mention')
        self.expression.setAccessibleName('Boolean entity filter: AND, OR, NOT and parentheses')
        self._row(layout,'Entities',self.expression)
        row=QHBoxLayout()
        self.entity_search=QLineEdit(self)
        self.entity_search.setPlaceholderText('Find a person, organization or concept…')
        self.entity_search.setAccessibleName('Search current entities in the selected source scope')
        self.entity_choices=QComboBox(self)
        self.entity_choices.setAccessibleName('Matching entities; choose a typed identity')
        self.entity_choices.setMinimumContentsLength(12)
        self.entity_choices.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        row.addWidget(self.entity_search,1)
        self._button(row,'Find entity','find_entity',self.find_entity)
        layout.addLayout(row)
        row=QHBoxLayout(); row.addWidget(self.entity_choices,1)
        self._button(row,'Add entity','add_entity',self.add_entity)
        layout.addLayout(row)
        self.entity_pager=CursorPager(self.ctx,self)
        self.entity_pager.register_actions('intelligence.timeline.entities','toolbar:intelligence:timeline')
        self.entity_pager.requested.connect(self.find_entity)
        layout.addWidget(self.entity_pager)
        make_shrinkable(self.entity_search,self.entity_choices,self.expression)
        self._date_controls(layout)
        self.status=QLabel('Event candidates are sentence associations, not verified real-world events.',self)
        self.status.setWordWrap(True)
        self.status.setAccessibleName('Timeline filter and evidence diagnostics')
        layout.addWidget(self.status)
        self.expression.returnPressed.connect(self.apply)
        self.entity_search.returnPressed.connect(self.find_entity)

    def _date_controls(self,layout):
        row=QHBoxLayout()
        self.date_from,self.date_to=QLineEdit(self),QLineEdit(self)
        for label,control in (('From',self.date_from),('Through',self.date_to)):
            control.setPlaceholderText('YYYY / YYYY-MM / YYYY-MM-DD')
            control.setAccessibleName('Timeline '+label.lower()+' date, inclusive calendar interval')
            row.addWidget(QLabel(label,self)); row.addWidget(control,1)
        layout.addLayout(row)
        self.zoom=QSpinBox(self); self.zoom.setRange(1,16)
        self.zoom.setAccessibleName('Calendar chart zoom, 1 to 16')
        self._row(layout,'Calendar zoom',self.zoom)
        row=QHBoxLayout()
        self.association=QComboBox(self)
        self.association.addItem('Same recorded sentence','sentence')
        self.association.addItem('Assigned roles on accepted event','event')
        self.association.addItem('Words from date, within its sentence','word_window')
        self.association.setMinimumContentsLength(12)
        self.association.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.association.setAccessibleName('Entity association rule')
        self.words=QSpinBox(self); self.words.setRange(0,MAX_WORD_WINDOW); self.words.setValue(30)
        self.words.setAccessibleName('Maximum whitespace-separated words between date and entity')
        row.addWidget(self.association,1); row.addWidget(self.words)
        layout.addLayout(row)
        self.unresolved=QCheckBox('Include unresolved dates',self)
        self.diagnostic=QCheckBox('All date observations (diagnostic)',self)
        self.hidden=QCheckBox('Show hidden events',self)
        for control in (self.unresolved,self.diagnostic,self.hidden):
            control.setAccessibleName(control.text()); layout.addWidget(control)
        row=QHBoxLayout()
        self._button(row,'Apply filters','apply',self.apply)
        self._button(row,'Timeline help','help',self.show_help)
        layout.addLayout(row)

    def _row(self,layout,label,control):
        row=QHBoxLayout(); caption=QLabel(label,self); caption.setBuddy(control)
        row.addWidget(caption); row.addWidget(control,1); layout.addLayout(row)

    def _button(self,layout,label,key,callback):
        button=QPushButton(label,self); button.setAccessibleName(label)
        button.clicked.connect(callback); layout.addWidget(button)
        bind_artifact_action(self.ctx,self,'intelligence.timeline.'+key,label,callback,'toolbar:intelligence:timeline',button)
        return button

    def current_filter(self):
        return coerce_filter({'expression':self.compiled_expression if self.expression.text()==self.compiled_text else None,
            'expression_text':self.expression.text(),'entity_bindings':dict(self.bindings),
            'association':self.association.currentData(),'window_words':self.words.value(),
            'date_from':self.date_from.text().strip(),'date_to':self.date_to.text().strip(),
            'include_unresolved':self.unresolved.isChecked(),'include_excluded':self.diagnostic.isChecked(),
            'excluded_artifact_ids':tuple(self.excluded),'show_hidden':self.hidden.isChecked(),
            'included_artifact_ids':tuple(self.included),'zoom':self.zoom.value()})

    def apply(self):
        controller=self.controller() if self.controller else None
        if controller and controller._rows['timeline'][1].form and controller._rows['timeline'][1].form.has_changes:
            self.status.setText('Save or discard the current artifact edit before applying timeline filters.')
            return False
        try:
            self.applied=self.current_filter()
        except ValueError as exc:
            self.status.setText(str(exc)); return False
        self.status.setText('Applying filters to current stored evidence…')
        self.changed.emit()
        return True

    def set_filter(self,value):
        value=coerce_filter(value)
        self.compiled_expression,self.compiled_text=value.expression,value.expression_text
        self.bindings=dict(value.entity_bindings); self.excluded=set(value.excluded_artifact_ids)
        self.included=set(value.included_artifact_ids); self.zoom.setValue(value.zoom)
        self.expression.setText(value.expression_text)
        self.date_from.setText(value.date_from); self.date_to.setText(value.date_to)
        self.association.setCurrentIndex(self.association.findData(value.association)); self.words.setValue(value.window_words)
        self.unresolved.setChecked(value.include_unresolved); self.diagnostic.setChecked(value.include_excluded)
        self.hidden.setChecked(value.show_hidden)

    def find_entity(self):
        controller=self.controller() if self.controller else None
        source=controller.scope.currentData() if controller else ''
        key=(source,self.entity_search.text())
        if key!=self._entity_query:
            self._entity_query=key
            self.entity_pager.cursor,self.entity_pager.next_cursor,self.entity_pager.history='','',[]
        self.finder.start('query_entities',query=ArtifactQuery(source_scope=SourceScope(source_ids=[source] if source else []),
            text=self.entity_search.text(),cursor=self.entity_pager.cursor,statuses=['complete'],review_states=['unreviewed','verified','corrected'],mention_limit=0,limit=20))

    def _entities_ready(self,_):
        result,self.finder.result=self.finder.result or {},None
        if result.get('invalidated'):
            self.entity_pager.reset(); return
        self.entity_pager.apply(result)
        self.entity_choices.clear()
        for index,row in enumerate(result.get('items') or [],1):
            name=row.get('exact_text') or row.get('normalized_key') or 'Unnamed entity'
            kind=(row.get('metadata') or {}).get('entity_type') or 'Entity'
            self.entity_choices.addItem(f'{name} ({kind}) · choice {index}',row['artifact_id'])
        self.status.setText('Choose a typed identity, then Add entity. Refine the search to find more matches.' if self.entity_choices.count() else 'No current matching entities in this scope. Run Entities processing or refine the search.')

    def add_entity(self):
        identity=self.entity_choices.currentData()
        if not identity:
            self.status.setText('Find and choose a current entity first.'); return
        label=self.entity_choices.currentText()
        suffix=1
        while label in self.bindings and self.bindings[label]!=identity:
            suffix+=1; label=self.entity_choices.currentText()+f' · alternate {suffix}'
        self.bindings[label]=identity
        text=self.expression.text().strip()
        pending=text.endswith('(') or (text and text.split()[-1].upper() in {'AND','OR','NOT'})
        separator=' ' if pending else ' AND '
        self.expression.setText(text+(separator if text else '')+json.dumps(label,ensure_ascii=False))

    def clear(self,*_):
        self.finder.cancel(); self.set_filter(None); self.applied=coerce_filter()
        self._entity_query=None
        self.entity_pager.cursor,self.entity_pager.next_cursor,self.entity_pager.history='','',[]
        self.entity_pager.apply({'items':[]})
        self.entity_choices.clear(); self.entity_search.clear()
        self.status.setText('Open a project to browse event candidates.')

    def show_result(self,snapshot):
        count=len(snapshot.get('timeline') or [])
        continuation=' More candidates may appear on later pages.' if snapshot.get('next_cursor') else ''
        diagnostic=' Diagnostic includes non-event dates.' if self.applied.include_excluded else ''
        self.status.setText(f'{count} event/date candidates on this page. Co-mention does not establish participation.'+
            continuation+diagnostic+' '+ ' '.join(snapshot.get('diagnostics') or []))

    def shutdown(self):
        self.finder.shutdown()
        if self._clear_signal is not None:
            try: self._clear_signal.disconnect(self.clear)
            except (TypeError,RuntimeError): pass
            self._clear_signal=None

    def show_help(self):
        bus=getattr(self.ctx,'bus',None)
        if bus:
            bus.help_action_requested.emit(HelpIntent.SHOW_TOPIC,HelpPayload(topic_id='core.intelligence.events'))

    def _help_target(self):
        self.setWhatsThis('Event candidates link current dates to recorded sentence evidence. AND/OR/NOT select co-mentioned entities. F1 explains intervals, proximity, uncertainty and personal filters.')
        registry=getattr(getattr(self.ctx,'help_service',None),'ui_target_registry',None)
        if registry:
            identity='intelligence.timeline.filters.'+str(id(self))
            registry.register_widget(identity,self,'core.intelligence.events')
            self.destroyed.connect(lambda *_,r=registry,key=identity:r.unregister(key))
