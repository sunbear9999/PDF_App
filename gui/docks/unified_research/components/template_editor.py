import json
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, 
                             QLabel, QLineEdit, QTextEdit, QComboBox, QWidget, 
                             QScrollArea, QMessageBox, QListWidget, QCheckBox, QListWidgetItem)
from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor
from core.models.ontology_model import EntityType, RelationType
from core.ontology.registry import OntologyRegistry

class FieldRowWidget(QWidget):
    """A single row in the visual schema builder."""
    def __init__(self, registry=None, theme=None, parent=None):
        super().__init__(parent)
        self.registry = registry or OntologyRegistry()
        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)

        self.registry_field_combo = QComboBox()
        self.registry_field_combo.addItem("Custom property", "")
        self._populate_registry_fields()
        self.registry_field_combo.currentIndexChanged.connect(self._apply_registry_field)
        
        self.key_input = QLineEdit()
        self.key_input.setPlaceholderText("Property key (e.g., thesis_role)")
        
        self.type_combo = QComboBox()
        self.type_combo.addItems([
            "Node/Relation Property (Text)", 
            "Node/Relation Property (List)", 
            "Argument Property Group",
            "Metric Property Group"
        ])
        
        self.desc_input = QLineEdit()
        self.desc_input.setPlaceholderText("How the AI should fill this property...")
        
        self.btn_remove = QPushButton("❌")
        self.btn_remove.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_remove.setFixedWidth(30)
        self.btn_remove.clicked.connect(self.deleteLater)
        
        self.layout.addWidget(self.registry_field_combo, 2)
        self.layout.addWidget(self.key_input, 2)
        self.layout.addWidget(self.type_combo, 2)
        self.layout.addWidget(self.desc_input, 3)
        self.layout.addWidget(self.btn_remove)

        if theme:
            style = f"background: {theme.get('bg_input', '#2b2b2b')}; color: {theme.get('text_main', '#fff')}; border: 1px solid {theme.get('border', '#444')}; padding: 4px; border-radius: 4px;"
            self.key_input.setStyleSheet(style)
            self.registry_field_combo.setStyleSheet(style)
            self.type_combo.setStyleSheet(style)
            self.desc_input.setStyleSheet(style)
            self.btn_remove.setStyleSheet("background: transparent; border: none;")

    def get_field_data(self):
        """Converts this visual row into the actual JSON schema format."""
        key = self.key_input.text().strip().replace(" ", "_")
        desc = self.desc_input.text().strip() or "string"
        ftype = self.type_combo.currentText()
        
        if not key: return None, None
        
        if ftype == "Node/Relation Property (Text)":
            return key, desc
        elif ftype == "Node/Relation Property (List)":
            return key, [desc]
        elif ftype == "Argument Property Group":
            return key, [{"claim": "string", "supporting_logic": desc}]
        elif ftype == "Metric Property Group":
            return key, [{"metric": "string", "value": desc}]
        return key, desc

    def _populate_registry_fields(self):
        for bp in self.registry.all_entities():
            for field in getattr(bp, "fields", []) or []:
                self.registry_field_combo.addItem(f"{bp.display_name}: {field.label}", {
                    "key": field.key,
                    "description": bp.description,
                    "value_type": field.value_type,
                })
        for bp in self.registry.all_relations():
            for field in getattr(bp, "fields", []) or []:
                self.registry_field_combo.addItem(f"{bp.display_name} link: {field.label}", {
                    "key": field.key,
                    "description": bp.description,
                    "value_type": field.value_type,
                })

    def _apply_registry_field(self):
        data = self.registry_field_combo.currentData()
        if not isinstance(data, dict):
            return
        self.key_input.setText(data.get("key", ""))
        if not self.desc_input.text().strip():
            self.desc_input.setText(data.get("description", ""))

