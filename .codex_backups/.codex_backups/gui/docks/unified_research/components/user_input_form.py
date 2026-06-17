# gui/docks/unified_research/components/user_input_form.py
from PySide6.QtWidgets import QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTextEdit, QLineEdit, QCheckBox
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QCursor

class UserInputFormWidget(QFrame):
    # Signal emits a dictionary of the harvested keys and values
    form_submitted = Signal(dict)

    def __init__(self, step_id, expected_inputs, theme=None, parent=None):
        super().__init__(parent)
        self.step_id = step_id
        self.expected_inputs = expected_inputs
        self.input_fields = {} # Store references to harvest data later
        
        self.setObjectName("UniversalInputForm")
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # Header
        self.lbl_header = QLabel("<b>⏸️ Pipeline Paused: Input Required</b>")
        layout.addWidget(self.lbl_header)
        
        self.lbl_desc = QLabel(f"<i>The tool requires additional information for step '{step_id}'.</i>")
        self.lbl_desc.setWordWrap(True)
        layout.addWidget(self.lbl_desc)

        # Generate Fields Dynamically based on the AI Blueprint schema
        for item in expected_inputs:
            key = item.get("key")
            label_text = item.get("label", key.replace("_", " ").title())
            field_type = item.get("type", "text")
            
            layout.addWidget(QLabel(f"<b>{label_text}:</b>"))
            
            if field_type == "textarea":
                field = QTextEdit()
                field.setMaximumHeight(80)
                self.input_fields[key] = field
                layout.addWidget(field)
            elif field_type == "boolean":
                field = QCheckBox(label_text)
                self.input_fields[key] = field
                layout.addWidget(field)
            else: # Default to a single-line QLineEdit
                field = QLineEdit()
                field.setPlaceholderText(f"Enter {label_text.lower()}...")
                self.input_fields[key] = field
                layout.addWidget(field)

        # Action Toolbar (Mimicking note_bubble.py layout)
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 8, 0, 0)
        
        self.btn_submit = QPushButton("▶ Submit & Resume Pipeline")
        self.btn_submit.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_submit.clicked.connect(self._harvest_and_submit)
        
        toolbar.addStretch()
        toolbar.addWidget(self.btn_submit)
        layout.addLayout(toolbar)

        if theme:
            self.update_theme(theme)

    def _harvest_and_submit(self):
        results = {}
        for key, field in self.input_fields.items():
            if isinstance(field, QLineEdit):
                results[key] = field.text().strip()
            elif isinstance(field, QTextEdit):
                results[key] = field.toPlainText().strip()
            elif isinstance(field, QCheckBox):
                results[key] = field.isChecked()
                
        # Lock the form to prevent double-submits while the pipeline resumes
        self.btn_submit.setEnabled(False)
        self.btn_submit.setText("🔄 Resuming...")
        for field in self.input_fields.values():
            field.setEnabled(False)
            
        self.form_submitted.emit(results)

    def update_theme(self, theme):
        bg_color = theme.get('bg_input', '#2b2b2b')
        border_color = theme.get('warning', '#ffaa00') # Use warning color to stand out as a "pause"
        text_color = theme.get('text_main', '#ffffff')
        muted_color = theme.get('text_muted', '#aaaaaa')
        
        # Consistent styling with NoteBubbleWidget
        self.setStyleSheet(f"""
            QFrame#UniversalInputForm {{
                background-color: {bg_color};
                border: 1px solid {theme.get('border', '#444')};
                border-left: 4px solid {border_color};
                border-radius: 6px;
                margin-top: 4px; margin-bottom: 4px;
            }}
        """)
        self.lbl_header.setStyleSheet(f"color: {border_color}; font-size: 14px;")
        self.lbl_desc.setStyleSheet(f"color: {muted_color}; margin-bottom: 8px;")
        
        field_style = f"background: transparent; color: {text_color}; border: 1px solid {theme.get('border', '#444')}; border-radius: 4px; padding: 4px;"
        
        for field in self.input_fields.values():
            if isinstance(field, QCheckBox):
                field.setStyleSheet(f"color: {text_color}; font-weight: bold;")
            else:
                field.setStyleSheet(field_style)
        
        # Primary Action Button Style
        self.btn_submit.setStyleSheet(f"background-color: {theme.get('accent', '#b366ff')}; color: white; border: none; padding: 6px 12px; border-radius: 4px; font-weight: bold;")