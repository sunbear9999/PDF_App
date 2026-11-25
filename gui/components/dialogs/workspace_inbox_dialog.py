"""Modeless paged Inbox with off-thread reads and explicit independent canvas copies."""
import weakref
from PySide6.QtCore import Qt,QPointF
from PySide6.QtWidgets import QDialog,QVBoxLayout,QComboBox,QPushButton,QTextEdit,QAbstractItemView
from core.events.domains.workspace_events import WorkspaceIntent,WorkspacePayload
from gui.components.paged_collection import PagedCollection
from gui.components.artifact_tasks import ArtifactTaskRunner
from gui.components.artifact_actions import bind_artifact_action


def open_workspace_inbox(ctx,view):
    manager=getattr(ctx,'dialog_manager',None)
    if manager is None: return None
    return manager.show(WorkspaceInboxDialog,key=f'workspace_inbox:{id(view)}',singleton=True,
        factory=lambda:WorkspaceInboxDialog(ctx,view).configure(view))


class WorkspaceInboxDialog(QDialog):
    def __init__(self,app_context,parent=None):
        super().__init__(parent)
        self.ctx,self.view,self.snapshot=app_context,None,{}
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle('Workspace Inbox — independent copies'); self.resize(780,720)
        layout=QVBoxLayout(self)
        self.kind=QComboBox(self)
        for label,key in (('Project notes & read-only source observations','notes'),('Canonical intelligence entities','entities'),
                          ('Saved discovery entities (legacy)','discoveries'),('Stored legacy note snapshots (including DB-only notes)','legacy_notes')):
            self.kind.addItem(label,key)
        self.kind.setAccessibleName('Inbox collection')
        layout.addWidget(self.kind)
        self.collection=PagedCollection(app_context,self)
        self.collection.view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.collection.pager.register_actions('intelligence.inbox','toolbar:workspace:inbox')
        self.detail=QTextEdit(self); self.detail.setReadOnly(True)
        self.detail.setAccessibleName('Selected Inbox quotation and commentary')
        self.collection.set_detail(self.detail,persistent=True)
        self.collection.selected.connect(self._selected)
        layout.addWidget(self.collection,1)
        self.add_button=QPushButton('Add selected page records as independent workspace copies',self)
        self.refresh_button=QPushButton('Refresh Inbox',self)
        for button,method,key,label in ((self.add_button,self.add_selected,'add','Add Inbox Selection'),
                                      (self.refresh_button,self.refresh,'refresh','Refresh Workspace Inbox')):
            button.clicked.connect(method); layout.addWidget(button)
            bind_artifact_action(app_context,self,'intelligence.inbox.'+key,label,method,'toolbar:workspace:inbox')
        self.reader=ArtifactTaskRunner(app_context,self,service_name='reference_note_service')
        self.reader.completed.connect(self._ready); self.reader.failed.connect(self._failed)
        self.reader.busy_changed.connect(lambda busy:self.add_button.setEnabled(not busy and bool(self.snapshot)))
        self.destroyed.connect(self.reader.shutdown)
        self.kind.currentIndexChanged.connect(self.refresh)
        self.collection.pager.requested.connect(self.read)
        bus=getattr(app_context,'bus',None)
        if bus is not None: bus.project_clearing_started.connect(self.close)
        self._help()

    def configure(self,view):
        self.view=weakref.ref(view); self.workspace_id=view.current_workspace_id
        self.refresh(); return self

    def refresh(self,*_):
        if self.view is None: return
        self.collection.pager.cursor,self.collection.pager.next_cursor,self.collection.pager.history='','',[]
        self.read()

    def read(self):
        view=self.view() if self.view else None
        if view is None or view.current_workspace_id!=self.workspace_id:
            self._failed('The workspace changed. Reopen its Inbox.'); return
        self.snapshot={}; self.add_button.setEnabled(False)
        self.excluded=tuple(sorted({key for node in view.nodes.values() for key in (node.node_id,node.highlight_id) if key}))
        self.collection.status.setText('Reading one bounded page; source files are observed only by this worker.')
        self.reader.start('query_workspace_inbox',kind=self.kind.currentData(),excluded_ids=self.excluded,
            cursor=self.collection.pager.cursor,limit=25)

    def _ready(self,operation):
        result,self.reader.result=self.reader.result or {},None
        if operation=='prepare_workspace_import':
            self._import(result); return
        self.snapshot=result; self.collection.apply(result)
        self.add_button.setEnabled(True)

    def _selected(self,index):
        items=self.collection.model.items
        if not 0<=index<len(items): self.detail.clear(); return
        item=items[index]
        self.detail.setPlainText('\n\n'.join(str(value) for value in (
            item.get('title'),item.get('pdf_path'),item.get('subject'),item.get('content'),item.get('preview')) if value))

    def add_selected(self):
        if not self.snapshot or self.reader.busy: return
        indices=self.collection.view.selectionModel().selectedRows()
        items=[self.collection.model.items[index.row()] for index in indices]
        if items:
            self.reader.start('prepare_workspace_import',items=items,expected_project=self.snapshot['project_identity'],
                expected_revision=self.snapshot['selection_revision'])

    def _import(self,result):
        view=self.view() if self.view else None
        db=getattr(self.ctx.project_manager,'db_intelligence',None)
        if view is None or view.current_workspace_id!=self.workspace_id or tuple(result['project_identity'])!=(id(db),id(getattr(db,'_conn',None))):
            self._failed('The project or workspace changed; no copies were added.'); return
        view.save_state_for_undo()
        center=view.mapToScene(view.viewport().rect().center())
        entities=[]
        for index,item in enumerate(result['items']):
            if item['kind']=='annotation':
                view.add_node_from_annotation(item['data'],persist=True,position=center+QPointF(0,index*120))
            else: entities.append(item['data'])
        if entities:
            self.ctx.bus.workspace_action_requested.emit(WorkspaceIntent.IMPORT_GRAPH,
                WorkspacePayload(extra={'graph':{'entities':entities,'relations':[]}}))
        view.update_scene_bounds()
        self.refresh()

    def _failed(self,error):
        self.collection.status.setText(str(error)); self.add_button.setEnabled(False)

    def _help(self):
        self.setWhatsThis('F1 explains bounded Inbox pages, read-only legacy observations and independent workspace copies. Adding copies never changes source notes or canonical entities.')
        registry=getattr(getattr(self.ctx,'help_service',None),'ui_target_registry',None)
        if registry:
            identity='intelligence.inbox.'+str(id(self))
            registry.register_widget(identity,self,'core.intelligence.inbox')
            self.destroyed.connect(lambda *_,r=registry,key=identity:r.unregister(key))

    def closeEvent(self,event):
        self.reader.shutdown(); self.collection.selection_timer.stop()
        super().closeEvent(event)