class TemplateEditorDialog(QDialog):
    def __init__(self, project_manager, theme=None, parent=None):
        super().__init__(parent)
        self.pm = project_manager
        self.theme = theme
        self.setWindowTitle("Analysis Modes Builder")
        self.resize(800, 500)
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
            "Analysis modes extract workspace-ready graph data: selected node types become nodes, "
            "selected connection types become relations, and extra properties are saved on those graph items."
        )
        help_label.setWordWrap(True)
        right_panel.addWidget(help_label)
        
        right_panel.addWidget(QLabel("<b>AI Instructions:</b>"))
        self.inst_input = QTextEdit()
        self.inst_input.setFixedHeight(80)
        self.inst_input.setPlaceholderText("Tell the AI what to look for in the text...")
        right_panel.addWidget(self.inst_input)

        prompt_keys = QHBoxLayout()
        prompt_keys.addWidget(QLabel("<b>Chunk Prompt:</b>"))
        self.chunk_prompt_input = QLineEdit("Graph Analysis Chunk System")
        self.chunk_prompt_input.setToolTip("PromptManager key used for per-chunk graph extraction.")
        prompt_keys.addWidget(self.chunk_prompt_input, 1)
        prompt_keys.addWidget(QLabel("<b>Final Pass Prompt:</b>"))
        self.master_prompt_input = QLineEdit("Graph Analysis Master System")
        self.master_prompt_input.setToolTip("PromptManager key used to merge chunks into the master graph.")
        prompt_keys.addWidget(self.master_prompt_input, 1)
        btn_prompts = QPushButton("Edit Prompts")
        btn_prompts.clicked.connect(self._open_prompt_manager)
        prompt_keys.addWidget(btn_prompts)
        right_panel.addLayout(prompt_keys)

        graph_row = QHBoxLayout()
        graph_col_nodes = QVBoxLayout()
        graph_col_rels = QVBoxLayout()
        graph_col_nodes.addWidget(QLabel("<b>Node types to extract:</b>"))
        self.node_type_list = QListWidget()
        self.node_type_list.setMinimumHeight(110)
        graph_col_nodes.addWidget(self.node_type_list)
        self.allow_text_nodes = QCheckBox("Allow fallback text nodes")
        self.allow_text_nodes.setChecked(True)
        graph_col_nodes.addWidget(self.allow_text_nodes)
        graph_col_rels.addWidget(QLabel("<b>Connection types to find:</b>"))
        self.relation_type_list = QListWidget()
        self.relation_type_list.setMinimumHeight(110)
        graph_col_rels.addWidget(self.relation_type_list)
        graph_row.addLayout(graph_col_nodes, 1)
        graph_row.addLayout(graph_col_rels, 1)
        right_panel.addLayout(graph_row)
        self._populate_registry_lists()
        
        # Visual Schema Builder
        right_panel.addWidget(QLabel("<b>Extra Graph Properties (saved onto extracted nodes/relations):</b>"))
        
        self.fields_container = QWidget()
        self.fields_layout = QVBoxLayout(self.fields_container)
        self.fields_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.fields_container)
        right_panel.addWidget(scroll)
        
        btn_add_field = QPushButton("➕ Add Graph Property")
        btn_add_field.clicked.connect(self._add_field_row)
        right_panel.addWidget(btn_add_field)
        
        # Save Area
        save_layout = QHBoxLayout()
        save_layout.addStretch()
        btn_save = QPushButton("💾 Save Mode")
        btn_save.setFixedSize(120, 35)
        btn_save.clicked.connect(self._save_current)
        save_layout.addWidget(btn_save)
        right_panel.addLayout(save_layout)
        
        main_layout.addLayout(right_panel)

    def _add_field_row(self, key="", ftype_idx=0, desc=""):
        row = FieldRowWidget(self.registry, self.theme)
        if key: row.key_input.setText(key)
        if desc: row.desc_input.setText(desc)
        row.type_combo.setCurrentIndex(ftype_idx)
        self.fields_layout.addWidget(row)

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
            default_checked = bp.type_key in {
                RelationType.SUPPORTS.value,
                RelationType.CONTRADICTS.value,
                RelationType.REASONS.value,
                RelationType.DERIVED_FROM.value,
            }
            item.setCheckState(Qt.CheckState.Checked if default_checked else Qt.CheckState.Unchecked)
            self.relation_type_list.addItem(item)

    def _clear_editor(self):
        self.title_input.clear()
        self.inst_input.clear()
        self.chunk_prompt_input.setText("Graph Analysis Chunk System")
        self.master_prompt_input.setText("Graph Analysis Master System")
        self._set_checked_types(self.node_type_list, {EntityType.CLAIM.value, EntityType.REASONING.value, EntityType.QUOTE.value})
        self._set_checked_types(self.relation_type_list, {RelationType.SUPPORTS.value, RelationType.CONTRADICTS.value, RelationType.REASONS.value, RelationType.DERIVED_FROM.value})
        self.allow_text_nodes.setChecked(True)
        for i in reversed(range(self.fields_layout.count())): 
            self.fields_layout.itemAt(i).widget().setParent(None)

    def _create_new(self):
        self.current_template_id = None
        self._clear_editor()
        self.title_input.setText("New Analysis Mode")
        self._add_field_row()

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
        self.chunk_prompt_input.setText(template.get("chunk_prompt_key", "Graph Analysis Chunk System"))
        self.master_prompt_input.setText(template.get("master_prompt_key", "Graph Analysis Master System"))
        self._set_checked_types(self.node_type_list, set(template.get("node_types") or []))
        self._set_checked_types(self.relation_type_list, set(template.get("relation_types") or []))
        self.allow_text_nodes.setChecked(bool(template.get("allow_text_nodes", True)))
        
        # Decompile the JSON string back into visual rows (Best effort)
        try:
            schema_dict = json.loads(template.get("schema", "{}"))
            for k, v in schema_dict.items():
                if isinstance(v, list) and len(v) > 0 and isinstance(v[0], dict) and "claim" in v[0]:
                    self._add_field_row(k, 2, v[0].get("supporting_logic", ""))
                elif isinstance(v, list) and len(v) > 0 and isinstance(v[0], dict) and "metric" in v[0]:
                    self._add_field_row(k, 3, v[0].get("value", ""))
                elif isinstance(v, list):
                    self._add_field_row(k, 1, str(v[0]) if v else "")
                else:
                    self._add_field_row(k, 0, str(v))
        except Exception:
            self._add_field_row() # Fallback

    def _save_current(self):
        title = self.title_input.text().strip()
        if not title: return QMessageBox.warning(self, "Error", "Title cannot be empty.")
        
        # Compile visual rows into JSON
        compiled_schema = {}
        for i in range(self.fields_layout.count()):
            widget = self.fields_layout.itemAt(i).widget()
            if isinstance(widget, FieldRowWidget):
                k, v = widget.get_field_data()
                if k: compiled_schema[k] = v

        if not compiled_schema:
            compiled_schema = {"graph_artifacts": "Registry-driven node and relation extraction"}

        schema_str = json.dumps(compiled_schema, indent=2)
        
        template_data = {
            "id": self.current_template_id or f"tmpl_{title.replace(' ', '_').lower()}",
            "title": title,
            "instructions": self.inst_input.toPlainText(),
            "schema": schema_str,
            "node_types": self._checked_types(self.node_type_list),
            "relation_types": self._checked_types(self.relation_type_list),
            "allow_text_nodes": self.allow_text_nodes.isChecked(),
            "chunk_prompt_key": self.chunk_prompt_input.text().strip() or "Graph Analysis Chunk System",
            "master_prompt_key": self.master_prompt_input.text().strip() or "Graph Analysis Master System",
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
        self.chunk_prompt_input.setStyleSheet(style)
        self.master_prompt_input.setStyleSheet(style)
        self.node_type_list.setStyleSheet(style)
        self.relation_type_list.setStyleSheet(style)
