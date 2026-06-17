from __future__ import annotations

import copy

from PySide6.QtCore import QObject, Signal

from core.api.workspace_ai import WorkspaceAIApi
from core.models.workspace_models import WorkspaceModel
from core.events.domains.workspace_events import WorkspaceEvent, WorkspaceEventPayload


class WorkspaceAIService(QObject):
    error = Signal(str)
    dialog_result = Signal(object, object)
    graph_result = Signal(object, bool)

    def __init__(self, main_window, workspace_service, graph_service, annot_service, ai_tools_registry, event_bus=None, workflow_runner_service=None, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.workspace_service = workspace_service
        self.graph_service = graph_service
        self.annot_service = annot_service
        self.ai_tools_registry = ai_tools_registry
        self.bus = event_bus
        self.workflow_runner_service = workflow_runner_service
        self.api = WorkspaceAIApi(getattr(main_window, "project_manager", None))

        self.active_model = "gemma4:e2b"
        self.current_workspace_id = 1

        if self.bus:
            self.bus.active_model_changed.connect(self._on_model_changed)
            self.bus.workspace_loaded.connect(self._on_ws_loaded)
            self.bus.run_ai_tool.connect(self._on_run_tool)
            self.bus.ai_graph_generated.connect(self._on_graph_generated)

    def _on_model_changed(self, event: WorkspaceEvent, payload: WorkspaceEventPayload):
        if event == WorkspaceEvent.ACTIVE_MODEL_CHANGED:
            self.active_model = payload.model_name

    def _on_ws_loaded(self, event: WorkspaceEvent, payload: WorkspaceEventPayload):
        if event == WorkspaceEvent.LOADED and payload.workspace_id is not None:
            self.current_workspace_id = payload.workspace_id

    def resolve_active_model(self) -> str:
        return self.active_model

    def _on_run_tool(self, event: WorkspaceEvent, payload: WorkspaceEventPayload):
        if event != WorkspaceEvent.RUN_AI_TOOL:
            return
        tool_id = payload.get("tool_id")
        selected_ids = payload.get("selected_ids", [])

        tool = self.ai_tools_registry.get(tool_id) if self.ai_tools_registry else None
        if not tool:
            self.error.emit(f"Workspace AI tool '{tool_id}' is not registered.")
            return

        current_model = self.workspace_service.load_workspace(self.current_workspace_id)
        ok, message = self.enqueue_tool(tool, current_model, selected_ids)
        if not ok:
            self.error.emit(message)

    def _on_graph_generated(self, event: WorkspaceEvent, payload: WorkspaceEventPayload):
        if event != WorkspaceEvent.AI_GRAPH_GENERATED:
            return
        ai_output_string = payload.result_text or ""
        current_model = self.workspace_service.load_workspace(self.current_workspace_id)
        success, result = self.process_response(ai_output_string, self.current_workspace_id, current_model)

        if not success:
            self.error.emit(f"AI Formatting Error: {result[:250]}...")
            return

        pm = getattr(self.main_window, "project_manager", None)
        existing_keys = [n.id for n in current_model.nodes]
        delta_model = self.graph_service.validate_delta(result, existing_keys)

        self.annot_service.attach_native_ai_annotations(delta_model.nodes)
        self.workspace_service.sync_delta(delta_model)

    def build_context(self, model: WorkspaceModel, filters=None) -> str:
        return self.api.build_ai_context(model, filters)

    def process_response(self, raw_ai_text: str, current_workspace_id: int, current_workspace: WorkspaceModel | None = None):
        return self.api.process_ai_response(raw_ai_text, current_workspace_id, current_workspace)

    def resolve_blueprint(self, tool_definition):
        blueprint_manager = getattr(self.main_window, "blueprint_manager", None)
        prompt_manager = getattr(self.main_window, "prompt_manager", None)
        if blueprint_manager:
            return blueprint_manager.get_blueprint(
                tool_definition.blueprint_key,
                tool_definition.fallback_factory,
                prompt_manager,
            )
        return copy.deepcopy(tool_definition.fallback_factory(prompt_manager))

    def enqueue_tool(self, tool_definition, workspace_model: WorkspaceModel, selected_ids):
        llm = getattr(self.main_window, "shared_llm_manager", None)
        if not llm or not getattr(llm, "ai_enabled", False):
            return False, "Local AI is not running."

        blueprint = self.resolve_blueprint(tool_definition)
        context_model = workspace_model
        if tool_definition.requires_selection:
            context_model = self.graph_service.selected_subset(workspace_model, selected_ids)
            if not context_model.nodes:
                return False, "Please select nodes to process."

        permissions = tool_definition.resolve_filters(blueprint)
        initial_state = {
            "workspace_data": self.build_context(context_model, permissions),
            "selected_model": self.resolve_active_model(),
        }

        runtime_blueprint = copy.deepcopy(blueprint)
        if not runtime_blueprint.name.startswith("Workspace:"):
            runtime_blueprint.name = f"Workspace: {runtime_blueprint.name}"

        if not self.workflow_runner_service:
            return False, "Workflow runner service is not configured."

        runner = self.workflow_runner_service.prepare_runner(runtime_blueprint, initial_state)
        runner.target_id = "workspace"
        if hasattr(runner, "prompt_trace"):
            runner.prompt_trace.target_id = "workspace"

        def _handle_completion(state):
            if not runtime_blueprint.steps:
                return
            last_step = runtime_blueprint.steps[-1]
            ai_output = state.get(last_step.output_key, "")
            output_mode = getattr(last_step, "output_mode", "workspace_update")
            ui_format = getattr(last_step, "ui_format", "")
            if output_mode == "dialog":
                self.dialog_result.emit(runtime_blueprint, ai_output)
            elif ui_format == "workspace_graph" or output_mode == "workspace_update":
                self.graph_result.emit(ai_output, tool_definition.review_before_apply)

        def _handle_error(err):
            self.error.emit(str(err))

        runner.action_complete.connect(_handle_completion)
        runner.error.connect(_handle_error)
        self.workflow_runner_service.start_runner(runner, job_name=runtime_blueprint.name)
        return True, "Queued"
