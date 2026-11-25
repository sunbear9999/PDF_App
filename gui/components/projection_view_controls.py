"""Project-local saved filters and personal event exclusions; no artifact mutation."""
import weakref
from dataclasses import asdict
from PySide6.QtCore import QSignalBlocker
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel,QComboBox,QLineEdit,QCheckBox
from core.events.domains.intelligence_events import IntelligenceEvent
from gui.components.artifact_tasks import ArtifactTaskRunner


class ProjectionViewControls(QWidget):
    def __init__(self,app_context,parent=None):
        super().__init__(parent)
        self.ctx,self.filters,self.controller=app_context,None,None
        self.snapshot,self.active_view={},''
        self.edit_guard=None
        self._connections=[]
        layout=QVBoxLayout(self)
        row=QHBoxLayout()
        self.heading=QLabel('Saved timeline filters',self); row.addWidget(self.heading)
        self.views=QComboBox(self); self.views.setAccessibleName('Project-local saved timeline filters')
        self.views.setMinimumContentsLength(12)
        self.views.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.views.addItem('Working filters',''); row.addWidget(self.views,1)
        layout.addLayout(row)
        self.name=QLineEdit(self); self.name.setMaxLength(80)
        self.name.setPlaceholderText('Name these filters; the current source scope stays separate')
        self.name.setAccessibleName('Saved timeline filter name')
        layout.addWidget(self.name)
        self.save_scope=QCheckBox('Save source scope and search with this perspective',self)
        self.save_scope.setAccessibleName('Include current source scope and search in saved perspective')
        layout.addWidget(self.save_scope)
        self.actions=QHBoxLayout(); layout.addLayout(self.actions)
        self.exclusions=QHBoxLayout(); layout.addLayout(self.exclusions)
        self.status=QLabel('Saved filters belong to this project; source artifacts are unchanged.',self)
        self.status.setWordWrap(True); self.status.setAccessibleName('Saved timeline filters status')
        layout.addWidget(self.status)
        self.reader=ArtifactTaskRunner(app_context,self); self.writer=ArtifactTaskRunner(app_context,self)
        self.reader.completed.connect(self._read); self.writer.completed.connect(self._written)
        self.reader.failed.connect(self.status.setText); self.writer.failed.connect(self.status.setText)
        self.destroyed.connect(self.reader.shutdown); self.destroyed.connect(self.writer.shutdown)
        self._listen()

    def attach(self,filters,controller):
        self.filters,self.controller=filters,weakref.ref(controller)
        self.kind=getattr(filters,'kind','timeline')
        self.heading.setText('Saved '+self.kind+' perspectives')
        self.name.setPlaceholderText('Name this research perspective')
        self.views.setAccessibleName('Saved '+self.kind+' perspectives')
        self.name.setAccessibleName('Saved '+self.kind+' perspective name')
        self.status.setAccessibleName('Saved '+self.kind+' perspective status')
        for label,key,callback in (('Load','load_view',self.load_view),('Save','save_view',self.save_view),
                ('New / reset','new_view',self.new_view),('Remove saved filters','delete_view',self.delete_view)):
            filters._button(self.actions,label,key,callback)
        for label,key,callback in (('Hide selected','hide_event',self.hide_selected),
                ('Restore hidden connections' if self.kind=='network' else 'Restore selected','restore_event',self.restore_selected)):
            filters._button(self.exclusions,label,key,callback)
        if self.kind=='timeline':
            filters._button(self.exclusions,'Include selected observation','include_event',self.include_selected)
            filters._button(self.exclusions,'Check replaced event IDs','check_ids',self.check_ids)
            filters._button(self.exclusions,'Apply confirmed replacements','apply_ids',self.apply_ids)
        self.reload()

    def _listen(self):
        bus=getattr(self.ctx,'bus',None)
        if bus is None: return
        for name,slot in (('project_loaded',self.reload),('project_clearing_started',self.clear),
                          ('intelligence_state_changed',self._changed)):
            signal=getattr(bus,name,None)
            if signal is not None:
                signal.connect(slot); self._connections.append((signal,slot))

    def reload(self,*_):
        self.reader.start('get_projection_views')

    def _changed(self,event,_payload):
        if event==IntelligenceEvent.PROJECTION_VIEW_CHANGED and not self.writer.busy:
            self.reload()

    def _read(self,_):
        self.snapshot,self.reader.result=self.reader.result or {},None
        self._choices(self.views.currentData())
        if self.active_view and self.edit_guard and self.edit_guard['expected_token']!=self.snapshot.get('token'):
            self.status.setText('Saved filters changed elsewhere. Your draft is retained; Load the current definition before overwriting it.')

    def _choices(self,selected=''):
        with QSignalBlocker(self.views):
            self.views.clear(); self.views.addItem('Working filters','')
            for view in self.snapshot.get('views') or []:
                if view['kind']==self.kind: self.views.addItem(view['name'],view['id'])
            self.views.setCurrentIndex(max(0,self.views.findData(selected)))

    def _guard(self):
        if self.writer.busy or self.reader.busy or not self.snapshot:
            self.status.setText('Wait for saved views to load, or reopen this project.'); return None
        return self.edit_guard if self.active_view and self.edit_guard else self._snapshot_guard()

    def _snapshot_guard(self):
        return {'expected_token':self.snapshot['token'],'expected_project':self.snapshot['project_identity']}

    def save_view(self):
        guard=self._guard()
        if guard is None: return
        try:
            value=asdict(self.filters.current_filter())
        except ValueError as exc:
            self.status.setText(str(exc)); return
        controller=self.controller()
        scope={'source_ids':[controller.scope.currentData()] if controller.scope.currentData() else [], 'text':controller.search.text()} if self.save_scope.isChecked() else None
        self.writer.start('save_projection_view',view_id=self.active_view,name=self.name.text(),filters=value,kind=self.kind,scope=scope,**guard)

    def delete_view(self):
        guard=self._guard()
        identity=self.views.currentData()
        if guard is not None and identity:
            self.writer.start('save_projection_view',view_id=identity,delete=True,**guard)

    def _written(self,_):
        if _=='check_projection_ids':
            self.id_check,self.writer.result=self.writer.result or {},None
            self.status.setText(f"{len(self.id_check.get('replacements',{}))} confirmed replacements available; {len(self.id_check.get('unresolved_ids',[]))} unresolved IDs retained. Apply confirmed replacements explicitly, then Save. No correspondence was guessed.")
            return
        self.snapshot,self.writer.result=self.writer.result or {},None
        identity=self.snapshot.get('view_id','')
        self.active_view=identity if any(view['id']==identity for view in self.snapshot.get('views') or []) else ''
        self.edit_guard=self._snapshot_guard()
        self._choices(self.active_view)
        self.status.setText('Saved filters updated. Working filters and source artifacts are unchanged; Load applies a saved choice.')

    def load_view(self):
        identity=self.views.currentData()
        view=next((view for view in self.snapshot.get('views') or [] if view['id']==identity),None)
        if view:
            self.filters.set_filter(view['filters']); self.name.setText(view['name'])
            scope=view.get('scope'); self.save_scope.setChecked(scope is not None)
            if scope is not None:
                controller=self.controller()
                identities=scope['source_ids']
                selected=controller.scope.findData(identities[0]) if identities else 0
                if len(identities)>1 or selected<0:
                    self.status.setText('A saved source is missing or this multi-source perspective needs the plugin query API. No scope was substituted.'); return
                controller.scope.setCurrentIndex(selected); controller.search.setText(scope['text'])
            if self.filters.apply():
                self.active_view=identity; self.edit_guard=self._snapshot_guard()

    def new_view(self):
        self.filters.set_filter(None)
        if self.filters.apply():
            self.active_view=''; self.name.clear(); self.views.setCurrentIndex(0)
            self.edit_guard=None
            self.save_scope.setChecked(False)

    def _exclusions(self,hide):
        controller=self.controller() if self.controller else None
        rows=controller._selected_rows(self.kind) if controller else []
        if self.kind=='network' and not hide:
            self.filters.excluded.clear(); self.filters.apply()
            self.status.setText('All hidden connections restored in working filters. Save to retain this change.'); return
        for row in rows:
            identity=row.get('assertion_id') if self.kind=='network' else row.get('artifact_id')
            if identity:
                if hide:
                    self.filters.excluded.add(identity)
                else:
                    self.filters.excluded.discard(identity)
        if rows:
            self.filters.apply()
            self.status.setText('Personal exclusions changed. Save these filters to retain them after reopening; no artifacts were rejected.')

    def include_selected(self):
        controller=self.controller()
        for row in controller._selected_rows('timeline'):
            self.filters.included.add(row['artifact_id']); self.filters.excluded.discard(row['artifact_id'])
        self.filters.apply()
        self.status.setText('Personally included observations remain labelled as observations. Save to retain this choice.')

    def check_ids(self):
        self.writer.start('check_projection_ids', filters=asdict(self.filters.current_filter()))

    def apply_ids(self):
        state=getattr(self,'id_check',{})
        controller=self.controller()
        db=getattr(getattr(self.ctx,'artifact_service',None),'db',None)
        if tuple(state.get('project_identity') or ())!=(id(db),id(getattr(db,'_conn',None))) or state.get('filters')!=asdict(self.filters.current_filter()):
            self.status.setText('Filters or project changed. Check replacements again before applying.'); return
        mapping=state['replacements']
        self.filters.excluded={mapping.get(key,key) for key in self.filters.excluded}
        self.filters.included={mapping.get(key,key) for key in self.filters.included}
        self.filters.apply(); self.id_check={}
        self.status.setText('Confirmed correspondence applied to working filters. Unresolved IDs remain unchanged. Save this perspective to retain the decision.')

    def hide_selected(self):
        self._exclusions(True)

    def restore_selected(self):
        self._exclusions(False)

    def clear(self,*_):
        self.reader.cancel(); self.writer.cancel(); self.snapshot={}; self.active_view=''
        self.edit_guard=None
        self.name.clear(); self._choices()

    def shutdown(self):
        self.reader.shutdown(); self.writer.shutdown()
        for signal,slot in self._connections:
            try: signal.disconnect(slot)
            except (TypeError,RuntimeError): pass
        self._connections.clear()
