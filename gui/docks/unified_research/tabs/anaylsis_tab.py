import json
import os
import fitz
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, 
                             QComboBox, QFrame, QScrollArea, QSizePolicy, QTabWidget, QSplitter, QTextEdit)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCursor

from core.engine.action_model import AIActionBlueprint, ActionStep
from core.engine.master_runner import MasterActionRunner
from gui.docks.unified_research.components.template_editor import TemplateEditorDialog
from gui.docks.unified_research.components.chat_streamer import ChatMessageWidget

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

class CollapsibleSection(QWidget):
    """A smooth collapsible widget for sections and categories."""
    def __init__(self, title, content_widget, theme, is_expanded=True, parent=None):
        super().__init__(parent)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 8)
        self.layout.setSpacing(0)
        
        self.btn_toggle = QPushButton(f"▼ {title}" if is_expanded else f"▶ {title}")
        self.btn_toggle.setCheckable(True)
        self.btn_toggle.setChecked(not is_expanded)
        self.btn_toggle.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_toggle.setStyleSheet(f"""
            QPushButton {{
                background-color: {theme.get('bg_panel', '#333')}; color: {theme.get('accent', '#b366ff')};
                border: 1px solid {theme.get('border', '#444')}; border-radius: 6px;
                padding: 8px; font-weight: bold; text-align: left; font-size: 14px;
            }}
            QPushButton:hover {{ background-color: {theme.get('bg_input', '#2b2b2b')}; }}
        """)
        self.btn_toggle.clicked.connect(self._toggle)
        
        self.content_widget = content_widget
        self.content_widget.setVisible(is_expanded)
        
        # Wrap content in a frame with a left border to show hierarchy
        self.content_frame = QFrame()
        self.content_frame.setStyleSheet(f"QFrame {{ border-left: 2px solid {theme.get('border', '#444')}; margin-left: 8px; padding-left: 8px; }}")
        cf_layout = QVBoxLayout(self.content_frame)
        cf_layout.setContentsMargins(0, 8, 0, 8)
        cf_layout.addWidget(self.content_widget)
        self.content_frame.setVisible(is_expanded)
        
        self.layout.addWidget(self.btn_toggle)
        self.layout.addWidget(self.content_frame)

    def _toggle(self):
        is_collapsed = self.btn_toggle.isChecked()
        self.content_frame.setVisible(not is_collapsed)
        self.btn_toggle.setText(self.btn_toggle.text().replace("▼" if is_collapsed else "▶", "▶" if is_collapsed else "▼"))


