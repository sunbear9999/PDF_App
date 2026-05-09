# gui/docks/anaylsis_tab.py
import json
import os
import fitz
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, 
                             QComboBox, QFrame, QScrollArea, QSizePolicy)
from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor

from core.engine.action_model import AIActionBlueprint, ActionStep
from gui.docks.unified_research.components.template_editor import TemplateEditorDialog

class InteractiveListButton(QPushButton):
    """A beautiful button styled like a tag that triggers RAG searches when clicked."""
    def __init__(self, text, theme, parent=None):
        super().__init__(text, parent)
        self.setStyleSheet(f"""
            QPushButton {{
                background-color: {theme.get('bg_panel', '#333')};
                color: {theme.get('accent', '#b366ff')};
                border: 1px solid {theme.get('border', '#444')};
                border-radius: 6px; padding: 6px 10px; text-align: left; font-weight: bold;
            }}
            QPushButton:hover {{ background-color: {theme.get('accent', '#b366ff')}; color: white; }}
        """)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

class InteractiveAnalysisViewer(QWidget):
    """Dynamically renders JSON dictionaries into an interactive GUI layout."""
    def __init__(self, json_data, page_range, annot_manager, theme, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 16)
        
        lbl_header = QLabel(f"<b>Section: {page_range}</b>")
        lbl_header.setStyleSheet(f"color: {theme.get('accent', '#b366ff')}; font-size: 16px; border-bottom: 1px solid {theme.get('border', '#444')}; padding-bottom: 4px;")
        layout.addWidget(lbl_header)

        for key, value in json_data.items():
            if not value: continue
            
            friendly_title = key.replace("_", " ").title()
            lbl_title = QLabel(f"<b>{friendly_title}</b>")
            lbl_title.setStyleSheet(f"color: {theme.get('text_main', '#fff')}; margin-top: 8px;")
            layout.addWidget(lbl_title)

            if isinstance(value, str):
                lbl_text = QLabel(value)
                lbl_text.setWordWrap(True)
                lbl_text.setStyleSheet(f"color: {theme.get('text_muted', '#aaa')};")
                layout.addWidget(lbl_text)
                
            elif isinstance(value, list) and all(isinstance(i, str) for i in value):
                for term in value:
                    btn = InteractiveListButton(f"🔍 {term}", theme)
                    btn.clicked.connect(lambda _, t=term: annot_manager.trigger_similar_context(t))
                    layout.addWidget(btn)
                
            elif isinstance(value, list) and all(isinstance(i, dict) for i in value):
                for item_dict in value:
                    keys = list(item_dict.keys())
                    if not keys: continue
                    
                    main_val = item_dict[keys[0]]
                    btn = InteractiveListButton(f"📌 {main_val}", theme)
                    btn.clicked.connect(lambda _, t=main_val: annot_manager.trigger_similar_context(str(t)))
                    layout.addWidget(btn)
                    
                    for sub_key in keys[1:]:
                        sub_lbl = QLabel(f"<i>{item_dict[sub_key]}</i>")
                        sub_lbl.setWordWrap(True)
                        sub_lbl.setStyleSheet(f"color: {theme.get('text_muted', '#aaa')}; padding-left: 16px; margin-bottom: 4px; border-left: 2px solid {theme.get('border', '#444')};")
                        layout.addWidget(sub_lbl)

