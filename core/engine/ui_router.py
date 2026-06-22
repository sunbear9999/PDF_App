from PySide6.QtCore import QObject
from core.engine.ui_payloads import RESTORABLE_UI_FORMATS, build_ui_payloads, serialize_payloads
from core.engine.execution_result import ExecutionResult
from core.utils.json_utils import extract_json_from_tags, strip_tagged_block
from core.utils.state_resolver import StateResolver
from core.events.event_bus import EventBus
import json
import shiboken6

class BlueprintUIRouter(QObject):
    """Routes workflow presentation data to UI tabs via the EventBus.

    Tabs subscribe to ``bus.ui_render_requested`` and self-filter by ``target_id``.
    The ``registered_targets`` dict is retained for direct-call fallback and
    process-registry lifetime tracking during the migration period.
    """

    def __init__(self, main_window=None, *, process_registry=None, project_manager=None):
        super().__init__()
        self.bus = EventBus.get_instance()
        # Legacy shim: allow main_window for backward compat while migration is ongoing
        if main_window is not None:
            self._process_registry = getattr(main_window, 'process_registry', None)
            self._project_manager = getattr(main_window, 'project_manager', None)
        else:
            self._process_registry = process_registry
            self._project_manager = project_manager
        self.registered_targets = {}
        # Phase A3: Pending ExecutionResult per step_id for dual-path dispatch.
        # step_result signal fires first (emitted before step_complete), stores here;
        # _handle_step_complete pops it to decide old vs new routing path.
        self._pending_results: dict[str, ExecutionResult] = {}

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
        # Phase A3: step_result must be connected BEFORE step_complete so it fires first
        if hasattr(runner, "step_result"):
            runner.step_result.connect(self._handle_step_result)
        runner.step_complete.connect(self._handle_step_complete)
        runner.step_started.connect(self._handle_step_started)
        runner.error.connect(self._handle_error)
        if hasattr(runner, 'user_input_requested'):
            runner.user_input_requested.connect(self._handle_user_input)

    def _get_runner(self):
        sender = self.sender()
        if sender: return sender
        registry = self._process_registry
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

        if target_id in ["floating", "search_tab", "analysis_tab"] or ui_format in {"silent", "card_grid", "data_table", "search_terms", "results_dialog"}: return
        self._dispatch(target_id, {"type": "status", "text": text})

    def _handle_stream(self, chunk):
        runner = self._get_runner()
        if not runner or not hasattr(runner, 'current_executing_step'): return
        current_step = runner.current_executing_step 
        if current_step and getattr(current_step, 'ui_format', 'silent') == "live_stream":
            target_id = getattr(current_step, 'ui_target', 'floating')
            self._dispatch(target_id, {"type": "stream_chunk", "chunk": chunk})

    def _handle_step_result(self, step_id: str, exec_result):
        """Phase A3: Store pre-built ExecutionResult; checked by _handle_step_complete."""
        if isinstance(exec_result, ExecutionResult):
            self._pending_results[step_id] = exec_result

    def _handle_step_complete(self, step_id, result_str, state_snapshot):
        runner = self._get_runner()
        if not runner: return
        
        # 1. Fetch the exact executing step, bypassing the unresolved blueprint defaults
        step = self._resolve_step(runner, step_id)

        ui_format = getattr(step, 'ui_format', 'silent') if step else 'silent'
        target_id = getattr(step, 'ui_target', 'floating') if step else 'floating'
        trace_id = getattr(runner, "trace_id", None)
        # 2. Pure-Python Manifest Update (No raw pattern matching)
        success, manifest_data = extract_json_from_tags(result_str, "UPDATE_MANIFEST")
        if success and isinstance(manifest_data, dict):
            pm = self._project_manager
            if pm and hasattr(pm, 'db_docs'):
                current_manifest_str = pm.get_metadata("project_manifest", "{}")
                try: current_manifest = json.loads(current_manifest_str)
                except json.JSONDecodeError: current_manifest = {}
                
                for key, value in manifest_data.items():
                    if value is None: current_manifest.pop(key, None)
                    else: current_manifest[key] = value
                pm.set_metadata("project_manifest", json.dumps(current_manifest))
            manifest_payload = {
                "type": "manifest_updated",
                "text": "Project brief updated",
                "changes": manifest_data,
            }
            self._dispatch(target_id, manifest_payload)
            if pm and target_id:
                pm.save_chat_message(
                    target_id,
                    "ai",
                    "Project brief updated: " + json.dumps(manifest_data, sort_keys=True),
                    "manifest_update",
                    trace_id,
                    serialize_payloads([manifest_payload]),
                )
        
        if runner and runner.blueprint.steps[-1].step_id == step_id:
            if getattr(runner.blueprint, 'name', '') == "Document Analysis" and self.get_target("analysis_tab"):
                self._dispatch("analysis_tab", {"type": "status", "text": "✅ Full Document Analysis Complete."})

        # ------------------------------------------------------------------
        # Phase A3 dual-path: use pre-built payloads from ExecutionResult when
        # the step class already built them; fall back to legacy build_ui_payloads()
        # for built-in steps not yet migrated and for legacy plugin steps.
        # ------------------------------------------------------------------
        pending = self._pending_results.pop(step_id, None)
        pre_built_payloads = pending.ui_payloads if (pending and pending.ui_payloads) else None

        presentation_payloads = []
        if pre_built_payloads is not None:
            # New path: step already built its own payloads
            presentation_payloads = pre_built_payloads
            for payload in presentation_payloads:
                self._dispatch(target_id, payload)

        elif ui_format in RESTORABLE_UI_FORMATS:
            # Legacy path: centrally build payloads from raw result string
            title = getattr(step, 'ui_title', 'AI Analysis') if step else 'AI Analysis'
            if ui_format == "nested_outline" and state_snapshot:
                state_dict = json.loads(state_snapshot) if isinstance(state_snapshot, str) else state_snapshot
                title = StateResolver.safe_format(title, state_dict)
            presentation_payloads = build_ui_payloads(
                ui_format,
                result_str,
                step=step,
                trace_id=trace_id,
                title=title,
            )
            for payload in presentation_payloads:
                self._dispatch(target_id, payload)

        # workspace_graph → handled by LLMQueryStep via bus_emit SystemEvent
        # results_dialog  → handled by RagSearchStep via ui_payloads

        # --- FIX 1: Immediately drop the active widget status when a live stream finishes ---
        if ui_format in ["live_stream", "chat_widgets"] and not presentation_payloads:
            if trace_id:
                self._dispatch(target_id, {
                    "type": "prompt_trace_available",
                    "trace_id": trace_id,
                    "blueprint_id": getattr(runner, "blueprint_id", None),
                })
            self._dispatch(target_id, {"type": "hide_status"})

        # --- FIX 2: Broadcast completion to ALL targets so no tab gets stuck waiting ---
        if runner and runner.blueprint.steps[-1].step_id == step_id:
            for t_id in list(self.registered_targets.keys()):
                self._dispatch(t_id, {"type": "hide_status"})
            # Notify any bus-subscribed tabs that haven't registered directly
            self.bus.ui_render_requested.emit("*", {"type": "hide_status"})
                
        if target_id and ui_format in RESTORABLE_UI_FORMATS:
            pm = self._project_manager
            if pm:
                saved_result = strip_tagged_block(result_str, "UPDATE_MANIFEST")
                pm.save_chat_message(
                    target_id,
                    "ai",
                    saved_result,
                    ui_format,
                    trace_id,
                    serialize_payloads(presentation_payloads),
                )

    def _handle_error(self, err_msg):
        runner = self._get_runner()
        if not runner: return
        
        current_step = getattr(runner, 'current_executing_step', None)
        target_id = getattr(current_step, 'ui_target', 'floating') if current_step else 'floating'

        self._dispatch(target_id, {"type": "error", "message": err_msg})

    def _dispatch(self, target_id: str, payload: dict):
        # Direct-call path for already-registered targets (existing tabs).
        target_ui = self.get_target(target_id)
        if target_ui and hasattr(target_ui, "receive_ai_payload"):
            target_ui.receive_ai_payload(payload)
        else:
            # No registered target: broadcast on bus so subscribed tabs (e.g. plugins) can handle it.
            self.bus.ui_render_requested.emit(target_id, payload)

    def _resolve_step(self, runner, step_id: str):
        resolved_steps = getattr(runner, "resolved_step_specs", {})
        if step_id in resolved_steps:
            return resolved_steps[step_id]

        current_step = getattr(runner, 'current_executing_step', None)
        if current_step and current_step.step_id == step_id:
            return current_step
        return next((s for s in runner.blueprint.steps if s.step_id == step_id), None)
