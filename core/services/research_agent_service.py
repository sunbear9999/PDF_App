from __future__ import annotations

import json
from typing import Any, Callable, Dict, Optional

from PySide6.QtCore import QObject, Signal

from core.models.research_agent_models import (
    ResearchAgentCheckpoint,
    ResearchAgentSession,
    ResearchAgentToolRun,
)
from core.events.domains.research_agent_events import ResearchAgentEvent, ResearchAgentPayload
from core.services.agent_loop_service import AgentContextBuilder, AgentLoopConfig
from core.utils.json_utils import extract_and_heal_json


WorkflowExecutor = Callable[[object, Dict[str, Any]], object]
RunnerStarter = Callable[[object], None]
ModelProvider = Callable[[], str]
PLANNER_BLUEPRINT_ID = "Research Agent Planner"
SESSION_METADATA_KEY = "research_agent_session"
RESEARCH_AGENT_CONFIG = AgentLoopConfig(
    name="Research Agent",
    planner_blueprint_id=PLANNER_BLUEPRINT_ID,
    session_metadata_key=SESSION_METADATA_KEY,
    ui_target="research_agent",
    goal_state_key="research_goal",
    default_tool_persona="RAG Agent Mode",
    memory_limit=5000,
    recent_artifact_limit=6,
)