class AnalysisTab(QWidget):
    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.theme = self.main_window.theme_manager.get_theme() if hasattr(main_window, 'theme_manager') else {}
        self.pm = main_window.project_manager
        self.templates = self.pm.get_analysis_templates()
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        ctrl_layout = QHBoxLayout()
        self.doc_selector = QComboBox()
        self._populate_docs()
        self.doc_selector.currentIndexChanged.connect(self._load_existing_analysis)
        ctrl_layout.addWidget(QLabel("<b>Doc:</b>"))
        ctrl_layout.addWidget(self.doc_selector, 1)

        self.combo_templates = QComboBox()
        self._populate_templates()
        self.combo_templates.currentIndexChanged.connect(self._load_existing_analysis)
        ctrl_layout.addWidget(QLabel("<b>Mode:</b>"))
        ctrl_layout.addWidget(self.combo_templates, 1)

        self.btn_edit = QPushButton("⚙️ Edit")
        self.btn_edit.clicked.connect(self._open_editor)
        ctrl_layout.addWidget(self.btn_edit)
        layout.addLayout(ctrl_layout)

        act_layout = QHBoxLayout()
        self.btn_run = QPushButton("🚀 Run Document Analysis")
        self.btn_run.setFixedHeight(35)
        self.btn_run.clicked.connect(self._trigger_analysis)
        
        self.btn_master_outline = QPushButton("📄 Master Outline")
        self.btn_master_outline.setFixedHeight(35)
        self.btn_master_outline.clicked.connect(self._generate_master_outline)
        
        act_layout.addWidget(self.btn_run)
        act_layout.addWidget(self.btn_master_outline)
        layout.addLayout(act_layout)

        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet(f"color: {self.theme.get('accent', '#b366ff')}; font-weight: bold;")
        layout.addWidget(self.status_lbl)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.results_container = QWidget()
        self.results_layout = QVBoxLayout(self.results_container)
        self.results_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll_area.setWidget(self.results_container)
        layout.addWidget(self.scroll_area, 1)

    def _populate_docs(self):
        self.doc_selector.clear()
        for pdf in self.pm.pdfs:
            self.doc_selector.addItem(os.path.basename(pdf), pdf)

    def _populate_templates(self):
        self.combo_templates.clear()
        self.templates = self.pm.get_analysis_templates()
        for t in self.templates:
            self.combo_templates.addItem(t.get("title", "Unnamed Mode"), t)

    def _open_editor(self):
        dlg = TemplateEditorDialog(self.pm, self.theme, self)
        if dlg.exec(): self._populate_templates()

    def _clear_results(self):
        while self.results_layout.count():
            item = self.results_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()

    def _load_existing_analysis(self):
        if self.doc_selector.count() == 0 or self.combo_templates.count() == 0: return
        self._clear_results()
        
        doc_path = self.doc_selector.currentData()
        template = self.combo_templates.currentData()
        records = self.pm.get_document_analyses(doc_path, template['id'])
        
        if not records:
            self.status_lbl.setText("No analysis found. Click 'Run' to generate.")
            return
            
        self.status_lbl.setText(f"Loaded existing analysis ({len(records)} sections).")
        for rec in records:
            try:
                data = json.loads(rec["json_data"])
                viewer = InteractiveAnalysisViewer(data, f"Section {rec['chunk_index']+1}", self.main_window.viewer.annot_manager, self.theme)
                self.results_layout.addWidget(viewer)
            except: pass

    def render_and_save_chunk(self, doc_path, template_id, chunk_idx, json_data, page_range):
        """Called by ui_router.py on the Main Thread when a chunk finishes."""
        # 1. Render UI
        viewer = InteractiveAnalysisViewer(json_data, page_range, self.main_window.viewer.annot_manager, self.theme)
        self.results_layout.addWidget(viewer)
        self.status_lbl.setText(f"✅ Analyzed Section: {page_range}")
        
        # 2. Safely save to DB (Main Thread = No SQLite Errors!)
        json_str = json.dumps(json_data)
        self.pm.save_document_analysis(doc_path, template_id, chunk_idx, json_str)

    def _trigger_analysis(self):
        if self.combo_templates.count() == 0 or self.doc_selector.count() == 0: return
        self._clear_results()
        
        template = self.combo_templates.currentData()
        doc_path = self.doc_selector.currentData()
        
        chunks = []
        try:
            import fitz
            doc = fitz.open(doc_path)
            current_text = ""
            start_page = 1
            for page_num in range(len(doc)):
                current_text += doc.load_page(page_num).get_text("text") + "\n"
                if len(current_text.split()) >= 2000 or page_num == len(doc) - 1:
                    end_page = page_num + 1
                    chunks.append({
                        "text": current_text,
                        "page_range": f"p.{start_page}" if start_page == end_page else f"p.{start_page}-{end_page}",
                        "chunk_index": len(chunks),
                        "doc_path": doc_path,
                        "template_id": template['id'],
                        "template_instructions": template.get('instructions', ''), # Inject variables for safe_format
                        "template_schema": template.get('schema', '{}')
                    })
                    current_text = ""
                    start_page = page_num + 2
            doc.close()
        except Exception as e:
            self.status_lbl.setText(f"Error reading PDF: {e}")
            return
            
        self.pm.clear_document_analyses(doc_path, template['id'])
        self.status_lbl.setText("⏳ Processing Document...")

        # 100% Decoupled
        from core.engine.default_blueprints import DefaultBlueprints
        blueprint = DefaultBlueprints.get_analysis_blueprint(chunks)
        self.main_window.execute_ai_blueprint(blueprint, {})

    def _generate_master_outline(self):
        doc_path = self.doc_selector.currentData()
        template = self.combo_templates.currentData()
        records = self.pm.get_document_analyses(doc_path, template['id'])
        
        if not records:
            self.status_lbl.setText("Run Analysis first before generating Master Outline.")
            return
            
        combined_text = "\n\n".join([f"--- Section {r['chunk_index']} ---\n{r['json_data']}" for r in records])
        
        # 100% Decoupled
        from core.engine.default_blueprints import DefaultBlueprints
        blueprint = DefaultBlueprints.get_master_outline_blueprint(os.path.basename(doc_path))
        self.main_window.execute_ai_blueprint(blueprint, {"combined_text": combined_text})

    def update_theme(self, theme):
        self.theme = theme
        self.setStyleSheet(f"background-color: {theme.get('bg_main', '#1e1e1e')}; color: {theme.get('text_main', '#fff')};")
        self.doc_selector.setStyleSheet(f"background-color: {theme.get('bg_input', '#2b2b2b')}; border: 1px solid {theme.get('border', '#444')}; padding: 4px; border-radius: 4px;")
        self.combo_templates.setStyleSheet(f"background-color: {theme.get('bg_input', '#2b2b2b')}; border: 1px solid {theme.get('border', '#444')}; padding: 4px; border-radius: 4px;")
        self.btn_run.setStyleSheet(f"background-color: {theme.get('accent', '#b366ff')}; font-weight: bold; color: white; border: none; border-radius: 4px;")
        btn_style = f"background-color: {theme.get('bg_panel', '#333')}; color: {theme.get('text_main', '#fff')}; border: 1px solid {theme.get('border', '#444')}; padding: 4px; border-radius: 4px;"
        self.btn_edit.setStyleSheet(btn_style)
        self.btn_master_outline.setStyleSheet(btn_style)
        self.scroll_area.setStyleSheet("background: transparent; border: none;")
        self.results_container.setStyleSheet("background: transparent;")