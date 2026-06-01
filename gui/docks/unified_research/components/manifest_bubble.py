from PySide6.QtWidgets import (QFrame, QVBoxLayout, QHBoxLayout, QLabel, 
                               QPushButton, QTextEdit, QWidget, QScrollArea, QLineEdit, QDialog)
from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor, QIcon
import json

def to_bullets(val):
    """Safely converts JSON data into readable bullet points."""
    if isinstance(val, list):
        return "\n\n".join(f"• {item}" for item in val) # Double newline for spacing
    if isinstance(val, dict):
        return "\n\n".join(f"• {k}: {v}" for k, v in val.items())
    if val is None:
        return "*(Deleted)*"
    return str(val)

def from_bullets(text):
    """Converts user-edited bullet points back into a Python list."""
    if text.strip().startswith("•"):
        return [line.lstrip("• ").strip() for line in text.split("\n") if line.strip()]
    try:
        if (text.startswith('{') and text.endswith('}')) or (text.startswith('[') and text.endswith(']')):
            return json.loads(text)
    except: pass
    return text

class CollapsibleManifestCard(QFrame):
    """A reusable, collapsible card for displaying or editing manifest categories."""
    def __init__(self, key_name, value_data, theme, is_editable=False, delete_callback=None):
        super().__init__()
        self.theme = theme or {}
        self.is_editable = is_editable
        self.delete_callback = delete_callback
        
        self.setObjectName("ManifestCard")
        bg_panel = self.theme.get('bg_panel', '#333')
        border = self.theme.get('border', '#444')
        self.setStyleSheet(f"QFrame#ManifestCard {{ background-color: {bg_panel}; border: 1px solid {border}; border-radius: 8px; margin-bottom: 4px; }}")
        
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(8, 8, 8, 8)
        self.layout.setSpacing(6)

        # --- HEADER ---
        header_layout = QHBoxLayout()
        
        self.btn_toggle = QPushButton("▼")
        self.btn_toggle.setFixedSize(24, 24)
        self.btn_toggle.setStyleSheet("background: transparent; border: none; font-weight: bold; color: #888;")
        self.btn_toggle.clicked.connect(self.toggle_collapse)
        header_layout.addWidget(self.btn_toggle)

        if self.is_editable:
            self.key_input = QLineEdit(key_name)
            self.key_input.setPlaceholderText("Category Name")
            self.key_input.setStyleSheet(f"font-weight: bold; font-size: 14px; color: {self.theme.get('accent', '#b366ff')}; background: transparent; border: none;")
            header_layout.addWidget(self.key_input, 1)
            
            btn_del = QPushButton("✖")
            btn_del.setFixedSize(24, 24)
            btn_del.setStyleSheet("background: transparent; border: none; color: #ff4444; font-weight: bold;")
            btn_del.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn_del.clicked.connect(lambda: self.delete_callback(self) if self.delete_callback else None)
            header_layout.addWidget(btn_del)
        else:
            self.key_input = QLabel(key_name.replace("_", " ").title())
            if value_data is None:
                self.key_input.setStyleSheet("font-weight: bold; font-size: 14px; color: #ff4444; text-decoration: line-through;")
            else:
                self.key_input.setStyleSheet(f"font-weight: bold; font-size: 14px; color: {self.theme.get('accent', '#55aa55')};")
            header_layout.addWidget(self.key_input, 1)

        self.layout.addLayout(header_layout)

        # --- BODY ---
        self.content_widget = QWidget()
        content_layout = QVBoxLayout(self.content_widget)
        content_layout.setContentsMargins(28, 0, 4, 4) # Indent content slightly
        
        self.val_input = QTextEdit()
        self.val_input.setPlainText(to_bullets(value_data))
        
        if self.is_editable:
            self.val_input.setPlaceholderText("Enter details or bullet points here...")
            self.val_input.setStyleSheet(f"background-color: {self.theme.get('bg_main', '#1e1e1e')}; color: {self.theme.get('text_main', '#fff')}; border: 1px solid {border}; border-radius: 4px; padding: 6px;")
            self.val_input.setMinimumHeight(80)
        else:
            self.val_input.setReadOnly(True)
            self.val_input.setStyleSheet(f"background: transparent; color: {self.theme.get('text_main', '#ddd')}; border: none;")
            self.val_input.setMaximumHeight(150) # Prevent massive chat bubbles
            
        content_layout.addWidget(self.val_input)
        self.layout.addWidget(self.content_widget)

    def toggle_collapse(self):
        is_visible = self.content_widget.isVisible()
        self.content_widget.setVisible(not is_visible)
        self.btn_toggle.setText("▶" if is_visible else "▼")

    def get_data(self):
        if not self.is_editable: return None, None
        key = self.key_input.text().strip()
        val = from_bullets(self.val_input.toPlainText().strip())
        return key, val