class ResearchAgentService(QObject):
    session_updated = Signal(object, object)
    status_changed = Signal(object, object)
    checkpoint_requested = Signal(object, object)
    error = Signal(object, object)

    def __init__(
        self,
        project_manager,
        prompt_manager,
        blueprint_registry,
        workflow_executor: WorkflowExecutor,
        runner_starter: Optional[RunnerStarter] = None,
        model_provider: Optional[ModelProvider] = None,
        config: AgentLoopConfig = RESEARCH_AGENT_CONFIG,
        parent=None,
    ):
        super().__init__(parent)
        self.config = config
        self.project_manager = project_manager
        self.prompt_manager = prompt_manager
        self.blueprint_registry = blueprint_registry
        self.workflow_executor = workflow_executor
        self.runner_starter = runner_starter
        self.model_provider = model_provider or (lambda: "")
        self.session: Optional[ResearchAgentSession] = None
        self._active_planner_runner = None
        self._active_tool_runner = None
        self.context_builder = AgentContextBuilder(project_manager, blueprint_registry, self.model_provider)
        self.load_session()

    def start_session(self, goal: str) -> ResearchAgentSession:
        self.session = ResearchAgentSession(goal=goal.strip())
        self._emit_update("Research session started.")
        self.plan_next()
        return self.session

    def reset_session(self):
        self.session = None
        if self.project_manager:
            self.project_manager.set_metadata(self.config.session_metadata_key, "")
        self.status_changed.emit(
            ResearchAgentEvent.STATUS_CHANGED,
            ResearchAgentPayload(message="Describe a research goal to begin."),
        )
        self.session_updated.emit(ResearchAgentEvent.SESSION_UPDATED, ResearchAgentPayload())

    def load_session(self) -> Optional[ResearchAgentSession]:
        pm = self.project_manager
        if not pm:
            return None
        raw = pm.get_metadata(self.config.session_metadata_key, "")
        if not raw:
            return None
        try:
            data = json.loads(raw)
            if isinstance(data, dict) and data.get("goal"):
                self.session = ResearchAgentSession.from_dict(data)
                return self.session
        except Exception as exc:
            print(f"[ResearchAgentService] Failed to load session: {exc}")
        return None

    def refresh_from_project(self):
        self.session = None
        loaded = self.load_session()
        if loaded:
            self.status_changed.emit(
                ResearchAgentEvent.STATUS_CHANGED,
                ResearchAgentPayload(message=f"Loaded research session: {loaded.goal}"),
            )
            self.session_updated.emit(
                ResearchAgentEvent.SESSION_UPDATED,
                ResearchAgentPayload(session=loaded.to_dict()),
            )
        else:
            self.status_changed.emit(
                ResearchAgentEvent.STATUS_CHANGED,
                ResearchAgentPayload(message="Describe a research goal to begin."),
            )
            self.session_updated.emit(ResearchAgentEvent.SESSION_UPDATED, ResearchAgentPayload())

    def save_session(self):
        if not self.project_manager or not self.session:
            return
        try:
            self.project_manager.set_metadata(self.config.session_metadata_key, json.dumps(self.session.to_dict()))
        except Exception as exc:
            print(f"[ResearchAgentService] Failed to save session: {exc}")

    def add_user_input(self, text: str):
        if not self.session:
            self.start_session(text)
            return

        clean_text = text.strip()
        checkpoint = self.session.pending_checkpoint()
        if checkpoint:
            checkpoint.resolve(clean_text)

        self.session.latest_user_input = clean_text
        self.session.add_artifact("user_input", "User Input", clean_text, source="research_agent")
        self.session.status = "planning"
        self._emit_update("User input added.")
        self.plan_next()

    def plan_next(self):
        if not self.session:
            return
        if self.session.pending_checkpoint():
            self.session.status = "waiting_for_user"
            self._emit_update("Waiting for user input.")
            return

        planner = self.blueprint_registry.create(self.config.planner_blueprint_id, pm=self.prompt_manager)
        if not planner:
            self._emit_error(f"{self.config.planner_blueprint_id} is not registered.")
            return

        state = self._build_base_state()
        self.session.status = "planning"
        self._emit_update("Planning next action...")
        runner = self.workflow_executor(planner, state)
        self._active_planner_runner = runner
        if runner:
            runner.action_complete.connect(self._handle_planner_complete)
            runner.error.connect(self._emit_error)
            self._start_runner(runner)

    def _handle_planner_complete(self, state: Dict[str, Any]):
        raw_plan = state.get("agent_plan", "")
        plan = self._parse_plan(raw_plan)
        if not plan:
            self._emit_error("Planner did not return a usable plan.")
            return

        session = self.session
        if not session:
            return

        memory_update = str(plan.get("memory_update", "")).strip()
        if memory_update:
            session.memory = self._compact_memory(session.memory, memory_update)

        summary = str(plan.get("summary", "")).strip()
        if summary:
            session.add_artifact("planner_summary", "Planner Summary", summary, source="Research Agent Planner")

        manifest_suggestions = plan.get("manifest_suggestions")
        if manifest_suggestions:
            session.add_artifact(
                "manifest_suggestions",
                "Manifest Suggestions",
                manifest_suggestions,
                source="Research Agent Planner",
            )

        next_action = plan.get("next_action") or {}
        action_type = next_action.get("type") or plan.get("status")

        if action_type in {"checkpoint", "waiting_for_user"}:
            self._add_checkpoint(next_action.get("checkpoint") or next_action)
            return

        if action_type == "run_blueprint":
            self._run_blueprint_action(next_action)
            return

        if action_type == "complete" or plan.get("status") == "complete":
            session.status = "complete"
            self._emit_update("Research session complete.")
            return

        self._add_checkpoint({
            "kind": "custom",
            "prompt": "The planner needs your direction before continuing.",
            "options": [],
        })

    def _run_blueprint_action(self, action: Dict[str, Any]):
        session = self.session
        if not session:
            return

        blueprint_id = action.get("blueprint_id")
        if not blueprint_id:
            self._emit_error("Planner requested a blueprint run without a blueprint_id.")
            return

        blueprint = self.blueprint_registry.create(blueprint_id, pm=self.prompt_manager)
        if not blueprint:
            self._emit_error(f"Planner requested unknown blueprint: {blueprint_id}")
            return

        inputs = action.get("inputs") if isinstance(action.get("inputs"), dict) else {}
        run = ResearchAgentToolRun(
            blueprint_id=blueprint_id,
            reason=str(action.get("reason", "")),
            inputs=dict(inputs),
        )
        session.tool_runs.append(run)
        session.status = "running_tool"
        self._emit_update(f"Running {blueprint_id}...")

        state = self._build_base_state()
        state.update(inputs)
        state.setdefault("goal", session.goal)
        state.setdefault("query", inputs.get("query") or inputs.get("goal") or session.goal)
        state.setdefault("user_query", inputs.get("user_query") or inputs.get("query") or session.goal)
        state.setdefault("chat_history", session.memory)
        state.setdefault("chat_persona", self.config.default_tool_persona)

        runner = self.workflow_executor(blueprint, state)
        self._active_tool_runner = runner
        if runner:
            runner.action_complete.connect(lambda result_state, tool_run=run: self._handle_tool_complete(tool_run, result_state))
            runner.error.connect(lambda message, tool_run=run: self._handle_tool_error(tool_run, message))
            self._start_runner(runner)

    def _handle_tool_complete(self, tool_run: ResearchAgentToolRun, state: Dict[str, Any]):
        summary = self._summarize_tool_state(tool_run.blueprint_id, state)
        tool_run.complete(summary)
        if self.session:
            self.session.add_artifact("tool_result", tool_run.blueprint_id, summary, source=tool_run.blueprint_id)
            self.session.status = "planning"
            self._emit_update(f"{tool_run.blueprint_id} complete.")
            self.plan_next()

    def _handle_tool_error(self, tool_run: ResearchAgentToolRun, message: str):
        tool_run.fail(message)
        if self.session:
            self.session.status = "error"
            self._emit_update(f"{tool_run.blueprint_id} failed.")
        self._emit_error(message)

    def _add_checkpoint(self, checkpoint_data: Dict[str, Any]):
        if not self.session:
            return
        checkpoint = ResearchAgentCheckpoint(
            kind=str(checkpoint_data.get("kind", "custom")),
            prompt=str(checkpoint_data.get("prompt", "What should the agent do next?")),
            options=self._coerce_options(checkpoint_data.get("options", [])),
        )
        self.session.checkpoints.append(checkpoint)
        self.session.status = "waiting_for_user"
        self.session.touch()
        self.checkpoint_requested.emit(
            ResearchAgentEvent.CHECKPOINT_REQUESTED,
            ResearchAgentPayload(checkpoint=checkpoint.to_dict()),
        )
        self._emit_update("Waiting for user input.")

    def _build_base_state(self) -> Dict[str, Any]:
        session = self.session
        return self.context_builder.build(session, self.config)

    def _parse_plan(self, raw_plan: Any) -> Optional[Dict[str, Any]]:
        if isinstance(raw_plan, dict):
            return raw_plan
        success, parsed = extract_and_heal_json(str(raw_plan))
        if success and isinstance(parsed, dict):
            return parsed.get("final_output", parsed)
        return None

    def _compact_memory(self, existing: str, update: str) -> str:
        chunks = [chunk.strip() for chunk in [existing, update] if chunk and chunk.strip()]
        return "\n".join(chunks)[-self.config.memory_limit:]

    def _coerce_options(self, value: Any) -> list:
        if isinstance(value, list):
            return [str(item) for item in value if str(item).strip()]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

    def _summarize_tool_state(self, blueprint_id: str, state: Dict[str, Any]) -> str:
        interesting_keys = ["final_answer", "search_array", "final_analysis", "agent_plan", "result"]
        for key in interesting_keys:
            value = state.get(key)
            if value:
                return str(value)[:4000]
        return json.dumps({k: str(v)[:500] for k, v in state.items() if not k.startswith("__")}, indent=2)[:4000]

    def _emit_update(self, status: str):
        self.status_changed.emit(
            ResearchAgentEvent.STATUS_CHANGED,
            ResearchAgentPayload(message=status),
        )
        if self.session:
            self.session.touch()
            self.save_session()
            self.session_updated.emit(
                ResearchAgentEvent.SESSION_UPDATED,
                ResearchAgentPayload(session=self.session.to_dict()),
            )

    def _emit_error(self, message: str):
        self.error.emit(
            ResearchAgentEvent.ERROR,
            ResearchAgentPayload(message=str(message)),
        )
        if self.session:
            self.session.status = "error"
            self._emit_update(f"Error: {message}")

    def _start_runner(self, runner):
        if self.runner_starter:
            self.runner_starter(runner)
