from PySide6.QtCore import QObject
from core.utils.citation_utils import extract_inline_citations, strip_inline_citation_block
from core.utils.json_utils import extract_and_heal_json, extract_json_from_tags
from core.utils.state_resolver import StateResolver
import json
import shiboken6

class BlueprintUIRouter(QObject):
    """Routes workflow presentation data without constructing GUI widgets."""

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.registered_targets = {}

    def register_target(self, target_id: str, widget_instance):
        """Allows any UI element to subscribe to AI pipeline outputs."""
        self.registered_targets[target_id] = widget_instance

    def get_target(self, target_id: str):
        """Safely fetches a target, cleaning up memory if it was destroyed."""
        target = self.registered_targets.get(target_id)
        if target:
            try:
                if not shiboken6.isValid(target):
                    del self.registered_targets[target_id]
                    return None
            except Exception:
                pass
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
        self._dispatch(target_id, {
            "type": "user_input",
            "step_id": step_id,
            "expected_inputs": expected_inputs,
            "runner": runner,
        })

    def _handle_step_started(self, step_id):
        runner = self._get_runner()
        if not runner: return

        current_step = self._resolve_step(runner, step_id)

        # Read the dynamic status directly from the blueprint model
        text = getattr(current_step, 'status_text', f"Processing {step_id}...")
        target_id = getattr(current_step, 'ui_target', 'chat_dock') if current_step else 'chat_dock'
        ui_format = getattr(current_step, "ui_format", "silent") if current_step else "silent"

        if target_id in ["floating", "search_tab", "analysis_tab"] or ui_format in {"card_grid", "data_table", "search_terms", "results_dialog"}: return
        self._dispatch(target_id, {"type": "status", "text": text})

    def _handle_stream(self, chunk):
        runner = self._get_runner()
        if not runner or not hasattr(runner, 'current_executing_step'): return
        current_step = runner.current_executing_step 
        if current_step and getattr(current_step, 'ui_format', 'silent') == "live_stream":
            target_id = getattr(current_step, 'ui_target', 'floating')
            self._dispatch(target_id, {"type": "stream_chunk", "chunk": chunk})

    def _handle_step_complete(self, step_id, result_str, state_snapshot):
        runner = self._get_runner()
        if not runner: return
        
        # 1. Fetch the exact executing step, bypassing the unresolved blueprint defaults
        step = self._resolve_step(runner, step_id)

        ui_format = getattr(step, 'ui_format', 'silent') if step else 'silent'
        target_id = getattr(step, 'ui_target', 'floating') if step else 'floating'
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
            if getattr(runner.blueprint, 'name', '') == "Document Analysis" and self.get_target("analysis_tab"):
                self._dispatch("analysis_tab", {"type": "status", "text": "✅ Full Document Analysis Complete."})

        if ui_format == "nested_outline":
            title = getattr(step, 'ui_title', 'AI Analysis')
            if state_snapshot:
                state_dict = json.loads(state_snapshot) if isinstance(state_snapshot, str) else state_snapshot
                title = StateResolver.safe_format(title, state_dict)

            success, parsed_data = extract_and_heal_json(result_str)
            if success and isinstance(parsed_data, list):
                for i, item in enumerate(parsed_data):
                    item_str = json.dumps(item)
                    self._dispatch(target_id, {
                        "type": "outline",
                        "title": f"{title} (Part {i+1})",
                        "content": item_str,
                        "raw_ai_data": item_str,
                    })
            else:
                self._dispatch(target_id, {
                    "type": "outline",
                    "title": title,
                    "content": result_str,
                    "raw_ai_data": result_str,
                })

        elif ui_format == "data_table":
            self._dispatch(target_id, {"type": "data_table", "content": result_str})

        elif ui_format == "card_grid":
            self._dispatch(target_id, {"type": "card_grid", "content": result_str})

        elif ui_format == "search_terms":
            success, items = extract_and_heal_json(result_str)
            self._dispatch(target_id, {
                "type": "search_terms",
                "items": items if success else result_str,
                "success": success,
            })

        elif ui_format == "chat_widgets":
            success, items = extract_and_heal_json(result_str)
            if success:
                if isinstance(items, dict):
                    for val in items.values():
                        if isinstance(val, list): items = val; break
                    if isinstance(items, dict): items = [items] 
                self._dispatch(target_id, {"type": "citation_cards", "items": items})

        elif ui_format == "workspace_graph":
            from core.events.event_bus import EventBus
            from core.events.domains.workspace_events import WorkspaceEvent, WorkspaceEventPayload
            EventBus.get_instance().ai_graph_generated.emit(
                WorkspaceEvent.AI_GRAPH_GENERATED,
                WorkspaceEventPayload(result_text=result_str),
            )
        elif ui_format == "results_dialog":
            success, items = extract_and_heal_json(result_str)
            if success and items:
                # Handle if LLM wraps the list in a dictionary
                if isinstance(items, dict):
                    for val in items.values():
                        if isinstance(val, list): items = val; break
                        
                if isinstance(items, list) and len(items) > 0:
                    title = getattr(step, 'ui_title', 'AI Results')
                    self._dispatch(target_id, {"type": "results_dialog", "title": title, "items": items})
                    return

            self._dispatch(target_id, {"type": "status", "text": "❌ No relevant context found."})

        elif ui_format == "live_stream" and getattr(step, "inline_citations", False):
            clean_text = strip_inline_citation_block(result_str)
            if clean_text != result_str:
                self._dispatch(target_id, {"type": "replace_stream_text", "text": clean_text})
            success, citations = extract_inline_citations(result_str)
            if success:
                self._dispatch(target_id, {"type": "citation_cards", "items": citations})

        if runner and runner.blueprint.steps[-1].step_id == step_id:
            self._dispatch(target_id, {"type": "hide_status"})

        if target_id in ["chat_dock", "brainstorm_dock"] and ui_format in ["live_stream", "chat_widgets"]:
            pm = getattr(self.main_window, 'project_manager', None)
            if pm: pm.save_chat_message(target_id, "ai", result_str, ui_format)

    def _handle_error(self, err_msg):
        runner = self._get_runner()
        if not runner: return
        
        current_step = getattr(runner, 'current_executing_step', None)
        target_id = getattr(current_step, 'ui_target', 'floating') if current_step else 'floating'

        self._dispatch(target_id, {"type": "error", "message": err_msg})

    def _dispatch(self, target_id: str, payload: dict):
        target_ui = self.get_target(target_id)
        if target_ui and hasattr(target_ui, "receive_ai_payload"):
            target_ui.receive_ai_payload(payload)

    def _resolve_step(self, runner, step_id: str):
        resolved_steps = getattr(runner, "resolved_step_specs", {})
        if step_id in resolved_steps:
            return resolved_steps[step_id]

        current_step = getattr(runner, 'current_executing_step', None)
        if current_step and current_step.step_id == step_id:
            return current_step
        return next((s for s in runner.blueprint.steps if s.step_id == step_id), None)
