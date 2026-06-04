# core/engine/master_runner.py
import traceback
import json
import re
from PySide6.QtCore import QMutex, QThread, QWaitCondition, Signal, Qt
from core.engine.action_model import AIActionBlueprint, ActionStep

def safe_format(template_str, state_dict):
    """Safely injects state variables, supporting nested JSON extraction."""
    if not isinstance(template_str, str): return template_str

    def get_nested_value(d, key_path):
        """Extracts nested values using dot or bracket notation (e.g., 'item.claim' or 'results[0]')"""
        keys = re.findall(r'([^.\[\]]+|\[\d+\])', key_path)
        current = d
        try:
            for key in keys:
                if key.startswith('[') and key.endswith(']'):
                    idx = int(key[1:-1])
                    current = current[idx]
                else:
                    # If the current level is a JSON string, try to parse it first
                    if isinstance(current, str) and (current.startswith('{') or current.startswith('[')):
                        current = json.loads(current)
                    current = current[key]
            return str(current) if not isinstance(current, (dict, list)) else json.dumps(current)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError):
            return None

    def replacer(match):
        full_match = match.group(0)
        var_path = match.group(1) 
        
        val = get_nested_value(state_dict, var_path)
        # --- THE FIX: Fall back to empty string instead of leaving raw template tokens ---
        return val if val is not None else ""

    # Matches {variable}, {variable.key}, {variable[0]}, etc.
    pattern = r'\{([a-zA-Z_][a-zA-Z0-9_\[\]\.]*)\}'
    return re.sub(pattern, replacer, template_str)


