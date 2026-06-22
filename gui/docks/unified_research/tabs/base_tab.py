# gui/docks/unified_research/tabs/base_tab.py
from PySide6.QtWidgets import QWidget
from PySide6.QtCore import QTimer
import json
import shiboken6
from core.engine.action_model import AIActionBlueprint
from core.events.event_bus import EventBus
from core.events.domains.workflow_events import WorkflowIntent, WorkflowPayload
from gui.docks.unified_research.components.chat_streamer import ChatMessageWidget
from gui.docks.unified_research.components.history_renderer import ChatHistoryRenderer
from gui.utils.document_helpers import prune_doc_names


class BaseTab(QWidget):
    def __init__(self, app_context, target_id=None, parent=None):
        super().__init__(parent)
        self.app_context = app_context
        self.target_id = target_id

        self.theme_manager = app_context.theme_manager
        self.theme = self.theme_manager.get_theme() if self.theme_manager else {}
        self.blueprint_manager = app_context.blueprint_manager
        self.prompt_manager = app_context.prompt_manager
        self.project_manager = app_context.project_manager
        self.llm_manager = app_context.llm_manager

        if self.target_id and app_context.ui_router:
            app_context.ui_router.register_target(self.target_id, self)
        self._active_stream_widget = None

    def receive_ai_widget(self, widget):
        """Universal UI Router receiver."""
        if hasattr(self, 'chat_layout'):
            count = self.chat_layout.count()
            if count > 0: self.chat_layout.insertWidget(count - 1, widget)
            else: self.chat_layout.addWidget(widget)

            if hasattr(self, 'scroll_area'):
                QTimer.singleShot(50, self._scroll_to_bottom_if_alive)
        elif hasattr(self, 'results_layout'):
            count = self.results_layout.count()
            if count > 0:
                self.results_layout.insertWidget(count - 1, widget)
            else:
                self.results_layout.addWidget(widget)

    def _scroll_to_bottom_if_alive(self):
        try:
            if not shiboken6.isValid(self) or not shiboken6.isValid(self.scroll_area):
                return
            scrollbar = self.scroll_area.verticalScrollBar()
            if shiboken6.isValid(scrollbar):
                scrollbar.setValue(scrollbar.maximum())
        except (AttributeError, RuntimeError):
            return

    def receive_ai_payload(self, payload: dict):
        payload_type = payload.get("type")

        if payload_type == "status":
            if hasattr(self, "status_lbl"):
                self.status_lbl.setText(payload.get("text", ""))
                return
            widget = self._ensure_stream_widget()
            if hasattr(widget, "update_status"):
                widget.update_status(payload.get("text", ""))
            return

        if payload_type == "stream_chunk":
            widget = self._ensure_stream_widget()
            widget.append_chunk(payload.get("chunk", ""))
            return

        if payload_type == "manifest_updated":
            widget = self._ensure_stream_widget()
            if hasattr(widget, "add_manifest_update"):
                widget.add_manifest_update(
                    payload.get("changes", {}),
                    self._open_project_manifest,
                )
            return

        if payload_type == "replace_stream_text":
            widget = self._ensure_stream_widget()
            if hasattr(widget, "set_final_text"):
                widget.set_final_text(payload.get("text", ""))
            return

        if payload_type == "hide_status":
            if self._active_stream_widget and hasattr(self._active_stream_widget, "hide_status"):
                self._active_stream_widget.hide_status()
            self._active_stream_widget = None
            return

        if payload_type == "prompt_trace_available":
            widget = self._active_stream_widget
            trace_id = payload.get("trace_id")
            if widget and trace_id and hasattr(widget, "set_prompt_trace"):
                widget.set_prompt_trace(trace_id)
            return

        if payload_type == "citation_cards":
            items = self._coerce_items(payload.get("items", []))
            widget = self._active_stream_widget
            if widget is None:
                widget = ChatMessageWidget("AI Agent", theme=self.theme, app_context=self.app_context)
                self.receive_ai_widget(widget)
            if payload.get("trace_id") and hasattr(widget, "set_prompt_trace"):
                widget.set_prompt_trace(payload.get("trace_id"))
            for item in items:
                if isinstance(item, dict):
                    source_meta = self._source_meta_for_citation(item)
                    widget.add_bubble(
                        doc_name=item.get("doc_name", "Unknown Document"),
                        quote=item.get("quote", item.get("text", "")),
                        note=item.get("note", item.get("reason", "")),
                        source_meta=source_meta,
                    )
            if hasattr(widget, "hide_status"):
                widget.hide_status()
            return

        if payload_type == "outline":
            from gui.docks.unified_research.components.dynamic_outlines import UniversalOutlineWidget
            annot_manager = self.app_context.viewer.annot_manager if self.app_context.viewer else None
            widget = UniversalOutlineWidget(payload.get("title", "AI Result"), payload.get("content", ""), self.theme, annot_manager)
            widget._raw_ai_data = payload.get("raw_ai_data", payload.get("content", ""))
            self.receive_ai_widget(widget)
            return

        if payload_type == "data_table":
            from gui.docks.unified_research.components.dynamic_data_table import DynamicDataTableWidget
            self.receive_ai_widget(DynamicDataTableWidget(payload.get("content", ""), self.theme))
            return

        if payload_type == "card_grid":
            from gui.docks.unified_research.components.dynamic_card_grid import DynamicCardGridWidget
            self.receive_ai_widget(DynamicCardGridWidget(payload.get("content", ""), self.theme))
            return

        if payload_type == "user_input":
            schema = payload.get("expected_inputs", {})
            runner = payload.get("runner")
            # Rich item-selector mode (SHOW_ITEM_SELECTOR step)
            if isinstance(schema, dict) and schema.get("_input_type") == "item_selector":
                from gui.docks.unified_research.components.item_selector_widget import ItemSelectorWidget
                widget = ItemSelectorWidget(
                    step_id=payload.get("step_id", ""),
                    items=schema.get("_items", []),
                    title=schema.get("_title", "Select Items"),
                    action_label=schema.get("_action_label", "Confirm"),
                    display_key=schema.get("_display_key", ""),
                    subtitle_key=schema.get("_subtitle_key", ""),
                    theme=self.theme,
                )
            else:
                from gui.docks.unified_research.components.user_input_form import UserInputFormWidget
                widget = UserInputFormWidget(payload.get("step_id", ""), schema, theme=self.theme)
            if runner:
                widget.form_submitted.connect(runner.submit_user_input)
            self.receive_ai_widget(widget)
            return

        if payload_type == "results_dialog":
            from gui.components.dialogs.tag_relatives_dialog import AIResultsDialog
            from gui.managers.dialog_manager import get_for_widget
            dlg = AIResultsDialog(payload.get("title", "AI Results"), payload.get("items", []), self.app_context, self)
            dm = get_for_widget(self)
            if dm:
                dm.show_instance(dlg)
            else:
                dlg.show()
            return

        if payload_type == "error":
            msg = payload.get("message", "")
            if hasattr(self, "status_lbl"):
                self.status_lbl.setText(f"❌ Pipeline Failed: {msg}")
                self.status_lbl.setStyleSheet("font-weight: bold; color: #ff4444;")
            else:
                widget = self._ensure_stream_widget()
                if hasattr(widget, "update_status"):
                    widget.update_status(f"❌ Error: {msg}")
            if hasattr(self, "btn_generate"):
                self.btn_generate.setEnabled(True)

    def _ensure_stream_widget(self):
        if self._active_stream_widget is None:
            self._active_stream_widget = ChatMessageWidget("AI Agent", theme=self.theme, app_context=self.app_context)
            self.receive_ai_widget(self._active_stream_widget)
        return self._active_stream_widget

    def _open_project_manifest(self):
        from gui.docks.unified_research.components.manifest_bubble import ProjectBriefDialog
        from gui.managers.dialog_manager import exec_as_modal, get_for_widget

        dialog_manager = get_for_widget(self)
        if dialog_manager:
            dialog_manager.show(
                ProjectBriefDialog,
                key="project_brief",
                singleton=True,
                factory=lambda: ProjectBriefDialog(self.project_manager, self.theme, self),
            )
        else:
            exec_as_modal(ProjectBriefDialog(self.project_manager, self.theme, self))

    def _persist_active_ai_widget(self):
        if getattr(self, "_replaying_history", False):
            return
        pm = self.project_manager
        widget = self._active_stream_widget
        content = getattr(widget, "raw_buffer", "") if widget else ""
        if pm and self.target_id and content.strip():
            pm.save_chat_message(self.target_id, "ai", content.strip(), "live_stream")

    def _coerce_items(self, items):
        if isinstance(items, str):
            try:
                items = json.loads(items)
            except Exception:
                return []
        if isinstance(items, dict):
            for val in items.values():
                if isinstance(val, list):
                    items = val
                    break
            if isinstance(items, dict):
                items = [items]
        return items if isinstance(items, list) else [items]

    def _source_meta_for_citation(self, item):
        quote = item.get("quote", item.get("text", ""))
        meta = {
            "source_type": item.get("source_type", "pdf"),
            "source_id": item.get("source_id", ""),
            "start": item.get("start", 0),
            "end": item.get("end", 0),
        }
        pm = self.project_manager
        doc_name = item.get("doc_name", "")
        if not pm or not hasattr(pm, "list_sources") or not doc_name:
            return meta
        source = next((s for s in pm.list_sources() if doc_name in __import__("os").path.basename(s.get("path", ""))), None)
        if not source:
            return meta
        meta["source_type"] = source.get("source_type", meta["source_type"])
        meta["source_id"] = source.get("id", meta["source_id"])
        if meta["source_type"] == "video" and not item.get("start"):
            transcript = pm.get_video_transcript(meta["source_id"]) if hasattr(pm, "get_video_transcript") else {}
            needle = " ".join(str(quote).lower().split())
            for segment in transcript.get("segments", []) or []:
                text = " ".join(str(segment.get("text", "")).lower().split())
                if needle and (needle in text or text in needle or " ".join(needle.split()[:6]) in text):
                    meta["start"] = segment.get("start", 0)
                    meta["end"] = segment.get("end", meta["start"])
                    break
        return meta

    def safe_load_history(self):
        """Universally loads history safely without destroying active stream widgets."""
        if not self.project_manager or not hasattr(self, 'chat_layout'): return

        # Prevent destructive wiping if widgets are already active
        if self.chat_layout.count() > 1: return

        history = self.project_manager.get_chat_history(self.target_id)
        self.render_chat_history(history)

    def render_chat_history(self, history, *, clear_existing=False):
        """Render persisted turns through the same rich component path as live output."""
        if not hasattr(self, "chat_layout"):
            return
        if clear_existing:
            self.clear_rendered_chat()
        self._replaying_history = True
        try:
            ChatHistoryRenderer(self, theme=self.theme, app_context=self.app_context).render(history)
        finally:
            self._active_stream_widget = None
            self._replaying_history = False

    def clear_rendered_chat(self):
        """Remove rendered turns while preserving the layout's trailing stretch."""
        self._active_stream_widget = None
        if not hasattr(self, "chat_layout"):
            return
        while self.chat_layout.count() > 1:
            item = self.chat_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def send_to_pipeline(self, blueprint: AIActionBlueprint, variables: dict, output_workspace: bool | None = None):
        initial_state = {**variables}
        if output_workspace is not None:
            initial_state["output_workspace"] = output_workspace

        # Inject global AI settings and standard RAG context from AppContext
        if self.project_manager:
            rag_ctx = self.app_context.build_rag_context_payload()

            # Apply global AI settings (don't overwrite caller-provided keys)
            for k, v in rag_ctx.pop("_global_ai_settings", {}).items():
                if k not in initial_state:
                    initial_state[k] = v

            # Inject RAG context (don't overwrite)
            for k, v in rag_ctx.items():
                if k not in initial_state:
                    initial_state[k] = v

        # Selected model: prefer tab-local combo, fall back to AppContext
        if "selected_model" not in initial_state:
            if hasattr(self, "model_combo"):
                initial_state["selected_model"] = self.model_combo.currentText()
            elif hasattr(self, "combo_models"):
                initial_state["selected_model"] = self.combo_models.currentText()
            else:
                initial_state["selected_model"] = self.app_context.get_active_ai_model()

        EventBus.get_instance().workflow_action_requested.emit(
            WorkflowIntent.RUN_BLUEPRINT,
            WorkflowPayload(
                blueprint=blueprint,
                initial_state=initial_state,
                target_id=self.target_id,
            ),
        )

    def _metadata_json(self, key, default):
        raw = self.project_manager.get_metadata(key, json.dumps(default))
        if isinstance(raw, list):
            return raw
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, list) else default
        except Exception:
            return default

    def update_theme(self, theme):
        self.theme = theme
        self.setStyleSheet(f"background-color: {theme.get('bg_main', '#1e1e1e')}; color: {theme.get('text_main', '#fff')};")
