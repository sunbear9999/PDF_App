# core/engine/master_runner.py
import traceback
import json
import re
import uuid
from PySide6.QtCore import QMutex, QThread, QWaitCondition, Signal
from core.engine.action_model import AIActionBlueprint, ActionStep
from core.engine.execution_result import ExecutionResult, SystemEvent
from core.events.domains.workflow_events import WorkflowIntent, WorkflowPayload
from core.models.prompt_models import PromptTraceCall, PromptTraceRecord, collect_step_prompt_usage
from core.utils.state_resolver import StateResolver
from core.utils.json_utils import extract_and_heal_json


class MasterActionRunner(QThread):
    """
    Data-driven workflow execution engine.

    All step logic lives in ``core/engine/steps/`` subclasses registered in
    ``BlueprintNodeTypeRegistry``.  FOREACH and BRANCH are the only built-in
    flow-control primitives handled natively here.

    Plugin steps can still register via ``register_plugin_step_handler()`` for
    backward compatibility; they fall into the legacy-handler fallback path.
    """

    progress_update = Signal(str)
    step_complete = Signal(str, str, dict)
    step_result = Signal(str, object)   # (step_id, ExecutionResult) — Phase A3
    action_complete = Signal(dict)
    error = Signal(str)
    step_started = Signal(str)
    state_snapshot = Signal(str, str)
    user_input_requested = Signal(str, dict)

    # Class-level registry of plugin-contributed step types (persists across runs).
    _plugin_step_handlers: dict = {}

    @classmethod
    def register_plugin_step_handler(cls, step_type: str, handler_cls: type) -> None:
        cls._plugin_step_handlers[step_type] = handler_cls

    @classmethod
    def unregister_plugin_step_handler(cls, step_type: str) -> None:
        cls._plugin_step_handlers.pop(step_type, None)

    def __init__(
        self,
        blueprint: AIActionBlueprint,
        initial_state: dict,
        *,
        llm_manager=None,
        prompt_manager=None,
        step_manager=None,
        process_registry=None,
        project_manager=None,
        ontology_registry=None,
        blueprint_manager=None,
        node_type_registry=None,
        data_provider_registry=None,
    ):
        super().__init__()
        self.llm_manager = llm_manager
        self.prompt_manager = prompt_manager
        self.step_manager = step_manager
        self.registry = process_registry
        self.project_manager = project_manager
        self.ontology_registry = ontology_registry
        self.blueprint_manager = blueprint_manager
        self.node_type_registry = node_type_registry
        self.data_provider_registry = data_provider_registry

        import copy
        self.blueprint = copy.deepcopy(blueprint)
        self.state = initial_state.copy()
        self.job = None
        self.blueprint_id = getattr(self.blueprint, "_registry_id", None)
        self.target_id = self.state.get("target_id")
        self.trace_id = str(uuid.uuid4())
        self.prompt_trace = PromptTraceRecord(
            trace_id=self.trace_id,
            blueprint_id=self.blueprint_id,
            blueprint_name=getattr(self.blueprint, "name", ""),
            target_id=self.target_id,
        )
        self.current_executing_step = None
        self.resolved_step_specs = {}
        self._pause_mutex = QMutex()
        self._wait_condition = QWaitCondition()
        self._user_response = None                   # legacy; kept for backward compat
        self._user_response_holder = [None]          # shared with UserInputStep via StepContext
        self.step_handlers = self._build_step_handlers()

    def _build_step_handlers(self) -> dict:
        """Build the step dispatch table.

        Only FOREACH and BRANCH are handled natively here.  All other built-in
        step types are dispatched through the BlueprintNodeTypeRegistry (Phase B).
        Plugin steps registered via register_plugin_step_handler() provide a
        backward-compat fallback.
        """
        runner = self
        handlers: dict = {
            "FOREACH": lambda step, inputs, model: self._run_foreach(step, inputs),
            "BRANCH":  lambda step, inputs, model: self._run_branch(step, inputs),
        }
        # Merge plugin-contributed step handlers (old registration path)
        for step_type, handler_cls in self.__class__._plugin_step_handlers.items():
            def _make(cls=handler_cls):
                instance = cls()
                def _handler(step, inputs, model):
                    from core.plugins.plugin_step_protocol import StepContext
                    abort_fn = runner.job.abort_event.is_set if runner.job else None
                    ctx = StepContext(
                        project_manager=runner.project_manager,
                        llm_manager=runner.llm_manager,
                        state=runner.state,
                        abort_check=abort_fn,
                        data_provider_registry=runner.data_provider_registry,
                    )
                    return instance.execute(ctx, inputs)
                return _handler
            handlers[step_type] = _make()
        return handlers

    def register_step_handler(self, step_type: str, handler):
        """Allow additional step types to attach without editing the dispatch table."""
        if step_type and handler:
            self.step_handlers[step_type] = handler

    # ------------------------------------------------------------------
    # Phase A2 — ExecutionResult coercion & registry dispatch
    # ------------------------------------------------------------------

    def _coerce_result(self, raw) -> ExecutionResult:
        if isinstance(raw, ExecutionResult):
            return raw
        return ExecutionResult(raw_value=raw)

    def _build_step_context(self, step):
        from core.plugins.plugin_step_protocol import StepContext
        from core.engine.workflow_step_api import WorkflowStepAPI

        abort_fn = self.job.abort_event.is_set if self.job else None
        queued_events: list[SystemEvent] = []

        def _queue(event_type: str, payload: dict) -> None:
            # Stream events are time-sensitive. A Qt signal safely queues this
            # worker-thread emission onto the UI thread; storing it until the
            # step returns would make the whole response appear at once.
            if event_type == "stream_chunk":
                chunk = str((payload or {}).get("chunk", ""))
                if chunk:
                    self.progress_update.emit(chunk)
                return
            queued_events.append(SystemEvent(event_type=event_type, payload=payload))

        api = WorkflowStepAPI(state_snapshot=dict(self.state), emit_fn=_queue)
        ctx = StepContext(
            project_manager=self.project_manager,
            llm_manager=self.llm_manager,
            state=dict(self.state),
            abort_check=abort_fn,
            api=api,
            prompt_manager=self.prompt_manager,
            ontology_registry=self.ontology_registry,
            trace_id=self.trace_id,
            node_type_registry=self.node_type_registry,
            data_provider_registry=self.data_provider_registry,
            _pause_mutex=self._pause_mutex,
            _wait_condition=self._wait_condition,
            _user_response_holder=self._user_response_holder,
        )
        ctx._queued_events = queued_events  # type: ignore[attr-defined]
        return ctx

    def _attach_step_proxy(self, ctx, step) -> None:
        ctx._step_proxy = step  # type: ignore[attr-defined]

    def _process_system_events(self, events: list) -> None:
        for event in events:
            try:
                self._handle_system_event(event)
            except Exception as exc:
                print(f"[Runner] SystemEvent '{event.event_type}' failed: {exc}")

    def _handle_system_event(self, event: SystemEvent) -> None:
        et = event.event_type
        p = event.payload

        if et == "prompt_trace":
            call = PromptTraceCall(**{k: v for k, v in p.items() if k in PromptTraceCall.__dataclass_fields__})
            self.prompt_trace.calls.append(call)
            self.state["llm_prompt_trace"] = [c.as_dict() for c in self.prompt_trace.calls]

        elif et == "bus_emit":
            from core.events.event_bus import EventBus
            bus = EventBus.get_instance()
            signal_name = p.get("signal", "")
            sig = getattr(bus, signal_name, None)
            if not (sig and callable(getattr(sig, "emit", None))):
                return
            intent_str = p.get("intent") or p.get("event")
            # Special-case: workspace_action_requested needs enum + WorkspacePayload
            if signal_name == "workspace_action_requested" and intent_str == "IMPORT_GRAPH":
                try:
                    from core.events.domains.workspace_events import WorkspaceIntent, WorkspacePayload
                    data = p.get("data", {})
                    sig.emit(WorkspaceIntent.IMPORT_GRAPH, WorkspacePayload(
                        extra={
                            "graph": data.get("graph"),
                            "workspace_id": data.get("workspace_id"),
                        },
                    ))
                except Exception as exc:
                    print(f"[Runner] workspace import failed: {exc}")
                return
            payload_obj = p.get("payload")
            if intent_str is not None and payload_obj is not None:
                sig.emit(intent_str, payload_obj)
            elif payload_obj is not None:
                sig.emit(payload_obj)

        elif et == "save_chat_message":
            pass  # handled post-step by UIRouter

        elif et == "user_input_requested":
            self.user_input_requested.emit(p.get("step_id", ""), p.get("schema", {}))

        elif et == "debug_log":
            print(f"[WorkflowStepAPI log] {p.get('msg', '')}")

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def run(self):
        try:
            self._execute_step_list(self.blueprint.steps)
            if not self.job or not self.job.abort_event.is_set():
                self.action_complete.emit(self.state)
        except Exception as e:
            err_msg = str(e)
            print(f"[{self.blueprint.name} Error]:\n{traceback.format_exc()}")
            if self.registry and self.job:
                self.registry.update_job_status(self.job.id, f"Error: {err_msg}")
            self.error.emit(err_msg)
        finally:
            if self.registry and self.job:
                self.registry.complete_job(self.job.id)

    def _execute_step_list(self, steps: list):
        for step in steps:
            if getattr(self, "skip_remaining", False):
                return
            if self.job and self.job.abort_event.is_set():
                self.registry.update_job_status(self.job.id, "Aborted by User")
                return

            # LIBRARY_REF resolution
            if step.step_ref and self.step_manager:
                library_step = self.step_manager.get_step(step.step_ref)
                if library_step:
                    base_dict = library_step.__dict__.copy()
                    empty_step = ActionStep(step_id="dummy")
                    override_dict = {
                        k: v for k, v in step.__dict__.items()
                        if getattr(empty_step, k, None) != v and v is not None and v != "LIBRARY_REF"
                    }
                    base_dict.update(override_dict)
                    step = ActionStep(**base_dict)

            self._execute_single_step(step)

    def _execute_single_step(self, step: ActionStep):
        if self.registry and self.job:
            self.registry.update_job_status(self.job.id, f"Running: {step.step_id}...")

        self.current_executing_step = step
        self.resolved_step_specs[step.step_id] = step
        self.step_started.emit(step.step_id)

        resolved_inputs = {}
        for k, v in step.inputs.items():
            if step.step_type == "PYTHON_SCRIPT" and k == "script":
                resolved_inputs[k] = v
            else:
                resolved_inputs[k] = StateResolver.resolve_val(v, self.state, self.prompt_manager)

        resolved_model = StateResolver.resolve_val(step.model, self.state, self.prompt_manager)
        if not resolved_model or resolved_model == "None":
            resolved_model = self.state.get("selected_model")

        if step.step_type == "LIBRARY_REF":
            raise ValueError(f"Missing Tool: Could not find '{step.step_ref}' in the Step Library.")

        raw = self._dispatch_step(step, resolved_inputs, resolved_model)
        exec_result = self._coerce_result(raw)

        self.state[step.output_key] = exec_result.raw_value
        for k, v in exec_result.state_updates.items():
            self.state[k] = v
        self._process_system_events(exec_result.system_events)

        print(f"Step Id: {step.step_id}, Result: {str(exec_result.raw_value)[:200]}")

        try:
            safe_state = {k: str(v)[:500] + ("..." if len(str(v)) > 500 else "") for k, v in self.state.items()}
            self.state_snapshot.emit(step.step_id, json.dumps(safe_state, indent=2))
        except Exception:
            pass

        if not self.job or not self.job.abort_event.is_set():
            self.step_result.emit(step.step_id, exec_result)
            self.step_complete.emit(step.step_id, str(exec_result.raw_value), self.state.copy())

    def _dispatch_step(self, step: ActionStep, resolved_inputs: dict, resolved_model=None):
        # 1. Registry-first: step class from BlueprintNodeTypeRegistry
        if self.node_type_registry:
            node_type = self.node_type_registry.get_by_step_type(step.step_type)
            if node_type and node_type.step_cls:
                instance = node_type.step_cls()
                ctx = self._build_step_context(step)
                self._attach_step_proxy(ctx, step)
                enriched = dict(resolved_inputs)
                enriched.setdefault("_resolved_model", resolved_model)
                enriched.setdefault("_ui_format", getattr(step, "ui_format", "silent"))
                result = instance.execute(ctx, enriched)
                extra_events = getattr(ctx, "_queued_events", [])
                if isinstance(result, ExecutionResult) and extra_events:
                    result.system_events.extend(extra_events)
                return result

        # 2. Legacy handler dict (FOREACH/BRANCH natives + old plugin registrations)
        handler = self.step_handlers.get(step.step_type)
        if handler:
            return handler(step, resolved_inputs, resolved_model)

        raise ValueError(f"Unknown step type: {step.step_type}")

    # ------------------------------------------------------------------
    # User input bridge
    # ------------------------------------------------------------------

    def submit_user_input(self, data: dict):
        self._pause_mutex.lock()
        self._user_response = data
        self._user_response_holder[0] = data
        self._wait_condition.wakeAll()
        self._pause_mutex.unlock()

    # ------------------------------------------------------------------
    # Native flow-control steps (FOREACH / BRANCH)
    # These cannot be step classes because they recurse into _execute_step_list.
    # ------------------------------------------------------------------

    def _run_branch(self, step, inputs):
        logic = inputs.get("logic", "False")
        try:
            passed = eval(logic, {}, {"state": self.state})
            branch_steps = step.if_true if passed else step.if_false
            if branch_steps:
                self._execute_step_list(branch_steps)
            return passed
        except Exception as e:
            self.error.emit(f"Branch Logic Error: {e}")
            return False

    def _run_foreach(self, step, inputs):
        target_list_raw = inputs.get("list", [])

        if isinstance(target_list_raw, str):
            try:
                target_list = json.loads(target_list_raw)
            except Exception:
                target_list = [line.strip() for line in target_list_raw.split("\n") if line.strip()]
        else:
            target_list = target_list_raw

        if isinstance(target_list, dict):
            for val in target_list.values():
                if isinstance(val, list):
                    target_list = val
                    break
            if isinstance(target_list, dict):
                target_list = list(target_list.values())

        if not isinstance(target_list, list):
            target_list = [target_list]

        if "sub_blueprint" in inputs and hasattr(inputs["sub_blueprint"], "steps"):
            sub_blueprint = inputs["sub_blueprint"]
        else:
            inline_type = inputs.get("inline_type")
            if inline_type in ("LLM_QUERY", "RAG_SEARCH"):
                from core.engine.default_blueprints import DefaultBlueprints
                sub_blueprint = DefaultBlueprints.get_inline_foreach_blueprint(
                    inline_type, inputs, step.llm_options, step.ui_format
                )
            else:
                sub_bp_name = inputs.get("sub_blueprint_name")
                sub_blueprint = None
                if self.blueprint_manager:
                    sub_blueprint = self.blueprint_manager.get_blueprint(sub_bp_name, lambda: None)
                if not sub_blueprint:
                    raise ValueError(f"FOREACH failed: Could not find tool '{sub_bp_name}'")

        aggregated_results = []
        for idx, item in enumerate(target_list):
            if self.job and self.job.abort_event.is_set():
                break
            if self.registry and self.job:
                self.registry.update_job_status(
                    self.job.id, f"Processing {idx + 1}/{len(target_list)}: {str(item)[:20]}..."
                )

            sub_state = self.state.copy()
            sub_state["item"] = item
            self.state["item"] = item

            for sub_step in sub_blueprint.steps:
                if self.job and self.job.abort_event.is_set():
                    break

                self.current_executing_step = sub_step
                self.resolved_step_specs[sub_step.step_id] = sub_step
                self.step_started.emit(sub_step.step_id)

                res_inputs = {
                    k: StateResolver.resolve_val(v, sub_state, self.prompt_manager)
                    for k, v in sub_step.inputs.items()
                }
                res_model = StateResolver.resolve_val(sub_step.model, sub_state, self.prompt_manager)

                parent_state = self.state
                self.state = sub_state
                try:
                    raw_out = self._dispatch_step(sub_step, res_inputs, res_model)
                finally:
                    self.state = parent_state

                # Coerce ExecutionResult → raw_value so the foreach loop
                # can JSON-serialize sub-step results without TypeError
                exec_out = self._coerce_result(raw_out)
                raw_val = exec_out.raw_value
                sub_state[sub_step.output_key] = raw_val
                self._process_system_events(exec_out.system_events)
                for k, v in exec_out.state_updates.items():
                    sub_state[k] = v

                if not self.job or not self.job.abort_event.is_set():
                    self.step_complete.emit(sub_step.step_id, str(raw_val), sub_state.copy())

            if self.job and self.job.abort_event.is_set():
                break

            final_result = sub_state.get(sub_blueprint.steps[-1].output_key)

            if isinstance(final_result, (dict, list)):
                # Already a Python object — no JSON round-trip needed.
                parsed_res = final_result
            else:
                try:
                    parsed_res = json.loads(final_result)
                except Exception:
                    success, healed = extract_and_heal_json(str(final_result))
                    parsed_res = healed if success else final_result

            if step.step_id == "process_all_chunks" and isinstance(item, dict):
                parsed_res = self._analysis_runtime().validate_chunk_observation_quotes(
                    parsed_res,
                    item.get("text", ""),
                )

            if isinstance(parsed_res, list):
                aggregated_results.extend(parsed_res)
            else:
                aggregated_results.append(parsed_res)

            # Analysis-pipeline chunk progress events
            if step.step_id == "process_all_chunks":
                self._emit_chunk_progress(idx, len(target_list), parsed_res)

            self.step_complete.emit(
                f"{step.step_id}_item_{idx}", str(final_result), sub_state.copy()
            )

        self.current_executing_step = step

        if step.step_id == "process_all_chunks":
            try:
                from core.events.event_bus import EventBus
                EventBus.get_instance().workflow_action_requested.emit(
                    WorkflowIntent.ANALYSIS_REFRESH_REQUESTED,
                    WorkflowPayload(),
                )
            except Exception:
                pass

        # Deduplicate quote-bearing results
        deduped: list = []
        seen: set = set()
        for res in aggregated_results:
            if isinstance(res, dict) and "quote" in res:
                key = re.sub(r"\W+", "", res["quote"].lower())
                if key not in seen:
                    seen.add(key)
                    deduped.append(res)
            else:
                deduped.append(res)

        return json.dumps(deduped)

    def _emit_chunk_progress(self, idx: int, total: int, parsed_res) -> None:
        """Emit per-chunk analysis progress events (used by the analysis pipeline FOREACH)."""
        try:
            from core.events.event_bus import EventBus
            from core.events.domains.analysis_events import AnalysisEvent, AnalysisPayload
            bus = EventBus.get_instance()

            # Hierarchical pipeline: EvidenceStoreStep already emitted CHUNK_EVIDENCE_READY.
            # The compact-refs dict {chunk_id, refs} is not a chunk observation — skip CHUNK_RESULT.
            if isinstance(parsed_res, dict) and "refs" in parsed_res and "chunk_id" in parsed_res:
                bus.analysis_result_changed.emit(
                    AnalysisEvent.PROGRESS,
                    AnalysisPayload(
                        doc_path=self.state.get("analysis_doc_path"),
                        template_id=self.state.get("analysis_template_id"),
                        run_id=self.state.get("analysis_run_id"),
                        result={"message": f"Extracted evidence from chunk {idx + 1}/{total}"},
                    ),
                )
                return

            runtime = self._analysis_runtime()
            contract = self.state.get("analysis_contract") or {}
            if isinstance(parsed_res, dict) and runtime._is_chunk_observation(parsed_res):
                chunk_norm = runtime._normalize_chunk_observation(parsed_res, idx)
            else:
                chunk_norm = runtime.normalize_graph_object(
                    parsed_res if isinstance(parsed_res, dict) else {}, f"chunk{idx}", contract
                )

            bus.analysis_result_changed.emit(
                AnalysisEvent.CHUNK_RESULT,
                AnalysisPayload(
                    doc_path=self.state.get("analysis_doc_path"),
                    template_id=self.state.get("analysis_template_id"),
                    run_id=self.state.get("analysis_run_id"),
                    result={
                        "chunk_number": idx + 1,
                        "total_chunks": total,
                        "doc_path": self.state.get("analysis_doc_path"),
                        "chunk": chunk_norm,
                    },
                ),
            )
            bus.analysis_result_changed.emit(
                AnalysisEvent.PROGRESS,
                AnalysisPayload(
                    doc_path=self.state.get("analysis_doc_path"),
                    template_id=self.state.get("analysis_template_id"),
                    run_id=self.state.get("analysis_run_id"),
                    result={"message": f"Completed chunk {idx + 1}/{total}."},
                ),
            )
        except Exception:
            pass

    def _analysis_runtime(self):
        from core.engine.analysis_runtime import AnalysisRuntime
        return AnalysisRuntime(
            self.project_manager,
            self.prompt_manager,
            self.ontology_registry,
        )


# Backward-compat alias
MasterWorkflowRunner = MasterActionRunner
