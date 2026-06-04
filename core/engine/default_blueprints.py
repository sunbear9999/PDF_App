# core/engine/default_blueprints.py
import json
from core.engine.action_model import AIActionBlueprint, ActionStep

class DefaultBlueprints:
    @staticmethod
    def get_universal_citation_step(answer_key="final_answer", context_key="rag_context", ui_target="floating") -> ActionStep:
        # THE FIX: We bypass the broken .replace() logic entirely and explicitly 
        # format the required state variables directly into the query string.
        query_text = (
            "{prompt:Universal Citation Query}\n\n"
            "--- SOURCE TEXT ---\n"
            f"{{{context_key}}}\n\n"
            "--- AI ANSWER ---\n"
            f"{{{answer_key}}}"
        )
        
        return ActionStep(
            step_id="extract_citations", 
            step_type="LLM_QUERY",
            inputs={"query": query_text},
            prompt_key="Evidence Extractor",
            output_schema={
                "citations": [{
                    "doc_name": "filename.pdf", 
                    "quote": "exact verbatim sentence from text", 
                    "note": "brief reason why it supports the answer"
                }]
            },
            llm_options={"temperature": 0.1},
            ui_format="chat_widgets",
            ui_target=ui_target,
            output_key="citations",
            required_context=[] # Pristine! No manifest noise.
        )

    @staticmethod
    def get_auto_build_graph_step(source_key="final_answer") -> ActionStep:
        # THE FIX: Explicitly append the source key so the Graph Builder knows what to map
        query_text = (
            "{prompt:Auto Build Graph Query}\n\n"
            "--- AI RESPONSE TO MAP ---\n"
            f"{{{source_key}}}"
        )
        
        graph_schema = {
            "nodes": [{
                "id": "n1",
                "quote": "OPTIONAL: Exact verbatim quote",
                "note": "Core concept",
                "doc_name": "OPTIONAL",
                "color": "#4a148c"
            }],
            "edges": [{"source": "n1", "target": "n2", "label": "relationship"}]
        }
        return ActionStep(
            step_id="auto_build_graph",
            step_type="LLM_QUERY",
            inputs={"query": query_text},
            system_prompt="{prompt:Auto Build Graph System}",
            output_schema=graph_schema,
            output_key="ai_graph",
            ui_format="workspace_graph", 
            ui_target="floating",
            llm_options={"temperature": 0.1, "json_mode": True, "num_predict": 4000},
            required_context=["workspace"] # Needs to know existing graph to avoid duplicate nodes
        )

    @staticmethod
    def _build_modular_workspace_step(step_id: str, prompt_key: str, permissions: list, output_mode: str = "workspace_update", additional_context: str = "") -> ActionStep:
        node_schema = {"id": "n1"}
        if "layout" in permissions or "all" in permissions: node_schema["group"] = "Cluster Name"
        if "edit_color" in permissions or "all" in permissions: node_schema["color"] = "#hexcolor"
        if "edit_text" in permissions or "all" in permissions: node_schema["text"] = "New or updated text"
            
        schema = {"nodes": [node_schema]}
        if "create_edges" in permissions or "all" in permissions: schema["edges"] = [{"source": "n1", "target": "n2", "label": "relates to"}]
        if "delete_nodes" in permissions or "all" in permissions: schema["delete_nodes"] = ["n3"]

        schema_str = json.dumps(schema, indent=2)
        
        # THE FIX: Explicitly append the JSON schema required for workspace manipulation
        query_text = f"{{prompt:Modular Workspace Query}}\n\n--- REQUIRED SCHEMA ---\n{schema_str}"
        
        if additional_context:
            query_text += f"\n\n--- ARCHITECTURAL PLAN TO EXECUTE ---\n{additional_context}"

        return ActionStep(
            step_id=step_id, step_type="LLM_QUERY", prompt_key=prompt_key,
            permissions=permissions, output_mode=output_mode,
            inputs={"query": query_text},
            llm_options={"json_mode": True},
            model="{selected_model}", output_key="ai_graph",
            required_context=["workspace"]
        )

    @staticmethod
    def get_workspace_organize_blueprint(pm=None) -> AIActionBlueprint:
        return AIActionBlueprint(name="Organize Workspace", description="Organize nodes in the workspace", steps=[
            DefaultBlueprints._build_modular_workspace_step("organize_nodes", "AI Organize Worker", permissions=["layout"])
        ])

    @staticmethod
    def get_workspace_consolidate_blueprint(pm=None) -> AIActionBlueprint:
        perms = ["layout", "edit_color", "edit_text", "create_edges", "delete_nodes"]
        return AIActionBlueprint(name="Consolidate Nodes", description="Restructure the workspace", steps=[
            ActionStep(
                step_id="plan_consolidation", step_type="LLM_QUERY", prompt_key="AI Consolidate Worker - Planner", 
                output_mode="silent",
                inputs={"query": "{prompt:Consolidate Workspace Plan Query}"},
                llm_options={"json_mode": False, "temperature": 0.3},
                output_key="consolidation_plan",
                required_context=["workspace", "manifest"]
            ),
            DefaultBlueprints._build_modular_workspace_step(
                step_id="execute_consolidation", prompt_key="AI Consolidate Worker - Executor", permissions=perms,
                additional_context="{consolidation_plan}" 
            )
        ])

    @staticmethod
    def get_chat_blueprint(pm, prompt_key: str, model: str = "{selected_model}", output_workspace: bool = False) -> AIActionBlueprint:
        steps = []
        if "Advanced RAG" in prompt_key:
            steps.extend([
                ActionStep(step_id="initial_rag", step_type="RAG_SEARCH", inputs={"queries": ["{user_query}"]}, output_key="initial_context", ui_target="chat_dock", ui_format="silent"),
                ActionStep(step_id="optimize_query", step_type="LLM_QUERY",
                           inputs={"query": pm.get_prompt("Advanced RAG Optimize Query")},
                           output_schema={"better_queries": ["query 1", "query 2"]},
                           output_key="deep_queries", ui_target="chat_dock", ui_format="silent", required_context=[]),
                ActionStep(step_id="deep_rag_search", step_type="RAG_SEARCH", inputs={"queries": "{deep_queries}"}, output_key="deep_context", ui_target="chat_dock", ui_format="silent"),
                ActionStep(step_id="combine_contexts", step_type="PYTHON_SCRIPT",
                           inputs={"script": "result = f\"--- INITIAL BROAD CONTEXT ---\\n{state.get('initial_context', '')}\\n\\n--- DEEP TARGETED CONTEXT ---\\n{state.get('deep_context', '')}\""},
                           output_key="context", ui_target="chat_dock", ui_format="silent")
            ])
        elif "RAG" in prompt_key:
            steps.append(
                ActionStep(
                    step_id="gather_context", step_type="RAG_SEARCH",
                    inputs={"queries": ["{user_query}"]}, output_key="context", 
                    ui_format="silent", ui_target="chat_dock"
                )
            )
        
        query_text = "{user_query}"
        if "RAG" in prompt_key:
            query_text += "\n\n--- DOCUMENT CONTEXT ---\n{context}"

        steps.append(
             ActionStep(
                step_id="chat_response", step_type="LLM_QUERY",
                inputs={"query": query_text},
                model=model, prompt_key=prompt_key,
                required_context=["manifest", "workspace", "selected_nodes", "analyses"], # Global Context Active
                ui_format="live_stream", ui_target="chat_dock", output_key="final_answer"
            )
        )
        
        if "RAG" in prompt_key: 
            steps.append(DefaultBlueprints.get_universal_citation_step("final_answer", "context", "chat_dock"))
        if output_workspace: 
            steps.append(DefaultBlueprints.get_auto_build_graph_step("final_answer"))
            
        return AIActionBlueprint(name="Chat", description="Chat Agent", steps=steps)

    @staticmethod
    def get_brainstorm_blueprint(pm, prompt_key: str, model: str = "{selected_model}", output_workspace: bool = False) -> AIActionBlueprint:
        steps = []
        if "RAG" in prompt_key:
            steps.append(ActionStep(
                step_id="gather_context", step_type="RAG_SEARCH", 
                inputs={"queries": ["{query}"]}, output_key="context", 
                ui_format="silent", ui_target="brainstorm_dock"
            ))
            
        query_text = "{prompt:Brainstorm Query}"
        if "RAG" in prompt_key: 
            query_text += "\n\nDOCUMENT CONTEXT:\n{context}"

        steps.append(
            ActionStep(
                step_id="brainstorm_reply", step_type="LLM_QUERY",
                inputs={"query": query_text},
                model=model, prompt_key=prompt_key,
                required_context=["manifest", "workspace", "selected_nodes", "analyses"], # Global Context Active
                ui_format="live_stream", ui_target="brainstorm_dock", output_key="final_answer"
            )
        )
        if "RAG" in prompt_key: 
            steps.append(DefaultBlueprints.get_universal_citation_step("final_answer", "context", "brainstorm_dock"))
        if output_workspace: 
            steps.append(DefaultBlueprints.get_auto_build_graph_step("final_answer"))
            
        return AIActionBlueprint(name="Brainstorming", description="Strategy agent", steps=steps)

    @staticmethod
    def get_search_terms_blueprint(pm, model: str = "{selected_model}") -> AIActionBlueprint:
        return AIActionBlueprint(name="Generate Search Terms", description="AI generates advanced boolean search queries.", steps=[
            ActionStep(
                step_id="generate_queries", step_type="LLM_QUERY",
                inputs={"query": "{prompt:Search Terms Query}"}, model=model, prompt_key="Search Term Generator",
                output_schema={
                    "search_terms": [{"term": "boolean search string", "reason": "Why it helps"}]
                },
                ui_format="search_terms", ui_target="search_tab", output_key="search_array",
                required_context=["manifest"] # Manifest context helps it build targeted goals
            )
        ])

    @staticmethod
    def get_python_example_blueprint(pm=None) -> AIActionBlueprint:
        return AIActionBlueprint(
            name="Keyword Density Analyzer (Python)", 
            description="Searches documents and runs a local python script to calculate keyword density.",
            expected_inputs=[
                {"key": "keyword", "type": "text", "label": "Target Keyword"},
                {"key": "target_doc", "type": "doc_selector", "label": "Document to Analyze"}
            ],
            steps=[
                ActionStep(
                    step_id="get_doc_text", step_type="RAG_SEARCH",
                    inputs={"queries": ["{keyword}"], "allowed_docs": ["{target_doc_name}"]}, 
                    output_key="doc_text", ui_format="silent"
                ),
                ActionStep(
                    step_id="calculate", step_type="PYTHON_SCRIPT",
                    inputs={
                        "script": "import re\ntext = state.get('doc_text', '')\nkw = state.get('keyword', '')\ncount = len(re.findall(rf'\\b{kw}\\b', text, re.IGNORECASE))\nresult = [{'title': f'Density: {kw}', 'Count': count, 'Total Context Length': len(text)}]"
                    },
                    ui_format="data_table", ui_target="custom_tools_tab", output_key="final_stats"
                )
            ]
        )

    @staticmethod
    def get_analysis_blueprint(pm, chunks: list) -> AIActionBlueprint:
        sub_blueprint = AIActionBlueprint(name="Analyze Chunk", description="", steps=[
            ActionStep(
                step_id="analyze_chunk", step_type="LLM_QUERY",
                inputs={"query": "{prompt:Analyze Chunk Query}"},
                prompt_key="Document Analyzer", llm_options={"json_mode": True, "num_predict": 2000},
                ui_format="nested_outline", 
                ui_target="analysis_tab", 
                ui_title="Section: {item.page_range}",
                output_key="chunk_json",
                required_context=[] # PRISTINE: Analysis blocks should not overlap with manifest data
            )
        ])
        return AIActionBlueprint(name="Document Analysis", description="", steps=[
            ActionStep(step_id="process_all_chunks", step_type="FOREACH", inputs={"list": chunks, "sub_blueprint": sub_blueprint}, output_key="final_analysis"),
            ActionStep(
                step_id="async_teardown_buffer", 
                step_type="LLM_QUERY", 
                inputs={"query": "{prompt:Async Teardown Buffer Query}"}, 
                model="{selected_model}",
                llm_options={"num_predict": 5},
                ui_format="silent",
                output_key="teardown_junk",
                required_context=[]
            )
        ])

    @staticmethod
    def get_master_outline_blueprint(pm, doc_name: str) -> AIActionBlueprint:
        return AIActionBlueprint(name="Master Project Outline", description="", steps=[
            ActionStep(
                step_id="write_outline", step_type="LLM_QUERY", inputs={"query": "{prompt:Master Outline Query}"},
                prompt_key="Master Outline Generator", ui_format="static_document", ui_target="floating", ui_title=f"Master Outline: {doc_name}",
                required_context=["manifest"]
            )
        ])

    @staticmethod
    def get_blueprint_architect(pm, model: str = "{selected_model}") -> AIActionBlueprint:
        return AIActionBlueprint(name="Blueprint Architect", description="AI Assistant to build custom tools", steps=[
            ActionStep(
                step_id="build_blueprint", step_type="LLM_QUERY",
                inputs={"query": "{prompt:Blueprint Architect Query}"},
                system_prompt="{prompt:Blueprint Architect System}", model=model, ui_format="silent", 
                output_key="architect_response", llm_options={"temperature": 0.3},
                required_context=["manifest", "workspace"] # Gives the architect full context
            )
        ])