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
        self.setMinimumSize(450, 700)
        self.pm = project_manager
        
        # Load Universal Settings from Project Metadata
        raw_settings = self.pm.get_metadata("global_ai_settings", "{}")
        self.global_settings = json.loads(raw_settings) if raw_settings else {
            "autopilot_enabled": False,
            "rag_enabled": True,
            "output_workspace": False,
            "search_analyses": False,
            "include_manifest": True,
            "allow_manifest_updates": True,
            "include_selected_nodes": False
        }
        
        self.selected_docs = current_docs
        self.selected_tags = current_tags
        self.tag_logic = current_logic 
        
        self._build_ui(theme)
        self._on_autopilot_toggled(self.chk_autopilot.isChecked())
        
    def _build_ui(self, theme):
        layout = QVBoxLayout(self)
        
        # --- NEW: MASTER AUTOPILOT ---
        self.chk_autopilot = QCheckBox("🤖 Global Auto-Pilot (AI determines context & tools dynamically)")
        self.chk_autopilot.setChecked(self.global_settings.get("autopilot_enabled", False))
        self.chk_autopilot.setStyleSheet(f"font-weight: bold; color: {theme.get('accent', '#b366ff') if theme else '#b366ff'}; padding: 4px;")
        self.chk_autopilot.toggled.connect(self._on_autopilot_toggled)
        layout.addWidget(self.chk_autopilot)
        
        sep0 = QFrame()
        sep0.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(sep0)

        # --- UNIVERSAL ENGINE TOGGLES ---
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

        # --- UNIVERSAL VARIABLES & MANIFEST PERMISSIONS ---
        layout.addWidget(QLabel("<b>🗂️ Universal Context & Permissions:</b>"))
        
        self.chk_include_manifest = QCheckBox("Include Project Manifest in LLM Context")
        self.chk_include_manifest.setChecked(self.global_settings.get("include_manifest", True))
        
        self.chk_allow_manifest_updates = QCheckBox("Allow Agent to Edit/Expand Project Manifest")
        self.chk_allow_manifest_updates.setChecked(self.global_settings.get("allow_manifest_updates", True))
        
        self.chk_include_selected_nodes = QCheckBox("Pass Currently Selected Workspace Graph Nodes")
        self.chk_include_selected_nodes.setChecked(self.global_settings.get("include_selected_nodes", False))
        
        layout.addWidget(self.chk_include_manifest)
        layout.addWidget(self.chk_allow_manifest_updates)
        layout.addWidget(self.chk_include_selected_nodes)
        
        sep_vars = QFrame()
        sep_vars.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(sep_vars)

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

    def _on_autopilot_toggled(self, checked):
        """Disables and unchecks manual options to visually indicate AI control."""
        targets = [
            self.chk_rag, self.chk_workspace, self.chk_analyses,
            self.chk_include_manifest, self.chk_allow_manifest_updates, self.chk_include_selected_nodes
        ]
        for chk in targets:
            chk.setEnabled(not checked)
            if checked:
                chk.setChecked(False)

    def _set_all(self, list_widget, state):
        for i in range(list_widget.count()):
            list_widget.item(i).setCheckState(state)

    def _save_and_accept(self):
        new_settings = {
            "autopilot_enabled": self.chk_autopilot.isChecked(),
            "rag_enabled": self.chk_rag.isChecked(),
            "output_workspace": self.chk_workspace.isChecked(),
            "search_analyses": self.chk_analyses.isChecked(),
            "include_manifest": self.chk_include_manifest.isChecked(),
            "allow_manifest_updates": self.chk_allow_manifest_updates.isChecked(),
            "include_selected_nodes": self.chk_include_selected_nodes.isChecked()
        }
        self.pm.set_metadata("global_ai_settings", json.dumps(new_settings))
        self.accept()

    def get_results(self):
        docs = [self.list_docs.item(i).text() for i in range(self.list_docs.count()) if self.list_docs.item(i).checkState() == Qt.CheckState.Checked]
        tags = [self.list_tags.item(i).text() for i in range(self.list_tags.count()) if self.list_tags.item(i).checkState() == Qt.CheckState.Checked]
        logic = "OR" if self.btn_or.isChecked() else "AND"
        return docs, tags, logic