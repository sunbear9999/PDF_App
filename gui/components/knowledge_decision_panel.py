"""Shared task-focused decisions with off-thread previews, guarded writes and undo."""
from PySide6.QtCore import Qt, QTimer, QSignalBlocker, Signal
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QLineEdit, QSplitter, QToolBar,
                               QCheckBox, QToolButton, QButtonGroup)
from gui.components.icon_buttons import iconize
from core.models.review_decisions import DIRECT_PAIR_ACTIONS, ENTITY_GROUP_ACTIONS, PAIR_ACTIONS
from core.events.domains.help_events import HelpIntent, HelpPayload
from gui.components.artifact_inspector import ArtifactInspector, method_text
from gui.components.artifact_tasks import ArtifactTaskRunner
from gui.components.artifact_source_navigator import ArtifactSourceNavigator
from gui.components.artifact_actions import bind_artifact_action
from gui.components.cursor_pager import CursorPager

REFRESH_DELAY_MS = 200
CHOICE_ICONS = {'entity_merge': '⇉', 'entity_never_merge': '≠', 'entity_subgroup': '⊂', 'entity_connect': '🔗'}
STATE_WORDS = {'unreviewed': 'not yet reviewed', 'verified': 'confirmed', 'rejected': 'rejected', 'corrected': 'corrected'}


def _titles(command):
    if command == 'migration_resolve':
        return 'Earlier reviewed span', 'Replacement span — keeps its quote and method'
    if command in {'entity_merge', 'entity_split'}:
        return 'From — entity whose mentions move', 'Into — entity to keep'
    if command == 'entity_subgroup':
        return 'Subgroup — stays its own entity', 'Broader entity it belongs under'
    if command == 'entity_connect':
        return 'First entity', 'Connected entity'
    if command.startswith('relation_'):
        return 'First item in the link', 'Second item in the link'
    return 'First entity', 'Second entity'


def _pair_text(command, result, kind_label):
    first = ((result.get('source') or {}).get('exact_text') or 'the first entity')
    second = ((result.get('target') or {}).get('exact_text') or 'the second entity')
    if command == 'entity_subgroup':
        return (f"“{first}” becomes a subgroup of “{second}”: both stay their own entity, and “{second}” lists "
                f"“{first}” with its mentions under it.")
    if command == 'entity_connect':
        return f"“{first}” and “{second}” stay separate, with a confirmed connection: {kind_label}."
    return command.replace('_', ' ').capitalize()


def _relation_status(assertion):
    label = (assertion.get('edge_type') or 'link').replace('_', ' ')
    confidence = assertion.get('confidence')
    score = f" · system confidence {round(float(confidence) * 100)}%" if isinstance(confidence, (int, float)) else ''
    state = STATE_WORDS.get(assertion.get('review_state'), assertion.get('review_state') or '')
    return f"Link: {label}{score} · {state} · {method_text(assertion).rstrip('.')}."