class InteractiveAnalysisViewer(QWidget):
    """Dynamically renders JSON dictionaries into an interactive GUI layout."""
    def __init__(self, json_data, annot_manager, theme, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        for key, value in json_data.items():
            if not value: continue
            
            friendly_title = key.replace("_", " ").title()
            lbl_title = QLabel(f"<b>{friendly_title}</b>")
            lbl_title.setStyleSheet(f"color: {theme.get('text_main', '#fff')}; margin-top: 4px;")
            layout.addWidget(lbl_title)

            if isinstance(value, str):
                lbl_text = QLabel(value)
                lbl_text.setWordWrap(True)
                lbl_text.setStyleSheet(f"color: {theme.get('text_muted', '#aaa')};")
                layout.addWidget(lbl_text)
                
            elif isinstance(value, list) and all(isinstance(i, str) for i in value):
                for term in value:
                    btn = InteractiveListButton(f"🔍 {term}", theme)
                    if annot_manager: btn.clicked.connect(lambda _, t=term: annot_manager.trigger_similar_context(t))
                    layout.addWidget(btn)
                
            elif isinstance(value, list) and all(isinstance(i, dict) for i in value):
                for item_dict in value:
                    keys = list(item_dict.keys())
                    if not keys: continue
                    
                    main_val = item_dict[keys[0]]
                    btn = InteractiveListButton(f"📌 {main_val}", theme)
                    if annot_manager: btn.clicked.connect(lambda _, t=main_val: annot_manager.trigger_similar_context(str(t)))
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
        
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(f"QTabWidget::pane {{ border: 1px solid {self.theme.get('border', '#444')}; background-color: transparent; }} QTabBar::tab {{ background: {self.theme.get('bg_panel', '#333')}; color: white; padding: 8px 16px; border-top-left-radius: 4px; border-top-right-radius: 4px; margin-right: 2px; }} QTabBar::tab:selected {{ background: {self.theme.get('accent', '#b366ff')}; }}")
        
        # --- TAB 1: Document Analysis ---
        self.doc_tab = QWidget()
        self._build_doc_tab(self.doc_tab)
        self.tabs.addTab(self.doc_tab, "📄 Document Analysis")
        
        # --- TAB 2: Compare Outlines ---
        self.compare_tab = QWidget()
        self._build_compare_tab(self.compare_tab)
        self.tabs.addTab(self.compare_tab, "⚖️ Compare Outlines")
        
        layout.addWidget(self.tabs)
        
        # --- THE FIX: Populate data ONLY AFTER both tabs are fully built ---
        self._populate_docs()
        self._populate_templates()
        
        self.update_theme(self.theme)

    def _build_doc_tab(self, parent_widget):
        layout = QVBoxLayout(parent_widget)
        
        ctrl_layout = QHBoxLayout()
        self.doc_selector = QComboBox()
        # [REMOVED _populate_docs() FROM HERE]
        self.doc_selector.currentIndexChanged.connect(self._load_existing_analysis)
        ctrl_layout.addWidget(QLabel("<b>Doc:</b>"))
        ctrl_layout.addWidget(self.doc_selector, 1)

        self.combo_templates = QComboBox()
        # [REMOVED _populate_templates() FROM HERE]
        self.combo_templates.currentIndexChanged.connect(self._load_existing_analysis)
        ctrl_layout.addWidget(QLabel("<b>Mode:</b>"))
        ctrl_layout.addWidget(self.combo_templates, 1)

        self.btn_edit = QPushButton("⚙️ Edit Templates")
        self.btn_edit.clicked.connect(self._open_editor)
        ctrl_layout.addWidget(self.btn_edit)
        layout.addLayout(ctrl_layout)

        act_layout = QHBoxLayout()
        self.btn_run = QPushButton("🚀 Run Document Analysis")
        self.btn_run.setFixedHeight(35)
        self.btn_run.clicked.connect(self._trigger_analysis)
        
        # Segmented Control for View Mode
        self.btn_view_sections = QPushButton("📑 Section-by-Section")
        self.btn_view_sections.setCheckable(True)
        self.btn_view_sections.setChecked(True)
        self.btn_view_sections.clicked.connect(self._load_existing_analysis)
        
        self.btn_view_master = QPushButton("👑 Master Outline")
        self.btn_view_master.setCheckable(True)
        self.btn_view_master.clicked.connect(self._load_existing_analysis)
        
        self.view_group = [self.btn_view_sections, self.btn_view_master]
        for btn in self.view_group: btn.clicked.connect(lambda _, b=btn: self._sync_view_toggle(b))
        
        act_layout.addWidget(self.btn_run)
        act_layout.addStretch()
        act_layout.addWidget(self.btn_view_sections)
        act_layout.addWidget(self.btn_view_master)
        layout.addLayout(act_layout)

        self.status_lbl = QLabel("")
        layout.addWidget(self.status_lbl)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.results_container = QWidget()
        self.results_layout = QVBoxLayout(self.results_container)
        self.results_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll_area.setWidget(self.results_container)
        layout.addWidget(self.scroll_area, 1)

    def _sync_view_toggle(self, active_btn):
        for btn in self.view_group: btn.setChecked(btn == active_btn)

    def _build_compare_tab(self, parent_widget):
        layout = QVBoxLayout(parent_widget)
        
        sel_layout = QHBoxLayout()
        self.cmp_doc_a = QComboBox()
        self.cmp_doc_b = QComboBox()
        self.cmp_template = QComboBox()
        
        sel_layout.addWidget(QLabel("<b>Doc A:</b>"))
        sel_layout.addWidget(self.cmp_doc_a, 1)
        sel_layout.addWidget(QLabel("<b>Doc B:</b>"))
        sel_layout.addWidget(self.cmp_doc_b, 1)
        sel_layout.addWidget(QLabel("<b>Template:</b>"))
        sel_layout.addWidget(self.cmp_template, 1)
        
        btn_load_cmp = QPushButton("Load Outlines")
        btn_load_cmp.clicked.connect(self._load_comparison)
        sel_layout.addWidget(btn_load_cmp)
        layout.addLayout(sel_layout)
        
        self.cmp_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.cmp_view_a = QScrollArea()
        self.cmp_view_a.setWidgetResizable(True)
        self.cmp_view_a.setMinimumWidth(50) # THE FIX: Allow squishing
        
        self.cmp_view_b = QScrollArea()
        self.cmp_view_b.setWidgetResizable(True)
        self.cmp_view_b.setMinimumWidth(50) # THE FIX: Allow squishing
        
        self.cont_a = QWidget(); self.lyt_a = QVBoxLayout(self.cont_a); self.cont_a.setLayout(self.lyt_a); self.lyt_a.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.cont_b = QWidget(); self.lyt_b = QVBoxLayout(self.cont_b); self.cont_b.setLayout(self.lyt_b); self.lyt_b.setAlignment(Qt.AlignmentFlag.AlignTop)
        
        self.cmp_view_a.setWidget(self.cont_a)
        self.cmp_view_b.setWidget(self.cont_b)
        self.cmp_splitter.addWidget(self.cmp_view_a)
        self.cmp_splitter.addWidget(self.cmp_view_b)
        layout.addWidget(self.cmp_splitter, 1)
        
        # Compare Chat Bar
        chat_frame = QFrame()
        chat_frame.setMinimumWidth(50) # THE FIX: Allow squishing
        chat_lyt = QVBoxLayout(chat_frame)
        self.cmp_chat_output = QScrollArea()
        self.cmp_chat_output.setWidgetResizable(True)
        self.cmp_chat_cont = QWidget()
        self.cmp_chat_lyt = QVBoxLayout(self.cmp_chat_cont)
        self.cmp_chat_lyt.setAlignment(Qt.AlignmentFlag.AlignBottom)
        self.cmp_chat_output.setWidget(self.cmp_chat_cont)
        
        input_lyt = QHBoxLayout()
        self.cmp_input = QTextEdit()
        self.cmp_input.setMaximumHeight(50)
        self.cmp_input.setPlaceholderText("Ask the AI to compare these two outlines...")
        btn_send_cmp = QPushButton("Ask AI")
        btn_send_cmp.clicked.connect(self._send_compare_chat)
        
        input_lyt.addWidget(self.cmp_input)
        input_lyt.addWidget(btn_send_cmp)
        
        chat_lyt.addWidget(self.cmp_chat_output)
        chat_lyt.addLayout(input_lyt)
        self.cmp_splitter.addWidget(chat_frame)
        
        # THE FIX: Use equal relative proportions rather than absolute pixel minimums
        self.cmp_splitter.setSizes([100, 100, 100])

    def _populate_docs(self):
        self.doc_selector.clear()
        self.cmp_doc_a.clear()
        self.cmp_doc_b.clear()
        for pdf in self.pm.pdfs:
            base = os.path.basename(pdf)
            self.doc_selector.addItem(base, pdf)
            self.cmp_doc_a.addItem(base, pdf)
            self.cmp_doc_b.addItem(base, pdf)

    def _populate_templates(self):
        self.combo_templates.clear()
        self.cmp_template.clear()
        self.templates = self.pm.get_analysis_templates()
        for t in self.templates:
            self.combo_templates.addItem(t.get("title", "Unnamed Mode"), t)
            self.cmp_template.addItem(t.get("title", "Unnamed Mode"), t)

    def _open_editor(self):
        dlg = TemplateEditorDialog(self.pm, self.theme, self)
        if dlg.exec(): self._populate_templates()

    def _clear_results(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()

    # --- THE LOGIC-BASED MASTER OUTLINE ---
    def _build_master_outline_dict(self, records):
        """Aggregates and deduplicates data across all chunks purely using Python logic."""
        master_data = {}
        for rec in records:
            try:
                data = json.loads(rec["json_data"])
                page_ref = rec.get("page_range", "Unknown")
                
                for key, val in data.items():
                    if not val: continue
                    if key not in master_data:
                        master_data[key] = [] if isinstance(val, list) else ""
                    
                    if isinstance(val, list):
                        for item in val:
                            if isinstance(item, dict):
                                # Convert dict to a unique string signature to prevent duplicates
                                sig = json.dumps(item, sort_keys=True)
                                if not any(json.dumps(existing, sort_keys=True) == sig for existing in master_data[key]):
                                    master_data[key].append(item)
                            elif isinstance(item, str):
                                if item not in master_data[key]:
                                    master_data[key].append(item)
                    elif isinstance(val, str):
                        # For strings (like section summaries), append chronologically
                        master_data[key] += f"<b>[{page_ref}]</b> {val}<br><br>"
            except Exception as e: print(f"Master Outline Error: {e}")
            
        return master_data

    def _load_existing_analysis(self):
        if self.doc_selector.count() == 0 or self.combo_templates.count() == 0: return
        self._clear_results(self.results_layout)
        
        doc_path = self.doc_selector.currentData()
        template = self.combo_templates.currentData()
        records = self.pm.get_document_analyses(doc_path, template['id'])
        
        if not records:
            self.status_lbl.setText("No analysis found. Click 'Run' to generate.")
            return
            
        is_master_mode = self.btn_view_master.isChecked()
        self.status_lbl.setText(f"Loaded {'Master Outline' if is_master_mode else 'Section View'} ({len(records)} sections found).")
        
        if is_master_mode:
            master_data = self._build_master_outline_dict(records)
            viewer = InteractiveAnalysisViewer(master_data, self.main_window.viewer.annot_manager, self.theme)
            self.results_layout.addWidget(viewer)
        else:
            for rec in records:
                try:
                    data = json.loads(rec["json_data"])
                    viewer = InteractiveAnalysisViewer(data, self.main_window.viewer.annot_manager, self.theme)
                    title = f"Section: {rec['page_range']}"
                    # Wrap in Collapsible
                    col_sec = CollapsibleSection(title, viewer, self.theme, is_expanded=False)
                    self.results_layout.addWidget(col_sec)
                except: pass

    def _load_comparison(self):
        self._clear_results(self.lyt_a)
        self._clear_results(self.lyt_b)
        
        path_a = self.cmp_doc_a.currentData()
        path_b = self.cmp_doc_b.currentData()
        tmpl = self.cmp_template.currentData()
        
        if not path_a or not path_b or not tmpl: return
        
        recs_a = self.pm.get_document_analyses(path_a, tmpl['id'])
        recs_b = self.pm.get_document_analyses(path_b, tmpl['id'])
        
        master_a = self._build_master_outline_dict(recs_a)
        master_b = self._build_master_outline_dict(recs_b)
        
        self.lyt_a.addWidget(InteractiveAnalysisViewer(master_a, None, self.theme))
        self.lyt_b.addWidget(InteractiveAnalysisViewer(master_b, None, self.theme))
        
        # Save to class state so the chat can use it
        self.current_cmp_data_a = json.dumps(master_a, indent=2)
        self.current_cmp_data_b = json.dumps(master_b, indent=2)

    def _send_compare_chat(self):
        text = self.cmp_input.toPlainText().strip()
        if not text or not hasattr(self, 'current_cmp_data_a'): return
        self.cmp_input.clear()
        
        user_msg = ChatMessageWidget("You", theme=self.theme, is_user=True)
        user_msg.append_chunk(text)
        self.cmp_chat_lyt.addWidget(user_msg)
        
        ai_msg = ChatMessageWidget("Comparison Agent", theme=self.theme)
        self.cmp_chat_lyt.addWidget(ai_msg)
        QTimer.singleShot(50, lambda: self.cmp_chat_output.verticalScrollBar().setValue(self.cmp_chat_output.verticalScrollBar().maximum()))
        
        bp = AIActionBlueprint(name="Compare Outlines", description="", steps=[
            ActionStep(
                step_id="compare", step_type="LLM_QUERY",
                system_prompt="You are an expert analyst. Compare the two provided document outlines to answer the user's question.",
                inputs={"query": f"USER QUESTION: {text}\n\n--- DOCUMENT A OUTLINE ---\n{{doc_a}}\n\n--- DOCUMENT B OUTLINE ---\n{{doc_b}}"},
                ui_format="silent" # We route it manually via signals below
            )
        ])
        
        state = {"doc_a": self.current_cmp_data_a, "doc_b": self.current_cmp_data_b}
        self.cmp_runner = MasterActionRunner(self.main_window, bp, state)
        self.cmp_runner.progress_update.connect(ai_msg.append_chunk)
        self.cmp_runner.start()

    def render_and_save_chunk(self, doc_path, template_id, chunk_idx, json_data, page_range):
        """Called by ui_router.py on the Main Thread when a chunk finishes."""
        if not self.btn_view_master.isChecked():
            viewer = InteractiveAnalysisViewer(json_data, self.main_window.viewer.annot_manager, self.theme)
            col_sec = CollapsibleSection(f"Section: {page_range}", viewer, self.theme, is_expanded=True)
            self.results_layout.addWidget(col_sec)
            
        self.status_lbl.setText(f"✅ Analyzed Section: {page_range}")
        
        json_str = json.dumps(json_data)
        self.pm.save_document_analysis(doc_path, template_id, chunk_idx, json_str)
        
        # Auto-refresh if we are in Master Outline mode
        if self.btn_view_master.isChecked():
            self._load_existing_analysis()

    def _trigger_analysis(self):
        if self.combo_templates.count() == 0 or self.doc_selector.count() == 0: return
        self._clear_results(self.results_layout)
        
        template = self.combo_templates.currentData()
        doc_path = self.doc_selector.currentData()
        
        chunks = []
        try:
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
                        "template_instructions": template.get('instructions', ''),
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

        from core.engine.default_blueprints import DefaultBlueprints
        blueprint = DefaultBlueprints.get_analysis_blueprint(chunks)
        self.main_window.execute_ai_blueprint(blueprint, {})

    def update_theme(self, theme):
        self.theme = theme
        self.setStyleSheet(f"background-color: {theme.get('bg_main', '#1e1e1e')}; color: {theme.get('text_main', '#fff')};")
        
        style = f"background-color: {theme.get('bg_input', '#2b2b2b')}; border: 1px solid {theme.get('border', '#444')}; padding: 4px; border-radius: 4px;"
        self.doc_selector.setStyleSheet(style)
        self.combo_templates.setStyleSheet(style)
        self.cmp_doc_a.setStyleSheet(style)
        self.cmp_doc_b.setStyleSheet(style)
        self.cmp_template.setStyleSheet(style)
        self.cmp_input.setStyleSheet(style)
        
        self.btn_run.setStyleSheet(f"background-color: {theme.get('accent', '#b366ff')}; font-weight: bold; color: white; border: none; border-radius: 4px;")
        
        btn_style = f"background-color: {theme.get('bg_panel', '#333')}; color: {theme.get('text_main', '#fff')}; border: 1px solid {theme.get('border', '#444')}; padding: 4px; border-radius: 4px;"
        self.btn_edit.setStyleSheet(btn_style)
        
        toggle_style = f"""
            QPushButton {{ background-color: {theme.get('bg_panel', '#333')}; color: {theme.get('text_main', '#fff')}; border: 1px solid {theme.get('border', '#444')}; padding: 8px; font-weight: bold; }}
            QPushButton:checked {{ background-color: {theme.get('accent', '#b366ff')}; color: white; border: none; }}
        """
        self.btn_view_sections.setStyleSheet(toggle_style)
        self.btn_view_master.setStyleSheet(toggle_style)
        
        self.status_lbl.setStyleSheet(f"color: {theme.get('accent', '#b366ff')}; font-weight: bold;")
        self.scroll_area.setStyleSheet("background: transparent; border: none;")
        self.cmp_view_a.setStyleSheet("background: transparent; border: none;")
        self.cmp_view_b.setStyleSheet("background: transparent; border: none;")
        self.cmp_chat_output.setStyleSheet("background: transparent; border: none;")
        self.results_container.setStyleSheet("background: transparent;")