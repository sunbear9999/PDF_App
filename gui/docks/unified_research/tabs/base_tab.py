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

        # Context contributors inject additional state into every pipeline call.
        # Subclasses register built-ins; plugins register via extension_registry.
        self._context_contributors: list = []

    # ------------------------------------------------------------------
    # Context contributor API
    # ------------------------------------------------------------------

    def register_context_contributor(self, contributor) -> None:
        """Register a TabContextContributor for this tab."""
        self._context_contributors.append(contributor)

    def _load_plugin_context_contributors(self) -> None:
        """
        Instantiate any TabContextContributorSpecs from the extension registry
        that match this tab's target_id.  Call this after _build_ui().
        """
        reg = getattr(self.app_context, "plugin_extension_registry", None)
        if not reg or not self.target_id:
            return
        for spec in reg.get_tab_context_contributor_specs(self.target_id):
            try:
                contributor = spec.factory(self)
                self.register_context_contributor(contributor)
            except Exception as exc:
                print(f"[BaseTab] Failed to instantiate tab context contributor from plugin: {exc}")

    def _gather_context_state(self) -> dict:
        """Collect state dicts from all registered contributors."""
        state: dict = {}
        for contributor in self._context_contributors:
            try:
                state.update(contributor.get_state())
            except Exception as exc:
                print(f"[BaseTab] Context contributor error: {exc}")
        return state

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
        loader = getattr(self, '_history_loader', None)
        if loader is not None and not getattr(self, '_replaying_history', False):
            loader.cancel()
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
            if hasattr(widget, "hide_status"):
                widget.hide_status()
            if hasattr(widget, "add_manifest_update"):
                widget.add_manifest_update(
                    payload.get("changes", {}),
                    self._open_project_manifest,
                )
            return

        if payload_type == "manifest_update_status":
            status = payload.get("status", "failed")
            if status == "attempted":
                # The manifest now runs as a separate queued workflow. Do not
                # manufacture an empty chat bubble just to show transient work.
                widget = self._active_stream_widget
                if widget is None:
                    if hasattr(self, "status_lbl"):
                        self.status_lbl.setText(
                            payload.get("text", "Updating project manifest…")
                        )
                    return
                if hasattr(widget, "update_status"):
                    widget.update_status(payload.get("text", "Updating project manifest…"))
                return
            widget = self._ensure_stream_widget()
            if hasattr(widget, "hide_status"):
                widget.hide_status()
            if hasattr(widget, "add_manifest_status"):
                widget.add_manifest_status(
                    status,
                    payload.get("text", "Project manifest update finished"),
                    payload.get("detail", ""),
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
            if not payload.get("keep_active", False):
                self._active_stream_widget = None
            return

        if payload_type == "prompt_trace_available":
            widget = self._active_stream_widget
            trace_id = payload.get("trace_id")
            if widget and trace_id and hasattr(widget, "set_prompt_trace"):
                widget.set_prompt_trace(trace_id)
            return

        if payload_type == "rag_context_view":
            widget = self._ensure_stream_widget()
            if hasattr(widget, "set_rag_context"):
                widget.set_rag_context(payload.get("audit") or {})
            return

        if payload_type == "rag_context_highlights":
            widget = self._ensure_stream_widget()
            if hasattr(widget, "apply_rag_context_highlights"):
                widget.apply_rag_context_highlights(
                    payload.get("audit") or {}
                )
            return

        if payload_type == "citation_cards":
            items = self._coerce_items(payload.get("items", []))
            widget = self._active_stream_widget
            if widget is None:
                widget = ChatMessageWidget("AI Agent", theme=self.theme, app_context=self.app_context)
                self.receive_ai_widget(widget)
            if payload.get("trace_id") and hasattr(widget, "set_prompt_trace"):
                widget.set_prompt_trace(payload.get("trace_id"))
            citations = [{'doc_name': item.get('doc_name', 'Unknown Document'),
                          'quote': item.get('quote', item.get('text', '')),
                          'note': item.get('note', item.get('reason', '')),
                          'source_meta': self._source_meta_for_citation(item)}
                         for item in items if isinstance(item, dict)]
            widget.set_citations(citations)
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
            if hasattr(self, "chat_layout"):
                widget = self._active_stream_widget
                if widget is None:
                    widget = ChatMessageWidget("AI Error", theme=self.theme, app_context=self.app_context)
                    self.receive_ai_widget(widget)
                if hasattr(widget, "set_error_text"):
                    widget.set_error_text(msg)
                else:
                    widget.append_chunk(f"Error: {msg}")
                if payload.get("trace_id") and hasattr(widget, "set_prompt_trace"):
                    widget.set_prompt_trace(payload.get("trace_id"))
                self._active_stream_widget = None
                if self.project_manager and self.target_id and not getattr(self, "_replaying_history", False):
                    try:
                        self.project_manager.save_chat_message(
                            self.target_id,
                            "ai",
                            f"Error: {msg}",
                            "error",
                            payload.get("trace_id"),
                            json.dumps([{"type": "error", "message": msg}]),
                        )
                    except Exception:
                        pass
                QTimer.singleShot(50, self._scroll_to_bottom_if_alive)
            elif hasattr(self, "status_lbl"):
                self.status_lbl.setText(f"❌ Pipeline Failed: {msg}")
                self.status_lbl.setStyleSheet("font-weight: bold; color: #ff4444;")
            else:
                widget = self._ensure_stream_widget()
                if hasattr(widget, "set_error_text"):
                    widget.set_error_text(msg)
                elif hasattr(widget, "update_status"):
                    widget.update_status(f"❌ Error: {msg}")
            if hasattr(self, "btn_generate"):
                self.btn_generate.setEnabled(True)
            return

        # --- Plugin-registered renderer fallthrough ----------------------------
        # Built-in types are all handled above with explicit returns.  Any
        # remaining payload type may have a renderer registered by a plugin via
        # api.widgets.register() → AIOutputWidgetFactory.
        from gui.base.ai_output_factory import AIOutputWidgetFactory
        plugin_widget = AIOutputWidgetFactory.create(payload_type, payload, parent=self)
        if plugin_widget is not None:
            self.receive_ai_widget(plugin_widget)

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
            **item,
            "source_type": item.get("source_type", "pdf"),
            "source_id": item.get("source_id", ""),
            "source_locator": item.get("source_locator", {}),
            "doc_name": item.get("doc_name", ""),
            "quote": quote,
            "start": item.get("start", 0),
            "end": item.get("end", 0),
        }
        if meta.get("artifact_id") or meta.get("artifact_ref"):
            return meta
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

        self.load_paged_history()

    def load_paged_history(self):
        from gui.components.chat_history_loader import ChatHistoryLoader
        if not hasattr(self, '_history_loader'):
            self._history_loader = ChatHistoryLoader(self.app_context, self)
        self._history_loader.pager.reset()

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

        # Selected model: centralized in AI Setup default model roles.
        if "selected_model" not in initial_state:
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
