# gui/components/ui_router.py
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QWidget
from gui.docks.unified_research.components.chat_streamer import ChatMessageWidget
from gui.docks.unified_research.components.note_bubble import NoteBubbleWidget
from gui.docks.unified_research.components.user_input_form import UserInputFormWidget
from core.utils.json_utils import extract_and_heal_json, extract_json_from_tags
from core.utils.state_resolver import StateResolver
import json
import shiboken6

class BlueprintUIRouter(QObject):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.theme = main_window.theme_manager.get_theme() if hasattr(main_window, 'theme_manager') else {}
        self.active_chat_widget = None
        self.registered_targets = {}

    def register_target(self, target_id: str, widget_instance: QWidget):
        """Allows any UI element to subscribe to AI pipeline outputs."""
        self.registered_targets[target_id] = widget_instance

    def get_target(self, target_id: str):
        """Safely fetches a target, cleaning up memory if it was destroyed."""
        target = self.registered_targets.get(target_id)
        if target and not shiboken6.isValid(target):
            del self.registered_targets[target_id]
            return None
        return target

    def attach_runner(self, runner):
        runner.progress_update.connect(self._handle_stream)
        runner.step_complete.connect(self._handle_step_complete)
        runner.step_started.connect(self._handle_step_started)
        runner.error.connect(self._handle_error)
        if hasattr(runner, 'user_input_requested'):
            runner.user_input_requested.connect(self._handle_user_input)

    def _get_runner(self):
        sender = self.sender()
        if sender: return sender
        registry = getattr(self.main_window, 'process_registry', None)
        if registry and registry.active_job: return registry.active_job.runner
        return None

    def _handle_user_input(self, step_id, expected_inputs):
        runner = self._get_runner()
        if not runner: return
        
        current_step = getattr(runner, 'current_executing_step', None)
        target_id = getattr(current_step, 'ui_target', 'chat_dock') if current_step else 'chat_dock'
        
        form_widget = UserInputFormWidget(step_id, expected_inputs, theme=self.theme)
        form_widget.form_submitted.connect(runner.submit_user_input)
        
        target_ui = self.get_target(target_id)
        if target_ui:
            target_ui.receive_ai_widget(form_widget)

    def _handle_step_started(self, step_id):
        runner = self._get_runner()
        if not runner: return
        
        if runner.blueprint.steps and runner.blueprint.steps[0].step_id == step_id:
            self.active_chat_widget = None

        current_step = getattr(runner, 'current_executing_step', None)
        
        # Read the dynamic status directly from the blueprint model
        text = getattr(current_step, 'status_text', f"Processing {step_id}...")
        target_id = getattr(current_step, 'ui_target', 'chat_dock') if current_step else 'chat_dock'
        
        if target_id in ["floating", "search_tab", "analysis_tab"]: return
            
        widget = self._get_or_create_chat_widget(target_id)
        if widget and hasattr(widget, 'update_status'):
            widget.update_status(text)
    
    def _get_or_create_chat_widget(self, target_id):
        if self.active_chat_widget and not shiboken6.isValid(self.active_chat_widget):
            self.active_chat_widget = None

        if not self.active_chat_widget:
            self.active_chat_widget = ChatMessageWidget("AI Agent", theme=self.theme)
            
            target_ui = self.get_target(target_id)
            if target_ui:
                target_ui.receive_ai_widget(self.active_chat_widget)
            elif target_id == "floating" and hasattr(self.main_window, 'universal_overlay'):
                self.main_window.universal_overlay.clear_content()
                self.main_window.universal_overlay.content_layout.addWidget(self.active_chat_widget)
                self.main_window.universal_overlay.show()
                self.main_window.universal_overlay.raise_()
                
        return self.active_chat_widget

    def _handle_stream(self, chunk):
        runner = self._get_runner()
        if not runner or not hasattr(runner, 'current_executing_step'): return
        current_step = runner.current_executing_step 
        if current_step and getattr(current_step, 'ui_format', 'silent') == "live_stream":
            target_id = getattr(current_step, 'ui_target', 'floating')
            widget = self._get_or_create_chat_widget(target_id)
            if widget: 
                widget.append_chunk(chunk)

    def _handle_step_complete(self, step_id, result_str, state_snapshot):
        runner = self._get_runner()
        if not runner: return
        
        # 1. Fetch the exact executing step, bypassing the unresolved blueprint defaults
        step = getattr(runner, 'current_executing_step', None)
        if not step or step.step_id != step_id:
            step = next((s for s in runner.blueprint.steps if s.step_id == step_id), None)

        ui_format = getattr(step, 'ui_format', 'silent') if step else 'silent'
        target_id = getattr(step, 'ui_target', 'floating') if step else 'floating'
        target_ui = self.get_target(target_id)

        # 2. Pure-Python Manifest Update (No raw pattern matching)
        success, manifest_data = extract_json_from_tags(result_str, "UPDATE_MANIFEST")
        if success and isinstance(manifest_data, dict):
            pm = getattr(self.main_window, 'project_manager', None)
            if pm and hasattr(pm, 'db_docs'):
                current_manifest_str = pm.get_metadata("project_manifest", "{}")
                try: current_manifest = json.loads(current_manifest_str)
                except json.JSONDecodeError: current_manifest = {}
                
                for key, value in manifest_data.items():
                    if value is None: current_manifest.pop(key, None)
                    else: current_manifest[key] = value
                pm.set_metadata("project_manifest", json.dumps(current_manifest))
        
        if runner and runner.blueprint.steps[-1].step_id == step_id:
            if self.active_chat_widget and hasattr(self.active_chat_widget, 'hide_status'):
                self.active_chat_widget.hide_status()
                
            if getattr(runner.blueprint, 'name', '') == "Document Analysis" and self.get_target("analysis_tab"):
                tab = self.get_target("analysis_tab")
                if hasattr(tab, 'status_lbl'):
                    tab.status_lbl.setText("✅ Full Document Analysis Complete.")

        target_widget = None

        if ui_format == "nested_outline":
            from gui.docks.unified_research.components.dynamic_outlines import UniversalOutlineWidget
            title = getattr(step, 'ui_title', 'AI Analysis')
            if state_snapshot:
                state_dict = json.loads(state_snapshot) if isinstance(state_snapshot, str) else state_snapshot
                title = StateResolver.safe_format(title, state_dict)
                
            annot_manager = self.main_window.viewer.annot_manager if hasattr(self.main_window, 'viewer') else None
            
            # THE FIX: If the LLM returned a JSON array (from multiple chunks), split it into separate widgets!
            success, parsed_data = extract_and_heal_json(result_str)
            if success and isinstance(parsed_data, list):
                for i, item in enumerate(parsed_data):
                    sub_title = f"{title} (Part {i+1})"
                    item_str = json.dumps(item)
                    tw = UniversalOutlineWidget(sub_title, item_str, self.theme, annot_manager)
                    tw._raw_ai_data = item_str # Safely inject for DB storage
                    if target_ui: target_ui.receive_ai_widget(tw)
            else:
                target_widget = UniversalOutlineWidget(title, result_str, self.theme, annot_manager)
                target_widget._raw_ai_data = result_str # Safely inject for DB storage

        elif ui_format == "data_table":
            from gui.docks.unified_research.components.dynamic_data_table import DynamicDataTableWidget
            target_widget = DynamicDataTableWidget(result_str, self.theme)

        elif ui_format == "card_grid":
            from gui.docks.unified_research.components.dynamic_card_grid import DynamicCardGridWidget
            target_widget = DynamicCardGridWidget(result_str, self.theme)

        elif ui_format == "search_terms":
            if target_ui and hasattr(target_ui, 'render_search_terms'):
                success, items = extract_and_heal_json(result_str)
                if success:
                    target_ui.render_search_terms(items)

        elif ui_format == "chat_widgets":
            # 3. Use the new JSON healing utility to isolate the data
            success, items = extract_and_heal_json(result_str)
            if success:
                # Handle the dictionary wrap you saw in the debug logs {"citations": [...]}
                if isinstance(items, dict):
                    for val in items.values():
                        if isinstance(val, list): items = val; break
                    if isinstance(items, dict): items = [items] 
                
                widget = self._get_or_create_chat_widget(target_id)
                for item in items:
                    if isinstance(item, dict):
                        widget.add_bubble(
                            doc_name=item.get("doc_name", "Unknown Document"),
                            quote=item.get("quote", item.get("text", "")),
                            note=item.get("note", item.get("reason", ""))
                        )

        elif ui_format == "workspace_graph":
            from PySide6.QtWidgets import QGraphicsView
            workspace_view = next((c for c in self.main_window.findChildren(QGraphicsView) if c.__class__.__name__ == "WorkspaceView"), None)
            if workspace_view:
                if hasattr(workspace_view, 'review_and_apply_ai_graph_update'):
                    workspace_view.review_and_apply_ai_graph_update(result_str)
                elif hasattr(workspace_view, 'apply_ai_graph_update'):
                    workspace_view.apply_ai_graph_update(result_str)
        elif ui_format == "results_dialog":
            success, items = extract_and_heal_json(result_str)
            if success and items:
                # Handle if LLM wraps the list in a dictionary
                if isinstance(items, dict):
                    for val in items.values():
                        if isinstance(val, list): items = val; break
                        
                if isinstance(items, list) and len(items) > 0:
                    # Spawn the beautiful custom dialog!
                    from gui.components.dialogs.tag_relatives_dialog import AIResultsDialog
                    title = getattr(step, 'ui_title', 'AI Results')
                    dlg = AIResultsDialog(title, items, self.main_window, self.main_window)
                    dlg.show() 
                    return
            
            # Fallback if empty
            if target_ui and hasattr(target_ui, 'status_lbl'):
                target_ui.status_lbl.setText("❌ No relevant context found.")
        # Inject the widget
        if target_widget and target_ui:
            target_ui.receive_ai_widget(target_widget)
                
        if target_id in ["chat_dock", "brainstorm_dock"] and ui_format in ["live_stream", "chat_widgets"]:
            pm = getattr(self.main_window, 'project_manager', None)
            if pm: pm.save_chat_message(target_id, "ai", result_str, ui_format)

    def _handle_error(self, err_msg):
        runner = self._get_runner()
        if not runner: return
        
        current_step = getattr(runner, 'current_executing_step', None)
        target_id = getattr(current_step, 'ui_target', 'floating') if current_step else 'floating'
        target_ui = self.get_target(target_id)

        try:
            if self.active_chat_widget and hasattr(self.active_chat_widget, 'update_status'):
                self.active_chat_widget.update_status(f"❌ Error: {err_msg}")
        except RuntimeError: pass

        if target_ui:
            if hasattr(target_ui, 'status_lbl'):
                target_ui.status_lbl.setText(f"❌ Pipeline Failed: {err_msg}")
                target_ui.status_lbl.setStyleSheet("font-weight: bold; color: #ff4444;") 
            if hasattr(target_ui, 'btn_generate'):
                target_ui.btn_generate.setEnabled(True)