class KnowledgeDecisionPanel(QWidget):
    decided = Signal(str)
    busy_changed = Signal(bool)

    def __init__(self, app_context, parent=None):
        super().__init__(parent)
        self.ctx, self.identity, self.command, self.payload = app_context, '', '', {}
        self.preview, self.receipt, self._last_command = {}, None, ''
        self._contexts = {}
        self._closing, self._dirty, self._committed = False, False, False
        layout = QVBoxLayout(self)
        self._controls(layout)
        self.explanation = QLabel('', self)
        self.explanation.setObjectName('decisionExplanation')
        self.explanation.setWordWrap(True)
        self.explanation.setTextFormat(Qt.TextFormat.PlainText)
        self.explanation.setAccessibleName('Why this proposal exists')
        self.explanation.hide()
        layout.addWidget(self.explanation)
        self.status = QLabel('Choose a decision and inspect its source context.', self)
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.source_frame,self.source_title,self.source = self._pane(splitter,'Source')
        self.target_frame,self.target_title,self.target = self._pane(splitter,'Target')
        self.evidence_frame,_,self.evidence = self._pane(splitter,'The sentence this is based on')
        self.evidence_frame.hide()
        layout.addWidget(splitter, 1)
        self.suggester = ArtifactTaskRunner(app_context, self)
        self.suggester.completed.connect(self._suggestions_ready)
        self.runner = ArtifactTaskRunner(app_context, self)
        self.runner.completed.connect(self._completed)
        self.runner.failed.connect(self._failed)
        self.runner.busy_changed.connect(self._busy)
        self.navigator = ArtifactSourceNavigator(app_context, self.status.setText, self)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(REFRESH_DELAY_MS)
        self.timer.timeout.connect(self._prepare)
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(REFRESH_DELAY_MS)
        self.search_timer.timeout.connect(self.pager.reset)
        self._register_help()

    def _pane(self, splitter, heading):
        frame = QWidget(splitter)
        layout = QVBoxLayout(frame)
        title = QLabel(heading,frame)
        layout.addWidget(title)
        inspector = ArtifactInspector(frame,app_context=self.ctx)
        layout.addWidget(inspector,1)
        return frame,title,inspector

    def _controls(self, layout):
        self.mode = QComboBox(self)
        self.mode.setAccessibleName('Entity grouping decision')
        for key, label in ENTITY_GROUP_ACTIONS:
            self.mode.addItem(label,key)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        self.mode.hide()                    # the choice buttons below drive it
        layout.addWidget(self._choice_row())
        self.connection_kind = QComboBox(self)
        self.connection_kind.setAccessibleName('Connection type to save')
        self.connection_kind.setToolTip('How the two are connected (e.g. a town named after a person: Related).')
        from core.services.intelligence.connection_review import connection_types
        for key, label in connection_types().items():
            self.connection_kind.addItem(label, key)
        self.connection_kind.hide()
        layout.addWidget(self.connection_kind)
        self.suggested = QComboBox(self)
        self.suggested.setAccessibleName('Suggested merges: entities with similar names')
        self.suggested.setToolTip('Entities of the same type whose names look alike (same name, one name inside the other, '
                                  'initials, or close spelling). Choosing one makes it the target; nothing merges until you confirm.')
        self.suggested.activated.connect(self._suggestion_chosen)
        layout.addWidget(self.suggested)
        self.search = QLineEdit(self)
        self.search.setPlaceholderText('Or search any entity of the same type…')
        self.search.setAccessibleName('Search decision candidates')
        self.search.textChanged.connect(self._search)
        layout.addWidget(self.search)
        self.candidates = QComboBox(self)
        self.candidates.setAccessibleName('Explicit decision target')
        self.candidates.currentIndexChanged.connect(self._target_changed)
        layout.addWidget(self.candidates)
        self.name = QLineEdit(self)
        self.name.setPlaceholderText('Name for the new entity')
        self.name.setAccessibleName('Name for the split entity')
        self.name.textChanged.connect(self._name_changed)
        layout.addWidget(self.name)
        self.correspondence = QCheckBox('I confirm this replacement span corresponds to the reviewed observation.',self)
        self.correspondence.toggled.connect(self._confirmation_changed)
        layout.addWidget(self.correspondence)
        self.pager = CursorPager(self.ctx, self)
        self.pager.register_actions('intelligence.decision', 'toolbar:intelligence:decision')
        self.pager.requested.connect(self._load_candidates)
        layout.addWidget(self.pager)
        toolbar = QToolBar(self)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.actions = {}
        for key, label in (('confirm','Confirm decision'),('reject','Reject proposal'),('undo','Undo decision'),('reload','Reload preview'),('discard','Discard decision draft'),('jump_source','Jump to source'),('jump_target','Jump to target'),('jump_evidence','Jump to evidence'),('guide','Decision help')):
            callback = getattr(self, key)
            self.actions[key] = toolbar.addAction(label, callback)
            bind_artifact_action(self.ctx, self, 'intelligence.decision.'+key, label, callback, 'toolbar:intelligence:decision', self.actions[key])
        layout.addWidget(toolbar)

    @property
    def has_changes(self):
        return self._dirty

    def configure(self, identity, command, payload=None):
        self.runner.cancel()
        self.timer.stop()
        self.search_timer.stop()
        self._committed = False
        self._contexts = {}
        self.identity, self.command, self.payload = identity, command, dict(payload or {})
        self._initial_payload = dict(self.payload)
        self.preview, self.receipt, self._dirty = {}, None, False
        self.source_title.setText(_titles(command)[0])
        self.target_title.setText(_titles(command)[1])
        self.explanation.hide()
        for inspector in (self.source,self.target,self.evidence):
            inspector.show_record(None,load_context=False,force=True)
        self.mode_row.setVisible(command in PAIR_ACTIONS)
        with QSignalBlocker(self.mode):
            self.mode.setCurrentIndex(max(0,self.mode.findData(command)))
        self.choice_buttons.button(self.mode.currentIndex()).setChecked(True)
        self.choice_label.setText(self.mode.currentText())
        self.correspondence.setVisible(command == 'migration_resolve')
        with QSignalBlocker(self.correspondence):
            self.correspondence.setChecked(False)
        self.payload.pop('correspondence_confirmed',None)
        self.name.setVisible(command == 'entity_split')
        selecting = command in PAIR_ACTIONS | {'migration_resolve'}
        for widget in (self.search, self.candidates, self.pager):
            widget.setVisible(selecting)
        merging = command in PAIR_ACTIONS
        self.connection_kind.setVisible(command == 'entity_connect')
        self.suggested.setVisible(merging)
        self.suggested.clear()
        if merging and identity:
            self.suggested.addItem('Looking for similar names…', '')
            self.suggester.start('merge_suggestions', artifact_id=identity)
        with QSignalBlocker(self.name):
            self.name.setText(self.payload.get('display_name') or '')
        with QSignalBlocker(self.search):
            self.search.clear()
        self.pager.cursor, self.pager.next_cursor, self.pager.history = '', '', []
        if selecting:
            self._load_candidates()
        else:
            self._prepare()

    def _suggestions_ready(self, _):
        result, self.suggester.result = self.suggester.result or {}, None
        self.suggested.clear()
        items = result.get('items') or []
        self.suggested.addItem(f'Suggested merges ({len(items)})…' if items else 'No similar names found', '')
        for item in items:
            index = self.suggested.count()
            self.suggested.addItem(f"{item['name']} · {item['mentions']} mentions · {item['reason']}", item['artifact_id'])
            self.suggested.setItemData(index, item['name'], Qt.ItemDataRole.UserRole + 1)

    def _suggestion_chosen(self, index):
        identity = self.suggested.itemData(index)
        if not identity:
            return
        if self.candidates.findData(identity) < 0:
            self.candidates.addItem(self.suggested.itemData(index, Qt.ItemDataRole.UserRole + 1) or identity, identity)
        self.candidates.setCurrentIndex(self.candidates.findData(identity))

    def _choice_row(self):
        """Merge / keep separate / subgroup / connect as visible toggle buttons, full name beside them."""
        self.mode_row = QWidget(self)
        row = QHBoxLayout(self.mode_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QLabel('Decision:', self.mode_row))
        self.choice_buttons = QButtonGroup(self.mode_row)
        for index, (key, label) in enumerate(ENTITY_GROUP_ACTIONS):
            button = QToolButton(self.mode_row)
            button.setCheckable(True)
            iconize(button, CHOICE_ICONS.get(key, '•'), label, label)
            self.choice_buttons.addButton(button, index)
            row.addWidget(button)
        self.choice_buttons.button(0).setChecked(True)
        self.choice_buttons.idClicked.connect(self.mode.setCurrentIndex)
        self.choice_label = QLabel(ENTITY_GROUP_ACTIONS[0][1], self.mode_row)
        self.choice_label.setWordWrap(True)
        row.addWidget(self.choice_label, 1)
        return self.mode_row

    def _mode_changed(self, *_):
        if self.choice_buttons.button(self.mode.currentIndex()):
            self.choice_buttons.button(self.mode.currentIndex()).setChecked(True)
        self.choice_label.setText(self.mode.currentText())
        self.command = self.mode.currentData()
        self.preview = {}
        self._dirty = True
        self.connection_kind.setVisible(self.command == 'entity_connect')
        self.source_title.setText(_titles(self.command)[0])
        self.target_title.setText(_titles(self.command)[1])
        self._load_candidates()     # merging lists same-type entities; subgroups and connections any type

    def _confirmation_changed(self, checked):
        self.payload['correspondence_confirmed'] = checked
        self._busy(False)

    def _search(self, *_):
        if self.identity and self.command in PAIR_ACTIONS | {'migration_resolve'}:
            self.preview = {}
            self._busy(False)
            self.search_timer.start()

    def _load_candidates(self):
        self.preview = {}
        if self.identity and not self._closing:
            self.runner.start('query_decision_candidates', artifact_id=self.identity, command=self.command, text=self.search.text(), cursor=self.pager.cursor, target_id=self.payload.get('target_id'))

    def _target_changed(self, *_):
        self.payload['target_id'] = self.candidates.currentData() or ''
        self._dirty = bool(self.payload['target_id'])
        self.preview = {}
        self._busy(False)
        self.timer.start()

    def _name_changed(self, text):
        self.payload['display_name'] = text
        self.preview, self._dirty = {}, bool(text.strip())
        self._busy(False)
        self.timer.start()

    def _prepare(self):
        if not self.identity or self._closing:
            return
        if self.command in PAIR_ACTIONS | {'migration_resolve'} and not self.payload.get('target_id'):
            self.status.setText('Choose a target explicitly, then inspect the proposed decision.')
            self._busy(False)
            return
        if self.command == 'entity_split' and not self.payload.get('display_name','').strip():
            self.status.setText('Enter a new name for the selected mentions.')
            self._busy(False)
            return
        if self.command in DIRECT_PAIR_ACTIONS:
            self.runner.start('entity_pair_preview', artifact_id=self.identity, target_id=self.payload['target_id'])
            return
        self.runner.start('prepare_review_decision', artifact_id=self.identity, command='relation_reject' if self.command.startswith('relation_') else self.command, payload=dict(self.payload))

    def _completed(self, operation):
        result, self.runner.result = self.runner.result, None
        if not result or self._closing:
            return
        if operation == 'query_decision_candidates':
            if result.get('invalidated'):
                self.pager.reset()
                return
            self._candidate_page(result)
        elif operation in {'prepare_review_decision', 'entity_pair_preview'}:
            self.preview = result
            self._contexts = {key:result.get(key+'_context') for key in ('source','target')}
            for inspector, key in ((self.source,'source'),(self.target,'target'),(self.evidence,'evidence')):
                inspector.show_record(result.get(key), editor_context={'read_only':True, 'strict_read_only':True,'preview_context':result.get(key+'_context')}, load_context=False, force=True)
            evidence = result.get('evidence') or {}
            self.evidence_frame.setVisible(bool(evidence) and evidence.get('artifact_id') not in {self.source.row.get('artifact_id'),self.target.row.get('artifact_id')})
            assertion = result.get('assertion') or {}
            self.explanation.setText(result.get('explanation') or '')
            self.explanation.setVisible(bool(result.get('explanation')))
            description = _relation_status(assertion) if assertion else _pair_text(self.command, result, self.connection_kind.currentText())
            self.status.setText((result.get('blocked_reason') or description) + (f" · Affected mentions: {result['affected_count']} across the project." if self.command in {'entity_merge','entity_split'} else '') + ' Check both sides and the sentence before deciding.')
            self._busy(False)
        elif operation in {'execute_review_command', 'set_entity_parent', 'connect_entities'}:
            self.receipt, self.preview, self._dirty = result.get('receipt'), {}, False
            self._committed = True
            self.status.setText('Decision undone.' if self._last_command == 'undo' else 'Decision saved. Reload before making another decision.')
            self.decided.emit(self._last_command)

    def _candidate_page(self, result):
        self.source.show_record(result.get('row'), editor_context={'read_only':True, 'strict_read_only':True}, load_context=False, force=True)
        desired = self.payload.get('target_id') or (getattr(self, '_initial_payload', None) or {}).get('target_id')
        with QSignalBlocker(self.candidates):
            self.candidates.clear()
            self.candidates.addItem('Choose a target…', '')
            for row in result.get('items') or []:
                locator = row.get('locator') or {}
                label = (row.get('exact_text') or 'Unnamed artifact')[:180]
                if locator.get('page') is not None:
                    label += f" · page {locator['page']}"
                self.candidates.addItem(label, row['artifact_id'])
            self.candidates.setCurrentIndex(max(0, self.candidates.findData(desired)))
        self.payload['target_id'] = self.candidates.currentData() or ''
        self.pager.apply(result)
        self._prepare()

    def _busy(self, busy):
        busy = busy or self.runner.busy
        self.mode.setEnabled(not busy)
        self.correspondence.setEnabled(not busy)
        self.search.setEnabled(not busy)
        self.candidates.setEnabled(not busy)
        self.name.setEnabled(not busy)
        self.pager.setEnabled(not busy)
        confirmed = self.command != 'migration_resolve' or self.correspondence.isChecked()
        self.actions['confirm'].setEnabled(not busy and bool(self.preview) and self.preview.get('can_confirm',True) and confirmed)
        self.actions['reject'].setEnabled(not busy and not self._committed and (self.command.startswith('relation_') or self.command == 'migration_resolve'))
        self.actions['undo'].setEnabled(not busy and bool(self.receipt))
        for key in ('reload','discard'):
            self.actions[key].setEnabled(not busy)
        for key,inspector in (('jump_source',self.source),('jump_target',self.target),('jump_evidence',self.evidence)):
            self.actions[key].setEnabled(bool(inspector.row.get('artifact_id')))
        entities = self.command in PAIR_ACTIONS     # each pane lists its mentions to jump to instead
        for key in ('jump_source', 'jump_target'):
            self.actions[key].setVisible(not entities)
        self.busy_changed.emit(busy)

    def _write(self, command):
        if self.runner.busy or self._closing:
            return
        if self._committed and command != "undo":
            self.status.setText("Reload the preview before making another decision.")
            return
        self._last_command = command
        self.status.setText('Applying the decision… A confirmed write finishes atomically even if this window closes.')
        expected = self.preview.get('project_identity')
        if expected is None:
            db = getattr(getattr(self.ctx,'artifact_service',None),'db',None)
            expected = [id(db),id(getattr(db,'_conn',None))]
        if command in DIRECT_PAIR_ACTIONS:
            if command == 'entity_subgroup':
                self.runner.start('set_entity_parent', artifact_id=self.identity, parent_id=self.payload.get('target_id'))
            else:
                self.runner.start('connect_entities', artifact_id=self.identity, target_id=self.payload.get('target_id'),
                                  edge_type=self.connection_kind.currentData())
            return
        self.runner.start('execute_review_command', artifact_id=self.identity, command=command,
            payload=dict(self.payload), expected_revision=self.source.row.get('revision') if command == 'reject' else None, expected_token=self.preview.get('token'), receipt=self.receipt, expected_project=expected)

    def reload(self):
        if self.runner.busy:
            return
        self._committed = False
        self._prepare()

    def discard(self):
        if not self.runner.busy:
            self.configure(self.identity,self.command,self._initial_payload)

    def confirm(self):
        if self.preview and self.preview.get('can_confirm',True) and (self.command != 'migration_resolve' or self.correspondence.isChecked()):
            self._write(self.command)

    def reject(self):
        self._write('relation_reject' if self.command.startswith('relation_') else 'reject')

    def undo(self):
        if self.receipt:
            self._write('undo')

    def jump_source(self):
        if self.source.row.get('artifact_id'):
            self.navigator.navigate((self._contexts.get('source') or self.source.row)['artifact_id'])

    def jump_target(self):
        if self.target.row.get('artifact_id'):
            self.navigator.navigate((self._contexts.get('target') or self.target.row)['artifact_id'])

    def jump_evidence(self):
        if self.evidence.row.get("artifact_id"):
            self.navigator.navigate(self.evidence.row["artifact_id"])

    def guide(self):
        self.ctx.bus.help_action_requested.emit(HelpIntent.SHOW_TOPIC, HelpPayload(topic_id='core.intelligence.decisions'))

    def _failed(self, error):
        self.preview = {}
        self.status.setText(error)

    def _register_help(self):
        registry = getattr(getattr(self.ctx,'help_service',None),'ui_target_registry',None)
        if registry:
            identity = f'intelligence.decisions.{id(self)}'
            registry.register_widget(identity,self,'core.intelligence.decisions')
            self.destroyed.connect(lambda *_, r=registry, key=identity: r.unregister(key))

    def shutdown(self):
        self._closing = True
        self.timer.stop()
        self.search_timer.stop()
        self.runner.shutdown()
        self.suggester.shutdown()
        self.source.shutdown()
        self.target.shutdown()
        self.evidence.shutdown()
        self.navigator.shutdown()
