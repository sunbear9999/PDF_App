import json
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, 
                             QLabel, QLineEdit, QTextEdit, QComboBox, QWidget, 
                             QScrollArea, QMessageBox, QListWidget)
from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor

class FieldRowWidget(QWidget):
    """A single row in the visual schema builder."""
    def __init__(self, theme=None, parent=None):
        super().__init__(parent)
        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        
        self.key_input = QLineEdit()
        self.key_input.setPlaceholderText("Field Name (e.g., main_themes)")
        
        self.type_combo = QComboBox()
        self.type_combo.addItems([
            "Short Answer (Text)", 
            "Bullet Points (List of Texts)", 
            "Argument Map (Claim + Logic)",
            "Data Extraction (Metric + Value)"
        ])
        
        self.desc_input = QLineEdit()
        self.desc_input.setPlaceholderText("Description for the AI...")
        
        self.btn_remove = QPushButton("❌")
        self.btn_remove.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_remove.setFixedWidth(30)
        self.btn_remove.clicked.connect(self.deleteLater)
        
        self.layout.addWidget(self.key_input, 2)
        self.layout.addWidget(self.type_combo, 2)
        self.layout.addWidget(self.desc_input, 3)
        self.layout.addWidget(self.btn_remove)

        if theme:
            style = f"background: {theme.get('bg_input', '#2b2b2b')}; color: {theme.get('text_main', '#fff')}; border: 1px solid {theme.get('border', '#444')}; padding: 4px; border-radius: 4px;"
            self.key_input.setStyleSheet(style)
            self.type_combo.setStyleSheet(style)
            self.desc_input.setStyleSheet(style)
            self.btn_remove.setStyleSheet("background: transparent; border: none;")

    def get_field_data(self):
        """Converts this visual row into the actual JSON schema format."""
        key = self.key_input.text().strip().replace(" ", "_")
        desc = self.desc_input.text().strip() or "string"
        ftype = self.type_combo.currentText()
        
        if not key: return None, None
        
        if ftype == "Short Answer (Text)":
            return key, desc
        elif ftype == "Bullet Points (List of Texts)":
            return key, [desc]
        elif ftype == "Argument Map (Claim + Logic)":
            return key, [{"claim": "string", "supporting_logic": desc}]
        elif ftype == "Data Extraction (Metric + Value)":
            return key, [{"metric": "string", "value": desc}]
        return key, desc

class TemplateEditorDialog(QDialog):
    def __init__(self, project_manager, theme=None, parent=None):
        super().__init__(parent)
        self.pm = project_manager
        self.theme = theme
        self.setWindowTitle("Analysis Modes Builder")
        self.resize(800, 500)
        self.templates = self.pm.get_analysis_templates()
        self.current_template_id = None
        
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
        
        right_panel.addWidget(QLabel("<b>AI Instructions:</b>"))
        self.inst_input = QTextEdit()
        self.inst_input.setFixedHeight(80)
        self.inst_input.setPlaceholderText("Tell the AI what to look for in the text...")
        right_panel.addWidget(self.inst_input)
        
        # Visual Schema Builder
        right_panel.addWidget(QLabel("<b>Output Structure (What the AI should extract):</b>"))
        
        self.fields_container = QWidget()
        self.fields_layout = QVBoxLayout(self.fields_container)
        self.fields_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.fields_container)
        right_panel.addWidget(scroll)
        
        btn_add_field = QPushButton("➕ Add Output Field")
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
        row = FieldRowWidget(self.theme)
        if key: row.key_input.setText(key)
        if desc: row.desc_input.setText(desc)
        row.type_combo.setCurrentIndex(ftype_idx)
        self.fields_layout.addWidget(row)

    def _clear_editor(self):
        self.title_input.clear()
        self.inst_input.clear()
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
            return QMessageBox.warning(self, "Error", "You must add at least one output field.")

        schema_str = json.dumps(compiled_schema, indent=2)
        
        template_data = {
            "id": self.current_template_id or f"tmpl_{title.replace(' ', '_').lower()}",
            "title": title,
            "instructions": self.inst_input.toPlainText(),
            "schema": schema_str
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

    def _apply_theme(self):
        if not self.theme: return
        self.setStyleSheet(f"background-color: {self.theme.get('bg_main', '#1e1e1e')}; color: {self.theme.get('text_main', '#fff')};")
        style = f"background-color: {self.theme.get('bg_input', '#2b2b2b')}; border: 1px solid {self.theme.get('border', '#444')}; border-radius: 4px;"
        self.list_widget.setStyleSheet(style)
        self.title_input.setStyleSheet(style)
        self.inst_input.setStyleSheet(style)