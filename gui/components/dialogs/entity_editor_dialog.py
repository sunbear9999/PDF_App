from PySide6.QtWidgets import (QDialog, QFormLayout, QLineEdit, QTextEdit, 
                             QDialogButtonBox, QSpinBox, QDoubleSpinBox,QBoxLayout, QComboBox, QVBoxLayout)
from core.ontology.registry import OntologyRegistry
class EntityEditorDialog(QDialog):
    """Dynamically builds an edit form based on the Ontology Blueprint."""
    def __init__(self, entity, theme=None, parent=None):
        super().__init__(parent)
        self.entity = entity
        self.theme = theme
        self.registry = OntologyRegistry()
        self.blueprint = self.registry.get_entity_blueprint(self.entity.entity_type)
        
        self.setWindowTitle(f"Edit {self.blueprint.display_name}")
        self.setMinimumWidth(400)
        
        self.field_widgets = {}
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        form_layout = QFormLayout()
        
        # Always show the main text/title
        self.txt_content = QTextEdit(self.entity.properties.get("text") or self.entity.properties.get("title") or "")
        self.txt_content.setMaximumHeight(80)
        form_layout.addRow("Content:", self.txt_content)

        # Dynamically generate fields based on the Ontology Blueprint
        for field_def in self.blueprint.fields:
            current_val = self.entity.properties.get(field_def.key, field_def.default)
            
            if field_def.choices:
                widget = QComboBox()
                widget.addItems(field_def.choices)
                if current_val in field_def.choices:
                    widget.setCurrentText(current_val)
            elif field_def.value_type == "float":
                widget = QDoubleSpinBox()
                widget.setMinimum(field_def.minimum if field_def.minimum is not None else -9999.0)
                widget.setMaximum(field_def.maximum if field_def.maximum is not None else 9999.0)
                widget.setValue(float(current_val) if current_val is not None else 0.0)
            elif field_def.value_type == "int":
                widget = QSpinBox()
                widget.setMinimum(int(field_def.minimum) if field_def.minimum is not None else -9999)
                widget.setMaximum(int(field_def.maximum) if field_def.maximum is not None else 9999)
                widget.setValue(int(current_val) if current_val is not None else 0)
            else:
                widget = QLineEdit(str(current_val) if current_val is not None else "")
                
            self.field_widgets[field_def.key] = widget
            form_layout.addRow(field_def.label + ":", widget)

        layout.addLayout(form_layout)

        # Dialog buttons
        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)
        
        if self.theme:
            self.setStyleSheet(f"background-color: {self.theme['bg_panel']}; color: {self.theme['text_main']};")

    def get_updated_properties(self):
        """Returns the dictionary of updated properties."""
        props = dict(self.entity.properties)
        props["text"] = self.txt_content.toPlainText()
        
        for key, widget in self.field_widgets.items():
            if isinstance(widget, QComboBox):
                props[key] = widget.currentText()
            elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                props[key] = widget.value()
            elif isinstance(widget, QLineEdit):
                props[key] = widget.text()
                
        return props