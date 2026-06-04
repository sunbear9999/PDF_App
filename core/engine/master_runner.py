# core/engine/master_runner.py
import traceback
import json
import sqlite3
import re
from PySide6.QtCore import QMutex, QThread, QWaitCondition, Signal, Qt
from core.engine.action_model import AIActionBlueprint, ActionStep
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

    def __init__(self, main_window, blueprint: AIActionBlueprint, initial_state: dict):
        super().__init__()
        self.main_window = main_window
        self.llm_manager = main_window.shared_llm_manager
        self.prompt_manager = main_window.prompt_manager
        self.step_manager = getattr(main_window, 'step_manager', None) 
        self.registry = getattr(main_window, 'process_registry', None)
        
        import copy
        self.blueprint = copy.deepcopy(blueprint)
        self.state = initial_state.copy() 
        self.job = None
        self.current_executing_step = None
        self._pause_mutex = QMutex()
        self._wait_condition = QWaitCondition()
        self._user_response = None

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
        self.step_started.emit(step.step_id)
        
        resolved_inputs = StateResolver.resolve_val(step.inputs, self.state, self.prompt_manager)
        resolved_model = StateResolver.resolve_val(step.model, self.state, self.prompt_manager)
        
        if not resolved_model or resolved_model == "None":
            resolved_model = self.state.get("selected_model")
        if step.step_type == 'LIBRARY_REF':
            raise ValueError(f"Missing Tool: Could not find '{step.step_ref}' in the Step Library.")
        if step.step_type == 'LLM_QUERY': result = self._run_llm(step, resolved_inputs, resolved_model)
        elif step.step_type == 'RAG_SEARCH': result = self._run_rag(step, resolved_inputs)
        elif step.step_type == 'FOREACH': result = self._run_foreach(step, resolved_inputs)
        elif step.step_type == 'PYTHON_SCRIPT': result = self._run_python(step, resolved_inputs)
        elif step.step_type == 'USER_INPUT': result = self._run_user_input(step, resolved_inputs)
        elif step.step_type == 'BRANCH': result = self._run_branch(step, resolved_inputs)
        elif step.step_type == 'DATABASE_WRITE': result = self._run_db_write(step, resolved_inputs)
        else: raise ValueError(f"Unknown step type: {step.step_type}")
        print(f"Step Id: {step.step_id}, Result: {result}")
        self.state[step.output_key] = result
        
        try:
            safe_state = {k: str(v)[:500] + ('...' if len(str(v)) > 500 else '') for k, v in self.state.items()}
            self.state_snapshot.emit(step.step_id, json.dumps(safe_state, indent=2))
        except: pass

        if not self.job or not self.job.abort_event.is_set():
            self.step_complete.emit(step.step_id, str(result), self.state.copy())

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
        local_scope = {"state": self.state.copy(), "result": None}
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
        pm = getattr(self.main_window, 'project_manager', None)
        
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
                if hasattr(self.main_window, 'blueprint_manager'):
                    sub_blueprint = self.main_window.blueprint_manager.get_blueprint(sub_bp_name, lambda: None)
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
                 self.step_started.emit(sub_step.step_id)
                 
                 # THE FIX: Use StateResolver cleanly here
                 res_inputs = {k: StateResolver.resolve_val(v, sub_state, self.prompt_manager) for k, v in sub_step.inputs.items()}
                 res_model = StateResolver.resolve_val(sub_step.model, sub_state, self.prompt_manager)
                 
                 if sub_step.step_type == 'LLM_QUERY': output = self._run_llm(sub_step, res_inputs, res_model)
                 elif sub_step.step_type == 'RAG_SEARCH': output = self._run_rag(sub_step, res_inputs)
                 elif sub_step.step_type == 'FOREACH': output = self._run_foreach(sub_step, res_inputs)
                 elif sub_step.step_type == 'PYTHON_SCRIPT': output = self._run_python(sub_step, res_inputs)
                 elif sub_step.step_type == 'BRANCH': output = self._run_branch(sub_step, res_inputs)
                 elif sub_step.step_type == 'DATABASE_WRITE': output = self._run_db_write(sub_step, res_inputs)
                 else: output = ""
                     
                 sub_state[sub_step.output_key] = output
                 
                 if not self.job or not self.job.abort_event.is_set():
                     self.step_complete.emit(sub_step.step_id, str(output), sub_state.copy())
                     
            if self.job and self.job.abort_event.is_set(): break
            final_result = sub_state.get(sub_blueprint.steps[-1].output_key)
            
            try:
                parsed_res = json.loads(final_result)
                if isinstance(parsed_res, list): aggregated_results.extend(parsed_res)
                else: aggregated_results.append(parsed_res)
            except Exception:
                aggregated_results.append(final_result)
                
            self.step_complete.emit(f"{step.step_id}_item_{idx}", str(final_result), sub_state.copy())
            
        self.current_executing_step = step 
        
        if step.step_id == "process_all_chunks":
            try:
                from PySide6.QtCore import QMetaObject, Qt
                from PySide6.QtWidgets import QDockWidget
                dock = self.main_window.findChild(QDockWidget, "UnifiedResearchDock")
                if dock and hasattr(dock, "tab_analysis"):
                    QMetaObject.invokeMethod(dock.tab_analysis, "_load_existing_analysis", Qt.ConnectionType.QueuedConnection)
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
            raw_prompt = self.prompt_manager.get_prompt(step.prompt_key)
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

        # THE FIX: Ensure we cleanly resolve variables in the composed system prompt
        system_prompt = StateResolver.resolve_val(system_prompt, self.state, self.prompt_manager)

        options = {"temperature": 0.7, "num_predict": 2048, "json_mode": False}
        options.update(getattr(step, 'llm_options', {}))
        if options["num_predict"] <= 0: options["num_predict"] = 2048
        
        import json
        if getattr(step, 'output_schema', None):
            options["json_mode"] = True
            schema_str = json.dumps(step.output_schema, indent=2)
            json_enforcer = self.prompt_manager.get_prompt("JSON Schema Enforcer")
            system_prompt += "\n\n" + json_enforcer.replace("{schema_str}", schema_str)
        elif options.get("json_mode") and "JSON" not in system_prompt:
             system_prompt += "\n\nCRITICAL: Output ONLY valid JSON. No markdown blocks, no explanations."

        raw_result = self.llm_manager.query(
            question=inputs.get('query', ''), 
            selected_model=resolved_model,
            custom_system_prompt=system_prompt, 
            abort_event=self.job.abort_event if self.job else None,
            callback=lambda chunk: self.progress_update.emit(chunk),
            rag_enabled=False, 
            json_mode=options.get("json_mode"), 
            temperature=options.get("temperature"),
            num_predict=options.get("num_predict"), 
            stop=options.get("stop_sequences")
        )

        if raw_result.strip().startswith("[Generation Error"):
            raise ConnectionError(f"Engine Failed: {raw_result.strip()}")
        
        if getattr(step, 'output_schema', None):
            # THE FIX 3: Use our Phase 1 utility to automatically repair truncated JSON arrays!
            success, parsed = extract_and_heal_json(raw_result)
            if success:
                if isinstance(parsed, dict) and "final_output" in parsed:
                    return json.dumps(parsed["final_output"])
                return json.dumps(parsed)
            else:
                print("[Master Runner] JSON Healer failed. Returning raw text fallback.")
                return parsed 
                
        return raw_result

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
                
        allowed_docs = inputs.get("allowed_docs", None)
        aggregated_docs = {}

        for q in queries_input:
            if not q.strip(): continue
            try:
                emb = self.llm_manager.get_embedding(q)
                where_clause = {"doc_name": {"$in": allowed_docs}} if allowed_docs else None
                
                results = self.llm_manager.collection.query(
                    query_embeddings=[emb], n_results=3, 
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
            return "[]" if step.ui_format == "chat_widgets" else "No relevant documents found."

        best_chunks = sorted(aggregated_docs.values(), key=lambda x: x['distance'])
        max_bubbles = min(20, len(queries_input) * 3) 
        top_chunks = best_chunks[:max_bubbles] 
        reading_order = sorted(top_chunks, key=lambda x: (x['doc_name'], x['page']))
        
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