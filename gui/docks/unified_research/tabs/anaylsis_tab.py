# gui/docks/anaylsis_tab.py
import os
import json
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, 
                             QComboBox, QFrame, QScrollArea, QTabWidget, QButtonGroup, QStackedWidget, QMessageBox)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCursor

from core.utils.doc_parser import DocumentParser
from core.events.event_bus import EventBus
from core.events.domains.workspace_events import WorkflowIntent, WorkflowPayload
from gui.docks.unified_research.tabs.base_tab import BaseTab
from gui.docks.unified_research.components.template_editor import TemplateEditorDialog

class AnalysisTab(BaseTab):
    def __init__(self, main_window, parent=None):
        super().__init__(main_window, target_id="analysis_tab", parent=parent)
        self.pm = self.project_manager
        self._save_counter = 0  # Strict counter for DB saves
        self.bus = EventBus.get_instance()
        self.bus.workflow_action_requested.connect(self._handle_workflow_action)
        self._build_ui()
        QTimer.singleShot(100, self._refresh_selectors)

    def _handle_workflow_action(self, action: WorkflowIntent, payload: WorkflowPayload):
        if action == WorkflowIntent.ANALYSIS_REFRESH_REQUESTED:
            QTimer.singleShot(0, self._load_existing_analysis)

    # --- BULLETPROOF DB EXTRACTORS ---
    def _parse_db_row(self, row):
        """Guarantees extraction of chunk_index and json_data regardless of SQLite row format."""
        c_idx, j_data = 0, "{}"
        if hasattr(row, 'keys'): 
            c_idx = row['chunk_index']
            j_data = row['json_data']
        elif isinstance(row, dict):
            c_idx = row.get('chunk_index', 0)
            j_data = row.get('json_data', '{}')
        else: # Ultimate Tuple Fallback
            for val in row:
                if isinstance(val, int) and val < 1000: c_idx = val
            for val in reversed(row):
                if isinstance(val, str) and (val.strip().startswith('{') or val.strip().startswith('[')):
                    j_data = val
                    break
        return c_idx, j_data

    def _get_safe_template_id(self, template):
        """Prevents crash if the template dropdown returns a raw string/tuple instead of a dict."""
        if not template: return None
        if hasattr(template, 'keys'): return template.get('id')
        if isinstance(template, dict): return template.get('id')
        if isinstance(template, tuple) and len(template) > 0: return template[0]
        return str(template)

    def _mathematical_merge(self, analyses):
        """Deterministically merges JSON chunks, mathematically stripping identical data."""
        if not analyses: return "{}"
        master_dict = {}
        
        for row in analyses:
            c_idx, j_data = self._parse_db_row(row)
            try:
                from core.utils.json_utils import extract_and_heal_json
                success, parsed = extract_and_heal_json(j_data)
                if not success: continue
                
                items_to_process = parsed if isinstance(parsed, list) else [parsed]
                
                for obj in items_to_process:
                    if not isinstance(obj, dict): continue
                    
                    for key, val in obj.items():
                        if key not in master_dict:
                            master_dict[key] = []
                            
                        # Flatten logic: if val is a list, extend. If scalar, append.
                        vals_to_add = val if isinstance(val, list) else [val]
                        
                        for v in vals_to_add:
                            # Deduplication Check
                            if isinstance(v, dict):
                                v_str = json.dumps(v, sort_keys=True)
                                if not any(isinstance(existing, dict) and json.dumps(existing, sort_keys=True) == v_str for existing in master_dict[key]):
                                    master_dict[key].append(v)
                            else:
                                if v not in master_dict[key]:
                                    master_dict[key].append(v)
            except Exception as e:
                print(f"[Merge Error] {e}")
                
        return json.dumps(master_dict)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        # TAB 1: RUN ANALYSIS
        tab_run = QWidget()
        run_layout = QVBoxLayout(tab_run)
        run_layout.setContentsMargins(8, 8, 8, 8)

        opts_layout = QHBoxLayout()
        opts_layout.addWidget(QLabel("<b>Document:</b>"))
        self.doc_selector = QComboBox()
        opts_layout.addWidget(self.doc_selector, 1)

        opts_layout.addWidget(QLabel("<b>Template:</b>"))
        self.combo_templates = QComboBox()
        opts_layout.addWidget(self.combo_templates, 1)

        self.btn_edit = QPushButton("✏️ Edit")
        self.btn_edit.clicked.connect(self._open_template_editor)
        opts_layout.addWidget(self.btn_edit)

        self.btn_run = QPushButton("▶ Run Analysis")
        self.btn_run.clicked.connect(self._trigger_analysis)
        opts_layout.addWidget(self.btn_run)
        run_layout.addLayout(opts_layout)

        self.status_lbl = QLabel("")
        run_layout.addWidget(self.status_lbl)

        self.run_scroll, self.results_layout = self._create_scroll_area()
        run_layout.addWidget(self.run_scroll, 1)

        self.tabs.addTab(tab_run, "🔬 Run Analysis")

        # TAB 2: ADVANCED SYNTHESIS & REVIEW
        tab_adv = QWidget()
        adv_layout = QVBoxLayout(tab_adv)
        adv_layout.setContentsMargins(8, 8, 8, 8)

        mode_layout = QHBoxLayout()
        self.btn_view_sections = QPushButton("📑 Sections")
        self.btn_view_master = QPushButton("📚 Master Outline")
        self.btn_compare = QPushButton("⚖️ Compare Outlines")
        
        self.mode_group = QButtonGroup(self)
        for i, btn in enumerate([self.btn_view_sections, self.btn_view_master, self.btn_compare]):
            btn.setCheckable(True)
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            self.mode_group.addButton(btn, i)
            mode_layout.addWidget(btn)
        
        self.btn_view_sections.setChecked(True)
        self.mode_group.idClicked.connect(lambda idx: self.mode_stack.setCurrentIndex(idx))
        adv_layout.addLayout(mode_layout)

        self.mode_stack = QStackedWidget()
        
        # Stack 0: Section Viewer
        view_sec = QWidget()
        sec_layout = QVBoxLayout(view_sec)
        sec_layout.setContentsMargins(0, 8, 0, 0)
        
        sec_opts = QHBoxLayout()
        self.cmp_doc = QComboBox()
        self.cmp_template = QComboBox()
        self.cmp_doc.currentIndexChanged.connect(self._load_existing_analysis)
        self.cmp_template.currentIndexChanged.connect(self._load_existing_analysis)
        sec_opts.addWidget(self.cmp_doc, 1)
        sec_opts.addWidget(self.cmp_template, 1)
        sec_layout.addLayout(sec_opts)

        self.saved_scroll, self.saved_layout = self._create_scroll_area()
        sec_layout.addWidget(self.saved_scroll, 1)
        self.mode_stack.addWidget(view_sec)

        # Stack 1: Master Outline
        view_master = QWidget()
        master_layout = QVBoxLayout(view_master)
        master_layout.setContentsMargins(0, 8, 0, 0)
        
        master_opts = QHBoxLayout()
        master_opts.addWidget(QLabel("<i>Uses the document selected in 'Sections' tab.</i>"))
        master_opts.addStretch()
        self.btn_gen_master = QPushButton("✨ Generate Master Outline")
        self.btn_gen_master.clicked.connect(self._trigger_master_outline)
        master_opts.addWidget(self.btn_gen_master)
        master_layout.addLayout(master_opts)
        
        self.master_scroll, self.master_layout = self._create_scroll_area()
        master_layout.addWidget(self.master_scroll, 1)
        self.mode_stack.addWidget(view_master)

        # Stack 2: Compare Outlines
        view_comp = QWidget()
        comp_layout = QVBoxLayout(view_comp)
        comp_layout.setContentsMargins(0, 8, 0, 0)
        
        comp_opts = QHBoxLayout()
        self.comp_doc1 = QComboBox()
        self.comp_doc2 = QComboBox()
        comp_opts.addWidget(self.comp_doc1, 1)
        comp_opts.addWidget(QLabel("<b>vs</b>"))
        comp_opts.addWidget(self.comp_doc2, 1)
        
        self.btn_gen_comp = QPushButton("⚖️ AI Comparison")
        self.btn_gen_comp.clicked.connect(self._trigger_comparison)
        comp_opts.addWidget(self.btn_gen_comp)
        comp_layout.addLayout(comp_opts)
        
        self.comp_scroll, self.compare_layout = self._create_scroll_area()
        comp_layout.addWidget(self.comp_scroll, 1)
        self.mode_stack.addWidget(view_comp)

        adv_layout.addWidget(self.mode_stack, 1)
        self.tabs.addTab(tab_adv, "📂 Review & Synthesize")

    def _create_scroll_area(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addStretch()
        scroll.setWidget(container)
        return scroll, layout

    def _clear_layout(self, layout):
        while layout.count() > 1:
            item = layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()

    def _refresh_selectors(self):
        if not self.pm: return
        for cb in [self.doc_selector, self.cmp_doc, self.comp_doc1, self.comp_doc2, self.combo_templates, self.cmp_template]:
            cb.blockSignals(True)
            cb.clear()

        for pdf in self.pm.pdfs:
            name = os.path.basename(pdf)
            for cb in [self.doc_selector, self.cmp_doc, self.comp_doc1, self.comp_doc2]:
                cb.addItem(name, pdf)

        for t in self.pm.get_analysis_templates():
            if isinstance(t, tuple) or not hasattr(t, 'get'): continue
            t_name = t.get('name', t.get('title', 'Unnamed Template'))
            t_id = t.get('id', 'unknown')
            self.combo_templates.addItem(t_name, t)
            self.cmp_template.addItem(t_name, t_id)

        for cb in [self.doc_selector, self.cmp_doc, self.comp_doc1, self.comp_doc2, self.combo_templates, self.cmp_template]:
            cb.blockSignals(False)

        self._load_existing_analysis()

    def _open_template_editor(self):
        dlg = TemplateEditorDialog(self.pm, self.theme, self)
        if dlg.exec():
            self._refresh_selectors()

    def _trigger_analysis(self):
        doc_path = self.doc_selector.currentData()
        template = self.combo_templates.currentData()
        if not doc_path or not template: return
        
        template_id = self._get_safe_template_id(template)
        self._clear_layout(self.results_layout)
        self.status_lbl.setText("⏳ Processing Document...")
        self.btn_run.setEnabled(False)
        self._save_counter = 0  # FIX: Reset the strict save index

        chunks = DocumentParser.chunk_document_for_analysis(doc_path, template_id, template.get('instructions', ''), template.get('schema', '{}'))
        if not chunks:
            self.status_lbl.setText("❌ Failed to parse document.")
            self.btn_run.setEnabled(True)
            return

        self.pm.clear_document_analyses(doc_path, template_id)

        from core.engine.default_blueprints import DefaultBlueprints
        blueprint = DefaultBlueprints.get_analysis_blueprint(self.prompt_manager, chunks)
        
        from PySide6.QtWidgets import QDockWidget
        dock = self.main_window.findChild(QDockWidget, "UnifiedResearchDock")
        selected_model = dock.model_combo.currentText() if dock and hasattr(dock, 'model_combo') else ""
        
        self.send_to_pipeline(blueprint, {"selected_model": selected_model})
        self.btn_run.setEnabled(True)

    def _trigger_master_outline(self):
        doc_path = self.cmp_doc.currentData()
        template_id = self._get_safe_template_id(self.cmp_template.currentData())
        analyses = self.pm.get_document_analyses(doc_path, template_id)
        
        if not analyses: 
            QMessageBox.warning(self, "No Data", "Run an analysis on this document first.")
            return
            
        self._clear_layout(self.master_layout)
        
        # INSTANT MATHEMATICAL MERGE
        merged_json = self._mathematical_merge(analyses)
        
        from gui.docks.unified_research.components.dynamic_outlines import UniversalOutlineWidget
        annot_manager = self.main_window.viewer.annot_manager if hasattr(self.main_window, 'viewer') else None
        
        widget = UniversalOutlineWidget("📚 Master Deduplicated Outline", merged_json, self.theme, annot_manager)
        
        count = self.master_layout.count()
        if count > 0: self.master_layout.insertWidget(count - 1, widget)
        else: self.master_layout.addWidget(widget)

    def _trigger_comparison(self):
        doc1 = self.comp_doc1.currentData()
        doc2 = self.comp_doc2.currentData()
        temp_id = self._get_safe_template_id(self.cmp_template.currentData())
        
        an1 = self.pm.get_document_analyses(doc1, temp_id)
        an2 = self.pm.get_document_analyses(doc2, temp_id)
        
        if not an1 or not an2: 
            QMessageBox.warning(self, "Missing Data", "Both documents must be analyzed with the selected template first.")
            return
            
        ctx1 = json.dumps([self._parse_db_row(r)[1] for r in an1])
        ctx2 = json.dumps([self._parse_db_row(r)[1] for r in an2])
        
        self._clear_layout(self.compare_layout)
        
        from core.engine.default_blueprints import DefaultBlueprints
        bp = DefaultBlueprints.get_compare_outlines_blueprint(self.prompt_manager)
        for step in bp.steps: step.ui_target = "analysis_tab"
            
        self.send_to_pipeline(bp, {"outline_1": ctx1, "outline_2": ctx2})

    def receive_ai_widget(self, widget):
        is_run_tab = self.tabs.currentIndex() == 0
        if is_run_tab: target_layout = self.results_layout
        else:
            mode = self.mode_group.checkedId()
            if mode == 0: target_layout = self.saved_layout
            elif mode == 1: target_layout = self.master_layout
            else: target_layout = self.compare_layout

        count = target_layout.count()
        if count > 0: target_layout.insertWidget(count - 1, widget)
        else: target_layout.addWidget(widget)

        if is_run_tab:
            from gui.docks.unified_research.components.dynamic_outlines import UniversalOutlineWidget
            if isinstance(widget, UniversalOutlineWidget):
                doc_path = self.doc_selector.currentData()
                template_id = self._get_safe_template_id(self.combo_templates.currentData())
                
                if doc_path and template_id:
                    chunk_idx = self._save_counter
                    self._save_counter += 1
                    
                    try:
                        # THE FIX: Prefer the exact JSON string injected by the UI router
                        data_to_save = getattr(widget, '_raw_ai_data', getattr(widget, 'raw_json', '{}'))
                        self.pm.save_document_analysis(doc_path, template_id, chunk_idx, data_to_save)
                    except Exception as e:
                        print(f"[AnalysisTab] Background save failed: {e}")

    def _load_existing_analysis(self):
        doc_path = self.cmp_doc.currentData()
        template_id = self._get_safe_template_id(self.cmp_template.currentData())
        if not doc_path or not template_id: return
        
        self._clear_layout(self.saved_layout)
        analyses = self.pm.get_document_analyses(doc_path, template_id)
        
        if not analyses:
            lbl = QLabel("<i>No saved analysis found for this document and template.</i>")
            lbl.setStyleSheet(f"color: {self.theme.get('text_muted', '#aaa')};")
            self.saved_layout.insertWidget(0, lbl)
            return

        from gui.docks.unified_research.components.dynamic_outlines import UniversalOutlineWidget
        annot_manager = self.main_window.viewer.annot_manager if hasattr(self.main_window, 'viewer') else None
        from core.utils.json_utils import extract_and_heal_json
        
        for row in analyses:
            try:
                c_idx, j_data = self._parse_db_row(row)
                
                success, parsed = extract_and_heal_json(j_data)
                if success and isinstance(parsed, list):
                    for i, item in enumerate(parsed):
                        start_page = ((c_idx + i) * 4) + 1
                        title = f"Section {c_idx + i + 1}: Pages {start_page}-{start_page + 3}"
                        widget = UniversalOutlineWidget(title, json.dumps(item), self.theme, annot_manager)
                        self.saved_layout.insertWidget(self.saved_layout.count() - 1, widget)
                else:
                    start_page = (c_idx * 4) + 1
                    title = f"Section {c_idx + 1}: Pages {start_page}-{start_page + 3}"
                    widget = UniversalOutlineWidget(title, j_data, self.theme, annot_manager)
                    self.saved_layout.insertWidget(self.saved_layout.count() - 1, widget)
                    
            except Exception as e:
                print(f"[AnalysisTab] Failed to load chunk: {e}")

    def update_theme(self, theme):
        super().update_theme(theme)
        self.tabs.setStyleSheet(f"QTabWidget::pane {{ border: 1px solid {theme.get('border', '#444')}; background-color: transparent; }} QTabBar::tab {{ background: {theme.get('bg_panel', '#333')}; color: {theme.get('text_muted', '#aaa')}; padding: 8px 16px; border: 1px solid {theme.get('border', '#444')}; border-bottom: none; border-top-left-radius: 4px; border-top-right-radius: 4px; }} QTabBar::tab:selected {{ background: {theme.get('bg_input', '#2b2b2b')}; color: {theme.get('text_main', '#fff')}; font-weight: bold; border-top: 2px solid {theme.get('accent', '#b366ff')}; }}")
        style = f"background-color: {theme.get('bg_input', '#2b2b2b')}; color: {theme.get('text_main', '#fff')}; border: 1px solid {theme.get('border', '#444')}; border-radius: 4px; padding: 4px;"
        for cb in [self.doc_selector, self.combo_templates, self.cmp_doc, self.cmp_template, self.comp_doc1, self.comp_doc2]: cb.setStyleSheet(style)
        self.btn_run.setStyleSheet(f"background-color: {theme.get('accent', '#b366ff')}; font-weight: bold; color: white; border: none; border-radius: 4px; padding: 6px 12px;")
        btn_style = f"background-color: {theme.get('bg_panel', '#333')}; color: {theme.get('text_main', '#fff')}; border: 1px solid {theme.get('border', '#444')}; padding: 4px 8px; border-radius: 4px;"
        for btn in [self.btn_edit, self.btn_gen_master, self.btn_gen_comp]: btn.setStyleSheet(btn_style)
        toggle_style = f"QPushButton {{ background-color: {theme.get('bg_panel', '#333')}; color: {theme.get('text_main', '#fff')}; border: 1px solid {theme.get('border', '#444')}; padding: 6px; font-weight: bold; border-radius: 4px; }} QPushButton:checked {{ background-color: {theme.get('accent', '#b366ff')}; color: white; border: none; }}"
        for btn in [self.btn_view_sections, self.btn_view_master, self.btn_compare]: btn.setStyleSheet(toggle_style)
        if hasattr(self, 'status_lbl'): self.status_lbl.setStyleSheet(f"color: {theme.get('accent', '#b366ff')}; font-weight: bold;")
        for scroll in [self.run_scroll, self.saved_scroll, self.master_scroll, self.comp_scroll]: scroll.setStyleSheet("background: transparent; border: none;")
