# gui/docks/anaylsis_tab.py
import os
import json
import hashlib
import re
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
                             QComboBox, QFrame, QScrollArea, QTabWidget, QButtonGroup, QStackedWidget, QMessageBox, QInputDialog, QDialog, QTextEdit, QToolButton)
from PySide6.QtCore import Qt, QTimer, Signal
from gui.managers.dialog_manager import exec_as_modal, get_for_widget
from PySide6.QtGui import QCursor

from core.events.event_bus import EventBus
from core.events.domains.analysis_events import AnalysisEvent, AnalysisPayload
from core.events.domains.document_events import DocumentEvent
from core.events.domains.project_events import ProjectEvent
from core.events.domains.workflow_events import WorkflowIntent, WorkflowPayload
from core.engine.default_blueprints import DefaultBlueprints
from gui.docks.unified_research.tabs.base_tab import BaseTab
from gui.docks.unified_research.components.template_editor import TemplateEditorDialog
from gui.docks.unified_research.components.note_bubble import NoteBubbleWidget
from gui.utils.document_helpers import active_pdf_paths
from core.utils.analysis_helpers import MASTER_CHUNK_SENTINEL, PAGES_PER_CHUNK


class AnalysisResultCard(QFrame):
    def __init__(self, result: dict, theme: dict, on_send, parent=None):
        super().__init__(parent)
        self.result = result
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {theme.get('bg_panel', '#2b2b2b')};
                border: 1px solid {theme.get('border', '#444')};
                border-radius: 6px;
            }}
            QLabel {{ color: {theme.get('text_main', '#fff')}; border: none; }}
            QPushButton {{
                background-color: {theme.get('accent', '#b366ff')};
                color: white; border: none; border-radius: 4px;
                padding: 6px 10px; font-weight: bold;
            }}
            QPushButton:hover {{ background-color: {theme.get('accent_hover', '#8e44dd')}; }}
        """)
        layout = QVBoxLayout(self)
        title = QLabel(f"<b>{result.get('template_title', 'Analysis')}</b>")
        layout.addWidget(title)
        counts = QHBoxLayout()
        for label, value in [
            ("Nodes", len(result.get("entities", []))),
            ("Links", len(result.get("relations", []))),
            ("Chunks", len(result.get("chunks", []))),
        ]:
            badge = QLabel(f"{label}: <b>{value}</b>")
            badge.setStyleSheet(f"padding: 4px 8px; background: {theme.get('bg_input', '#333')}; border-radius: 4px;")
            counts.addWidget(badge)
        counts.addStretch()
        layout.addLayout(counts)

        preview = []
        for entity in result.get("entities", [])[:8]:
            label = entity.get("title") or entity.get("text") or entity.get("exact_text") or entity.get("type")
            preview.append(f"{entity.get('type', 'entity')} - {str(label)[:120]}")
        body = QLabel("<br>".join(preview) if preview else "<i>No graph artifacts extracted.</i>")
        body.setWordWrap(True)
        layout.addWidget(body)

        actions = QHBoxLayout()
        actions.addStretch()
        if result.get("prompt_trace"):
            btn_trace = QPushButton("View Prompt Trace")
            btn_trace.clicked.connect(self._show_prompt_trace)
            actions.addWidget(btn_trace)
        btn_send = QPushButton("Send to Workspace")
        btn_send.clicked.connect(lambda: on_send(result))
        actions.addWidget(btn_send)
        layout.addLayout(actions)

    def _show_prompt_trace(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Document Analysis Prompt Trace")
        dlg.setMinimumSize(900, 650)
        layout = QVBoxLayout(dlg)
        editor = QTextEdit()
        editor.setReadOnly(True)
        editor.setPlainText(json.dumps(self.result.get("prompt_trace", {}), indent=2))
        layout.addWidget(editor)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dlg.accept)
        layout.addWidget(close_btn)
        dm = get_for_widget(self)
        if dm:
            dm.show_instance(dlg)
        else:
            exec_as_modal(dlg)


class AnalysisChunkCard(QFrame):
    def __init__(self, chunk_number: int, total_chunks: int, chunk: dict, theme: dict, doc_name: str = "", viewer=None, parent=None):
        super().__init__(parent)
        self.viewer = viewer
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {theme.get('bg_input', '#252525')};
                border: 1px solid {theme.get('border', '#444')};
                border-radius: 6px;
            }}
            QLabel {{ color: {theme.get('text_main', '#fff')}; border: none; }}
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        header = QHBoxLayout()
        self.btn_toggle = QToolButton()
        self.btn_toggle.setText("▾")
        self.btn_toggle.setCheckable(True)
        self.btn_toggle.setChecked(True)
        self.btn_toggle.clicked.connect(self._set_expanded)
        header.addWidget(self.btn_toggle)
        title = QLabel(f"<b>Section {chunk_number}/{total_chunks}</b>")
        header.addWidget(title)
        header.addStretch()
        layout.addLayout(header)
        
        summary = str(chunk.get("summary") or "").strip()
        claims = chunk.get("claims") or (chunk.get("raw_observations") or {}).get("c") or []
        quotes = chunk.get("quotes") or (chunk.get("raw_observations") or {}).get("q") or []
        if not quotes:
            quotes = self._quotes_from_entities(chunk.get("entities") or [])
        if claims:
            quote_count = sum(len(claim.get("q") or []) for claim in claims if isinstance(claim, dict))
            counts = f"{len(claims)} claims, {quote_count} quotes"
        elif quotes:
            counts = f"{len(quotes)} selected quotes"
        else:
            counts = f"{len(chunk.get('entities', []))} nodes, {len(chunk.get('relations', []))} links"
        body = QLabel(f"{counts}" + (f" - {summary[:180]}" if summary else ""))
        body.setWordWrap(True)
        layout.addWidget(body)

        self.content = QWidget()
        content_layout = QVBoxLayout(self.content)
        content_layout.setContentsMargins(0, 6, 0, 0)
        layout.addWidget(self.content)
        for quote in quotes[:5]:
            if not isinstance(quote, dict):
                continue
            quote_text = str(quote.get("x") or "").strip()
            if not quote_text:
                continue
            bubble = NoteBubbleWidget(doc_name, quote_text, str(quote.get("n") or ""), theme, self)
            if viewer:
                bubble.jump_requested.connect(viewer.jump_to_source)
            bubble.btn_save.hide()
            bubble.btn_similar.hide()
            content_layout.addWidget(bubble)
        for claim in claims[:6]:
            if not isinstance(claim, dict):
                continue
            claim_text = str(claim.get("x") or "").strip()
            reason = str(claim.get("r") or "").strip()
            if claim_text:
                claim_lbl = QLabel(f"<b>Claim:</b> {claim_text}")
                claim_lbl.setWordWrap(True)
                content_layout.addWidget(claim_lbl)
            if reason:
                reason_lbl = QLabel(f"<b>Reason:</b> {reason}")
                reason_lbl.setWordWrap(True)
                content_layout.addWidget(reason_lbl)
            for quote in (claim.get("q") or [])[:3]:
                if not isinstance(quote, dict):
                    continue
                quote_text = str(quote.get("x") or "").strip()
                if not quote_text:
                    continue
                bubble = NoteBubbleWidget(doc_name, quote_text, str(quote.get("n") or claim_text), theme, self)
                if viewer:
                    bubble.jump_requested.connect(viewer.jump_to_source)
                bubble.btn_save.hide()
                bubble.btn_similar.hide()
                content_layout.addWidget(bubble)

    def _set_expanded(self, expanded: bool):
        self.content.setVisible(expanded)
        self.btn_toggle.setText("▾" if expanded else "▸")

    def _quotes_from_entities(self, entities: list) -> list:
        quotes = []
        seen = set()
        for entity in entities or []:
            if not isinstance(entity, dict):
                continue
            props = entity.get("properties") if isinstance(entity.get("properties"), dict) else {}
            quote_text = str(entity.get("exact_text") or props.get("exact_text") or props.get("quote") or "").strip()
            if not quote_text:
                continue
            key = re.sub(r"\W+", "", quote_text.lower())[:180]
            if key in seen:
                continue
            seen.add(key)
            quotes.append({
                "x": quote_text,
                "n": str(props.get("note_text") or entity.get("text") or entity.get("title") or "").strip(),
            })
        return quotes


class AnalysisTab(BaseTab):
    # 1. Define Thread-Safe Signals
    ui_signal = Signal(object, object)
    workflow_signal = Signal(object, object)

    def __init__(self, app_context, parent=None):
        super().__init__(app_context, target_id="analysis_tab", parent=parent)
        self.pm = self.project_manager
        self._save_counter = 0  # Strict counter for DB saves
        self.bus = app_context.bus
        
        # 2. Connect the signals to safely hop back to the Main UI Thread
        self.ui_signal.connect(self._safe_analysis_event, Qt.ConnectionType.QueuedConnection)
        self.workflow_signal.connect(self._safe_workflow_action, Qt.ConnectionType.QueuedConnection)
        
        self.bus.workflow_action_requested.connect(self._handle_workflow_action)
        self.bus.analysis_result_changed.connect(self._handle_analysis_event)
        self.bus.document_added.connect(self._handle_document_list_event)
        self.bus.pdf_removed.connect(self._handle_document_list_event)
        self.bus.pdf_renamed.connect(self._handle_document_list_event)
        self.bus.project_loaded.connect(self._handle_project_event)
        
        self._build_ui()
        QTimer.singleShot(100, self._refresh_selectors)

    def _handle_workflow_action(self, action: WorkflowIntent, payload: WorkflowPayload):
        self.workflow_signal.emit(action, payload)

    def _handle_analysis_event(self, event, payload):
        self.ui_signal.emit(event, payload)

    def _handle_document_list_event(self, event, payload):
        if event in {DocumentEvent.DOCUMENT_ADDED, DocumentEvent.PDF_REMOVED, DocumentEvent.PDF_RENAMED}:
            QTimer.singleShot(0, self._refresh_selectors)

    def _handle_project_event(self, event, payload):
        if event == ProjectEvent.LOADED:
            QTimer.singleShot(0, self._refresh_selectors)

    # --- Main Thread Safely Executed Methods (GUI UPDATES GO HERE) ---
    def _safe_workflow_action(self, action, payload):
        if action == WorkflowIntent.ANALYSIS_REFRESH_REQUESTED:
            QTimer.singleShot(0, self._load_existing_analysis)

    def _safe_analysis_event(self, event, payload):
        if event == AnalysisEvent.RUN_STARTED:
            self.status_lbl.setText("Processing document graph analysis...")
            self.btn_run.setEnabled(False)
        elif event == AnalysisEvent.PROGRESS:
            self.status_lbl.setText(payload.result.get("message", "Analysis is running..."))
        elif event == AnalysisEvent.CHUNK_RESULT:
            self._render_chunk_result(payload.result)
        elif event == AnalysisEvent.RUN_FAILED:
            self.status_lbl.setText(f"Analysis failed: {'; '.join(payload.errors or ['Unknown error'])}")
            self.btn_run.setEnabled(True)
        elif event == AnalysisEvent.RESULT_READY:
            self._render_analysis_result(payload.result)
        elif event == AnalysisEvent.RUN_COMPLETED:
            self.status_lbl.setText("Analysis complete.")
            self.btn_run.setEnabled(True)
            self._load_existing_analysis()
        elif event == AnalysisEvent.SENT_TO_WORKSPACE:
            self.status_lbl.setText(f"Sent {payload.result.get('entity_count', 0)} nodes to workspace {payload.workspace_id}.")

    # --- BULLETPROOF DB EXTRACTORS ---
    def _parse_db_row(self, row):
        from core.utils.analysis_helpers import parse_db_row
        return parse_db_row(row)

    def _get_safe_template_id(self, template):
        """Prevents crash if the template dropdown returns a raw string/tuple instead of a dict."""
        if not template: return None
        if hasattr(template, 'keys'): return template.get('id')
        if isinstance(template, dict): return template.get('id')
        if isinstance(template, tuple) and len(template) > 0: return template[0]
        return str(template)

    def _mathematical_merge(self, analyses):
        from core.utils.analysis_helpers import mathematical_merge
        return mathematical_merge(analyses)

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
        self.doc_selector.currentIndexChanged.connect(self._load_run_existing_analysis)
        opts_layout.addWidget(self.doc_selector, 1)

        opts_layout.addWidget(QLabel("<b>Analysis Mode:</b>"))
        self.combo_templates = QComboBox()
        self.combo_templates.setToolTip("Selects the node/relation contract and PromptManager keys passed into the Document Analysis blueprint.")
        self.combo_templates.currentIndexChanged.connect(self._load_run_existing_analysis)
        opts_layout.addWidget(self.combo_templates, 1)

        self.btn_edit = QPushButton("✏️ Edit Modes")
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
    def receive_ai_widget(self, widget):
        """Routes widgets without attempting dangerous concurrent DB writes."""
        is_run_tab = self.tabs.currentIndex() == 0
        
        if is_run_tab: 
            target_layout = self.results_layout
        else:
            mode = self.mode_group.checkedId()
            if mode == 0: target_layout = self.saved_layout
            elif mode == 1: target_layout = self.master_layout
            else: target_layout = self.compare_layout

        count = target_layout.count()
        if count > 0: 
            target_layout.insertWidget(count - 1, widget)
        else: 
            target_layout.addWidget(widget)
   
    def _clear_layout(self, layout):
        while layout.count() > 1:
            item = layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()

    def _refresh_selectors(self):
        import shiboken6
        if not self.pm or not shiboken6.isValid(self):
            return
        selectors = [
            getattr(self, name, None) for name in (
                "doc_selector", "cmp_doc", "comp_doc1", "comp_doc2",
                "combo_templates", "cmp_template",
            )
        ]
        if any(widget is None or not shiboken6.isValid(widget) for widget in selectors):
            return
        previous_docs = {
            self.doc_selector: self.doc_selector.currentData(),
            self.cmp_doc: self.cmp_doc.currentData(),
            self.comp_doc1: self.comp_doc1.currentData(),
            self.comp_doc2: self.comp_doc2.currentData(),
        }
        previous_templates = {
            self.combo_templates: self._get_safe_template_id(self.combo_templates.currentData()),
            self.cmp_template: self._get_safe_template_id(self.cmp_template.currentData()),
        }

        for cb in [self.doc_selector, self.cmp_doc, self.comp_doc1, self.comp_doc2, self.combo_templates, self.cmp_template]:
            cb.blockSignals(True)
            cb.clear()

        for pdf in active_pdf_paths(self.pm):
            name = os.path.basename(pdf)
            for cb in [self.doc_selector, self.cmp_doc, self.comp_doc1, self.comp_doc2]:
                cb.addItem(name, pdf)

        for t in self.pm.get_analysis_templates():
            if isinstance(t, tuple) or not hasattr(t, 'get'): continue
            t_name = t.get('name', t.get('title', 'Unnamed Template'))
            t_id = t.get('id', 'unknown')
            self.combo_templates.addItem(t_name, t)
            self.cmp_template.addItem(t_name, t_id)

        for cb, previous in previous_docs.items():
            idx = cb.findData(previous)
            if idx >= 0:
                cb.setCurrentIndex(idx)

        for cb, previous in previous_templates.items():
            for idx in range(cb.count()):
                if self._get_safe_template_id(cb.itemData(idx)) == previous:
                    cb.setCurrentIndex(idx)
                    break

        for cb in [self.doc_selector, self.cmp_doc, self.comp_doc1, self.comp_doc2, self.combo_templates, self.cmp_template]:
            cb.blockSignals(False)

        if self.doc_selector.count() == 0:
            self.status_lbl.setText("No PDFs available for analysis.")
            self._clear_layout(self.results_layout)
            self._clear_layout(self.saved_layout)
            self._clear_layout(self.master_layout)
            self._clear_layout(self.compare_layout)
            return

        self._load_existing_analysis()
        self._load_run_existing_analysis()

    def _open_template_editor(self):
        dlg = TemplateEditorDialog(self.pm, self.theme, self)
        dlg.accepted.connect(self._refresh_selectors)
        dm = get_for_widget(self)
        if dm:
            dm.show_instance(dlg)
        else:
            if exec_as_modal(dlg):
                self._refresh_selectors()

    def _trigger_analysis(self):
        doc_path = self.doc_selector.currentData()
        template = self.combo_templates.currentData()
        if not doc_path or not template: return
        
        self._clear_layout(self.results_layout)
        self.status_lbl.setText("Running Document Analysis blueprint...")
        self.btn_run.setEnabled(False)
        template_id = self._get_safe_template_id(template)
        run_id = self._analysis_run_id(doc_path, template_id)
        self._save_counter = 0
        blueprint = DefaultBlueprints.get_analysis_blueprint(self.prompt_manager)
        self.bus.analysis_result_changed.emit(
            AnalysisEvent.RUN_STARTED,
            AnalysisPayload(doc_path=doc_path, template_id=template_id, template=template, run_id=run_id),
        )
        self.bus.workflow_action_requested.emit(
            WorkflowIntent.RUN_BLUEPRINT,
            WorkflowPayload(
                blueprint=blueprint,
                initial_state={
                    "selected_model": self.app_context.get_active_ai_model(),
                    "analysis_doc_path": doc_path,
                    "target_doc": doc_path,
                    "analysis_template_id": template_id,
                    "analysis_template": template,
                    "analysis_run_id": run_id,
                },
                job_name=blueprint.name,
                target_id="analysis_tab",
            ),
        )

    def _analysis_run_id(self, doc_path, template_id):
        raw = f"{doc_path}:{template_id}:{os.path.getmtime(doc_path) if doc_path and os.path.exists(doc_path) else ''}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

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

    

    def _render_analysis_result(self, result: dict):
        # Keep completed chunk cards visible above the final master result.
        card = AnalysisResultCard(result, self.theme, self._send_result_to_workspace, self)
        self.results_layout.insertWidget(self.results_layout.count() - 1, card)

    def _load_run_existing_analysis(self):
        if not self.pm or not hasattr(self, "results_layout"):
            return
        doc_path = self.doc_selector.currentData()
        template = self.combo_templates.currentData()
        template_id = self._get_safe_template_id(template)
        if not doc_path or not template_id:
            return
        self._clear_layout(self.results_layout)
        master = self._saved_master_result(doc_path, template_id)
        if master:
            self.status_lbl.setText("Loaded saved analysis for the selected document and mode.")
            self.results_layout.insertWidget(
                self.results_layout.count() - 1,
                AnalysisResultCard(master, self.theme, self._send_result_to_workspace, self),
            )
        else:
            self.status_lbl.setText("No saved analysis loaded for this document and mode.")

    def _render_chunk_result(self, result: dict):
        chunk = result.get("chunk") or {}
        doc_name = os.path.basename(result.get("doc_path") or self.doc_selector.currentData() or "")
        card = AnalysisChunkCard(
            result.get("chunk_number", 0),
            result.get("total_chunks", 0),
            chunk,
            self.theme,
            doc_name,
            self.app_context.viewer,
            self,
        )
        self.results_layout.insertWidget(self.results_layout.count() - 1, card)

    def _send_result_to_workspace(self, result: dict):
        workspaces = self.pm.get_workspaces() if self.pm else []
        labels = []
        ids = []
        for ws in workspaces:
            ws_id = ws.get("id") if isinstance(ws, dict) else ws[0]
            name = ws.get("name") if isinstance(ws, dict) else ws[1]
            labels.append(f"{ws_id}: {name}")
            ids.append(int(ws_id))
        if not labels:
            labels, ids = ["1: Main Board"], [1]
        label, ok = QInputDialog.getItem(self, "Send to Workspace", "Choose board:", labels, 0, False)
        if not ok:
            return
        workspace_id = ids[labels.index(label)]
        blueprint = DefaultBlueprints.get_analysis_send_to_workspace_blueprint()
        self.bus.workflow_action_requested.emit(
            WorkflowIntent.RUN_BLUEPRINT,
            WorkflowPayload(
                blueprint=blueprint,
                initial_state={
                    "analysis_result": result,
                    "analysis_workspace_id": workspace_id,
                    "selected_model": self.app_context.get_active_ai_model(),
                },
                job_name=blueprint.name,
                target_id="analysis_tab",
            ),
        )

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

        from core.utils.json_utils import extract_and_heal_json
        
        for row in analyses:
            try:
                c_idx, j_data = self._parse_db_row(row)
                if c_idx == MASTER_CHUNK_SENTINEL:
                    try:
                        parsed_master = self._parse_saved_master_json(j_data)
                        self._clear_layout(self.master_layout)
                        self.master_layout.insertWidget(
                            self.master_layout.count() - 1,
                            AnalysisResultCard(parsed_master, self.theme, self._send_result_to_workspace, self),
                        )
                    except Exception as e:
                        print(f"Master parse error: {e}")
                    continue
            
                success, parsed = extract_and_heal_json(j_data)
                items = parsed if (success and isinstance(parsed, list)) else [j_data]
                
                for i, item in enumerate(items):
                    start_page = ((c_idx + i) * PAGES_PER_CHUNK) + 1 if isinstance(parsed, list) else (c_idx * PAGES_PER_CHUNK) + 1
                    title = f"Section {c_idx + i + 1 if isinstance(parsed, list) else c_idx + 1}: Pages {start_page}-{start_page + 3}"
                    container = QFrame()
                    container.setStyleSheet(f"background-color: {self.theme.get('bg_input', '#2b2b2b')}; border: 1px solid {self.theme.get('border', '#444')}; border-radius: 6px;")
                    vbox = QVBoxLayout(container)
                    vbox.setContentsMargins(0, 0, 0, 0)
                    heading = QLabel(f"<b>{title}</b>")
                    heading.setStyleSheet(f"padding: 8px; color: {self.theme.get('text_main', '#fff')};")
                    vbox.addWidget(heading)
                    payload_dict = item if isinstance(item, dict) else {}
                    if "raw_observations" in payload_dict:
                        section_chunk = payload_dict
                    elif "c" in payload_dict or "claims" in payload_dict:
                        section_chunk = {
                            "summary": payload_dict.get("s") or payload_dict.get("summary") or "",
                            "claims": payload_dict.get("c") or payload_dict.get("claims") or [],
                        }
                    else:
                        section_chunk = payload_dict
                    section_card = AnalysisChunkCard(
                        c_idx + 1,
                        len(analyses),
                        section_chunk,
                        self.theme,
                        os.path.basename(doc_path),
                        self.app_context.viewer,
                        self,
                    )
                    vbox.addWidget(section_card)
                    self.saved_layout.insertWidget(self.saved_layout.count() - 1, container)
                    
            except Exception as e:
                print(f"[AnalysisTab] Failed to load chunk: {e}")

    def _saved_master_result(self, doc_path, template_id):
        try:
            analyses = self.pm.get_document_analyses(doc_path, template_id)
        except Exception:
            return {}
        for row in analyses or []:
            c_idx, j_data = self._parse_db_row(row)
            if c_idx == MASTER_CHUNK_SENTINEL:
                return self._parse_saved_master_json(j_data)
        return {}

    def _parse_saved_master_json(self, j_data):
        try:
            parsed = json.loads(j_data)
            return parsed.get("master", {}) if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    def update_theme(self, theme):
        super().update_theme(theme)
        self.tabs.setStyleSheet(f"QTabWidget::pane {{ border: 1px solid {theme.get('border', '#444')}; background-color: transparent; }} QTabBar::tab {{ background: {theme.get('bg_panel', '#333')}; color: {theme.get('text_muted', '#aaa')}; padding: 8px 16px; border: 1px solid {theme.get('border', '#444')}; border-bottom: none; border-top-left-radius: 4px; border-top-right-radius: 4px; }} QTabBar::tab:selected {{ background: {theme.get('bg_input', '#2b2b2b')}; color: {theme.get('text_main', '#fff')}; font-weight: bold; border-top: 2px solid {theme.get('accent', '#b366ff')}; }}")
        style = f"background-color: {theme.get('bg_input', '#2b2b2b')}; color: {theme.get('text_main', '#fff')}; border: 1px solid {theme.get('border', '#444')}; border-radius: 4px; padding: 4px;"
        for cb in [self.doc_selector, self.combo_templates, self.cmp_doc, self.cmp_template, self.comp_doc1, self.comp_doc2]: cb.setStyleSheet(style)
        self.btn_run.setStyleSheet(f"background-color: {theme.get('accent', '#b366ff')}; font-weight: bold; color: white; border: none; border-radius: 4px; padding: 6px 12px;")
        btn_style = f"background-color: {theme.get('bg_panel', '#333')}; color: {theme.get('text_main', '#fff')}; border: 1px solid {theme.get('border', '#444')}; padding: 4px 8px; border-radius: 4px;"
        for btn in [self.btn_edit, self.btn_gen_comp]:
            btn.setStyleSheet(btn_style)
        toggle_style = f"QPushButton {{ background-color: {theme.get('bg_panel', '#333')}; color: {theme.get('text_main', '#fff')}; border: 1px solid {theme.get('border', '#444')}; padding: 6px; font-weight: bold; border-radius: 4px; }} QPushButton:checked {{ background-color: {theme.get('accent', '#b366ff')}; color: white; border: none; }}"
        for btn in [self.btn_view_sections, self.btn_view_master, self.btn_compare]: btn.setStyleSheet(toggle_style)
        if hasattr(self, 'status_lbl'): self.status_lbl.setStyleSheet(f"color: {theme.get('accent', '#b366ff')}; font-weight: bold;")
        for scroll in [self.run_scroll, self.saved_scroll, self.master_scroll, self.comp_scroll]: scroll.setStyleSheet("background: transparent; border: none;")
