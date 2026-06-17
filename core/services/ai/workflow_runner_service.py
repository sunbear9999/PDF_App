from __future__ import annotations

from typing import Any, Callable, Optional

from PySide6.QtCore import QObject

from core.engine.master_runner import MasterActionRunner
from core.events.domains.workflow_events import WorkflowEvent, WorkflowIntent, WorkflowPayload
from core.events.event_bus import EventBus


class WorkflowRunnerService(QObject):
    """Owns workflow runner creation, queueing, and runner lifecycle events."""

    def __init__(
        self,
        *,
        llm_manager,
        prompt_manager,
        step_manager=None,
        process_registry=None,
        project_manager=None,
        ontology_registry=None,
        blueprint_manager=None,
        event_bus: Optional[EventBus] = None,
        ui_router=None,
        model_provider: Optional[Callable[[], str]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.llm_manager = llm_manager
        self.prompt_manager = prompt_manager
        self.step_manager = step_manager
        self.process_registry = process_registry
        self.project_manager = project_manager
        self.ontology_registry = ontology_registry
        self.blueprint_manager = blueprint_manager
        self.bus = event_bus or EventBus.get_instance()
        self.ui_router = ui_router
        self.model_provider = model_provider
        self.bus.workflow_action_requested.connect(self.handle_intent)

    def set_ui_router(self, ui_router):
        self.ui_router = ui_router

    def set_model_provider(self, provider: Callable[[], str]):
        self.model_provider = provider

    def handle_intent(self, intent, payload):
        if isinstance(payload, dict):
            payload = WorkflowPayload(**payload)
        if intent == WorkflowIntent.RUN_BLUEPRINT:
            self.run_blueprint(
                payload.blueprint,
                payload.initial_state,
                is_express=payload.is_express,
                job_name=payload.job_name,
                job_type=payload.job_type,
                request_id=payload.job_id,
                target_id=payload.target_id,
                blueprint_id=getattr(payload, "blueprint_id", None),
            )
        elif intent == WorkflowIntent.ABORT_WORKFLOW:
            self._abort_active_workflow()

    def run_blueprint(self, blueprint, initial_state: Optional[dict] = None, request_id: Optional[str] = None, **kwargs):
        runner = self.prepare_runner(blueprint, initial_state or {})
        runner.workflow_request_id = request_id
        runner.target_id = kwargs.pop("target_id", None) or getattr(runner, "target_id", None)
        runner.blueprint_id = kwargs.pop("blueprint_id", None) or getattr(runner, "blueprint_id", None)
        if hasattr(runner, "prompt_trace"):
            runner.prompt_trace.target_id = runner.target_id
            runner.prompt_trace.blueprint_id = runner.blueprint_id
        self.start_runner(runner, **kwargs)
        return runner

    def prepare_runner(self, blueprint, initial_state: Optional[dict] = None):
        if blueprint is None:
            raise ValueError("Workflow blueprint is required.")
        state = dict(initial_state or {})
        if "selected_model" not in state and self.model_provider:
            state["selected_model"] = self.model_provider()

        if self.llm_manager and getattr(self.llm_manager, "collection", None) is None and self.project_manager:
            self.project_manager._mount_project_database()

        runner = MasterActionRunner(
            blueprint,
            state,
            llm_manager=self.llm_manager,
            prompt_manager=self.prompt_manager,
            step_manager=self.step_manager,
            process_registry=self.process_registry,
            project_manager=self.project_manager,
            ontology_registry=self.ontology_registry,
            blueprint_manager=self.blueprint_manager,
        )
        self._attach_runner(runner)
        return runner

    def start_runner(self, runner, is_express: bool = False, job_name: Optional[str] = None, job_type: Optional[str] = None):
        blueprint = getattr(runner, "blueprint", None)
        resolved_job_name = job_name or getattr(blueprint, "name", "AI Action")
        resolved_job_type = job_type or ("Express Tool" if is_express else "Agent")
        self.bus.workflow_state_changed.emit(
            WorkflowEvent.STARTED,
            WorkflowPayload(blueprint=blueprint, initial_state=getattr(runner, "state", {}), job_id=getattr(runner, "workflow_request_id", None), job_name=resolved_job_name, runner=runner),
        )
        if self.process_registry:
            self.process_registry.enqueue_runner(runner, resolved_job_name, resolved_job_type, is_express=is_express)
            job = getattr(runner, "job", None)
            if job:
                job.trace_id = getattr(runner, "trace_id", None)
                job.target_id = getattr(runner, "target_id", None)
                job.blueprint_id = getattr(runner, "blueprint_id", None) or getattr(blueprint, "_registry_id", None)
        else:
            runner.start()
        return runner

    def _persist_prompt_trace(self, runner):
        trace = getattr(runner, "prompt_trace", None)
        if not trace or not getattr(trace, "calls", None):
            return None
        trace.trace_id = getattr(runner, "trace_id", None) or trace.trace_id
        trace.job_id = getattr(runner, "workflow_request_id", None) or trace.job_id
        trace.blueprint_id = getattr(runner, "blueprint_id", None) or trace.blueprint_id
        trace.target_id = getattr(runner, "target_id", None) or trace.target_id
        blueprint = getattr(runner, "blueprint", None)
        trace.blueprint_name = getattr(blueprint, "name", trace.blueprint_name)
        pm = self.project_manager
        trace_id = pm.save_prompt_trace(trace) if pm and hasattr(pm, "save_prompt_trace") else None
        if trace_id:
            runner.trace_id = trace_id
            job = getattr(runner, "job", None)
            if job:
                job.trace_id = trace_id
        return trace_id

    def _attach_runner(self, runner):
        if self.ui_router:
            self.ui_router.attach_runner(runner)
        runner.progress_update.connect(
            lambda chunk, r=runner: self.bus.workflow_state_changed.emit(
                WorkflowEvent.PROGRESS,
                WorkflowPayload(blueprint=getattr(r, "blueprint", None), job_id=getattr(r, "workflow_request_id", None), data={"chunk": chunk}, runner=r),
            )
        )
        runner.step_started.connect(
            lambda step_id, r=runner: self.bus.workflow_state_changed.emit(
                WorkflowEvent.PROGRESS,
                WorkflowPayload(blueprint=getattr(r, "blueprint", None), job_id=getattr(r, "workflow_request_id", None), data={"step_id": step_id}, runner=r),
            )
        )
        runner.step_complete.connect(
            lambda step_id, result, snapshot, r=runner: self.bus.workflow_state_changed.emit(
                WorkflowEvent.STEP_COMPLETE,
                WorkflowPayload(blueprint=getattr(r, "blueprint", None), job_id=getattr(r, "workflow_request_id", None), data={"step_id": step_id, "result": result, "state": snapshot}, runner=r),
            )
        )
        runner.state_snapshot.connect(
            lambda step_id, state_json, r=runner: self.bus.workflow_state_changed.emit(
                WorkflowEvent.STATE_SNAPSHOT,
                WorkflowPayload(blueprint=getattr(r, "blueprint", None), job_id=getattr(r, "workflow_request_id", None), data={"step_id": step_id, "state_json": state_json}, runner=r),
            )
        )
        runner.action_complete.connect(
            lambda final_state, r=runner: self._emit_completion(r, final_state)
        )
        runner.error.connect(
            lambda err, r=runner: self._emit_failure(r, err)
        )
        if hasattr(runner, "user_input_requested"):
            runner.user_input_requested.connect(
                lambda step_id, expected, r=runner: self.bus.workflow_state_changed.emit(
                    WorkflowEvent.USER_INPUT_REQUESTED,
                    WorkflowPayload(blueprint=getattr(r, "blueprint", None), job_id=getattr(r, "workflow_request_id", None), data={"step_id": step_id, "expected_inputs": expected}, runner=r),
                )
            )

    def _trace_payload_data(self, runner):
        return {
            "trace_id": getattr(runner, "trace_id", None),
            "blueprint_id": getattr(runner, "blueprint_id", None),
            "target_id": getattr(runner, "target_id", None),
        }

    def _emit_completion(self, runner, final_state):
        trace_id = self._persist_prompt_trace(runner)
        data = self._trace_payload_data(runner)
        if trace_id:
            self.bus.ui_render_requested.emit("*", {"type": "prompt_trace_available", **data})
        self.bus.workflow_state_changed.emit(
            WorkflowEvent.COMPLETED,
            WorkflowPayload(
                blueprint=getattr(runner, "blueprint", None),
                job_id=getattr(runner, "workflow_request_id", None),
                initial_state=final_state,
                data=data,
                target_id=getattr(runner, "target_id", None),
                runner=runner,
            ),
        )

    def _emit_failure(self, runner, err):
        trace_id = self._persist_prompt_trace(runner)
        data = self._trace_payload_data(runner)
        if trace_id:
            self.bus.ui_render_requested.emit("*", {"type": "prompt_trace_available", **data})
        self.bus.workflow_state_changed.emit(
            WorkflowEvent.FAILED,
            WorkflowPayload(
                blueprint=getattr(runner, "blueprint", None),
                job_id=getattr(runner, "workflow_request_id", None),
                errors=str(err),
                data=data,
                target_id=getattr(runner, "target_id", None),
                runner=runner,
            ),
        )

    def _abort_active_workflow(self):
        registry = self.process_registry
        job = getattr(registry, "active_job", None) if registry else None
        if job and hasattr(job, "cancel"):
            job.cancel()