class ManifestUpdateWidget(QFrame):
    """The read-only bubble that appears in the Chat/Brainstorm stream."""
    def __init__(self, new_data, theme=None, parent=None):
        super().__init__(parent)
        self.theme = theme or {}
        self.setObjectName("ManifestUpdateBubble")
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        self.lbl_header = QLabel("<b>📝 Manifest Updated</b>")
        self.lbl_header.setStyleSheet(f"color: {self.theme.get('text_main', '#fff')}; margin-bottom: 4px;")
        layout.addWidget(self.lbl_header)

        # Generate a read-only collapsible card for each updated key
        for key, value in new_data.items():
            card = CollapsibleManifestCard(key, value, self.theme, is_editable=False)
            layout.addWidget(card)

        # Action Toolbar
        self.btn_open = QPushButton("📂 Edit Manifest")
        self.btn_open.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_open.setStyleSheet(f"background-color: {self.theme.get('bg_panel', '#333')}; color: {self.theme.get('text_main', '#fff')}; border: 1px solid {self.theme.get('border', '#444')}; padding: 6px 12px; border-radius: 4px; margin-top: 4px;")
        
        toolbar = QHBoxLayout()
        toolbar.addWidget(self.btn_open)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        self.setStyleSheet(f"""
            QFrame#ManifestUpdateBubble {{
                background-color: {self.theme.get('bg_input', '#2b2b2b')};
                border: 1px solid {self.theme.get('border', '#444')};
                border-left: 4px solid {self.theme.get('accent', '#55aa55')};
                border-radius: 6px;
                margin-top: 6px; margin-bottom: 6px;
            }}
        """)


class ProjectBriefDialog(QDialog):
    """The main interactive editor dialog for the Project Manifest."""
    def __init__(self, pm, theme, parent=None):
        super().__init__(parent)
        self.pm = pm
        self.theme = theme or {}
        self.setWindowTitle("📝 Project Manifest Control Room")
        self.resize(600, 700)
        
        self.layout = QVBoxLayout(self)
        self.layout.addWidget(QLabel("<b>Project Manifest</b><br><i>Manage structural objectives. The AI agent syncs directly with these sections.</i>"))
        
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.container = QWidget()
        self.rows_layout = QVBoxLayout(self.container)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(self.container)
        self.layout.addWidget(self.scroll, 1)
        
        self.row_widgets = []
        self._load_manifest_data()
        
        bottom_bar = QHBoxLayout()
        btn_add = QPushButton("➕ Add Category")
        btn_add.clicked.connect(lambda: self._create_row_ui("", ""))
        
        btn_save = QPushButton("💾 Save Manifest")
        btn_save.clicked.connect(self.save_and_close)
        
        bottom_bar.addWidget(btn_add)
        bottom_bar.addStretch()
        bottom_bar.addWidget(btn_save)
        self.layout.addLayout(bottom_bar)
        
        self.setStyleSheet(f"background-color: {self.theme.get('bg_main', '#1e1e1e')}; color: {self.theme.get('text_main', '#fff')};")
        btn_add.setStyleSheet(f"background-color: {self.theme.get('bg_panel', '#333')}; border: 1px solid {self.theme.get('border', '#444')}; padding: 6px; border-radius: 4px;")
        btn_save.setStyleSheet(f"background-color: {self.theme.get('accent', '#b366ff')}; color: white; padding: 6px 12px; font-weight: bold; border-radius: 4px;")

    def _load_manifest_data(self):
        raw = self.pm.get_metadata("project_manifest", "{}")
        try: data = json.loads(raw) if raw.strip() else {}
        except: data = {"main_goal": str(raw)} 
        
        if not data:
            data = {"main_goal": "", "key_claims": []}
            
        for k, v in data.items():
            self._create_row_ui(k, v)

    def _create_row_ui(self, key, val):
        card = CollapsibleManifestCard(key, val, self.theme, is_editable=True, delete_callback=self._remove_row)
        self.rows_layout.addWidget(card)
        self.row_widgets.append(card)

    def _remove_row(self, widget):
        self.rows_layout.removeWidget(widget)
        self.row_widgets.remove(widget)
        widget.deleteLater()

    def save_and_close(self):
        compiled = {}
        for row in self.row_widgets:
            k, v = row.get_data()
            if k: compiled[k] = v
                
        self.pm.set_metadata("project_manifest", json.dumps(compiled))
        self.accept()