"""Typed processing-stage and all/any artifact prerequisites for reusable modes."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QListView,
    QPushButton, QVBoxLayout, QWidget,
)
from core.models.analysis_requirements import normalize_requirements, template_prerequisites
from core.models.artifact_types import artifact_type_choices
from core.registries.intelligence_stage_registry import builtin_stage_specs
from gui.components.analysis_requirement_model import AnalysisRequirementModel
from gui.components.artifact_actions import bind_artifact_action
from gui.components.checkable_choice_list import CheckableChoiceList


class AnalysisPrerequisitesEditor(QWidget):
    changed = Signal()

    def __init__(self, app_context, parent=None):
        super().__init__(parent)
        self.ctx, self._loading, self._connections = app_context, False, []
        self._stage_error = False
        modes = getattr(app_context, 'analysis_mode_registry', None)
        self.registry = (getattr(modes, 'stage_registry', None)
                         or getattr(app_context, 'intelligence_stage_registry', None))
        layout = QVBoxLayout(self)
        explanation = QLabel(
            'Checked processing passes run before analysis, with their source-specific dependencies. '
            'Every artifact requirement group must pass. All requires every selected type; '
            'Any requires at least one. Choose whether each source must qualify independently. '
            'These rules control readiness; they do not implement new graph projections.', self)
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        self._build_stages(layout)
        self._build_groups(layout)
        self.refresh_catalog()
        self._connect_catalog()
        self._register_help()

    def _register_help(self):
        registry = getattr(getattr(self.ctx, 'help_service', None), 'ui_target_registry', None)
        if registry:
            identity = f'analysis.template.prerequisites.{id(self)}'
            registry.register_widget(identity, self, 'core.analysis.custom_templates')
            self.destroyed.connect(lambda *_, r=registry, key=identity: r.unregister(key))

    def _build_stages(self, layout):
        box = QGroupBox('Processing to prepare', self)
        inner = QVBoxLayout(box)
        self.stages = CheckableChoiceList(box)
        self.stages.view.setAccessibleName('Required intelligence processing stages')
        self.stages.view.setMinimumHeight(70)
        self.stages.view.setMaximumHeight(85)
        inner.addWidget(self.stages)
        layout.addWidget(box)
        self.stages.changed.connect(self._stages_edited)

    def _stages_edited(self):
        if not self._loading:
            self._stage_error = False
            self.changed.emit()

    def _build_groups(self, layout):
        box = QGroupBox('Artifact requirements — every group must pass', self)
        inner = QHBoxLayout(box)
        left = QVBoxLayout()
        left.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.groups = AnalysisRequirementModel(self)
        self.group_view = QListView(box)
        self.group_view.setAccessibleName('Analysis artifact requirement groups')
        self.group_view.setMinimumHeight(100)
        self.group_view.setMaximumHeight(120)
        self.group_view.setModel(self.groups)
        left.addWidget(self.group_view)
        buttons = QHBoxLayout()
        for name, label in (('add_group', 'Add group'), ('remove_group', 'Remove group')):
            button = QPushButton(label, box)
            button.clicked.connect(getattr(self, name))
            buttons.addWidget(button)
            bind_artifact_action(self.ctx, self, 'analysis.template.' + name, label,
                                 getattr(self, name), 'toolbar:analysis:template')
        left.addLayout(buttons)
        inner.addLayout(left, 1)
        self._build_group_fields(inner, box)
        layout.addWidget(box, 1)
        self.group_view.selectionModel().currentChanged.connect(self._select_group)
        self.groups.changed.connect(self.changed)

    def _build_group_fields(self, layout, parent):
        self.fields = QWidget(parent)
        form = QFormLayout(self.fields)
        self.match = QComboBox(self.fields)
        for label, value in (('All selected types', 'all'), ('Any selected type', 'any')):
            self.match.addItem(label, value)
        self.scope = QComboBox(self.fields)
        for label, value in (('Across selected sources', 'selection'), ('In each selected source', 'each_source')):
            self.scope.addItem(label, value)
        self.types = CheckableChoiceList(self.fields)
        self.types.view.setAccessibleName('Artifact types required by this group')
        self.types.view.setMinimumHeight(110)
        self.types.setMaximumHeight(125)
        form.addRow('Require', self.match)
        form.addRow('Where', self.scope)
        form.addRow('Artifact types', self.types)
        self.match.setAccessibleName('Requirement group all or any policy')
        self.scope.setAccessibleName('Requirement group source scope')
        layout.addWidget(self.fields, 1, Qt.AlignmentFlag.AlignTop)
        self.match.currentIndexChanged.connect(self._update_group)
        self.scope.currentIndexChanged.connect(self._update_group)
        self.types.changed.connect(self._update_group)
        self.fields.setEnabled(False)

    def _catalog(self):
        stages = self.registry.all(include_disabled=True) if self.registry else builtin_stage_specs()
        stage_choices = [dict(id=stage.id, label=stage.label or stage.id.replace('_', ' '),
                              available=stage.enabled, description=stage.description) for stage in stages]
        service = getattr(self.ctx, 'artifact_service', None)
        extensions = (service.list_registered_extensions().get('artifact_types') or {}) if service else {}
        kinds = set(artifact_type_choices(extensions))
        kinds.update(kind for stage in stages for kind in stage.output_artifact_types)
        type_choices = [dict(id=kind, label=kind.replace('_', ' '),
                             description=self._type_description(kind, extensions, stages))
                        for kind in sorted(kinds)]
        return stage_choices, type_choices

    @staticmethod
    def _type_description(kind, extensions, stages):
        description = str((extensions.get(kind) or {}).get('description') or kind.replace('_', ' '))
        producers = [stage.label or stage.id for stage in stages if stage.enabled and kind in stage.output_artifact_types]
        hint = ('Prepared by: ' + ', '.join(producers) + '. Choose the pass above if this type is missing.'
                if producers else 'No automatic pass declares this type; eligible artifacts must already exist.')
        return description + '\n' + hint

    def refresh_catalog(self, *_):
        stages, self._type_choices = self._catalog()
        self.stages.set_choices(stages)
        self.types.set_choices(self._type_choices)
        self.changed.emit()

    def _connect_catalog(self):
        bus = getattr(self.ctx, 'bus', None)
        for name in ('plugin_loaded', 'plugin_unloaded', 'intelligence_state_changed'):
            signal = getattr(bus, name, None)
            if signal is not None:
                signal.connect(self.refresh_catalog)
                self._connections.append(signal)

    def shutdown(self):
        for signal in self._connections:
            try:
                signal.disconnect(self.refresh_catalog)
            except (RuntimeError, TypeError):
                pass  # The owner or event bus may already have been destroyed.
        self._connections.clear()

    def load(self, template):
        defaults = template_prerequisites({})
        self._loading = True
        stages, self._type_choices = self._catalog()
        selected = template.get('required_stages', defaults['required_stages'])
        self._stage_error = not isinstance(selected, list) or any(not isinstance(value, str) for value in selected)
        self.stages.set_choices(stages, [] if self._stage_error else selected)
        # Preserve malformed/unknown saved groups visibly for explicit repair/removal.
        groups = template.get('artifact_requirements')
        if groups is None:
            try:
                groups = normalize_requirements(None, template.get('required_artifact_types', ['argument_unit']))
            except ValueError:
                groups = [{'invalid_legacy_types': template.get('required_artifact_types')}]
        self.groups.load(groups)
        self._loading = False
        self.group_view.setCurrentIndex(self.groups.index(0))
        self._select_group(self.group_view.currentIndex())
        self.changed.emit()

    def add_group(self):
        self.group_view.setCurrentIndex(self.groups.append())

    def remove_group(self):
        row = self.group_view.currentIndex().row()
        self.groups.remove(row)
        index = self.groups.index(min(row, self.groups.rowCount() - 1))
        self.group_view.setCurrentIndex(index)
        self._select_group(index)

    def _select_group(self, index, *_):
        self._loading = True
        group = self.groups.groups[index.row()] if index.isValid() else None
        valid = isinstance(group, dict)
        self.fields.setEnabled(valid)
        if valid:
            self._set_choice(self.match, group.get('match'))
            self._set_choice(self.scope, group.get('scope'))
            types = group.get('artifact_types')
            self.types.set_choices(self._type_choices, types if isinstance(types, list) and
                                   all(isinstance(value, str) for value in types) else [])
        else:
            self.types.set_choices(self._type_choices, [])
        self._loading = False

    @staticmethod
    def _set_choice(combo, value):
        index = combo.findData(value)
        if index < 0:
            combo.addItem(f'Unavailable: {value}', value)
            index = combo.count() - 1
        combo.setCurrentIndex(index)

    def _update_group(self, *_):
        if not self._loading:
            self.groups.update(self.group_view.currentIndex().row(), {
                'match': self.match.currentData(), 'scope': self.scope.currentData(),
                'artifact_types': self.types.selected()})

    def value(self):
        if self._stage_error:
            raise ValueError('Saved required stages must be a list of names. Explicitly choose valid processing passes.')
        stage_choices, type_choices = self._catalog()
        stages = self.stages.selected()
        available_stages = {choice['id'] for choice in stage_choices if choice.get('available', True)}
        available_types = {choice['id'] for choice in type_choices}
        groups = normalize_requirements(self.groups.groups)
        unavailable = (set(stages) - available_stages) | {
            kind for group in groups for kind in group['artifact_types']
            if kind not in available_types}
        if unavailable:
            raise ValueError('Unavailable prerequisite choices: ' + ', '.join(sorted(unavailable)) +
                             '. Restore their provider or explicitly remove/replace them.')
        return dict(required_stages=stages, artifact_requirements=groups, required_artifact_types=[])
