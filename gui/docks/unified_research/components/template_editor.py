import json
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
                             QLabel, QLineEdit, QTextEdit, QWidget,
                             QMessageBox, QListWidget, QCheckBox, QListWidgetItem, QGroupBox, QTabWidget, QScrollArea)
from PySide6.QtCore import Qt
from gui.managers.dialog_manager import exec_as_modal, get_for_widget
from gui.base.forms import SchemaFormBuilder
from gui.components.analysis_prerequisites_editor import AnalysisPrerequisitesEditor
from gui.components.analysis_template_limits import AnalysisTemplateLimits
from core.models.ontology_model import EntityType
from core.ontology.registry import RelationTrait
from core.registries.analysis_mode_registry import AnalysisModeRegistry
from core.models.analysis_requirements import describe_missing
from core.models.analysis_roles import analysis_entity_type
from core.models.analysis_relations import canonical_relation

class TemplateEditorDialog(QDialog):
    def __init__(self, app_context, parent=None):
        super().__init__(parent)
        self.ctx = app_context
        self.pm = app_context.project_manager
        self.theme = getattr(parent, 'theme', None)
        self.deterministic_pass_registry = getattr(app_context, 'deterministic_pass_registry', None)
        self.setWindowTitle("Analysis Modes Builder")
        self.resize(1000, 760)
        self.templates = self.pm.get_analysis_templates()
        self.current_template_id = None
        self.modes = getattr(app_context, 'analysis_mode_registry', None) or AnalysisModeRegistry()
        self.compiler = self.modes.compiler
        self.registry = self.compiler.ontology
        self._det_pass_forms = {}

        self._build_ui()
        self._load_template_list()
        self._apply_theme()
        self._update_contract_preview()
        self._project_signal = getattr(getattr(app_context, 'bus', None), 'project_clearing_started', None)
        if self._project_signal is not None:
            self._project_signal.connect(self.close)

    def _build_ui(self):
        main_layout = QHBoxLayout(self)
        self._build_sidebar(main_layout)
        right_panel = QVBoxLayout()
        self._build_goal_fields(right_panel)
        self.options_tabs = QTabWidget(self)
        graph, legacy = QWidget(self), QWidget(self)
        graph_layout, legacy_layout = QVBoxLayout(graph), QVBoxLayout(legacy)
        self._build_quote_controls(graph_layout)
        self._build_vocabulary(graph_layout)
        graph_layout.addStretch()
        self.prerequisites = AnalysisPrerequisitesEditor(self.ctx, self)
        self._add_option_tab(graph, 'Graph and evidence')
        self._add_option_tab(self.prerequisites, 'Required intelligence')
        legacy_layout.addWidget(self.limits_editor.legacy_panel)
        legacy_layout.addWidget(self.allow_text_nodes)
        self._build_pass_controls(legacy_layout)
        legacy_layout.addStretch()
        self._add_option_tab(legacy, 'Legacy workflows')
        right_panel.addWidget(self.options_tabs, 1)
        self.validation_label = QLabel(self)
        self.validation_label.setWordWrap(True)
        self.validation_label.setProperty('help_topic', 'core.analysis.custom_templates')
        right_panel.addWidget(self.validation_label)
        self.prerequisites.changed.connect(self._update_contract_preview)
        self.limits_editor.changed.connect(self._update_contract_preview)
        self.inst_input.textChanged.connect(self._update_contract_preview)
        self.prerequisites.load({})
        self._build_save_controls(right_panel)
        main_layout.addLayout(right_panel, 1)

    def _add_option_tab(self, widget, title):
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setWidget(widget)
        self.options_tabs.addTab(scroll, title)

    def _build_sidebar(self, main_layout):
        # --- Left Sidebar: List of Templates ---
        left_panel = QVBoxLayout()
        self.list_widget = QListWidget()
        self.list_widget.setFixedWidth(200)
        self.list_widget.itemClicked.connect(self._on_template_selected)
        left_panel.addWidget(QLabel("<b>Saved Modes</b>"))
        left_panel.addWidget(self.list_widget)

        btn_new = QPushButton("➕ Create New Mode")
        btn_new.clicked.connect(self._create_new)
        left_panel.addWidget(btn_new)
        main_layout.addLayout(left_panel)

    def _build_goal_fields(self, right_panel):
        # Title & Instructions
        right_panel.addWidget(QLabel("<b>Mode Title:</b>"))
        self.title_input = QLineEdit()
        right_panel.addWidget(self.title_input)

        help_label = QLabel(
            "Choose the graph vocabulary and describe the goal. Source artifacts form "
            "a grounded baseline; the model may suggest bounded text and connection "
            "changes. Missing coverage stays visible as a partial analysis."
        )
        help_label.setWordWrap(True)
        right_panel.addWidget(help_label)

        right_panel.addWidget(QLabel("<b>Analysis Goal:</b>"))
        self.inst_input = QTextEdit()
        self.inst_input.setFixedHeight(70)
        self.inst_input.setPlaceholderText("Example: Diagram the connections between people throughout this paper.")
        right_panel.addWidget(self.inst_input)

    def _build_quote_controls(self, right_panel):
        self.limits_editor = AnalysisTemplateLimits(self.ctx, self)
        for name in ('quotes_per_chunk', 'quote_words', 'max_quote_words', 'explanation_words',
                     'max_sections', 'max_analysis_quotes'):
            setattr(self, name, getattr(self.limits_editor, name))
        right_panel.addWidget(self.limits_editor)
        btn_prompts = QPushButton('Advanced: Edit Shared Prompts', self)
        btn_prompts.clicked.connect(self._open_prompt_manager)
        right_panel.addWidget(btn_prompts)

    def _build_vocabulary(self, right_panel):
        graph_row = QHBoxLayout()
        graph_col_nodes = QVBoxLayout()
        graph_col_rels = QVBoxLayout()
        graph_col_nodes.addWidget(QLabel("<b>Final graph node types:</b>"))
        self.node_type_list = QListWidget()
        self.node_type_list.setMinimumHeight(110)
        graph_col_nodes.addWidget(self.node_type_list)
        self.allow_text_nodes = QCheckBox("Legacy workflow: allow fallback text nodes")
        self.allow_text_nodes.setChecked(False)
        graph_col_rels.addWidget(QLabel("<b>Final graph connection types:</b>"))
        self.relation_type_list = QListWidget()
        self.relation_type_list.setMinimumHeight(110)
        graph_col_rels.addWidget(self.relation_type_list)
        graph_row.addLayout(graph_col_nodes, 1)
        graph_row.addLayout(graph_col_rels, 1)
        right_panel.addLayout(graph_row)
        self._populate_registry_lists()
        self.node_type_list.itemChanged.connect(self._update_contract_preview)
        self.relation_type_list.itemChanged.connect(self._update_contract_preview)

    def _build_pass_controls(self, right_panel):
        # Deterministic pre-filter passes: no-LLM ranking/filtering run before each
        # chunk's evidence-extraction call — see DeterministicPassRegistry.
        self.det_pass_box = QGroupBox("Legacy Chunk Workflow Pre-Filters")
        det_pass_layout = QVBoxLayout(self.det_pass_box)
        det_pass_layout.addWidget(QLabel(
            "These settings apply to legacy chunk workflows. Artifact analysis uses shared "
            "focus filtering and canonical classifications instead."
        ))
        self.det_pass_list = QListWidget()
        self.det_pass_list.setMaximumHeight(90)
        self.det_pass_list.itemChanged.connect(self._rebuild_det_pass_forms)
        det_pass_layout.addWidget(self.det_pass_list)
        self.det_pass_forms_widget = QWidget()
        self.det_pass_forms_layout = QVBoxLayout(self.det_pass_forms_widget)
        self.det_pass_forms_layout.setContentsMargins(0, 0, 0, 0)
        det_pass_layout.addWidget(self.det_pass_forms_widget)
        self._populate_det_pass_list()
        right_panel.addWidget(self.det_pass_box)
        self.det_pass_box.setVisible(self.deterministic_pass_registry is not None)

    def _build_save_controls(self, right_panel):
        # Save Area
        save_layout = QHBoxLayout()
        save_layout.addStretch()
        btn_save = self.btn_save = QPushButton("💾 Save Mode")
        btn_save.setFixedSize(120, 35)
        btn_save.clicked.connect(self._save_current)
        save_layout.addWidget(btn_save)
        right_panel.addLayout(save_layout)

    def _populate_registry_lists(self):
        for bp in self.registry.all_entities():
            item = QListWidgetItem(bp.display_name)
            item.setData(Qt.ItemDataRole.UserRole, bp.type_key)
            item.setToolTip(bp.description)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            default_checked = bp.type_key in {
                EntityType.CLAIM.value,
                EntityType.REASONING.value,
                EntityType.QUOTE.value,
            }
            item.setCheckState(Qt.CheckState.Checked if default_checked else Qt.CheckState.Unchecked)
            self.node_type_list.addItem(item)
        for bp in self.registry.all_relations():
            item = QListWidgetItem(bp.display_name)
            item.setData(Qt.ItemDataRole.UserRole, bp.type_key)
            item.setToolTip(bp.description)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            default_checked = self._is_default_argument_relation(bp)
            item.setCheckState(Qt.CheckState.Checked if default_checked else Qt.CheckState.Unchecked)
            self.relation_type_list.addItem(item)

    def _clear_editor(self):
        self.title_input.clear()
        self.inst_input.clear()
        self.limits_editor.load({})
        self.prerequisites.load({})
        self._set_checked_types(self.node_type_list, {EntityType.CLAIM.value, EntityType.REASONING.value, EntityType.QUOTE.value})
        self._set_checked_types(self.relation_type_list, self._default_argument_relation_types())
        self.allow_text_nodes.setChecked(False)
        self._set_checked_det_passes([{"pass_id": "quote_candidate_rank", "top_n": 25, "params": {}}])

    # ── Deterministic pass configuration ─────────────────────────────────

    def _populate_det_pass_list(self):
        if not self.deterministic_pass_registry:
            return
        for spec in self.deterministic_pass_registry.all():
            item = QListWidgetItem(spec.pass_name)
            item.setData(Qt.ItemDataRole.UserRole, spec.pass_id)
            item.setToolTip(spec.description)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.det_pass_list.addItem(item)

    def _rebuild_det_pass_forms(self, *_args):
        # Preserve in-progress values for passes that stay checked.
        current_values = {pid: form.get_values() for pid, form in self._det_pass_forms.items()}
        while self.det_pass_forms_layout.count():
            child = self.det_pass_forms_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        self._det_pass_forms = {}

        if not self.deterministic_pass_registry:
            return
        for idx in range(self.det_pass_list.count()):
            item = self.det_pass_list.item(idx)
            if item.checkState() != Qt.CheckState.Checked:
                continue
            pass_id = item.data(Qt.ItemDataRole.UserRole)
            spec = self.deterministic_pass_registry.get(pass_id)
            if not spec:
                continue
            schema_list = [{"key": key, **field} for key, field in (spec.param_schema or {}).items()]
            if not schema_list:
                continue
            self.det_pass_forms_layout.addWidget(QLabel(f"<b>{spec.pass_name}</b>"))
            form = SchemaFormBuilder(schema_list, self.theme)
            if pass_id in current_values:
                form.set_values(current_values[pass_id])
            self.det_pass_forms_layout.addWidget(form)
            self._det_pass_forms[pass_id] = form

    def _set_checked_det_passes(self, deterministic_passes):
        by_id = {p.get("pass_id"): p for p in (deterministic_passes or []) if isinstance(p, dict)}
        for idx in range(self.det_pass_list.count()):
            item = self.det_pass_list.item(idx)
            pass_id = item.data(Qt.ItemDataRole.UserRole)
            item.setCheckState(Qt.CheckState.Checked if pass_id in by_id else Qt.CheckState.Unchecked)
        self._rebuild_det_pass_forms()
        for pass_id, form in self._det_pass_forms.items():
            saved = by_id.get(pass_id) or {}
            values = dict(saved.get("params") or {})
            if "top_n" in saved:
                values.setdefault("top_n", saved["top_n"])
            form.set_values(values)

    def _collect_det_passes(self) -> list:
        passes = []
        for idx in range(self.det_pass_list.count()):
            item = self.det_pass_list.item(idx)
            if item.checkState() != Qt.CheckState.Checked:
                continue
            pass_id = item.data(Qt.ItemDataRole.UserRole)
            form = self._det_pass_forms.get(pass_id)
            values = form.get_values() if form else {}
            entry = {"pass_id": pass_id, "params": values}
            if "top_n" in values:
                entry["top_n"] = values["top_n"]
            passes.append(entry)
        return passes

    def _create_new(self):
        self.current_template_id = None
        self._clear_editor()
        self.title_input.setText("New Analysis Mode")

    def _load_template_list(self):
        self.list_widget.clear()
        for t in self.templates:
            self.list_widget.addItem(t.get("title", "Unnamed"))

    def _on_template_selected(self, item):
        idx = self.list_widget.row(item)
        template = self.templates[idx]
        self.current_template_id = template.get("id")

        self._clear_editor()
        self.title_input.setText(template.get("title", ""))
        self.inst_input.setText(template.get("instructions", ""))
        self.limits_editor.load(template)
        self.prerequisites.load(template)
        self._set_checked_types(self.node_type_list, set(template.get("node_types") or []))
        self._set_checked_types(self.relation_type_list, set(template.get("relation_types") or []))
        self.allow_text_nodes.setChecked(bool(template.get("allow_text_nodes", True)))
        self._set_checked_det_passes(template.get("deterministic_passes") or [])
        self._update_contract_preview()

    def _save_current(self):
        title = self.title_input.text().strip()
        if not title: return QMessageBox.warning(self, "Error", "Title cannot be empty.")
        instructions = self.inst_input.toPlainText().strip()
        try:
            contract = self._selection_contract()
            self._selection_spec(contract)
        except ValueError as exc:
            self.validation_label.setText(str(exc))
            return
        template_data = self._template_data(title, instructions, contract)

        if self.current_template_id:
            for i, t in enumerate(self.templates):
                if t["id"] == self.current_template_id:
                    self.templates[i] = template_data
                    break
        else:
            self.templates.append(template_data)

        self.pm.save_analysis_templates(self.templates)
        self._load_template_list()
        QMessageBox.information(self, "Saved", "Analysis Mode saved successfully!")
        self.accept()

    def _template_data(self, title, instructions, contract):
        previous = self._selected_template()
        return {
            **dict(chunk_prompt_key='Graph Analysis Chunk Observations System',
                   synthesis_prompt_key='Graph Analysis Synthesis System', master_prompt_key='Graph Analysis Master System',
                   chunk_query_prompt_key='Graph Analysis Chunk Observations Query',
                   synthesis_query_prompt_key='Graph Analysis Synthesis Query', master_query_prompt_key='Graph Analysis Master Query'),
            **previous,
            'id': self.current_template_id or f"tmpl_{title.replace(' ', '_').lower()}",
            'title': title, 'instructions': instructions,
            'schema': json.dumps(contract, indent=2), 'graph_contract': contract,
            'node_types': self._checked_types(self.node_type_list),
            'relation_types': self._checked_types(self.relation_type_list),
            'allow_text_nodes': self.allow_text_nodes.isChecked(),
            'analysis_template_version': 15,
            'limits': self.limits_editor.legacy_values(previous),
            'deterministic_passes': self._collect_det_passes(),
            **self.prerequisites.value(),
        }

    def _checked_types(self, list_widget):
        values = []
        for idx in range(list_widget.count()):
            item = list_widget.item(idx)
            if item.checkState() == Qt.CheckState.Checked:
                values.append(item.data(Qt.ItemDataRole.UserRole))
        return values

    def _default_argument_relation_types(self):
        return {bp.type_key for bp in self.registry.all_relations() if self._is_default_argument_relation(bp)}

    def _is_default_argument_relation(self, bp):
        node_types = {EntityType.CLAIM.value, EntityType.REASONING.value, EntityType.QUOTE.value}
        traits = set(bp.traits or [])
        if not traits.intersection({RelationTrait.EVIDENTIARY, RelationTrait.HIERARCHICAL, RelationTrait.SEMANTIC}):
            return False
        if not any(node_type in bp.valid_source_types for node_type in node_types):
            return False
        if not any(node_type in bp.valid_target_types for node_type in node_types):
            return False
        return any(self.registry.validate_relation(bp.type_key, src, tgt) for src in node_types for tgt in node_types)

    def _set_checked_types(self, list_widget, selected):
        selected = {analysis_entity_type(value) if list_widget is self.node_type_list
                    else f'relation.{canonical_relation(value)}' for value in selected or []}
        known = {list_widget.item(i).data(Qt.ItemDataRole.UserRole) for i in range(list_widget.count())}
        for value in sorted(selected - known):
            item = QListWidgetItem(f'Unavailable: {value}')
            item.setData(Qt.ItemDataRole.UserRole, value)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setToolTip('Uncheck this type and choose a registered replacement, or restore its plugin.')
            list_widget.addItem(item)
        for idx in range(list_widget.count()):
            item = list_widget.item(idx)
            item.setCheckState(Qt.CheckState.Checked if item.data(Qt.ItemDataRole.UserRole) in selected else Qt.CheckState.Unchecked)

    def _selection_contract(self):
        nodes = self._checked_types(self.node_type_list)
        relations = self._checked_types(self.relation_type_list)
        if not nodes or not relations:
            raise ValueError('Choose at least one node type and one connection type.')
        base = dict(self._selected_template().get('graph_contract') or {})
        goal = self.inst_input.toPlainText().strip()
        base.update(purpose=goal, custom_instructions=goal)
        base['evidence_selection'] = self.limits_editor.selection()
        return self.compiler.compile(base, template={'node_types': nodes, 'relation_types': relations,
                                                     'instructions': goal})

    def _selected_template(self):
        return next((template for template in self.templates
                     if template.get('id') == self.current_template_id), {})

    def _selection_spec(self, contract):
        template = self._template_data(self.title_input.text().strip() or 'Preview',
                                       self.inst_input.toPlainText().strip(), contract)
        return self.modes.validate(self.modes.saved_template_spec(template))

    def _update_contract_preview(self, *_):
        if not hasattr(self, 'btn_save'):
            return
        try:
            contract = self._selection_contract()
            spec = self._selection_spec(contract)
            stages = ', '.join(stage.replace('_', ' ') for stage in spec.required_stages) or 'no automatic passes'
            requirements = describe_missing([{'kind': 'artifact_types', **group} for group in spec.artifact_requirements]) or 'No required artifact groups.'
            self.validation_label.setText(
                f"Vocabulary checked: {len(contract['node_types'])} node types and "
                f"{len(contract['relation_types'])} connection types. Coverage depends on source artifacts; "
                f'new connections remain suggestions. Prepares: {stages}. {requirements}')
            self.btn_save.setEnabled(True)
        except ValueError as exc:
            self.validation_label.setText(str(exc))
            self.btn_save.setEnabled(False)

    def closeEvent(self, event):
        self.prerequisites.shutdown()
        if self._project_signal is not None:
            self._project_signal.disconnect(self.close)
            self._project_signal = None
        super().closeEvent(event)

    def _open_prompt_manager(self):
        manager = getattr(self.ctx, 'prompt_manager', None)
        if manager is None:
            return
        from gui.components.dialogs.prompt_editor_dialog import PromptEditorDialog
        dlg = PromptEditorDialog(manager, self)
        dlg.view_mode_combo.setCurrentIndex(2)
        dm = get_for_widget(self)
        if dm:
            dm.show_instance(dlg)
        else:
            exec_as_modal(dlg)

    def _apply_theme(self):
        if not self.theme: return
        self.setStyleSheet(f"background-color: {self.theme.get('bg_main', '#1e1e1e')}; color: {self.theme.get('text_main', '#fff')};")
        style = f"background-color: {self.theme.get('bg_input', '#2b2b2b')}; border: 1px solid {self.theme.get('border', '#444')}; border-radius: 4px;"
        self.list_widget.setStyleSheet(style)
        self.title_input.setStyleSheet(style)
        self.inst_input.setStyleSheet(style)
        self.node_type_list.setStyleSheet(style)
        self.relation_type_list.setStyleSheet(style)
        for spin in [self.quotes_per_chunk, self.quote_words, self.max_quote_words, self.explanation_words]:
            spin.setStyleSheet(style)
