# gui/docks/unified_research/tabs/base_tab.py
from PySide6.QtWidgets import QWidget
from PySide6.QtCore import QTimer
import json
from core.engine.action_model import AIActionBlueprint
from core.events.event_bus import EventBus
from core.events.domains.workflow_events import WorkflowIntent, WorkflowPayload
from gui.docks.unified_research.components.chat_streamer import ChatMessageWidget

class BaseTab(QWidget):
    def __init__(self, main_window, target_id=None, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.target_id = target_id
        
        self.theme_manager = getattr(main_window, 'theme_manager', None)
        self.theme = self.theme_manager.get_theme() if self.theme_manager else {}
        self.blueprint_manager = getattr(main_window, 'blueprint_manager', None)
        self.prompt_manager = getattr(main_window, 'prompt_manager', None)
        self.project_manager = getattr(main_window, 'project_manager', None)

        if self.target_id and hasattr(main_window, 'ui_router'):
            main_window.ui_router.register_target(self.target_id, self)
        self._active_stream_widget = None

    def receive_ai_widget(self, widget):
        """Universal UI Router receiver."""
        if hasattr(self, 'chat_layout'):
            count = self.chat_layout.count()
            if count > 0: self.chat_layout.insertWidget(count - 1, widget)
            else: self.chat_layout.addWidget(widget)
            
            if hasattr(self, 'scroll_area'):
                QTimer.singleShot(50, lambda: self.scroll_area.verticalScrollBar().setValue(self.scroll_area.verticalScrollBar().maximum()))
        elif hasattr(self, 'results_layout'):
            count = self.results_layout.count()
            if count > 0:
                self.results_layout.insertWidget(count - 1, widget)
            else:
                self.results_layout.addWidget(widget)

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

        if payload_type == "citation_cards":
            items = self._coerce_items(payload.get("items", []))
            widget = self._active_stream_widget
            if widget is None:
                widget = ChatMessageWidget("AI Agent", theme=self.theme)
                self.receive_ai_widget(widget)
            for item in items:
                if isinstance(item, dict):
                    widget.add_bubble(
                        doc_name=item.get("doc_name", "Unknown Document"),
                        quote=item.get("quote", item.get("text", "")),
                        note=item.get("note", item.get("reason", ""))
                    )
            if hasattr(widget, "hide_status"):
                widget.hide_status()
            self._active_stream_widget = None
            return

        if payload_type == "outline":
            from gui.docks.unified_research.components.dynamic_outlines import UniversalOutlineWidget
            annot_manager = self.main_window.viewer.annot_manager if hasattr(self.main_window, "viewer") else None
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
            from gui.docks.unified_research.components.user_input_form import UserInputFormWidget
            widget = UserInputFormWidget(payload.get("step_id", ""), payload.get("expected_inputs", {}), theme=self.theme)
            runner = payload.get("runner")
            if runner:
                widget.form_submitted.connect(runner.submit_user_input)
            self.receive_ai_widget(widget)
            return

        if payload_type == "results_dialog":
            from gui.components.dialogs.tag_relatives_dialog import AIResultsDialog
            dlg = AIResultsDialog(payload.get("title", "AI Results"), payload.get("items", []), self.main_window, self.main_window)
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
            self._active_stream_widget = ChatMessageWidget("AI Agent", theme=self.theme)
            self.receive_ai_widget(self._active_stream_widget)
        return self._active_stream_widget

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

    def safe_load_history(self):
        """Universally loads history safely without destroying active stream widgets."""
        if not self.project_manager or not hasattr(self, 'chat_layout'): return
        
        # Prevent destructive wiping if widgets are already active
        if self.chat_layout.count() > 1: return 

        history = self.project_manager.get_chat_history(self.target_id)
        for msg in history:
            is_user = (msg['role'] == "user")
            sender = "You" if is_user else "AI Agent"
            
            if msg["ui_format"] == "chat_widgets":
                try:
                    items = json.loads(msg["content"])
                    if isinstance(items, dict):
                        for val in items.values():
                            if isinstance(val, list): items = val; break
                        if isinstance(items, dict): items = [items] 
                    
                    widget = ChatMessageWidget(sender, theme=self.theme, is_user=is_user)
                    for item in items:
                        if isinstance(item, dict):
                            widget.add_bubble(
                                doc_name=item.get("doc_name", "Unknown Document"),
                                quote=item.get("quote", item.get("text", "")),
                                note=item.get("note", item.get("reason", ""))
                            )
                    self.receive_ai_widget(widget)
                except Exception: pass
            else:
                widget = ChatMessageWidget(sender, theme=self.theme, is_user=is_user)
                widget.append_chunk(msg['content'])
                if not is_user and hasattr(widget, 'hide_status'):
                    widget.hide_status()
                self.receive_ai_widget(widget)

    def send_to_pipeline(self, blueprint: AIActionBlueprint, variables: dict, output_workspace: bool = False):
        initial_state = {**variables}
        initial_state["output_workspace"] = output_workspace
        
        # Global Context Injection
        if self.project_manager:
            try:
                settings = json.loads(self.project_manager.get_metadata("global_ai_settings", "{}"))
                for k, v in settings.items():
                    if k not in initial_state:
                        initial_state[k] = v
            except Exception: pass
            
            initial_state["project_manifest"] = self.project_manager.get_metadata("project_manifest", "{}")
            initial_state["active_rag_docs"] = self._metadata_json("active_rag_docs", [])
            initial_state["active_rag_tags"] = self._metadata_json("active_rag_tags", [])
            initial_state["active_rag_tag_logic"] = self.project_manager.get_metadata("active_rag_tag_logic", "OR")
            
            try:
                from core.api.workspace_ai import WorkspaceAIApi
                ws_data = self.project_manager.get_workspace_data()
                api = WorkspaceAIApi(self.project_manager)
                initial_state["workspace_data"] = api.build_ai_context(ws_data)
            except Exception as e:
                initial_state["workspace_data"] = "{}"

        if "selected_model" not in initial_state and hasattr(self.main_window, "_get_active_ai_model"):
            initial_state["selected_model"] = self.main_window._get_active_ai_model()
        elif "selected_model" not in initial_state and hasattr(self, "model_combo"):
            initial_state["selected_model"] = self.model_combo.currentText()
        elif "selected_model" not in initial_state and hasattr(self, "combo_models"):
            initial_state["selected_model"] = self.combo_models.currentText()

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
