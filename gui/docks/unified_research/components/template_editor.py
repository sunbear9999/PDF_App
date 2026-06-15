import json
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QPushButton, 
                             QLabel, QLineEdit, QTextEdit,
                             QMessageBox, QListWidget, QCheckBox, QListWidgetItem, QSpinBox, QGroupBox)
from PySide6.QtCore import Qt
from core.models.ontology_model import EntityType
from core.ontology.registry import OntologyRegistry, RelationTrait

class TemplateEditorDialog(QDialog):
    def __init__(self, project_manager, theme=None, parent=None):
        super().__init__(parent)
        self.pm = project_manager
        self.theme = theme
        self.setWindowTitle("Analysis Modes Builder")
        self.resize(920, 640)
        self.templates = self.pm.get_analysis_templates()
        self.current_template_id = None
        self.registry = OntologyRegistry()
        
        self._build_ui()
        self._load_template_list()
        self._apply_theme()

    def _build_ui(self):
        main_layout = QHBoxLayout(self)
        
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
        
        # --- Right Panel: Visual Editor ---
        right_panel = QVBoxLayout()
        
        # Title & Instructions
        right_panel.addWidget(QLabel("<b>Mode Title:</b>"))
        self.title_input = QLineEdit()
        right_panel.addWidget(self.title_input)

        help_label = QLabel(
            "Describe the analysis goal, choose the graph node/link types, and set quote limits. "
            "The app automatically runs: quote selection per section, evidence synthesis, then registry-driven graph generation."
        )
        help_label.setWordWrap(True)
        right_panel.addWidget(help_label)
        
        right_panel.addWidget(QLabel("<b>Analysis Goal:</b>"))
        self.inst_input = QTextEdit()
        self.inst_input.setFixedHeight(95)
        self.inst_input.setPlaceholderText("Example: Diagram the connections between people throughout this paper.")
        right_panel.addWidget(self.inst_input)

        limits_box = QGroupBox("Quote Selection Limits")
        limits_layout = QGridLayout(limits_box)
        self.quotes_per_chunk = self._spin(1, 12, 5)
        self.quote_words = self._spin(4, 30, 10)
        self.max_quote_words = self._spin(6, 40, 18)
        self.explanation_words = self._spin(4, 30, 10)
        limits_layout.addWidget(QLabel("Quotes per section"), 0, 0)
        limits_layout.addWidget(self.quotes_per_chunk, 0, 1)
        limits_layout.addWidget(QLabel("Target quote words"), 0, 2)
        limits_layout.addWidget(self.quote_words, 0, 3)
        limits_layout.addWidget(QLabel("Max quote words"), 1, 0)
        limits_layout.addWidget(self.max_quote_words, 1, 1)
        limits_layout.addWidget(QLabel("Explanation words"), 1, 2)
        limits_layout.addWidget(self.explanation_words, 1, 3)
        btn_prompts = QPushButton("Advanced: Edit Shared Prompts")
        btn_prompts.clicked.connect(self._open_prompt_manager)
        limits_layout.addWidget(btn_prompts, 0, 4, 2, 1)
        right_panel.addWidget(limits_box)

        graph_row = QHBoxLayout()
        graph_col_nodes = QVBoxLayout()
        graph_col_rels = QVBoxLayout()
        graph_col_nodes.addWidget(QLabel("<b>Final graph node types:</b>"))
        self.node_type_list = QListWidget()
        self.node_type_list.setMinimumHeight(110)
        graph_col_nodes.addWidget(self.node_type_list)
        self.allow_text_nodes = QCheckBox("Allow fallback text nodes")
        self.allow_text_nodes.setChecked(False)
        graph_col_nodes.addWidget(self.allow_text_nodes)
        graph_col_rels.addWidget(QLabel("<b>Final graph connection types:</b>"))
        self.relation_type_list = QListWidget()
        self.relation_type_list.setMinimumHeight(110)
        graph_col_rels.addWidget(self.relation_type_list)
        graph_row.addLayout(graph_col_nodes, 1)
        graph_row.addLayout(graph_col_rels, 1)
        right_panel.addLayout(graph_row)
        self._populate_registry_lists()
        
        # Save Area
        save_layout = QHBoxLayout()
        save_layout.addStretch()
        btn_save = QPushButton("💾 Save Mode")
        btn_save.setFixedSize(120, 35)
        btn_save.clicked.connect(self._save_current)
        save_layout.addWidget(btn_save)
        right_panel.addLayout(save_layout)
        
        main_layout.addLayout(right_panel)

    def _spin(self, minimum, maximum, value):
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        return spin

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
        self.quotes_per_chunk.setValue(5)
        self.quote_words.setValue(10)
        self.max_quote_words.setValue(18)
        self.explanation_words.setValue(10)
        self._set_checked_types(self.node_type_list, {EntityType.CLAIM.value, EntityType.REASONING.value, EntityType.QUOTE.value})
        self._set_checked_types(self.relation_type_list, self._default_argument_relation_types())
        self.allow_text_nodes.setChecked(False)

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
        limits = template.get("limits") if isinstance(template.get("limits"), dict) else {}
        self.quotes_per_chunk.setValue(int(limits.get("max_quotes_per_chunk", 5) or 5))
        self.quote_words.setValue(int(limits.get("quote_words", 10) or 10))
        self.max_quote_words.setValue(int(limits.get("max_quote_words", 18) or 18))
        self.explanation_words.setValue(int(limits.get("explanation_words", 10) or 10))
        self._set_checked_types(self.node_type_list, set(template.get("node_types") or []))
        self._set_checked_types(self.relation_type_list, set(template.get("relation_types") or []))
        self.allow_text_nodes.setChecked(bool(template.get("allow_text_nodes", True)))

    def _save_current(self):
        title = self.title_input.text().strip()
        if not title: return QMessageBox.warning(self, "Error", "Title cannot be empty.")
        instructions = self.inst_input.toPlainText().strip()
        compiled_schema = {
            "analysis_goal": instructions,
            "chunk_stage": "Select short verbatim quotes and short relevance notes for this goal.",
            "synthesis_stage": "Synthesize numbered quotes into one connected analysis plan.",
            "graph_stage": "Use selected registry node and relation types for final graph output.",
            "quote_selection_limits": {
                "quotes_per_section": self.quotes_per_chunk.value(),
                "target_quote_words": self.quote_words.value(),
                "max_quote_words": self.max_quote_words.value(),
                "explanation_words": self.explanation_words.value(),
            },
        }

        schema_str = json.dumps(compiled_schema, indent=2)
        
        template_data = {
            "id": self.current_template_id or f"tmpl_{title.replace(' ', '_').lower()}",
            "title": title,
            "instructions": instructions,
            "schema": schema_str,
            "node_types": self._checked_types(self.node_type_list),
            "relation_types": self._checked_types(self.relation_type_list),
            "allow_text_nodes": self.allow_text_nodes.isChecked(),
            "chunk_prompt_key": "Graph Analysis Chunk Observations System",
            "synthesis_prompt_key": "Graph Analysis Synthesis System",
            "master_prompt_key": "Graph Analysis Master System",
            "chunk_query_prompt_key": "Graph Analysis Chunk Observations Query",
            "synthesis_query_prompt_key": "Graph Analysis Synthesis Query",
            "master_query_prompt_key": "Graph Analysis Master Query",
            "analysis_template_version": 12,
            "limits": {
                "chunk_pages": 4,
                "max_chunk_chars": 14000,
                "max_master_chars": 36000,
                "num_ctx": 24576,
                "chunk_num_predict": 1400,
                "synthesis_num_predict": 3500,
                "master_num_predict": 4000,
                "max_entities_per_chunk": 12,
                "max_relations_per_chunk": 16,
                "max_quotes_per_chunk": self.quotes_per_chunk.value(),
                "quote_words": self.quote_words.value(),
                "max_quote_words": self.max_quote_words.value(),
                "explanation_words": self.explanation_words.value(),
            },
        }

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
        selected = set(selected or [])
        for idx in range(list_widget.count()):
            item = list_widget.item(idx)
            item.setCheckState(Qt.CheckState.Checked if item.data(Qt.ItemDataRole.UserRole) in selected else Qt.CheckState.Unchecked)

    def _open_prompt_manager(self):
        parent = self.parent()
        while parent and not hasattr(parent, "prompt_manager"):
            parent = parent.parent()
        if not parent:
            return QMessageBox.information(self, "Prompt Manager", "Open the main prompt manager to edit these prompt keys.")
        from gui.components.dialogs.prompt_editor_dialog import PromptEditorDialog
        dlg = PromptEditorDialog(parent.prompt_manager, parent)
        dlg.view_mode_combo.setCurrentIndex(2)
        dlg.exec()

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
