# core/engine/master_runner.py
import traceback
import json
import sqlite3
import re
from PySide6.QtCore import QMutex, QThread, QWaitCondition, Signal, Qt
from core.engine.action_model import AIActionBlueprint, ActionStep
from core.events.domains.workflow_events import WorkflowIntent, WorkflowPayload
from core.utils.state_resolver import StateResolver
from core.utils.json_utils import extract_and_heal_json

class MasterActionRunner(QThread):
    progress_update = Signal(str)
    step_complete = Signal(str, str, dict)
    action_complete = Signal(dict)
    error = Signal(str)
    step_started = Signal(str)
    state_snapshot = Signal(str, str)
    user_input_requested = Signal(str, dict)

    def __init__(
        self,
        blueprint: AIActionBlueprint,
        initial_state: dict,
        *,
        llm_manager,
        prompt_manager,
        step_manager=None,
        process_registry=None,
        project_manager=None,
        ontology_registry=None,
        blueprint_manager=None,
        extra_step_handlers=None,
    ):
        super().__init__()
        self.llm_manager = llm_manager
        self.prompt_manager = prompt_manager
        self.step_manager = step_manager
        self.registry = process_registry
        self.project_manager = project_manager
        self.ontology_registry = ontology_registry
        self.blueprint_manager = blueprint_manager

        import copy
        self.blueprint = copy.deepcopy(blueprint)
        self.state = initial_state.copy()
        self.job = None
        self.current_executing_step = None
        self.resolved_step_specs = {}
        self._pause_mutex = QMutex()
        self._wait_condition = QWaitCondition()
        self._user_response = None
        self.step_handlers = self._build_step_handlers()
        if extra_step_handlers:
            self.step_handlers.update(extra_step_handlers)

    def _build_step_handlers(self):
        return {
            "LLM_QUERY": lambda step, inputs, model: self._run_llm(step, inputs, model),
            "RAG_SEARCH": lambda step, inputs, model: self._run_rag(step, inputs),
            "FOREACH": lambda step, inputs, model: self._run_foreach(step, inputs),
            "PYTHON_SCRIPT": lambda step, inputs, model: self._run_python(step, inputs),
            "USER_INPUT": lambda step, inputs, model: self._run_user_input(step, inputs),
            "BRANCH": lambda step, inputs, model: self._run_branch(step, inputs),
            "DATABASE_WRITE": lambda step, inputs, model: self._run_db_write(step, inputs),
            "GRAPH_VALIDATOR": lambda step, inputs, model: self._run_graph_validator(step, inputs), # NEW
            "ANALYSIS_CONTRACT": lambda step, inputs, model: self._run_analysis_contract(step, inputs),
            "DOCUMENT_CHUNK": lambda step, inputs, model: self._run_document_chunk(step, inputs),
            "ANALYSIS_COMPACT": lambda step, inputs, model: self._run_analysis_compact(step, inputs),
            "ANALYSIS_FINALIZE": lambda step, inputs, model: self._run_analysis_finalize(step, inputs),
            "ANALYSIS_SEND_TO_WORKSPACE": lambda step, inputs, model: self._run_analysis_send_to_workspace(step, inputs),
        }

    def register_step_handler(self, step_type: str, handler):
        """Allow future workflow/plugin step types to attach execution without editing the runner dispatch ladder."""
        if step_type and handler:
            self.step_handlers[step_type] = handler

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
            if getattr(self, 'skip_remaining', False): return
            if self.job and self.job.abort_event.is_set():
                self.registry.update_job_status(self.job.id, "Aborted by User")
                return
            
            # --- PHASE 3: STEP REF RESOLUTION ---
            if step.step_ref and self.step_manager:
                library_step = self.step_manager.get_step(step.step_ref)
                if library_step:
                    base_dict = library_step.__dict__.copy()
                    
                    # Create an empty dummy step to check defaults
                    empty_step = ActionStep(step_id="dummy")
                    
                    # THE FIX: Only override if the value differs from the default dataclass value!
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
        
        resolved_inputs = StateResolver.resolve_val(step.inputs, self.state, self.prompt_manager)
        resolved_model = StateResolver.resolve_val(step.model, self.state, self.prompt_manager)
        
        if not resolved_model or resolved_model == "None":
            resolved_model = self.state.get("selected_model")
        if step.step_type == 'LIBRARY_REF':
            raise ValueError(f"Missing Tool: Could not find '{step.step_ref}' in the Step Library.")
        result = self._dispatch_step(step, resolved_inputs, resolved_model)
        print(f"Step Id: {step.step_id}, Result: {result}")
        self.state[step.output_key] = result
        
        try:
            safe_state = {k: str(v)[:500] + ('...' if len(str(v)) > 500 else '') for k, v in self.state.items()}
            self.state_snapshot.emit(step.step_id, json.dumps(safe_state, indent=2))
        except: pass

        if not self.job or not self.job.abort_event.is_set():
            self.step_complete.emit(step.step_id, str(result), self.state.copy())

    def _dispatch_step(self, step: ActionStep, resolved_inputs: dict, resolved_model=None):
        handler = self.step_handlers.get(step.step_type)
        if not handler:
            raise ValueError(f"Unknown step type: {step.step_type}")
        return handler(step, resolved_inputs, resolved_model)

    def _run_user_input(self, step, inputs):
        schema = step.expected_inputs if hasattr(step, 'expected_inputs') else inputs
        self.user_input_requested.emit(step.step_id, schema)
        
        self._pause_mutex.lock()
        self._wait_condition.wait(self._pause_mutex)
        self._pause_mutex.unlock()
        
        result = self._user_response
        self._user_response = None 
        return json.dumps(result) if isinstance(result, dict) else str(result)

    def submit_user_input(self, data: dict):
        self._pause_mutex.lock()
        self._user_response = data
        self._wait_condition.wakeAll()
        self._pause_mutex.unlock()

    def _run_python(self, step, inputs):
        script = inputs.get('script', '') 
        
        # THE FIX: Expose analysis_api so users can call graph normalization, 
        # chunking, and schema contracts in their own custom Python tools.
        local_scope = {
            "state": self.state.copy(), 
            "result": None,
            "analysis_api": self._analysis_runtime() 
        }
        
        try:
            exec(script, {}, local_scope)
            return local_scope.get("result", "")
        except Exception as e:
            self.error.emit(f"Python Execution Error: {e}")
            return f"Script Execution Error: {e}"

    def _run_branch(self, step, inputs):
        logic = inputs.get('logic', 'False')
        try:
            passed = eval(logic, {}, {"state": self.state})
            branch_steps = step.if_true if passed else step.if_false
            if branch_steps:
                self._execute_step_list(branch_steps)
            return passed
        except Exception as e:
            self.error.emit(f"Branch Logic Error: {e}")
            return False

    def _run_db_write(self, step, inputs):
        table_name = inputs.get('table')
        payload = inputs.get('payload', {})
        pm = self.project_manager

        if not pm or not pm.project_filepath or not table_name or not payload:
            return "Failed: Missing DB Context or Payload"

        try:
            conn = sqlite3.connect(pm.project_filepath, timeout=10.0)
            cursor = conn.cursor()
            columns = ', '.join(payload.keys())
            placeholders = ', '.join(['?'] * len(payload))
            values = tuple(payload.values())
            query = f"INSERT OR REPLACE INTO {table_name} ({columns}) VALUES ({placeholders})"
            cursor.execute(query, values)
            conn.commit()
            conn.close()
            return f"Success: Wrote to {table_name}"
        except Exception as e:
            self.error.emit(f"Database Write Error: {e}")
            return f"DB Error: {e}"

    def _analysis_runtime(self):
        from core.engine.analysis_runtime import AnalysisRuntime
        return AnalysisRuntime(self.project_manager, self.prompt_manager, self.ontology_registry)

    def _run_analysis_contract(self, step, inputs):
        runtime = self._analysis_runtime()
        template = inputs.get("template") or {}
        contract = runtime.build_contract(template)
        contract["prompt_contract"] = runtime.build_prompt_contract(contract)
        self.state["analysis_limits"] = contract.get("limits", {})
        self.state["template_instructions"] = template.get("instructions", "")
        self.state["template_schema"] = contract["prompt_contract"]
        self.state["analysis_contract"] = contract
        self.state["analysis_chunk_system_prompt"] = runtime.chunk_system_prompt(template, contract)
        self.state["analysis_synthesis_system_prompt"] = runtime.synthesis_system_prompt(template, contract)
        self.state["analysis_master_system_prompt"] = runtime.master_system_prompt(template, contract)
        self.state["analysis_chunk_query_prompt"] = runtime.chunk_query_prompt(template)
        self.state["analysis_synthesis_query_prompt"] = runtime.synthesis_query_prompt(template)
        self.state["analysis_master_query_prompt"] = runtime.master_query_prompt(template)
        self.state["llm_prompt_trace"] = []
        self.state["analysis_prompt_trace"] = [
            {"step": "analysis_contract", "name": "chunk_system_prompt", "content": self.state["analysis_chunk_system_prompt"]},
            {"step": "analysis_contract", "name": "chunk_query_prompt_template", "content": self.state["analysis_chunk_query_prompt"]},
            {"step": "analysis_contract", "name": "synthesis_system_prompt", "content": self.state["analysis_synthesis_system_prompt"]},
            {"step": "analysis_contract", "name": "synthesis_query_prompt_template", "content": self.state["analysis_synthesis_query_prompt"]},
            {"step": "analysis_contract", "name": "master_system_prompt", "content": self.state["analysis_master_system_prompt"]},
            {"step": "analysis_contract", "name": "master_query_prompt_template", "content": self.state["analysis_master_query_prompt"]},
            {"step": "analysis_contract", "name": "template_schema", "content": self.state["template_schema"]},
        ]
        return contract

    def _run_document_chunk(self, step, inputs):
        runtime = self._analysis_runtime()
        template = inputs.get("template") or {}
        contract = inputs.get("contract") or self.state.get("analysis_contract") or {}
        doc_path = inputs.get("doc_path") or self.state.get("analysis_doc_path") or self.state.get("target_doc")
        template_id = inputs.get("template_id") or self.state.get("analysis_template_id") or template.get("id") or "analysis"
        chunks = runtime.chunk_document(doc_path, template_id, template, contract)
        if not chunks:
            raise ValueError("Could not parse document into analysis chunks.")
        return chunks

    def _run_analysis_compact(self, step, inputs):
        runtime = self._analysis_runtime()
        limits = self.state.get("analysis_limits") or {}
        contract = inputs.get("contract") or self.state.get("analysis_contract") or {}
        max_chars = int(inputs.get("max_chars") or limits.get("max_master_chars") or 16000)
        return runtime.compact_master_input(inputs.get("chunks", self.state.get("final_analysis", [])), max_chars, contract)

    def _run_analysis_finalize(self, step, inputs):
        runtime = self._analysis_runtime()
        template = inputs.get("template") or {}
        template_id = inputs.get("template_id") or self.state.get("analysis_template_id") or template.get("id") or "analysis"
        doc_path = inputs.get("doc_path") or self.state.get("analysis_doc_path") or self.state.get("target_doc")
        run_id = inputs.get("run_id") or self.state.get("analysis_run_id") or runtime.run_id(doc_path, template_id)
        contract = inputs.get("contract") or self.state.get("analysis_contract") or runtime.build_contract(template)
        result = runtime.normalize_result(
            doc_path,
            template_id,
            run_id,
            template,
            contract,
            inputs.get("chunks", self.state.get("final_analysis", [])),
            inputs.get("master", self.state.get("master_diagram", {})),
            inputs.get("synthesis", self.state.get("argument_synthesis", "")),
        )
        result["prompt_trace"] = {
            "rendered_templates": self.state.get("analysis_prompt_trace", []),
            "llm_calls": self.state.get("llm_prompt_trace", []),
            "blueprint": "DefaultBlueprints.get_analysis_blueprint",
        }
        runtime.save_result(result)
        self._emit_analysis_event("RESULT_READY", result, doc_path, template_id, run_id, template)
        self._emit_analysis_event("RUN_COMPLETED", result, doc_path, template_id, run_id, template)
        return result

    def _run_analysis_send_to_workspace(self, step, inputs):
        runtime = self._analysis_runtime()
        result = inputs.get("result") or self.state.get("analysis_result") or {}
        workspace_id = int(inputs.get("workspace_id") or self.state.get("analysis_workspace_id") or 1)
        summary = runtime.send_to_workspace(result, workspace_id)
        try:
            from core.events.event_bus import EventBus
            from core.events.domains.analysis_events import AnalysisEvent, AnalysisPayload
            EventBus.get_instance().analysis_result_changed.emit(
                AnalysisEvent.SENT_TO_WORKSPACE,
                AnalysisPayload(doc_path=result.get("doc_path"), template_id=result.get("template_id"), run_id=result.get("run_id"), workspace_id=workspace_id, result=summary),
            )
        except Exception:
            pass
        try:
            from core.events.event_bus import EventBus
            from core.events.domains.workspace_events import WorkspaceEvent, WorkspaceEventPayload
            EventBus.get_instance().workspace_changed.emit(
                WorkspaceEvent.CHANGED,
                WorkspaceEventPayload(workspace_id=workspace_id, summary={"analysis_sent_to_workspace": True, **summary}),
            )
        except Exception:
            pass
        return summary

    def _emit_analysis_event(self, event_name, result, doc_path=None, template_id=None, run_id=None, template=None):
        try:
            from core.events.event_bus import EventBus
            from core.events.domains.analysis_events import AnalysisEvent, AnalysisPayload
            event = getattr(AnalysisEvent, event_name)
            EventBus.get_instance().analysis_result_changed.emit(
                event,
                AnalysisPayload(doc_path=doc_path, template_id=template_id, run_id=run_id, template=template or {}, result=result if isinstance(result, dict) else {"value": result}),
            )
        except Exception:
            pass

    def _run_foreach(self, step, inputs):
        target_list_raw = inputs.get('list', [])
        
        if isinstance(target_list_raw, str):
            try: target_list = json.loads(target_list_raw)
            except: target_list = [line.strip() for line in target_list_raw.split('\n') if line.strip()]
        else:
            target_list = target_list_raw

        if isinstance(target_list, dict):
            for val in target_list.values():
                if isinstance(val, list):
                    target_list = val
                    break
            if isinstance(target_list, dict): target_list = list(target_list.values())
                
        if not isinstance(target_list, list): target_list = [target_list]

        if 'sub_blueprint' in inputs and hasattr(inputs['sub_blueprint'], 'steps'):
            sub_blueprint = inputs['sub_blueprint']
        else:
            inline_type = inputs.get('inline_type')
            if inline_type in ["LLM_QUERY", "RAG_SEARCH"]:
                from core.engine.default_blueprints import DefaultBlueprints
                sub_blueprint = DefaultBlueprints.get_inline_foreach_blueprint(inline_type, inputs, step.llm_options, step.ui_format)
            else:
                sub_bp_name = inputs.get('sub_blueprint_name')
                sub_blueprint = None
                if self.blueprint_manager:
                    sub_blueprint = self.blueprint_manager.get_blueprint(sub_bp_name, lambda: None)
                if not sub_blueprint:
                    raise ValueError(f"FOREACH failed: Could not find tool '{sub_bp_name}'")
        
        aggregated_results = []
        for idx, item in enumerate(target_list):
            if self.job and self.job.abort_event.is_set(): break
            if self.registry and self.job: self.registry.update_job_status(self.job.id, f"Processing {idx+1}/{len(target_list)}: {str(item)[:20]}...")
            
            sub_state = self.state.copy()
            sub_state['item'] = item  
            self.state['item'] = item 
            
            for sub_step in sub_blueprint.steps:
                 if self.job and self.job.abort_event.is_set(): break
                 
                 self.current_executing_step = sub_step 
                 self.resolved_step_specs[sub_step.step_id] = sub_step
                 self.step_started.emit(sub_step.step_id)
                 
                 # THE FIX: Use StateResolver cleanly here
                 res_inputs = {k: StateResolver.resolve_val(v, sub_state, self.prompt_manager) for k, v in sub_step.inputs.items()}
                 res_model = StateResolver.resolve_val(sub_step.model, sub_state, self.prompt_manager)
                 
                 parent_state = self.state
                 self.state = sub_state
                 try:
                     output = self._dispatch_step(sub_step, res_inputs, res_model)
                 finally:
                     self.state = parent_state
                     
                 sub_state[sub_step.output_key] = output
                 
                 if not self.job or not self.job.abort_event.is_set():
                     self.step_complete.emit(sub_step.step_id, str(output), sub_state.copy())
                     
            if self.job and self.job.abort_event.is_set(): break
            final_result = sub_state.get(sub_blueprint.steps[-1].output_key)
            
            try:
                parsed_res = json.loads(final_result)
            except Exception:
                success, healed = extract_and_heal_json(str(final_result))
                parsed_res = healed if success else final_result
            if isinstance(parsed_res, list):
                aggregated_results.extend(parsed_res)
            else:
                aggregated_results.append(parsed_res)

            if step.step_id == "process_all_chunks":
                try:
                    runtime = self._analysis_runtime()
                    contract = self.state.get("analysis_contract") or {}
                    if isinstance(parsed_res, dict) and runtime._is_chunk_observation(parsed_res):
                        chunk_norm = runtime._normalize_chunk_observation(parsed_res, idx)
                    else:
                        chunk_norm = runtime.normalize_graph_object(parsed_res if isinstance(parsed_res, dict) else {}, f"chunk{idx}", contract)
                    from core.events.event_bus import EventBus
                    from core.events.domains.analysis_events import AnalysisEvent, AnalysisPayload
                    EventBus.get_instance().analysis_result_changed.emit(
                        AnalysisEvent.CHUNK_RESULT,
                        AnalysisPayload(
                            doc_path=self.state.get("analysis_doc_path"),
                            template_id=self.state.get("analysis_template_id"),
                            run_id=self.state.get("analysis_run_id"),
                            result={
                                "chunk_number": idx + 1,
                                "total_chunks": len(target_list),
                                "doc_path": self.state.get("analysis_doc_path"),
                                "chunk": chunk_norm,
                            },
                        ),
                    )
                    EventBus.get_instance().analysis_result_changed.emit(
                        AnalysisEvent.PROGRESS,
                        AnalysisPayload(
                            doc_path=self.state.get("analysis_doc_path"),
                            template_id=self.state.get("analysis_template_id"),
                            run_id=self.state.get("analysis_run_id"),
                            result={"message": f"Completed chunk {idx + 1}/{len(target_list)}."},
                        ),
                    )
                except Exception:
                    pass
                
            self.step_complete.emit(f"{step.step_id}_item_{idx}", str(final_result), sub_state.copy())
            
        self.current_executing_step = step 

        if step.step_id == "process_all_chunks":
            try:
                from core.events.event_bus import EventBus
                EventBus.get_instance().workflow_action_requested.emit(
                    WorkflowIntent.ANALYSIS_REFRESH_REQUESTED,
                    WorkflowPayload(),
                )
            except Exception: pass

        deduped_results = []
        seen_quotes = set()
        for res in aggregated_results:
            if isinstance(res, dict) and "quote" in res:
                clean_quote = re.sub(r'\W+', '', res["quote"].lower())
                if clean_quote not in seen_quotes:
                    seen_quotes.add(clean_quote)
                    deduped_results.append(res)
            else:
                deduped_results.append(res)

        return json.dumps(deduped_results)

    def _run_llm(self, step, inputs, resolved_model):
        system_prompt = getattr(step, 'system_prompt', None) or inputs.get('system_prompt', '')
        
        if getattr(step, 'prompt_key', None):
            resolved_prompt_key = StateResolver.resolve_val(step.prompt_key, self.state, self.prompt_manager)
            raw_prompt = self.prompt_manager.get_prompt(resolved_prompt_key)
            if raw_prompt: 
                system_prompt = raw_prompt + "\n\n" + system_prompt

        req_context = getattr(step, 'required_context', [])
        
        if "manifest" in req_context:
            if self.state.get("allow_manifest_updates", False):
                system_prompt += "\n\n" + self.prompt_manager.get_prompt("Manifest Update Directive")
            system_prompt += "\n\n" + self.prompt_manager.get_prompt("Context Inject - Manifest")
            
        if "workspace" in req_context:
            system_prompt += "\n\n" + self.prompt_manager.get_prompt("Context Inject - Workspace")
            
        if "selected_nodes" in req_context:
            system_prompt += "\n\n" + self.prompt_manager.get_prompt("Context Inject - Selected")
            
        if "analyses" in req_context:
            system_prompt += "\n\n" + self.prompt_manager.get_prompt("Context Inject - Analyses")

        ui_format = getattr(step, 'ui_format', '')
        if ui_format == "chat_widgets":
            system_prompt += "\n\n" + self.prompt_manager.get_prompt("Format Enforcer - Chat Widgets")
        elif ui_format == "data_table":
            system_prompt += "\n\n" + self.prompt_manager.get_prompt("Format Enforcer - Data Table")
        elif ui_format == "card_grid":
            system_prompt += "\n\n" + self.prompt_manager.get_prompt("Format Enforcer - Card Grid")

        if getattr(step, "inline_citations", False) and self._has_citation_source(step):
            system_prompt += "\n\n" + self.prompt_manager.get_prompt("Inline Citation Directive")

        # THE FIX: Ensure we cleanly resolve variables in the composed system prompt
        system_prompt = StateResolver.resolve_val(system_prompt, self.state, self.prompt_manager)

        options = self._resolve_llm_options(step)
        
        import json
        if getattr(step, 'output_schema', None):
            options["json_mode"] = True
            schema_str = json.dumps(step.output_schema, indent=2)
            json_enforcer = self.prompt_manager.get_prompt("JSON Schema Enforcer")
            system_prompt += "\n\n" + json_enforcer.replace("{schema_str}", schema_str)
        elif options.get("json_mode") and "JSON" not in system_prompt:
             system_prompt += "\n\n" + self.prompt_manager.get_prompt("JSON Mode Enforcer")

        query_text = StateResolver.resolve_val(inputs.get('query', ''), self.state, self.prompt_manager)
        self._record_llm_prompt_trace(step, system_prompt, query_text, options, resolved_model)
        raw_result = self.llm_manager.query(
            question=query_text, 
            selected_model=resolved_model,
            custom_system_prompt=system_prompt, 
            abort_event=self.job.abort_event if self.job else None,
            callback=lambda chunk: self.progress_update.emit(chunk),
            rag_enabled=False, 
            json_mode=options.get("json_mode"), 
            temperature=options.get("temperature"),
            num_predict=options.get("num_predict"), 
            num_ctx=options.get("num_ctx") or 16384,
            stop=options.get("stop_sequences")
        )

        if raw_result.strip().startswith("[Generation Error"):
            raise ConnectionError(f"Engine Failed: {raw_result.strip()}")
        
        if getattr(step, 'output_schema', None):
            success, parsed = extract_and_heal_json(raw_result)
            if success:
                if isinstance(parsed, dict) and "final_output" in parsed:
                    return json.dumps(parsed["final_output"])
                # Handle cases where the LLM wrapped the desired array in a dict key
                if isinstance(parsed, dict) and len(parsed) == 1:
                    first_key = list(parsed.keys())[0]
                    if isinstance(parsed[first_key], (list, dict)):
                        return json.dumps(parsed[first_key])
                return json.dumps(parsed)
            else:
                print("[Master Runner] JSON Healer failed. Returning raw text fallback.")
                return raw_result.strip("` \n").removeprefix("json\n")
                
        return raw_result

    def _record_llm_prompt_trace(self, step, system_prompt: str, query_text: str, options: dict, resolved_model):
        step_id = getattr(step, "step_id", "")
        if step_id not in {"analyze_chunk_graph", "argument_synthesis_pass", "master_diagram_pass"}:
            return
        trace = self.state.setdefault("llm_prompt_trace", [])
        item = self.state.get("item")
        page_range = item.get("page_range") if isinstance(item, dict) else None
        trace.append({
            "step": step_id,
            "page_range": page_range,
            "model": resolved_model,
            "options": dict(options or {}),
            "system_prompt": system_prompt,
            "query": query_text,
        })

    def _resolve_llm_options(self, step) -> dict:
        options = {"temperature": 0.7, "num_predict": 2048, "num_ctx": 16384, "json_mode": False}
        raw_options = getattr(step, "llm_options", {}) or {}
        resolved_options = StateResolver.resolve_val(raw_options, self.state, self.prompt_manager)
        if isinstance(resolved_options, dict):
            options.update(resolved_options)

        options["num_predict"] = self._coerce_int_option(options.get("num_predict"), 2048, minimum=1)
        options["num_ctx"] = self._coerce_int_option(options.get("num_ctx"), 16384, minimum=1)
        options["temperature"] = self._coerce_float_option(options.get("temperature"), 0.7, minimum=0.0)
        options["json_mode"] = self._coerce_bool_option(options.get("json_mode"), False)
        return options

    @staticmethod
    def _coerce_int_option(value, default: int, minimum=None) -> int:
        try:
            coerced = int(float(value))
        except (TypeError, ValueError):
            coerced = default
        if minimum is not None and coerced < minimum:
            return default
        return coerced

    @staticmethod
    def _coerce_float_option(value, default: float, minimum=None) -> float:
        try:
            coerced = float(value)
        except (TypeError, ValueError):
            coerced = default
        if minimum is not None and coerced < minimum:
            return default
        return coerced

    @staticmethod
    def _coerce_bool_option(value, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
        return default if value in (None, "") else bool(value)

    def _has_citation_source(self, step) -> bool:
        source_key = getattr(step, "citation_source_key", None)
        if not source_key:
            return False
        value = self.state.get(source_key)
        if value is None:
            return False
        text = str(value).strip()
        return bool(text and text not in {"[]", "{}", "No relevant documents found.", "RAG is offline or collection is empty."})

    def _run_rag(self, step, inputs):
        if self.state.get('autopilot_disable_rag', False):
            return "[]" if step.ui_format == "chat_widgets" else "Context skipped by Auto-Pilot. Rely on internal knowledge."
        if not self.llm_manager.ai_enabled or not self.llm_manager.collection:
            return "[]" if step.ui_format == "chat_widgets" else "RAG is offline or collection is empty."
            
        queries_input = inputs.get("queries", [])
        
        if isinstance(queries_input, str):
            try: 
                match = re.search(r'\{.*\}|\[.*\]', queries_input, re.DOTALL)
                parsed = json.loads(match.group(0)) if match else json.loads(queries_input)
                if isinstance(parsed, dict):
                    for v in parsed.values():
                        if isinstance(v, list): parsed = v; break
                queries_input = parsed if isinstance(parsed, list) else [str(parsed)]
            except: 
                lines = [re.sub(r'^\d+\.\s*|^- \s*', '', line).strip() for line in queries_input.split('\n')]
                queries_input = [l for l in lines if len(l) > 5]
                
        allowed_docs = self._normalize_filter_list(inputs.get("allowed_docs", None))
        tag_filters = self._normalize_filter_list(inputs.get("tag_filters", None))
        tag_logic = inputs.get("tag_logic", "AND")
        aggregated_docs = {}

        for q in queries_input:
            if not q.strip(): continue
            try:
                emb = self.llm_manager.get_embedding(q)
                
                # --- FIX 1: Prevent ChromaDB crash on single documents ---
                where_clause = self._build_rag_where_clause(allowed_docs, tag_filters, tag_logic)
                
                # --- FIX 2: Allow the blueprint to ask for more results ---
                n_res = inputs.get("n_results", 3)
                
                results = self.llm_manager.collection.query(
                    query_embeddings=[emb], n_results=n_res, 
                    where=where_clause, include=["documents", "metadatas", "distances"]
                )
                
                if results.get('documents') and results['documents'][0]:
                    for idx, doc_text in enumerate(results['documents'][0]):
                        doc_id_val = results['ids'][0][idx]
                        if doc_id_val not in aggregated_docs:
                            aggregated_docs[doc_id_val] = {
                                "text": doc_text,
                                "doc_name": results['metadatas'][0][idx].get('doc_name', ''),
                                "page": results['metadatas'][0][idx].get('page', 0),
                                "distance": results['distances'][0][idx],
                                "query_used": q 
                            }
            except Exception: continue

        if not aggregated_docs:
            return "[]" if step.ui_format in ["chat_widgets", "results_dialog"] else "No relevant documents found."

        best_chunks = sorted(aggregated_docs.values(), key=lambda x: x['distance'])
        max_bubbles = min(20, len(queries_input) * 3) 
        top_chunks = best_chunks[:max_bubbles] 
        reading_order = sorted(top_chunks, key=lambda x: (x['doc_name'], x['page']))
        if step.ui_format == "results_dialog":
            matches = []
            for d in reading_order:
                matches.append({
                    "doc_name": d['doc_name'],
                    "page": d['page'],
                    "text": d['text'][:450] + "..." if len(d['text']) > 450 else d['text']
                })
            return json.dumps(matches)
        if step.ui_format == "chat_widgets":
            bubbles = []
            
            distances = [d['distance'] for d in reading_order]
            min_d = min(distances) if distances else 0
            max_d = max(distances) if distances else 1
            if max_d - min_d < 0.0001: max_d = min_d + 1
            
            for d in reading_order:
                match_pct = 95 - int(45 * (d['distance'] - min_d) / (max_d - min_d))
                bubbles.append({
                    "doc_name": d['doc_name'],
                    "quote": d['text'][:400] + "..." if len(d['text']) > 400 else d['text'],
                    "note": f"Confidence: {match_pct}% | Found via: '{d['query_used'][:30]}...'"
                })
            return json.dumps(bubbles) 
            
        context_pieces = [f"--- DOCUMENT: {d['doc_name']} | PAGE {d['page'] + 1} ---\n{d['text']}" for d in reading_order]
        return "\n\n".join(context_pieces)

    def _normalize_filter_list(self, value):
        if not value:
            return []
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                value = parsed
            except Exception:
                value = [value]
        if not isinstance(value, list):
            value = [value]
        return [str(item).strip() for item in value if str(item).strip()]

    def _build_rag_where_clause(self, allowed_docs, tag_filters, tag_logic):
        conditions = []
        if allowed_docs:
            if len(allowed_docs) == 1:
                conditions.append({"doc_name": allowed_docs[0]})
            else:
                conditions.append({"doc_name": {"$in": allowed_docs}})

        if tag_filters:
            tag_conditions = [{f"tag_{tag}": True} for tag in tag_filters]
            if str(tag_logic).upper() == "OR" and len(tag_conditions) > 1:
                conditions.append({"$or": tag_conditions})
            else:
                conditions.extend(tag_conditions)

        if not conditions:
            return None
        if len(conditions) == 1:
            return conditions[0]
        return {"$and": conditions}
    # In core/engine/master_runner.py -> _run_graph_validator
    def _run_graph_validator(self, step, inputs):
        raw_data = inputs.get("tuple_data", "{}")
        try:
            parsed = json.loads(raw_data)
        except Exception:
            success, parsed = extract_and_heal_json(str(raw_data))
            if not success: return "{}"

        nodes_in = parsed.get("nodes", [])
        edges_in = parsed.get("edges", [])
        
        valid_nodes = {}
        
        # 1. Parse Tuple-Arrays (Dynamic Nodes)
        for n in nodes_in:
            if isinstance(n, list) and len(n) >= 3:
                n_id, n_type, n_text = str(n[0]), str(n[1]), str(n[2])
                
                # Extract the trailing properties dict if the LLM provided one
                custom_props = n[3] if len(n) > 3 and isinstance(n[3], dict) else {}
                
                node_data = {
                    "id": n_id,
                    "type": n_type,
                    "text": n_text,
                    "exact_text": n_text if "quote" in n_type else None,
                }
                # Merge custom template properties (confidence, strength, etc.)
                node_data.update(custom_props)
                valid_nodes[n_id] = node_data

        healed_edges = []
        
        # 2 & 3. Parse Tuple-Arrays (Dynamic Edges & Registry Healing)
        for e in edges_in:
            if isinstance(e, list) and len(e) >= 4:
                e_id, e_type, e_src, e_tgt = str(e[0]), str(e[1]), str(e[2]), str(e[3])
                custom_props = e[4] if len(e) > 4 and isinstance(e[4], dict) else {}
                
                if e_src not in valid_nodes or e_tgt not in valid_nodes:
                    continue 

                src_type = valid_nodes[e_src]["type"]
                tgt_type = valid_nodes[e_tgt]["type"]

                # Check the ontology registry, not hardcoded strings
                if self.ontology_registry:
                    rel_bp = self.ontology_registry.get_relation_blueprint(e_type)

                    if not rel_bp or not rel_bp.allows(src_type, tgt_type):
                        # Dynamic Healer: Find ANY valid relation for these two specific node types
                        valid_fallback = None
                        for potential_rel in self.ontology_registry.all_relations():
                            if potential_rel.allows(src_type, tgt_type):
                                valid_fallback = potential_rel.type_key
                                break
                        
                        if valid_fallback:
                            e_type = valid_fallback
                        else:
                            continue # Drop edge if ontology absolutely forbids connecting these two types

                edge_data = {
                    "id": e_id, 
                    "type": e_type, 
                    "source": e_src, 
                    "target": e_tgt
                }
                edge_data.update(custom_props)
                healed_edges.append(edge_data)

        return json.dumps({
            "entities": list(valid_nodes.values()),
            "relations": healed_edges
        })


MasterWorkflowRunner = MasterActionRunner