class MasterActionRunner(QThread):
    progress_update = Signal(str)
    step_complete = Signal(str, str, dict)
    action_complete = Signal(dict)   
    error = Signal(str)
    step_started = Signal(str)
    state_snapshot = Signal(str, str)
    user_input_requested = Signal(str, dict) # step_id, input_schema
    force_db_save = Signal(dict, str)
    def __init__(self, main_window, blueprint: AIActionBlueprint, initial_state: dict):
        super().__init__()
        self.main_window = main_window
        self.llm_manager = main_window.shared_llm_manager
        self.prompt_manager = main_window.prompt_manager
        self.registry = getattr(main_window, 'process_registry', None)
        
        self.blueprint = AIActionBlueprint(
            name=blueprint.name, 
            description=blueprint.description, 
            steps=blueprint.steps.copy(),
            expected_inputs=blueprint.expected_inputs
        )
        self.state = initial_state.copy() 
        self.job = None
        self.current_executing_step = None
        self._pause_mutex = QMutex()
        self._wait_condition = QWaitCondition()
        self._user_response = None

        # --- NEW: UNIVERSAL AUTO-PILOT INJECTION ---
        pm = getattr(self.main_window, 'project_manager', None)
        if pm:
            try:
                global_settings = json.loads(pm.get_metadata("global_ai_settings", "{}"))
                if global_settings.get("autopilot_enabled", False):
                    self._inject_autopilot()
                
                # --- NEW: Inject Analysis Search ---
                if global_settings.get("search_analyses", False):
                    self._inject_analysis_search()
                    
                # --- NEW: Inject Universal Context & Workspace (Works for ALL tools) ---
                self._inject_universal_context(global_settings)
                    
            except Exception as e:
                print(f"[Master Runner] Failed to inject global settings: {e}")
        # --- THE FIX PART 2: Connect Signal to the Main Thread Tab ---
        try:
            from PySide6.QtWidgets import QDockWidget
            dock = self.main_window.findChild(QDockWidget, "UnifiedResearchDock")
            if dock and hasattr(dock, "tab_analysis"):
                # Use a BlockingQueuedConnection to freeze the background thread 
                # until the Main Thread fully finishes the SQLite write.
                self.force_db_save.connect(
                    dock.tab_analysis.save_chunk_to_db,
                    Qt.ConnectionType.BlockingQueuedConnection
                )
        except Exception as e:
            print(f"Failed to connect DB save signal: {e}")
    def _inject_autopilot(self):
        intent = self.state.get("user_query") or self.state.get("query") or self.state.get("goal") or "Execute tool"
        self.state["__autopilot_intent__"] = intent
        target = self.blueprint.steps[-1].ui_target if self.blueprint.steps else "floating"

        # --- FIXED: Route through Default Blueprints ---
        from core.engine.default_blueprints import DefaultBlueprints
        auto_steps = DefaultBlueprints.get_autopilot_injection_steps(self.prompt_manager, target)
        
        # Prepend the routing steps
        self.blueprint.steps = auto_steps + self.blueprint.steps
    def _inject_analysis_search(self):
        intent = self.state.get("user_query") or self.state.get("query") or self.state.get("goal") or "Execute tool"
        self.state["__analysis_intent__"] = intent
        pm = getattr(self.main_window, 'project_manager', None)
        self.state["__db_path__"] = pm.project_filepath if pm else ""

        # --- FIXED: Route through Default Blueprints ---
        from core.engine.default_blueprints import DefaultBlueprints
        analysis_steps = DefaultBlueprints.get_analysis_search_injection_steps(self.prompt_manager)

        # Step 4: Dynamically rewrite all Original Blueprint Steps to utilize the new context
        for step in self.blueprint.steps:
            if step.step_type == 'LLM_QUERY' and 'query' in step.inputs and isinstance(step.inputs['query'], str):
                step.inputs['query'] += self.prompt_manager.get_prompt("Context Inject - Analyses")
            elif step.step_type == 'RAG_SEARCH':
                if 'queries' in step.inputs:
                    queries_input = step.inputs['queries']
                    
                    # Handle if queries are generated dynamically via string key vs direct list
                    if isinstance(queries_input, str):
                        queries_input = queries_input.replace('{user_query}', '{enhanced_intent}')
                        queries_input = queries_input.replace('{query}', '{enhanced_intent}')
                        queries_input = queries_input.replace('{goal}', '{enhanced_intent}')
                        step.inputs['queries'] = queries_input
                        
                    elif isinstance(queries_input, list):
                        new_queries = []
                        for q in queries_input:
                            if isinstance(q, str):
                                q = q.replace('{user_query}', '{enhanced_intent}')
                                q = q.replace('{query}', '{enhanced_intent}')
                                q = q.replace('{goal}', '{enhanced_intent}')
                            new_queries.append(q)
                        step.inputs['queries'] = new_queries

        # Step 5: Prepend our new setup steps to the execution pipeline
        self.blueprint.steps = analysis_steps + self.blueprint.steps
    def _inject_universal_context(self, global_settings):
        """Dynamically injects universal context into the pipeline for ANY tool."""
        pm = getattr(self.main_window, 'project_manager', None)
        if not pm: return
        
        # 1. State First: Check if a custom tool manually passed data
        manifest_data = self.state.get("project_manifest", "")
        workspace_data = self.state.get("workspace_data", "")
        selected_nodes_data = self.state.get("selected_nodes", "")

        # 2. Fallbacks: Fetch dynamically if empty
        if manifest_data in ["", "{}"] and global_settings.get("include_manifest", True):
            manifest_data = pm.get_metadata("project_manifest", "{}")
            
        if hasattr(self.main_window, 'workspace_view'):
            # Fetch full workspace ONLY if toggled
            if workspace_data in ["", "{}"] and (global_settings.get("output_workspace", False) or global_settings.get("autopilot_enabled", False)):
                try: workspace_data = self.main_window.workspace_view.get_workspace_state_as_json(only_selected=False)
                except: pass
                
            # AUTO-FETCH SELECTED NODES: If you highlighted notes, it grabs them immediately.
            if selected_nodes_data in ["", "{}", "[]"]:
                try: 
                    # Only fetch if there's an actual selection to prevent pulling an empty grid
                    if self.main_window.workspace_view.scene_obj.selectedItems():
                        selected_nodes_data = self.main_window.workspace_view.get_workspace_state_as_json(only_selected=True)
                except: pass

        # 3. Save finalized data back to state
        self.state["project_manifest"] = manifest_data if manifest_data else "{}"
        self.state["workspace_data"] = workspace_data if workspace_data else "{}"
        self.state["selected_nodes"] = selected_nodes_data if selected_nodes_data else "{}"
        self.state["allow_manifest_updates"] = global_settings.get("allow_manifest_updates", True)
        
        def has_nodes(data_str):
            if not data_str or data_str in ["", "{}", "[]"]: return False
            if '"nodes":[]' in data_str.replace(" ", "").replace("\n", ""): return False
            return True

        # 4. Dynamically append context to LLM queries
        for step in self.blueprint.steps:
            if step.step_type == 'LLM_QUERY' and 'query' in step.inputs and isinstance(step.inputs['query'], str):
                query_str = step.inputs['query']
                appended_context = ""
                
                if self.state["project_manifest"] not in ["", "{}"] and "{project_manifest}" not in query_str:
                    appended_context += f"\n\n--- PROJECT MANIFEST ---\n{self.state['project_manifest']}"
                    
                if has_nodes(self.state["workspace_data"]) and "{workspace_data}" not in query_str:
                    appended_context += f"\n\n--- WORKSPACE GRAPH DATA ---\n{self.state['workspace_data']}"
                    
                if has_nodes(self.state["selected_nodes"]) and "{selected_nodes}" not in query_str:
                    appended_context += f"\n\n--- SELECTED WORKSPACE NODES ---\n{self.state['selected_nodes']}"
                    
                step.inputs['query'] = query_str + appended_context
                
        # 5. Dynamically append the Graph Builder step if output_workspace is toggled
        if global_settings.get("output_workspace", False):
            has_workspace_step = any(getattr(s, 'ui_format', '') == "workspace_graph" for s in self.blueprint.steps)
            if not has_workspace_step and self.blueprint.steps:
                from core.engine.default_blueprints import DefaultBlueprints
                last_llm_step = next((s for s in reversed(self.blueprint.steps) if s.step_type == "LLM_QUERY"), None)
                if last_llm_step:
                    graph_step = DefaultBlueprints.get_auto_build_graph_step(source_key=last_llm_step.output_key)
                    self.blueprint.steps.append(graph_step)
    def run(self):
        try:
            # --- THE FIX: Universal Dynamic Workspace Appending ---
            if self.state.get("output_workspace") and self.blueprint.steps:
                # Find the last step that produces an LLM output to use as the source key
                last_llm_step = next(
                    (step for step in reversed(self.blueprint.steps) if step.step_type == "LLM_QUERY"), 
                    None
                )
                source_key = last_llm_step.output_key if last_llm_step else "final_answer"
                
                # Check if a graph step is already appended to prevent duplicates
                has_graph_step = any(step.step_id == "auto_build_graph" for step in self.blueprint.steps)
                
                if not has_graph_step:
                    from core.engine.default_blueprints import DefaultBlueprints
                    graph_step = DefaultBlueprints.get_auto_build_graph_step(source_key=source_key)
                    # Force a unique step ID for execution tracking
                    graph_step.step_id = "auto_build_graph" 
                    self.blueprint.steps.append(graph_step)
            for step in self.blueprint.steps:
                if self.job and self.job.abort_event.is_set():
                    self.registry.update_job_status(self.job.id, "Aborted by User")
                    return
                self._execute_step(step)
                
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

    # --- Hyper-Aggressive Variable Resolver ---
    def _run_user_input(self, step, inputs):
        """Pauses the QThread and asks the main GUI for data."""
        schema = step.expected_inputs if hasattr(step, 'expected_inputs') else inputs
        
        # 1. Emit signal to GUI to build the form
        self.user_input_requested.emit(step.step_id, schema)
        
        # 2. Lock and sleep the thread
        self._pause_mutex.lock()
        self._wait_condition.wait(self._pause_mutex)
        self._pause_mutex.unlock()
        
        # 3. Thread wakes up when submit_user_input is called
        result = self._user_response
        self._user_response = None 
        return json.dumps(result) if isinstance(result, dict) else str(result)

    def submit_user_input(self, data: dict):
        """Called by the main GUI thread when the user clicks Submit."""
        self._pause_mutex.lock()
        self._user_response = data
        self._wait_condition.wakeAll()
        self._pause_mutex.unlock()
    def _resolve_val(self, val, state_dict):
        if isinstance(val, str): 
            # --- THE FIX: Dynamic Prompt Fetching ---
            # Resolves {prompt:Prompt Name} exactly when the step runs!
            if hasattr(self, 'prompt_manager') and self.prompt_manager:
                import re
                for match in re.findall(r'\{prompt:(.*?)\}', val):
                    live_prompt = self.prompt_manager.get_prompt(match)
                    # Swap the tag for the live prompt (or leave blank if not found)
                    val = val.replace(f"{{prompt:{match}}}", live_prompt if live_prompt else "")

            res = safe_format(val, state_dict)
            
            # Auto-parse JSON strings safely
            if (res.strip().startswith('[') and res.strip().endswith(']')) or \
               (res.strip().startswith('{') and res.strip().endswith('}')):
                try: return json.loads(res)
                except: pass
            return res
            
        elif isinstance(val, list): 
            return [self._resolve_val(i, state_dict) for i in val]
        elif isinstance(val, dict): 
            return {k: self._resolve_val(v, state_dict) for k, v in val.items()}
        return val

    def _execute_step(self, step):
        if getattr(self, 'skip_remaining', False): return # Abort flag from conditions

        if self.registry and self.job:
            self.registry.update_job_status(self.job.id, f"Running: {step.step_id}...")
        self.current_executing_step = step
        self.step_started.emit(step.step_id)
        
        resolved_inputs = {k: self._resolve_val(v, self.state) for k, v in step.inputs.items()}
        resolved_model = self._resolve_val(step.model, self.state)
        
        # --- THE FIX: Guard step execution against empty/legacy blueprint configurations ---
        if not resolved_model or resolved_model == "None":
            resolved_model = self.state.get("selected_model")

        # Route to new executors
        if step.step_type == 'LLM_QUERY': result = self._run_llm(step, resolved_inputs, resolved_model)
        elif step.step_type == 'RAG_SEARCH': result = self._run_rag(step, resolved_inputs)
        elif step.step_type == 'FOREACH': result = self._run_foreach(step, resolved_inputs)
        elif step.step_type == 'PYTHON_SCRIPT': result = self._run_python(step, resolved_inputs)
        elif step.step_type == 'CONDITION': result = self._run_condition(step, resolved_inputs)
        elif step.step_type == 'USER_INPUT': result = resolved_inputs.get('default', '')
        else: raise ValueError(f"Unknown step type: {step.step_type}")

        self.state[step.output_key] = result
        
        # NEW: Broadcast the internal state matrix for the debugger
        try:
            safe_state = {k: str(v)[:500] + ('...' if len(str(v)) > 500 else '') for k, v in self.state.items()}
            self.state_snapshot.emit(step.step_id, json.dumps(safe_state, indent=2))
        except: pass

        if not self.job or not self.job.abort_event.is_set():
            self.step_complete.emit(step.step_id, str(result), self.state.copy())
    def _run_python(self, step, inputs):
        """Executes raw python locally to parse data, do math, or manipulate state."""
        # This allows the script to be hardcoded in the blueprint OR generated dynamically by a previous LLM step
        script = inputs.get('script', '') 
        
        # Define a safe execution environment. 
        # 'state' is read-only context. 'result' is what gets passed to the next step.
        local_scope = {"state": self.state.copy(), "result": None}
        
        try:
            # Execute the script. The user/LLM script must assign its final output to the 'result' variable.
            # Example LLM generated script: 
            #   data = state.get('llm_list_output', [])
            #   result = sorted(data, key=lambda x: x['score'])
            exec(script, {}, local_scope)
            return local_scope.get("result", "")
        except Exception as e:
            self.error.emit(f"Python Execution Error: {e}")
            return f"Script Execution Error: {e}"

    def _run_condition(self, step, inputs):
        """Evaluates logic. If false, it stops the pipeline."""
        logic = inputs.get('logic', 'False')
        try:
            passed = eval(logic, {}, {"state": self.state})
            if not passed:
                self.skip_remaining = True # Custom flag to halt cleanly
                if self.job: self.job.abort_event.set()
            return passed
        except Exception as e:
            self.error.emit(f"Condition Logic Error: {e}")
            return False

    def _run_foreach(self, step, inputs):
        target_list_raw = inputs.get('list', [])
        
        if isinstance(target_list_raw, str):
            try: target_list = json.loads(target_list_raw)
            except: target_list = [line.strip() for line in target_list_raw.split('\n') if line.strip()]
        else:
            target_list = target_list_raw

        # Smart unwrapping: If it's a dict like {"search_phrases": ["a", "b"]}, extract the list
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
                # --- FIXED: Route through Default Blueprints ---
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
            
            # --- THE FIX: Expose the current item to the main state so the UI router can grab metadata ---
            self.state['item'] = item 
            
            for sub_step in sub_blueprint.steps:
                 if self.job and self.job.abort_event.is_set(): break
                 
                 # --- THE FIX: Tell the UI Router that an inner step is currently executing ---
                 self.current_executing_step = sub_step 
                 self.step_started.emit(sub_step.step_id)
                 
                 res_inputs = {k: self._resolve_val(v, sub_state) for k, v in sub_step.inputs.items()}
                 res_model = self._resolve_val(sub_step.model, sub_state)
                 
                 if sub_step.step_type == 'LLM_QUERY': output = self._run_llm(sub_step, res_inputs, res_model)
                 elif sub_step.step_type == 'RAG_SEARCH': output = self._run_rag(sub_step, res_inputs)
                 elif sub_step.step_type == 'FOREACH': output = self._run_foreach(sub_step, res_inputs)
                 elif sub_step.step_type == 'PYTHON_SCRIPT': output = self._run_python(sub_step, res_inputs)
                 elif sub_step.step_type == 'CONDITION': output = self._run_condition(sub_step, res_inputs)
                 else: output = ""
                     
                 sub_state[sub_step.output_key] = output
                 
                 if not self.job or not self.job.abort_event.is_set():
                     self.step_complete.emit(sub_step.step_id, str(output), sub_state.copy())
                     
                     # --- THE FIX: Engine-Level Auto-Save ---
                     # Bypasses the UI widget's async debounce delay by saving directly to SQLite 
                     # the exact millisecond the LLM finishes streaming.
                     if sub_step.ui_target == "analysis_tab" and "item" in sub_state:
                         self.force_db_save.emit(sub_state.copy(), str(output))
            if self.job and self.job.abort_event.is_set(): break
            final_result = sub_state.get(sub_blueprint.steps[-1].output_key)
            
            try:
                parsed_res = json.loads(final_result)
                if isinstance(parsed_res, list): aggregated_results.extend(parsed_res)
                else: aggregated_results.append(parsed_res)
            except Exception:
                aggregated_results.append(final_result)
                
            self.step_complete.emit(f"{step.step_id}_item_{idx}", str(final_result), sub_state.copy())
            
        # Restore the parent step context
        self.current_executing_step = step 
        
        # --- THE FIX: Engine-Level UI Refresh ---
        # Force the UI to repaint from the DB safely once the entire loop is done
        if step.step_id == "process_all_chunks":
            try:
                from PySide6.QtCore import QMetaObject, Qt
                from PySide6.QtWidgets import QDockWidget
                dock = self.main_window.findChild(QDockWidget, "UnifiedResearchDock")
                if dock and hasattr(dock, "tab_analysis"):
                    # Use QueuedConnection to safely jump from the background thread to the Main GUI thread
                    QMetaObject.invokeMethod(dock.tab_analysis, "_load_existing_analysis", Qt.ConnectionType.QueuedConnection)
            except Exception: pass

        # Bulletproof Deduplication
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
        
        # --- 1. CORE PERSONA PROMPT ---
        if getattr(step, 'prompt_key', None):
            raw_prompt = self.prompt_manager.get_prompt(step.prompt_key)
            if raw_prompt: 
                system_prompt = raw_prompt + "\n\n" + system_prompt

        # --- 2. DECLARATIVE CONTEXT INJECTION ---
        # Only inject the global system state if the blueprint explicitly asked for it
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

        # --- 3. UI FORMAT ENFORCERS ---
        ui_format = getattr(step, 'ui_format', '')
        if ui_format == "chat_widgets":
            system_prompt += "\n\n" + self.prompt_manager.get_prompt("Format Enforcer - Chat Widgets")
        elif ui_format == "data_table":
            system_prompt += "\n\n" + self.prompt_manager.get_prompt("Format Enforcer - Data Table")
        elif ui_format == "card_grid":
            system_prompt += "\n\n" + self.prompt_manager.get_prompt("Format Enforcer - Card Grid")

        # --- 4. RESOLVE VARIABLES IN SYSTEM PROMPT ---
        # Because we just dynamically stacked the templates, we must resolve any tags (like {project_manifest}) now.
        system_prompt = self._resolve_val(system_prompt, self.state)

        # --- 5. JSON CONFIGURATION ---
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

        # --- 6. EXECUTE QUERY ---
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
        
        # --- 7. AUTO-UNWRAP SCHEMA ---
        if getattr(step, 'output_schema', None):
            try:
                first_brace = raw_result.find('{')
                last_brace = raw_result.rfind('}')
                first_bracket = raw_result.find('[')
                last_bracket = raw_result.rfind(']')
                
                is_dict = first_brace != -1 and last_brace != -1 and (first_bracket == -1 or first_brace < first_bracket)
                is_list = first_bracket != -1 and last_bracket != -1 and (first_brace == -1 or first_bracket < first_brace)
                
                if is_dict: clean_str = raw_result[first_brace:last_brace+1]
                elif is_list: clean_str = raw_result[first_bracket:last_bracket+1]
                else: clean_str = raw_result
                
                parsed = json.loads(clean_str)
                return json.dumps(parsed.get("final_output", parsed))
            except Exception as e:
                print(f"[Master Runner] Failed to unwrap schema: {e}")
                return clean_str if 'clean_str' in locals() else raw_result
                
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
                
                # Get the 3 best chunks per query
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
            
            # --- THE FIX: Relative Distance Math ---
            distances = [d['distance'] for d in reading_order]
            min_d = min(distances) if distances else 0
            max_d = max(distances) if distances else 1
            if max_d - min_d < 0.0001: max_d = min_d + 1
            
            for d in reading_order:
                # The best match gets 95%, the worst match gets 50%
                match_pct = 95 - int(45 * (d['distance'] - min_d) / (max_d - min_d))
                
                bubbles.append({
                    "doc_name": d['doc_name'],
                    "quote": d['text'][:400] + "..." if len(d['text']) > 400 else d['text'],
                    "note": f"Confidence: {match_pct}% | Found via: '{d['query_used'][:30]}...'"
                })
            return json.dumps(bubbles) 
            
        context_pieces = [f"--- DOCUMENT: {d['doc_name']} | PAGE {d['page'] + 1} ---\n{d['text']}" for d in reading_order]
        return "\n\n".join(context_pieces)