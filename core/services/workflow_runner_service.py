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
        app_context,
        event_bus: Optional[EventBus] = None,
        process_registry=None,
        ui_router=None,
        model_provider: Optional[Callable[[], str]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.app_context = app_context
        self.bus = event_bus or EventBus.get_instance()
        self.process_registry = process_registry or getattr(app_context, "process_registry", None)
        self.ui_router = ui_router
        self.model_provider = model_provider
        self.bus.workflow_action_requested.connect(self.handle_intent)

    def set_ui_router(self, ui_router):
        self.ui_router = ui_router

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
            )
        elif intent == WorkflowIntent.ABORT_WORKFLOW:
            self._abort_active_workflow()

    def run_blueprint(self, blueprint, initial_state: Optional[dict] = None, request_id: Optional[str] = None, **kwargs):
        runner = self.prepare_runner(blueprint, initial_state or {})
        runner.workflow_request_id = request_id
        self.start_runner(runner, **kwargs)
        return runner

    def prepare_runner(self, blueprint, initial_state: Optional[dict] = None):
        if blueprint is None:
            raise ValueError("Workflow blueprint is required.")
        state = dict(initial_state or {})
        if "selected_model" not in state and self.model_provider:
            state["selected_model"] = self.model_provider()

        llm_manager = getattr(self.app_context, "shared_llm_manager", None)
        project_manager = getattr(self.app_context, "project_manager", None)
        if llm_manager and getattr(llm_manager, "collection", None) is None and project_manager:
            project_manager._mount_project_database()

        runner = MasterActionRunner(self.app_context, blueprint, state)
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
        else:
            runner.start()
        return runner

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
            lambda final_state, r=runner: self.bus.workflow_state_changed.emit(
                WorkflowEvent.COMPLETED,
                WorkflowPayload(blueprint=getattr(r, "blueprint", None), job_id=getattr(r, "workflow_request_id", None), initial_state=final_state, runner=r),
            )
        )
        runner.error.connect(
            lambda err, r=runner: self.bus.workflow_state_changed.emit(
                WorkflowEvent.FAILED,
                WorkflowPayload(blueprint=getattr(r, "blueprint", None), job_id=getattr(r, "workflow_request_id", None), errors=str(err), runner=r),
            )
        )
        if hasattr(runner, "user_input_requested"):
            runner.user_input_requested.connect(
                lambda step_id, expected, r=runner: self.bus.workflow_state_changed.emit(
                    WorkflowEvent.USER_INPUT_REQUESTED,
                    WorkflowPayload(blueprint=getattr(r, "blueprint", None), job_id=getattr(r, "workflow_request_id", None), data={"step_id": step_id, "expected_inputs": expected}, runner=r),
                )
            )

    def _abort_active_workflow(self):
        registry = self.process_registry
        job = getattr(registry, "active_job", None) if registry else None
        if job and hasattr(job, "cancel"):
            job.cancel()
