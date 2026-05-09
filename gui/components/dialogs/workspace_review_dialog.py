# gui/components/dialogs/workspace_review_dialog.py
import json
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, 
                             QLabel, QFrame, QTextEdit)
from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor

class WorkspaceReviewDialog(QDialog):
    def __init__(self, ai_json_str, theme=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("🛡️ Review AI Changes")
        self.setMinimumSize(400, 300)
        self.theme = theme
        self.ai_json_str = ai_json_str
        
        # Determine what the AI is trying to do
        self.stats = self._calculate_diff()
        
        self._build_ui()

    def _calculate_diff(self):
        """Parses the raw JSON string to figure out the scope of the changes."""
        stats = {"nodes": 0, "edges": 0, "deletes": 0, "error": None}
        try:
            # We use the same auto-healing logic if needed, but assuming clean JSON here
            data = json.loads(self.ai_json_str) if isinstance(self.ai_json_str, str) else self.ai_json_str
            if isinstance(data, list):
                data = {"nodes": data}
                
            stats["nodes"] = len(data.get("nodes", []))
            stats["edges"] = len(data.get("edges", []))
            stats["deletes"] = len(data.get("delete_nodes", []))
        except Exception as e:
            stats["error"] = str(e)
            
        return stats

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Header
        lbl_header = QLabel("<b>The AI has proposed changes to your workspace.</b>")
        lbl_header.setStyleSheet("font-size: 14px;")
        layout.addWidget(lbl_header)

        # Content Summary
        summary_frame = QFrame()
        summary_layout = QVBoxLayout(summary_frame)
        
        if self.stats["error"]:
            lbl_err = QLabel(f"⚠️ <b>Warning:</b> The AI returned malformed data.<br><i>{self.stats['error']}</i>")
            lbl_err.setStyleSheet("color: #ff4444;")
            summary_layout.addWidget(lbl_err)
        else:
            if self.stats["nodes"] > 0:
                summary_layout.addWidget(QLabel(f"✨ <b>Add / Update:</b> {self.stats['nodes']} Nodes"))
            if self.stats["edges"] > 0:
                summary_layout.addWidget(QLabel(f"🔗 <b>Connect:</b> {self.stats['edges']} Edges"))
            if self.stats["deletes"] > 0:
                lbl_del = QLabel(f"🗑️ <b>Delete:</b> {self.stats['deletes']} Nodes")
                if self.theme: lbl_del.setStyleSheet(f"color: {self.theme.get('error', '#ff4444')};")
                summary_layout.addWidget(lbl_del)
                
            if sum(v for k, v in self.stats.items() if k != "error") == 0:
                summary_layout.addWidget(QLabel("<i>No structural changes detected.</i>"))

        layout.addWidget(summary_frame)

        # Advanced / Raw JSON View (Optional toggle for power users)
        self.btn_raw = QPushButton("▶ Show Raw Data")
        self.btn_raw.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_raw.clicked.connect(self._toggle_raw)
        layout.addWidget(self.btn_raw)

        self.raw_view = QTextEdit()
        self.raw_view.setReadOnly(True)
        self.raw_view.setPlainText(self.ai_json_str if isinstance(self.ai_json_str, str) else json.dumps(self.ai_json_str, indent=2))
        self.raw_view.hide()
        layout.addWidget(self.raw_view)

        layout.addStretch()

        # Action Buttons
        btn_layout = QHBoxLayout()
        
        self.btn_reject = QPushButton("❌ Discard")
        self.btn_reject.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_reject.clicked.connect(self.reject)
        
        self.btn_approve = QPushButton("✅ Approve & Apply")
        self.btn_approve.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_approve.clicked.connect(self.accept)
        
        btn_layout.addWidget(self.btn_reject)
        btn_layout.addWidget(self.btn_approve)
        layout.addLayout(btn_layout)

        self._apply_theme()

    def _toggle_raw(self):
        visible = not self.raw_view.isVisible()
        self.raw_view.setVisible(visible)
        self.btn_raw.setText("▼ Hide Raw Data" if visible else "▶ Show Raw Data")

    def _apply_theme(self):
        if not self.theme: return
        self.setStyleSheet(f"background-color: {self.theme.get('bg_main', '#1e1e1e')}; color: {self.theme.get('text_main', '#fff')};")
        
        btn_style = f"background-color: {self.theme.get('bg_panel', '#333')}; border: 1px solid {self.theme.get('border', '#444')}; padding: 8px; border-radius: 4px; font-weight: bold; color: {self.theme.get('text_main', '#fff')};"
        self.btn_reject.setStyleSheet(btn_style)
        
        approve_style = f"background-color: {self.theme.get('success', '#00cc66')}; border: none; padding: 8px; border-radius: 4px; font-weight: bold; color: white;"
        self.btn_approve.setStyleSheet(approve_style)
        
        self.btn_raw.setStyleSheet(f"background: transparent; color: {self.theme.get('text_muted', '#aaa')}; text-align: left; border: none;")
        self.raw_view.setStyleSheet(f"background-color: rgba(0,0,0,0.2); border: 1px solid {self.theme.get('border', '#444')}; color: {self.theme.get('text_muted', '#aaa')};")