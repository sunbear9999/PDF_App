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
from gui.docks.unified_research.components.dynamic_outlines import UniversalOutlineWidget, InteractiveListButton
from gui.docks.unified_research.tabs.base_tab import BaseTab

class AnalysisTab(BaseTab):
    def __init__(self, main_window, parent=None):
        super().__init__(main_window, parent)
        self.pm = self.project_manager
        self.templates = self.pm.get_analysis_templates()
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(f"QTabWidget::pane {{ border: 1px solid {self.theme.get('border', '#444')}; background-color: transparent; }} QTabBar::tab {{ background: {self.theme.get('bg_panel', '#333')}; color: white; padding: 8px 16px; border-top-left-radius: 4px; border-top-right-radius: 4px; margin-right: 2px; }} QTabBar::tab:selected {{ background: {self.theme.get('accent', '#b366ff')}; }}")
        
        self.doc_tab = QWidget()
        self._build_doc_tab(self.doc_tab)
        self.tabs.addTab(self.doc_tab, "📄 Document Analysis")
        
        self.compare_tab = QWidget()
        self._build_compare_tab(self.compare_tab)
        self.tabs.addTab(self.compare_tab, "⚖️ Compare Outlines")
        
        layout.addWidget(self.tabs)
        
        self._populate_docs()
        self._populate_templates()
        
        self.update_theme(self.theme)

    def _build_doc_tab(self, parent_widget):
        layout = QVBoxLayout(parent_widget)
        
        ctrl_layout = QHBoxLayout()
        self.doc_selector = QComboBox()
        self.doc_selector.currentIndexChanged.connect(self._load_existing_analysis)
        ctrl_layout.addWidget(QLabel("<b>Doc:</b>"))
        ctrl_layout.addWidget(self.doc_selector, 1)

        self.combo_templates = QComboBox()
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
        
        self.btn_view_sections = QPushButton("📑 Section-by-Section")
        self.btn_view_sections.setCheckable(True)
        self.btn_view_sections.setChecked(True)
        
        self.btn_view_master = QPushButton("👑 Master Outline")
        self.btn_view_master.setCheckable(True)
        
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
        self._load_existing_analysis() 

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
        self.cmp_view_a.setMinimumWidth(50) 
        
        self.cmp_view_b = QScrollArea()
        self.cmp_view_b.setWidgetResizable(True)
        self.cmp_view_b.setMinimumWidth(50) 
        
        self.cont_a = QWidget(); self.lyt_a = QVBoxLayout(self.cont_a); self.cont_a.setLayout(self.lyt_a); self.lyt_a.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.cont_b = QWidget(); self.lyt_b = QVBoxLayout(self.cont_b); self.cont_b.setLayout(self.lyt_b); self.lyt_b.setAlignment(Qt.AlignmentFlag.AlignTop)
        
        self.cmp_view_a.setWidget(self.cont_a)
        self.cmp_view_b.setWidget(self.cont_b)
        self.cmp_splitter.addWidget(self.cmp_view_a)
        self.cmp_splitter.addWidget(self.cmp_view_b)
        layout.addWidget(self.cmp_splitter, 1)
        
        chat_frame = QFrame()
        chat_frame.setMinimumWidth(50) 
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
            
    def refresh_project_ui(self):
        curr_doc = self.doc_selector.currentData()
        curr_a = self.cmp_doc_a.currentData()
        curr_b = self.cmp_doc_b.currentData()
        
        self.doc_selector.blockSignals(True)
        self.cmp_doc_a.blockSignals(True)
        self.cmp_doc_b.blockSignals(True)
        
        self._populate_docs()
        
        if curr_doc: self.doc_selector.setCurrentIndex(max(0, self.doc_selector.findData(curr_doc)))
        if curr_a: self.cmp_doc_a.setCurrentIndex(max(0, self.cmp_doc_a.findData(curr_a)))
        if curr_b: self.cmp_doc_b.setCurrentIndex(max(0, self.cmp_doc_b.findData(curr_b)))
        
        self.doc_selector.blockSignals(False)
        self.cmp_doc_a.blockSignals(False)
        self.cmp_doc_b.blockSignals(False)

    def _open_editor(self):
        dlg = TemplateEditorDialog(self.pm, self.theme, self)
        if dlg.exec(): self._populate_templates()

    def _clear_results(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()

    def _build_master_outline_dict(self, records):
        master_data = {}
        import re
        for rec in records:
            try:
                clean_str = rec["json_data"]
                clean_str = re.sub(r'^```json', '', clean_str, flags=re.MULTILINE)
                clean_str = re.sub(r'^```', '', clean_str, flags=re.MULTILINE).strip()
                
                try: 
                    data = json.loads(clean_str, strict=False)
                except:
                    clean_str = re.sub(r',\s*\}', '}', clean_str)
                    clean_str = re.sub(r',\s*\]', ']', clean_str)
                    try: 
                        data = json.loads(clean_str, strict=False)
                    except:
                        try: data = json.loads(clean_str + "}", strict=False)
                        except: data = json.loads(clean_str + "]}", strict=False)

                page_ref = rec.get("page_range", f"Part {rec.get('chunk_index', 0) + 1}")
                
                if not isinstance(data, dict): continue
                
                for key, val in data.items():
                    if not val: continue
                    if key not in master_data:
                        master_data[key] = [] if isinstance(val, list) else ""
                    
                    if isinstance(val, list):
                        for item in val:
                            if isinstance(item, dict):
                                sig = json.dumps(item, sort_keys=True)
                                if not any(json.dumps(existing, sort_keys=True) == sig for existing in master_data[key]):
                                    master_data[key].append(item)
                            elif isinstance(item, str):
                                if item not in master_data[key]:
                                    master_data[key].append(item)
                    elif isinstance(val, str):
                        master_data[key] += f"<b>[{page_ref}]</b> {val}<br><br>"
            except Exception as e: 
                print(f"Master Outline Error: {e}")
            
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
        
        from gui.docks.unified_research.components.dynamic_outlines import UniversalOutlineWidget
        
        if is_master_mode:
            master_data = self._build_master_outline_dict(records)
            viewer = UniversalOutlineWidget("Master Outline", master_data, self.theme, self.main_window.viewer.annot_manager)
            self.results_layout.addWidget(viewer)
        else:
            for rec in records:
                try:
                    chunk_idx = rec.get('chunk_index', 0)
                    page_ref = rec.get('page_range', f"Part {chunk_idx + 1}")
                    title = f"Section: {page_ref}"
                    
                    viewer = UniversalOutlineWidget(title, rec["json_data"], self.theme, self.main_window.viewer.annot_manager, is_expanded=False)
                    self.results_layout.addWidget(viewer)
                except Exception as e:
                    print(f"Error loading section {rec.get('chunk_index', 'Unknown')}: {e}")

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
        
        # Note: InteractiveAnalysisViewer would need to be imported or refactored into UniversalOutlineWidget based on your previous component code!
        self.lyt_a.addWidget(UniversalOutlineWidget("Outline A", master_a, self.theme, self.main_window.viewer.annot_manager))
        self.lyt_b.addWidget(UniversalOutlineWidget("Outline B", master_b, self.theme, self.main_window.viewer.annot_manager))
        
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
        
        # --- FIXED: Route through Default Blueprints ---
        from core.engine.default_blueprints import DefaultBlueprints
        bp = DefaultBlueprints.get_compare_outlines_blueprint(self.pm)
        
        state = {
            "user_query": text, 
            "doc_a": self.current_cmp_data_a, 
            "doc_b": self.current_cmp_data_b
        }
        
        self.cmp_runner = MasterActionRunner(self.main_window, bp, state)
        self.cmp_runner.progress_update.connect(ai_msg.append_chunk)
        self.cmp_runner.start()

    def save_chunk_to_db(self, state, json_str):
        item = state.get('item')
        if not item and hasattr(self, '_active_chunks'):
            if getattr(self, '_chunk_save_idx', 0) < len(self._active_chunks):
                item = self._active_chunks[self._chunk_save_idx]
                
        if not item: 
            return
            
        try:
            self.pm.save_document_analysis(
                item.get('doc_path'), 
                item.get('template_id'), 
                item.get('chunk_index'), 
                json_str
            )
            self.status_lbl.setText(f"✅ Analyzed Section: {item.get('page_range')}")
            
            if hasattr(self, '_chunk_save_idx'):
                self._chunk_save_idx += 1
                
            is_final_chunk = hasattr(self, '_active_chunks') and self._chunk_save_idx >= len(self._active_chunks)
            
            if self.btn_view_master.isChecked() or is_final_chunk:
                QTimer.singleShot(200, self._load_existing_analysis)
                
        except Exception as e:
            print(f"Failed to save chunk to DB: {e}")

    def _trigger_analysis(self):
        doc_path = self.doc_selector.currentData()
        template = self.combo_templates.currentData()
        
        if not doc_path or not template: return
        
        doc = fitz.open(doc_path)
        chunks = []
        
        for i in range(0, doc.page_count, 4):
            chunk_text = ""
            for j in range(i, min(i+4, doc.page_count)):
                chunk_text += f"\n--- Page {j+1} ---\n" + doc.load_page(j).get_text()
            
            chunks.append({
                "doc_path": doc_path,
                "template_id": template['id'],
                "template_instructions": template.get('instructions', ''),
                "template_schema": template.get('schema', '{}'),
                "chunk_index": i // 4,
                "page_range": f"{i+1}-{min(i+4, doc.page_count)}",
                "text": chunk_text
            })
            
        self.pm.clear_document_analyses(doc_path, template['id'])
        self.status_lbl.setText("⏳ Processing Document...")

        self._active_chunks = chunks.copy()
        self._chunk_save_idx = 0

        from core.engine.default_blueprints import DefaultBlueprints
        blueprint = DefaultBlueprints.get_analysis_blueprint(self.prompt_manager, chunks)
        
        from PySide6.QtWidgets import QDockWidget
        dock = self.main_window.findChild(QDockWidget, "UnifiedResearchDock")
        selected_model = dock.model_combo.currentText() if dock and hasattr(dock, 'model_combo') else ""
        
        state_dict = {"selected_model": selected_model}
        
        self.send_to_pipeline(blueprint, state_dict)

    def update_theme(self, theme):
        super().update_theme(theme)
        
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