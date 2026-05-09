# gui/docks/unified_research/components/context_filter_dialog.py
import os
import json
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, 
                             QLabel, QListWidget, QListWidgetItem, QRadioButton, QFrame, QCheckBox)
from PySide6.QtCore import Qt

class ContextFilterDialog(QDialog):
    def __init__(self, project_manager, current_docs, current_tags, current_logic, theme, parent=None):
        super().__init__(parent)
        self.setWindowTitle("⚙️ Global AI Engine Settings")
        self.setMinimumSize(450, 600)
        self.pm = project_manager
        
        # Load Universal Settings from Project Metadata
        raw_settings = self.pm.get_metadata("global_ai_settings", "{}")
        self.global_settings = json.loads(raw_settings) if raw_settings else {
            "rag_enabled": True,
            "output_workspace": False,
            "search_analyses": False
        }
        
        self.selected_docs = current_docs
        self.selected_tags = current_tags
        self.tag_logic = current_logic 
        
        self._build_ui(theme)
        
    def _build_ui(self, theme):
        layout = QVBoxLayout(self)
        
        # --- UNIVERSAL ENGINE TOGGLES (NEW) ---
        layout.addWidget(QLabel("<b>🚀 Universal Engine Toggles:</b>"))
        
        self.chk_rag = QCheckBox("Enable RAG (Search Documents before answering)")
        self.chk_rag.setChecked(self.global_settings.get("rag_enabled", True))
        
        self.chk_workspace = QCheckBox("Output to Workspace Graph (AI builds nodes automatically)")
        self.chk_workspace.setChecked(self.global_settings.get("output_workspace", False))
        
        self.chk_analyses = QCheckBox("Search Saved Document Analyses")
        self.chk_analyses.setChecked(self.global_settings.get("search_analyses", False))
        
        layout.addWidget(self.chk_rag)
        layout.addWidget(self.chk_workspace)
        layout.addWidget(self.chk_analyses)
        
        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(sep1)

        # --- Documents Section ---
        layout.addWidget(QLabel("<b>📄 Target Documents:</b> (Searches across ANY selected)"))
        self.list_docs = QListWidget()
        layout.addWidget(self.list_docs)
        
        for path in self.pm.pdfs:
            doc_name = os.path.basename(path)
            item = QListWidgetItem(doc_name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if doc_name in self.selected_docs or not self.selected_docs else Qt.CheckState.Unchecked)
            self.list_docs.addItem(item)

        btn_all_docs = QPushButton("Select All")
        btn_all_docs.clicked.connect(lambda: self._set_all(self.list_docs, Qt.CheckState.Checked))
        layout.addWidget(btn_all_docs)
        
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(sep2)

        # --- Tags Section ---
        layout.addWidget(QLabel("<b>🏷️ Target Tags:</b>"))
        
        logic_layout = QHBoxLayout()
        self.btn_and = QRadioButton("Match ALL selected tags (AND)")
        self.btn_or = QRadioButton("Match ANY selected tag (OR)")
        if self.tag_logic == "OR": self.btn_or.setChecked(True)
        else: self.btn_and.setChecked(True)
        
        logic_layout.addWidget(self.btn_and)
        logic_layout.addWidget(self.btn_or)
        layout.addLayout(logic_layout)

        self.list_tags = QListWidget()
        layout.addWidget(self.list_tags)
        
        for tag in self.pm.get_all_tags():
            tag_name = tag.get("name")
            item = QListWidgetItem(tag_name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if tag_name in self.selected_tags else Qt.CheckState.Unchecked)
            self.list_tags.addItem(item)
            
        btn_clear_tags = QPushButton("Clear Tags")
        btn_clear_tags.clicked.connect(lambda: self._set_all(self.list_tags, Qt.CheckState.Unchecked))
        layout.addWidget(btn_clear_tags)
        
        # --- Save/Cancel ---
        btn_layout = QHBoxLayout()
        btn_save = QPushButton("Save Settings")
        btn_save.clicked.connect(self._save_and_accept)
        btn_layout.addStretch()
        btn_layout.addWidget(btn_save)
        layout.addLayout(btn_layout)

        if theme:
            self.setStyleSheet(f"background-color: {theme.get('bg_main', '#1e1e1e')}; color: {theme.get('text_main', '#fff')};")
            self.list_docs.setStyleSheet(f"background-color: {theme.get('bg_input', '#2b2b2b')}; border: 1px solid {theme.get('border', '#444')};")
            self.list_tags.setStyleSheet(f"background-color: {theme.get('bg_input', '#2b2b2b')}; border: 1px solid {theme.get('border', '#444')};")
            btn_save.setStyleSheet(f"background-color: {theme.get('accent', '#b366ff')}; color: white; font-weight: bold; padding: 6px; border-radius: 4px;")

    def _set_all(self, list_widget, state):
        for i in range(list_widget.count()):
            list_widget.item(i).setCheckState(state)

    def _save_and_accept(self):
        # Save universal settings directly to project state
        new_settings = {
            "rag_enabled": self.chk_rag.isChecked(),
            "output_workspace": self.chk_workspace.isChecked(),
            "search_analyses": self.chk_analyses.isChecked()
        }
        self.pm.set_metadata("global_ai_settings", json.dumps(new_settings))
        self.accept()

    def get_results(self):
        docs = [self.list_docs.item(i).text() for i in range(self.list_docs.count()) if self.list_docs.item(i).checkState() == Qt.CheckState.Checked]
        tags = [self.list_tags.item(i).text() for i in range(self.list_tags.count()) if self.list_tags.item(i).checkState() == Qt.CheckState.Checked]
        logic = "OR" if self.btn_or.isChecked() else "AND"
        return docs, tags, logic