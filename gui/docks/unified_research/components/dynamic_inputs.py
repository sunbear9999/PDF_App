# gui/docks/unified_research/components/dynamic_inputs.py
import os
from PySide6.QtWidgets import QWidget, QHBoxLayout, QCheckBox, QComboBox, QLineEdit, QLabel
from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor

class DynamicInputWidget(QWidget):
    """
    Reads a blueprint's 'expected_inputs' array and dynamically generates the UI.
    """
    def __init__(self, expected_inputs, theme, project_manager=None, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.pm = project_manager
        self.expected_inputs = expected_inputs or []
        self.input_widgets = {}
        
        # Proper spacing and vertical alignment so it blends with the Send button
        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 10, 0) # Add a little margin on the right
        self.layout.setSpacing(8)
        self.layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        
        self._build_ui()

    def _build_ui(self):
        for inp in self.expected_inputs:
            key = inp.get("key")
            label_text = inp.get("label", key.replace("_", " ").title())
            field_type = inp.get("type", "text")
            default_val = inp.get("default", "")

            if field_type == "boolean":
                # Upgrade: Style booleans as sleek toggle buttons
                widget = QCheckBox(label_text)
                widget.setChecked(bool(default_val))
                widget.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
                
                if self.theme:
                    # Hide the default checkbox box and style the whole widget like a pill
                    widget.setStyleSheet(f"""
                        QCheckBox {{
                            background-color: {self.theme.get('bg_panel', '#333')};
                            color: {self.theme.get('text_main', '#fff')};
                            border: 1px solid {self.theme.get('border', '#444')};
                            padding: 4px 10px;
                            border-radius: 6px;
                            font-size: 11px;
                        }}
                        QCheckBox::indicator {{
                            width: 0px; height: 0px; border: none; /* Hide standard box */
                        }}
                        QCheckBox:checked {{
                            background-color: {self.theme.get('accent', '#b366ff')};
                            color: white;
                            border: none;
                            font-weight: bold;
                        }}
                        QCheckBox:hover:!checked {{
                            border: 1px solid {self.theme.get('accent', '#b366ff')};
                        }}
                    """)
                self.layout.addWidget(widget)
                self.input_widgets[key] = widget

            elif field_type == "dropdown":
                lbl = QLabel(f"<b>{label_text}:</b>")
                lbl.setStyleSheet(f"color: {self.theme.get('text_muted', '#aaa')}; font-size: 11px;")
                self.layout.addWidget(lbl)
                
                widget = QComboBox()
                widget.addItems(inp.get("options", []))
                if default_val: widget.setCurrentText(str(default_val))
                self._apply_style(widget)
                self.layout.addWidget(widget)
                self.input_widgets[key] = widget

            elif field_type == "doc_selector":
                lbl = QLabel(f"<b>{label_text}:</b>")
                lbl.setStyleSheet(f"color: {self.theme.get('text_muted', '#aaa')}; font-size: 11px;")
                self.layout.addWidget(lbl)
                
                widget = QComboBox()
                if self.pm:
                    for pdf in self.pm.pdfs: 
                        widget.addItem(os.path.basename(pdf), pdf)
                self._apply_style(widget)
                self.layout.addWidget(widget)
                self.input_widgets[key] = widget

            else: # Default Text Line
                widget = QLineEdit()
                widget.setPlaceholderText(label_text)
                if default_val: widget.setText(str(default_val))
                self._apply_style(widget)
                self.layout.addWidget(widget)
                self.input_widgets[key] = widget

    def _apply_style(self, widget):
        if self.theme:
            widget.setStyleSheet(f"background-color: {self.theme.get('bg_input', '#2b2b2b')}; color: {self.theme.get('text_main', '#fff')}; border: 1px solid {self.theme.get('border', '#444')}; padding: 4px 8px; border-radius: 4px; font-size: 11px;")

    def get_values(self) -> dict:
        results = {}
        for key, widget in self.input_widgets.items():
            if isinstance(widget, QCheckBox):
                results[key] = widget.isChecked()
            elif isinstance(widget, QComboBox):
                results[key] = widget.currentData() or widget.currentText()
                if "doc" in key.lower():
                    results[f"{key}_name"] = os.path.basename(results[key] or "")
            elif isinstance(widget, QLineEdit):
                results[key] = widget.text().strip()
        return results