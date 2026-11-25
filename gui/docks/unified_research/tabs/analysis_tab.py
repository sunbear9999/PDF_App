# gui/docks/anaylsis_tab.py
import os
import json
import html
import hashlib
import re
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
                             QComboBox, QFrame, QScrollArea, QTabWidget, QButtonGroup, QStackedWidget, QMessageBox, QInputDialog, QDialog, QTextEdit, QToolButton, QCheckBox)
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
from gui.components.artifact_tasks import ArtifactTaskRunner
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
        count_items = [
            ("Nodes", len(result.get("entities", []))),
            ("Links", len(result.get("relations", []))),
        ]
        if result.get("cache_status"):
            count_items.append(("Cache", str(result["cache_status"]).title()))
        envelope = result.get("analysis_envelope") or {}
        model_usage = envelope.get("model_usage") or {}
        if model_usage.get("call_count") is not None:
            count_items.append(("LLM calls", int(model_usage.get("call_count") or 0)))
        if result.get("chunks"):
            count_items.append(("Sections", len(result.get("chunks", []))))
        if result.get("entities"):
            evidence_count = sum(
                1 for node in result.get("entities", [])
                if (node.get("analysis_role") in {"evidence", "quote", "quotation"}
                    or node.get("type") in {"entity.evidence", "entity.quote"})
            )
            if evidence_count:
                count_items.append(("Quoted evidence", evidence_count))
        if result.get("complete_base_node_count") is not None:
            count_items.append(
                ("Reusable base", result.get("complete_base_node_count"))
            )
        quality = result.get("quality") or {}
        role_counts = quality.get("role_counts") or {}
        if role_counts:
            count_items.extend([
                ("Claims", int(role_counts.get("claim", 0))
                 + int(role_counts.get("methodology", 0))),
                ("Reasoning", int(role_counts.get("reasoning", 0))),
            ])
        for label, key in (
            ("Propositions", "proposition_count"),
            ("Branches", "branch_count"),
            ("Source sections", "section_count"),
        ):
            if quality.get(key) is not None:
                count_items.append((label, quality.get(key)))
        for label, value in count_items:
            badge = QLabel(f"{label}: <b>{value}</b>")
            badge.setStyleSheet(f"padding: 4px 8px; background: {theme.get('bg_input', '#333')}; border-radius: 4px;")
            counts.addWidget(badge)
        counts.addStretch()
        layout.addLayout(counts)
        if quality:
            status = (
                "Partial analysis" if quality.get("limited_coverage")
                else "Ready for review" if quality.get('model_overlay_valid')
                else "Validated" if quality.get("valid") else "Validation failed"
            )
            origin = str(quality.get("origin") or "unknown").replace("_", " ")
            quality_label = QLabel(
                f"<b>{status}</b> · Origin: {html.escape(origin.title())}"
            )
            quality_label.setStyleSheet(
                f"color: {theme.get('text_muted', '#aaa')}; font-size: 10px;"
            )
            layout.addWidget(quality_label)
            if quality.get('semantic_relation_count') is not None:
                relations_label = QLabel(
                    f"Semantic links: {quality['semantic_relation_count']} · "
                    f"Source/grouping links: {quality.get('structural_relation_count', 0)}. "
                    "Source links identify quotations; they do not establish support.")
                relations_label.setWordWrap(True)
                layout.addWidget(relations_label)

        preview = []
        for entity in result.get("entities", [])[:8]:
            role = (
                entity.get("analysis_role")
                or str(entity.get("type", "entity")).split(".")[-1]
            ).replace("_", " ").title()
            is_evidence = str(entity.get("analysis_role") or "") == "evidence"
            label = (
                entity.get("exact_text") if is_evidence else entity.get("text")
            ) or entity.get("title") or entity.get("type")
            preview.append(f"{role} — {str(label)[:120]}")
        content = result.get("content")
        if isinstance(content, (dict, list)):
            content = json.dumps(content, ensure_ascii=False, indent=2)
        body_text = (
            "<br/>".join(html.escape(item) for item in preview)
            if preview else html.escape(str(content or ""))[:5000]
        )
        body = QLabel(body_text if body_text else "<i>No result content.</i>")
        body.setTextFormat(Qt.TextFormat.RichText)
        body.setWordWrap(True)
        layout.addWidget(body)
        diagnostics = result.get("diagnostics") or {}
        packing = diagnostics.get('context_packing') or quality.get('context_packing') or {}
        if packing:
            coverage = QLabel(
                f"Model context: {packing.get('selected_count', 0)} of "
                f"{packing.get('total_count', 0)} evidence groups supplied. "
                "The full source-backed baseline is retained. Context uses a conservative text estimate.")
            coverage.setWordWrap(True)
            layout.addWidget(coverage)
        if diagnostics.get("focused_deterministic_fallback"):
            warning = QLabel(
                "The model proposal was not accepted. Showing the grounded baseline. "
                "Missing analytical connections remain unresolved; shared sections and "
                "source quotations alone do not prove support or contradiction."
            )
            warning.setWordWrap(True)
            warning.setStyleSheet(
                f"color: {theme.get('warning', '#ffaa00')}; font-size: 10px;"
            )
            layout.addWidget(warning)
        if len(result.get("entities", [])) > len(preview):
            preview_note = QLabel(
                f"Preview shows {len(preview)} of {len(result.get('entities', []))} "
                "selected graph nodes. Send to Workspace imports every selected node; "
                "the complete reusable base remains saved in Intelligence."
            )
            preview_note.setWordWrap(True)
            preview_note.setStyleSheet(
                f"color: {theme.get('text_muted', '#aaa')}; font-size: 10px;"
            )
            layout.addWidget(preview_note)

        actions = QHBoxLayout()
        actions.addStretch()
        if result.get("prompt_trace"):
            btn_trace = QPushButton("View Prompt Trace")
            btn_trace.clicked.connect(self._show_prompt_trace)
            actions.addWidget(btn_trace)
        if result.get("entities"):
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
    def __init__(self, chunk_number: int, total_chunks: int, chunk: dict, theme: dict, doc_name: str = "",
                 viewer=None, parent=None, section_number: int = None, total_sections: int = None, *, app_context=None):
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
        if section_number is not None and total_sections:
            title_text = f"Chunk {chunk_number}/{total_chunks} (Section {section_number}/{total_sections})"
        else:
            title_text = f"Section {chunk_number}/{total_chunks}"
        title = QLabel(f"<b>{title_text}</b>")
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
        # Full summary (the header only shows a 180-char preview) — selectable.
        if summary:
            sum_lbl = QLabel(f"<b>Summary:</b> {summary}")
            sum_lbl.setWordWrap(True)
            sum_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            content_layout.addWidget(sum_lbl)
        # Show all selected quotes (was capped at 5, which under-reported the
        # count shown in the header); the card is collapsible so this is fine.
        for quote in quotes[:60]:
            if not isinstance(quote, dict):
                continue
            quote_text = str(quote.get("x") or "").strip()
            if not quote_text:
                continue
            bubble = NoteBubbleWidget(doc_name, quote_text, str(quote.get("n") or ""), theme, self,
                                      source_meta=quote, app_context=app_context)
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
                bubble = NoteBubbleWidget(doc_name, quote_text, str(quote.get("n") or claim_text), theme, self,
                                          source_meta=quote, app_context=app_context)
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
        self._analysis_run_requested = False
        self.bus = app_context.bus
        self._mode_runner = ArtifactTaskRunner(app_context, self, service_name='analysis_mode_service')
        self._mode_runner.completed.connect(self._mode_dispatch_completed)
        self._mode_runner.failed.connect(self._mode_dispatch_failed)
        self._mode_runner.busy_changed.connect(self._mode_dispatch_busy)
        self.destroyed.connect(self._mode_runner.shutdown)
        
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
        self.combo_intel_modes.currentIndexChanged.connect(
            self._load_saved_intelligence_result
        )
        self.combo_intel_modes.currentIndexChanged.connect(
            self._save_analysis_preferences
        )
        self.combo_analysis_detail.currentIndexChanged.connect(
            self._save_analysis_preferences
        )
        self.combo_analysis_scope.currentIndexChanged.connect(
            self._save_analysis_preferences
        )
        self.check_analysis_repair.toggled.connect(
            self._save_analysis_preferences
        )
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
        result_payload = getattr(payload, "result", None) or {}
        target_id = result_payload.get("target_id")
        if target_id and target_id != self.target_id:
            return
        # Legacy/global extraction events must not populate Analysis.  This tab
        # accepts only a run explicitly initiated here (or a blueprint whose
        # initiating target is this tab).
        if not target_id and not self._analysis_run_requested:
            return
        if event == AnalysisEvent.RUN_STARTED:
            self.status_lbl.setText("Processing document graph analysis...")
            self.btn_run.setEnabled(False)
        elif event == AnalysisEvent.PROGRESS:
            self.status_lbl.setText(payload.result.get("message", "Analysis is running..."))
        elif event == AnalysisEvent.CHUNK_RESULT:
            self._render_chunk_result(payload.result)
        elif event == AnalysisEvent.CHUNK_EVIDENCE_READY:
            self._render_chunk_evidence(payload.result)
        elif event == AnalysisEvent.SECTION_SYNTHESIS_READY:
            n = payload.result.get("section_number", "?")
            total = payload.result.get("total_sections", "?")
            summary = str(payload.result.get("summary", ""))[:120]
            self.status_lbl.setText(f"Section {n}/{total} synthesised. {summary}{'...' if len(summary) == 120 else ''}")
        elif event == AnalysisEvent.GRAPH_PLAN_READY:
            self.status_lbl.setText(
                payload.result.get("message", "Merge complete. Connecting and polishing final graph...")
            )
        elif event == AnalysisEvent.GRAPH_HYDRATED:
            n_nodes = payload.result.get("hydrated_entity_count", 0)
            missing = len(payload.result.get("missing_quote_ids") or [])
            msg = f"Graph hydrated: {n_nodes} nodes."
            if missing:
                msg += f" ({missing} quote reference(s) unresolved.)"
            self.status_lbl.setText(msg)
        elif event == AnalysisEvent.RUN_FAILED:
            self.status_lbl.setText(f"Analysis failed: {'; '.join(payload.errors or ['Unknown error'])}")
            self.btn_run.setEnabled(True)
        elif event == AnalysisEvent.RESULT_READY:
            self._render_analysis_result(payload.result)
        elif event == AnalysisEvent.RUN_COMPLETED:
            self.status_lbl.setText("Analysis complete.")
            self.btn_run.setEnabled(True)
            self.btn_run_mode.setEnabled(True)
            self._analysis_run_requested = False
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

        opts_layout.addWidget(QLabel("<b>Graph Contract:</b>"))
        self.combo_templates = QComboBox()
        self.combo_templates.setToolTip(
            "A built-in or user-created node/relation contract passed as data to the universal analysis blueprint"
        )
        self.combo_templates.currentIndexChanged.connect(self._load_run_existing_analysis)
        opts_layout.addWidget(self.combo_templates, 1)

        self.btn_edit = QPushButton("✏️ Edit Contracts")
        self.btn_edit.clicked.connect(self._open_template_editor)
        opts_layout.addWidget(self.btn_edit)

        run_layout.addLayout(opts_layout)

        from PySide6.QtWidgets import QTextEdit
        focus_layout = QHBoxLayout()
        focus_layout.addWidget(QLabel("Research Focus:"))
        self._user_prompt_edit = QTextEdit()
        self._user_prompt_edit.setPlaceholderText(
            "Optional — describe a specific research question, concept to track, or focus area for the analysis…"
        )
        self._user_prompt_edit.setFixedHeight(54)
        self._user_prompt_edit.setAcceptRichText(False)
        focus_layout.addWidget(self._user_prompt_edit, 1)
        run_layout.addLayout(focus_layout)

        # Intelligence modes: run the upgraded artifact-aware pipeline (fused
        # semantic + entity + keyword retrieval, segment-id citations). Each mode
        # is a registered blueprint dispatched through AnalysisModeService.
        intel_layout = QHBoxLayout()
        intel_layout.addWidget(QLabel("<b>Analysis Mode:</b>"))
        self.combo_intel_modes = QComboBox()
        self.combo_intel_modes.setToolTip(
            "All built-in and user/plugin modes run through the same universal master-runner blueprint"
        )
        intel_layout.addWidget(self.combo_intel_modes, 1)
        intel_layout.addWidget(QLabel("<b>Detail:</b>"))
        self.combo_analysis_detail = QComboBox()
        self.combo_analysis_detail.addItem("Focused", "focused")
        self.combo_analysis_detail.addItem("Standard", "standard")
        self.combo_analysis_detail.addItem("Exhaustive", "exhaustive")
        saved_detail = "standard"
        try:
            saved_detail = str(
                self.pm.get_metadata("analysis_detail_profile", "standard")
                or "standard"
            )
        except Exception:
            pass
        detail_index = self.combo_analysis_detail.findData(saved_detail)
        self.combo_analysis_detail.setCurrentIndex(
            detail_index if detail_index >= 0 else 1
        )
        self.combo_analysis_detail.setToolTip(
            "Standard targets 20–40 proposition nodes plus grounded quote evidence."
        )
        intel_layout.addWidget(self.combo_analysis_detail)
        intel_layout.addWidget(QLabel("<b>Scope:</b>"))
        self.combo_analysis_scope = QComboBox()
        self.combo_analysis_scope.addItem("Current source", "source")
        self.combo_analysis_scope.addItem("Project", "project")
        saved_scope = "source"
        try:
            saved_scope = str(
                self.pm.get_metadata("analysis_source_scope", "source")
                or "source"
            )
        except Exception:
            pass
        scope_index = self.combo_analysis_scope.findData(saved_scope)
        self.combo_analysis_scope.setCurrentIndex(
            scope_index if scope_index >= 0 else 0
        )
        intel_layout.addWidget(self.combo_analysis_scope)
        self.check_analysis_repair = QCheckBox("Allow repair")
        repair_enabled = True
        try:
            repair_enabled = str(
                self.pm.get_metadata("analysis_allow_repair", "true")
            ).lower() not in {"0", "false", "no"}
        except Exception:
            pass
        self.check_analysis_repair.setChecked(repair_enabled)
        self.check_analysis_repair.setToolTip(
            "Permit one targeted model repair before deterministic fallback."
        )
        intel_layout.addWidget(self.check_analysis_repair)
        self.btn_run_mode = QPushButton("▶ Run Analysis")
        self.btn_run_mode.setToolTip(
            "Run the selected mode and graph contract through the universal intelligence pipeline."
        )
        self.btn_run_mode.clicked.connect(self._run_intelligence_mode)
        intel_layout.addWidget(self.btn_run_mode)
        # Compatibility alias for existing event handlers and automation.
        self.btn_run = self.btn_run_mode
        run_layout.addLayout(intel_layout)
        self.template_status_label = QLabel()
        self.template_status_label.setWordWrap(True)
        self.template_status_label.setVisible(False)
        run_layout.addWidget(self.template_status_label)

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

        analysis_templates = self.pm.get_analysis_templates()
        for t in analysis_templates:
            if isinstance(t, tuple) or not hasattr(t, 'get'): continue
            t_name = t.get('name', t.get('title', 'Unnamed Template'))
            t_id = t.get('id', 'unknown')
            self.combo_templates.addItem(t_name, t)
            self.cmp_template.addItem(t_name, t_id)

        # Intelligence modes are pulled dynamically from the registry (rules §13.3).
        combo_intel = getattr(self, "combo_intel_modes", None)
        registry = getattr(self.app_context, "analysis_mode_registry", None)
        if combo_intel is not None:
            previous_mode = combo_intel.currentData()
            if not previous_mode:
                try:
                    previous_mode = self.pm.get_metadata(
                        "analysis_last_mode", "argument_map"
                    )
                except Exception:
                    previous_mode = "argument_map"
            combo_intel.blockSignals(True)
            combo_intel.clear()
            if registry is not None:
                if hasattr(registry, "sync_saved_templates"):
                    registry.sync_saved_templates(analysis_templates)
                    errors = getattr(registry, 'template_errors', {})
                    self.template_status_label.setText(
                        'Unavailable saved modes — open Modes Builder to correct their vocabulary: '
                        + '; '.join(f'{key}: {value}' for key, value in errors.items()))
                    self.template_status_label.setVisible(bool(errors))
                    combo_intel.setToolTip('Unavailable saved modes: ' + '; '.join(
                        f'{key}: {value}' for key, value in errors.items()) if errors else
                        'Built-in and saved modes use the shared analysis compiler.')
                modes = registry.for_mount("analysis_mode") or registry.all()
                for spec in modes:
                    combo_intel.addItem(spec.label, spec.id)
                default_index = combo_intel.findData(
                    previous_mode or "argument_map"
                )
                if default_index >= 0:
                    combo_intel.setCurrentIndex(default_index)
            combo_intel.blockSignals(False)

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
        self._load_saved_intelligence_result()

    def _open_template_editor(self):
        dlg = TemplateEditorDialog(self.app_context, self)
        dlg.accepted.connect(self._refresh_selectors)
        dm = get_for_widget(self)
        if dm:
            dm.show_instance(dlg)
        else:
            if exec_as_modal(dlg):
                self._refresh_selectors()

    def _save_analysis_preferences(self, *_args):
        if not self.pm or not hasattr(self.pm, "set_metadata"):
            return
        try:
            self.pm.set_metadata(
                "analysis_last_mode", self.combo_intel_modes.currentData()
                or "argument_map"
            )
            self.pm.set_metadata(
                "analysis_detail_profile",
                self.combo_analysis_detail.currentData() or "standard",
            )
            self.pm.set_metadata(
                "analysis_source_scope",
                self.combo_analysis_scope.currentData() or "source",
            )
            self.pm.set_metadata(
                "analysis_allow_repair",
                "true" if self.check_analysis_repair.isChecked() else "false",
            )
        except Exception:
            pass

    def _run_intelligence_mode(self):
        """Run a registered intelligence mode (blueprint) over the Research Focus.

        Fully blueprint-driven: AnalysisModeService resolves the mode's blueprint
        and dispatches WorkflowIntent.RUN_BLUEPRINT (no direct LLM call).
        """
        svc = getattr(self.app_context, "analysis_mode_service", None)
        combo = getattr(self, "combo_intel_modes", None)
        if not svc or combo is None or combo.count() == 0:
            self.status_lbl.setText("Intelligence modes are unavailable.")
            return
        mode_id = combo.currentData()
        if not mode_id:
            return
        focus = self._user_prompt_edit.toPlainText().strip()
        if not focus and mode_id not in (
            "intelligence_summaries", "summaries", "argument_map",
            "argument_map_enriched", "timeline",
        ):
            self.status_lbl.setText("Enter a question/claim/field description in Research Focus first.")
            return
        doc_path = self.doc_selector.currentData()
        source_id = None
        try:
            rec = self.pm.get_source_record_by_path(doc_path) if doc_path else None
            source_id = rec.get("id") if rec else None
        except Exception:
            source_id = None
        params = {
            "user_query": focus, "claim": focus, "extraction_query": focus,
            "summary_focus": focus or "overview",
            "doc_path": doc_path or "",
            "source_id": source_id or doc_path or "",
            "allowed_source_ids": [source_id] if source_id else [],
            "selected_model": self.app_context.get_active_ai_model(),
            "analysis_focus": focus,
            "analysis_detail_profile": self.combo_analysis_detail.currentData() or "standard",
            "analysis_allow_repair": self.check_analysis_repair.isChecked(),
            "scope": self.combo_analysis_scope.currentData() or "source",
        }
        template = self.combo_templates.currentData()
        if str(mode_id).startswith("custom:"):
            saved_id = str(mode_id).split(":", 1)[1]
            template = next(
                (
                    item for item in self.pm.get_analysis_templates()
                    if isinstance(item, dict) and str(item.get("id")) == saved_id
                ),
                template,
            )
        if isinstance(template, dict):
            params["analysis_template"] = template
            params["analysis_template_id"] = self._get_safe_template_id(template)
        if mode_id == "claim_tracking" or params["scope"] == "project":
            try:
                params["allowed_source_ids"] = [
                    str(item.get("id"))
                    for item in self.pm.list_sources(include_removed=False)
                    if item.get("id")
                ]
                params["scope"] = "project"
            except Exception:
                pass
        if mode_id == "custom_graph":
            params["custom_instructions"] = focus
        if mode_id == "extraction":
            params["extraction_schema"] = focus
        self._analysis_run_requested = True
        self.btn_run_mode.setEnabled(False)
        self.status_lbl.setText('Checking the analysis contract and source readiness…')
        self._mode_runner.start('run', mode_id=mode_id, params=params, target_id='analysis_tab')

    def _mode_dispatch_failed(self, message):
        self.status_lbl.setText(f'Mode error: {message}')
        self._analysis_run_requested = False
        self.btn_run_mode.setEnabled(True)

    def _mode_dispatch_busy(self, busy):
        if not busy and self._mode_runner.result is None:
            self._analysis_run_requested = False
            self.btn_run_mode.setEnabled(True)

    def _mode_dispatch_completed(self, _operation):
        result = self._mode_runner.result
        if isinstance(result, dict) and result.get("error"):
            self.status_lbl.setText(f"Mode error: {result['error']}")
            self._analysis_run_requested = False
            self.btn_run_mode.setEnabled(True)
        elif isinstance(result, dict) and result.get('status') == 'not_ready':
            self.status_lbl.setText(f"Not ready: {result.get('message', '')}")
            self._analysis_run_requested = False
            self.btn_run_mode.setEnabled(True)
        elif isinstance(result, dict) and result.get('status') in {'cached', 'cancelled'}:
            self.status_lbl.setText('Loaded cached analysis.' if result['status'] == 'cached' else 'Analysis cancelled.')
            self._analysis_run_requested = False
            self.btn_run_mode.setEnabled(True)
        else:
            label = self.combo_intel_modes.currentText()
            self.status_lbl.setText(
                f"Running '{label}' through the master runner. Local preparation "
                "and summaries will be reused by later analyses."
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

    def _load_saved_intelligence_result(self):
        """Load the most recent canonical artifact for the selected mode."""
        if not self.pm or not hasattr(self, "combo_intel_modes"):
            return
        mode_id = self.combo_intel_modes.currentData()
        doc_path = self.doc_selector.currentData() if hasattr(self, "doc_selector") else ""
        source = (
            self.pm.get_source_record_by_path(doc_path)
            if doc_path and hasattr(self.pm, "get_source_record_by_path") else None
        )
        source_id = source.get("id") if source else None
        db = getattr(self.pm, "db_intelligence", None)
        if not mode_id or not db:
            return
        candidates = db.list_artifacts(
            source_id=source_id,
            status="complete",
            limit=5000,
        )
        artifact = next(
            (
                row for row in candidates
                if (row.get("metadata") or {}).get("stage") == "analysis"
                and (row.get("metadata") or {}).get("mode_id") == mode_id
            ),
            None,
        )
        if not artifact:
            return
        raw = artifact.get("exact_text") or ""
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = {"content": raw}
        if not isinstance(parsed, dict):
            parsed = {"content": parsed}
        parsed.setdefault(
            "template_title",
            self.combo_intel_modes.currentText() or "Saved Analysis",
        )
        parsed.setdefault("analysis_artifact_id", artifact.get("artifact_id"))
        parsed.setdefault("saved", True)
        self._clear_layout(self.results_layout)
        self._render_analysis_result(parsed)
        self.status_lbl.setText(
            f"Loaded saved {self.combo_intel_modes.currentText()} result."
        )

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
            app_context=self.app_context,
        )
        self.results_layout.insertWidget(self.results_layout.count() - 1, card)

    def _render_chunk_evidence(self, result: dict):
        chunk_id = result.get("chunk_id", 0)
        refs = result.get("compact_refs") or result.get("refs") or []
        doc_name = os.path.basename(result.get("doc_path") or self.doc_selector.currentData() or "")
        # Map compact_refs into the {x, n} quote format AnalysisChunkCard expects
        quotes = [
            {**r, "x": r.get("snippet", ""), "n": r.get("note", ""), "id": r.get("quote_id", "")}
            for r in refs
            if r.get("snippet")
        ]
        synthetic_chunk = {
            "quotes": quotes,
            "chunk_index": chunk_id,
        }
        position = result.get("chunk_number") or (chunk_id + 1)
        total = result.get("total_chunks", 0)
        card = AnalysisChunkCard(
            position,
            total,
            synthetic_chunk,
            self.theme,
            doc_name,
            self.app_context.viewer,
            self,
            section_number=result.get("section_number"),
            total_sections=result.get("total_sections"),
            app_context=self.app_context,
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
        blueprint = DefaultBlueprints.get_analysis_send_to_workspace_blueprint(
            result=result,
            workspace_id=workspace_id,
        )
        self.bus.workflow_action_requested.emit(
            WorkflowIntent.RUN_BLUEPRINT,
            WorkflowPayload(
                blueprint=blueprint,
                initial_state={
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
                        app_context=self.app_context,
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
        if hasattr(self, '_user_prompt_edit'): self._user_prompt_edit.setStyleSheet(f"background-color: {theme.get('bg_input', '#2b2b2b')}; color: {theme.get('text_main', '#fff')}; border: 1px solid {theme.get('border', '#444')}; border-radius: 4px; padding: 4px;")
        for scroll in [self.run_scroll, self.saved_scroll, self.master_scroll, self.comp_scroll]: scroll.setStyleSheet("background: transparent; border: none;")
