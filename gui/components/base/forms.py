from PySide6.QtWidgets import QWidget, QVBoxLayout, QFormLayout, QLineEdit, QCheckBox, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit
from gui.components.base.core import ThemedMixin

class SchemaFormBuilder(QWidget, ThemedMixin):
    def __init__(self, schema: list, theme: dict = None, parent=None):
        super().__init__(parent)
        self.schema = schema
        self.field_widgets = {}
        
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.form_layout = QFormLayout()
        self.form_layout.setVerticalSpacing(12) # Breathe better
        self.layout.addLayout(self.form_layout)
        
        self.apply_base_theme(theme)
        self._build_form()

    def _build_form(self):
        for field in self.schema:
            key = field.get("key")
            label = field.get("label", key.replace("_", " ").title())
            f_type = field.get("type", "text")
            default = field.get("default", "")
            choices = field.get("choices", [])

            widget = None
            if choices or f_type == "select":
                widget = QComboBox()
                widget.addItems([str(c) for c in choices])
                if default in choices:
                    widget.setCurrentText(str(default))
            elif f_type == "boolean":
                widget = QCheckBox()
                widget.setChecked(bool(default))
            elif f_type == "number":
                widget = QDoubleSpinBox() if isinstance(default, float) else QSpinBox()
                widget.setRange(-999999, 999999)
                widget.setValue(default if default else 0)
            elif f_type == "long_text":
                widget = QTextEdit()
                widget.setPlainText(str(default))
                widget.setMaximumHeight(80)
            else:
                widget = QLineEdit()
                widget.setText(str(default))

            self.field_widgets[key] = widget
            
            # Subtly style the labels
            self.form_layout.addRow(f"<span style='font-size:13px; font-weight:600;'>{label}:</span>", widget)
            self._style_widget(widget)

    def _style_widget(self, widget):
        if isinstance(widget, QCheckBox):
            widget.setStyleSheet(f"color: {self.theme['text_main']}; font-size: 13px;")
        else:
            widget.setStyleSheet(self.get_input_style())

    def get_values(self) -> dict:
        results = {}
        for key, widget in self.field_widgets.items():
            if isinstance(widget, QCheckBox):
                results[key] = widget.isChecked()
            elif isinstance(widget, QComboBox):
                results[key] = widget.currentText()
            elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                results[key] = widget.value()
            elif isinstance(widget, QTextEdit):
                results[key] = widget.toPlainText().strip()
            else:
                results[key] = widget.text().strip()
        return results

    def update_theme(self, theme: dict):
        super().update_theme(theme)
        for widget in self.field_widgets.values():
            self._style_widget(widget)