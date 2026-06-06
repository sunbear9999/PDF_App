# core/engine/default_blueprints.py
import json
from core.engine.action_model import AIActionBlueprint, ActionStep

class DefaultBlueprints:
    @staticmethod
    def get_universal_citation_step(answer_key="final_answer", context_key="rag_context", ui_target="floating") -> ActionStep:
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
            required_context=[] 
        )

    @staticmethod
    def get_auto_build_graph_step(source_key="final_answer") -> ActionStep:
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
            required_context=["workspace"] 
        )

    @staticmethod
    def _build_modular_workspace_step(step_id: str, prompt_key: str, permissions: list, output_mode: str = "workspace_update", additional_context: str = "") -> ActionStep:
        is_create_only = (
            ("create_nodes" in permissions or "all" in permissions)
            and not any(p in permissions for p in ("layout", "edit_color", "edit_text", "all"))
        )
        node_schema = {"id": "g1" if is_create_only else "n1"}
        if "layout" in permissions or "all" in permissions:
            node_schema["x"] = 120
            node_schema["y"] = 240
        if "create_nodes" in permissions or "all" in permissions:
            node_schema["text"] = "New group or concept label"
            node_schema["color"] = "#hexcolor"
        if "edit_color" in permissions or "all" in permissions: node_schema["color"] = "#hexcolor"
        if "edit_text" in permissions or "all" in permissions: node_schema["text"] = "New or updated text"
            
        schema = {"nodes": [node_schema]}
        if "create_edges" in permissions or "all" in permissions:
            schema["edges"] = [{"source": node_schema["id"], "target": "n1", "label": "relates to"}]
        if "delete_nodes" in permissions or "all" in permissions: schema["delete_nodes"] = ["n3"]

        schema_str = json.dumps(schema, indent=2)
        
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
    def get_workspace_group_blueprint(pm=None) -> AIActionBlueprint:
        return AIActionBlueprint(name="Group Selected Nodes", description="Create group nodes and connect selected evidence nodes to them", steps=[
            DefaultBlueprints._build_modular_workspace_step(
                "group_nodes",
                "AI Group Worker",
                permissions=["create_nodes", "create_edges"],
                additional_context=(
                    "Create only new group/hub nodes in the nodes array. Reference existing selected nodes only "
                    "inside edges using their provided ids such as n1, n2. Do not include existing node ids in "
                    "the nodes array unless you are explicitly changing allowed fields."
                ),
            )
        ])

    @staticmethod
    def get_workspace_connections_blueprint(pm=None) -> AIActionBlueprint:
        return AIActionBlueprint(name="Find Workspace Connections", description="Find new relationships between workspace nodes", steps=[
            DefaultBlueprints._build_modular_workspace_step("find_connections", "AI Connections Worker", permissions=["create_edges"])
        ])

    @staticmethod
    def get_workspace_outline_blueprint(pm=None) -> AIActionBlueprint:
        return AIActionBlueprint(name="Generate Workspace Outline", description="Generate an outline from workspace nodes", steps=[
            ActionStep(
                step_id="generate_outline",
                step_type="LLM_QUERY",
                prompt_key="Master Outline Generator",
                output_mode="dialog",
                inputs={"query": "{prompt:Workspace Outline Query}"},
                llm_options={"json_mode": False, "temperature": 0.3},
                output_key="outline_text",
                required_context=["workspace"],
            )
        ])

    @staticmethod
    def get_workspace_weakpoints_blueprint(pm=None) -> AIActionBlueprint:
        return AIActionBlueprint(name="Identify Workspace Weakpoints", description="Identify weak arguments or missing support", steps=[
            ActionStep(
                step_id="identify_weakpoints",
                step_type="LLM_QUERY",
                prompt_key="General Assistant",
                output_mode="dialog",
                inputs={"query": "{prompt:Workspace Weakpoints Query}"},
                llm_options={"json_mode": False, "temperature": 0.2},
                output_key="weakpoints_text",
                required_context=["workspace"],
            )
        ])

    @staticmethod
    def get_workspace_fill_blueprint(pm=None) -> AIActionBlueprint:
        return AIActionBlueprint(name="Fill Workspace Graph", description="Suggest and create missing nodes and connections", steps=[
            DefaultBlueprints._build_modular_workspace_step("fill_graph", "AI Organize Worker", permissions=["all"])
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
    def get_universal_chat_blueprint(pm, model: str = "{selected_model}") -> AIActionBlueprint:
        advanced_steps = [
            ActionStep(step_id="opt_q", step_type="LLM_QUERY", inputs={"query": "{prompt:Advanced RAG Optimize Query}"}, output_key="deep_q", ui_format="silent"),
            ActionStep(
                step_id="deep_rag",
                step_type="RAG_SEARCH",
                inputs={"queries": "{deep_q}", "allowed_docs": "{active_rag_docs}", "tag_filters": "{active_rag_tags}", "tag_logic": "{active_rag_tag_logic}"},
                output_key="rag_context",
                ui_format="silent"
            )
        ]
        
        standard_steps = [
            ActionStep(
                step_id="fast_rag",
                step_type="RAG_SEARCH",
                inputs={"queries": ["{user_query}"], "allowed_docs": "{active_rag_docs}", "tag_filters": "{active_rag_tags}", "tag_logic": "{active_rag_tag_logic}"},
                output_key="rag_context",
                ui_format="silent"
            )
        ]

        return AIActionBlueprint(
            name="Chat - Universal Agent", 
            description="Dynamically scales its research depth based on your settings.",
            expected_inputs=[
                {"key": "use_advanced_rag", "type": "boolean", "label": "Enable Deep Research", "default": False}
            ],
            steps=[
                ActionStep(
                    step_id="rag_router", 
                    step_type="BRANCH", 
                    inputs={"logic": "state.get('use_advanced_rag', False) == True"},
                    if_true=advanced_steps,
                    if_false=standard_steps
                ),
                ActionStep(
                    step_id="chat_response", 
                    step_type="LLM_QUERY",
                    # Tell the LLM to read the history variable
                    inputs={"query": "--- PREVIOUS CHAT HISTORY ---\n{chat_history}\n\n--- DOCUMENT CONTEXT ---\n{rag_context}\n\nUser: {user_query}"},
                    model=model, 
                    
                    # Tell the LLM to read the dynamic persona
                    prompt_key="{chat_persona}", 
                    
                    required_context=["manifest"], 
                    ui_format="live_stream", 
                    ui_target="chat_dock", 
                    output_key="final_answer",
                    inline_citations=True,
                    citation_source_key="rag_context"
                ),
                ActionStep(
                    step_id="graph_router",
                    step_type="BRANCH",
                    inputs={"logic": "state.get('output_workspace', False) == True"},
                    if_true=[ActionStep(step_id="graph", step_ref="core_build_graph", ui_target="floating")],
                    if_false=[]
                )
            ]
        )

    @staticmethod
    def get_brainstorm_blueprint(pm, prompt_key: str, model: str = "{selected_model}", output_workspace: bool = False) -> AIActionBlueprint:
        steps = []
        if "RAG" in prompt_key:
            steps.append(ActionStep(
                step_id="gather_context", step_type="RAG_SEARCH", 
                inputs={"queries": ["{query}"], "allowed_docs": "{active_rag_docs}", "tag_filters": "{active_rag_tags}", "tag_logic": "{active_rag_tag_logic}"},
                output_key="context",
                ui_format="silent", ui_target="brainstorm_dock"
            ))
            
        query_text = "{prompt:Brainstorm Query}"
        if "RAG" in prompt_key: 
            query_text += "\n\nDOCUMENT CONTEXT:\n{context}"

        required_context = ["manifest"]
        if "Workspace" in prompt_key:
            required_context.extend(["workspace", "selected_nodes"])
        if "Analysis" in prompt_key:
            required_context.append("analyses")

        steps.append(
            ActionStep(
                step_id="brainstorm_reply", step_type="LLM_QUERY",
                inputs={"query": query_text},
                model=model, prompt_key=prompt_key,
                required_context=required_context,
                ui_format="live_stream", ui_target="brainstorm_dock", output_key="final_answer",
                inline_citations=("RAG" in prompt_key),
                citation_source_key="context" if "RAG" in prompt_key else None,
            )
        )
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
                required_context=["manifest"] 
            )
        ])

    @staticmethod
    def get_research_agent_planner_blueprint(pm, model: str = "{selected_model}") -> AIActionBlueprint:
        return AIActionBlueprint(
            name="Research Agent Planner",
            description="Chooses the next human-in-the-loop research action from the registered blueprint catalog.",
            steps=[
                ActionStep(
                    step_id="plan_next_action",
                    step_type="LLM_QUERY",
                    inputs={"query": "{prompt:Research Agent Planner Query}"},
                    system_prompt="{prompt:Research Agent Planner System}",
                    model=model,
                    output_key="agent_plan",
                    output_schema={
                        "status": "planning|waiting_for_user|run_blueprint|complete",
                        "summary": "Short explanation for the user.",
                        "next_action": {
                            "type": "checkpoint|run_blueprint|complete",
                            "blueprint_id": "Registry blueprint id when type is run_blueprint.",
                            "reason": "Why this action is useful now.",
                            "inputs": {"key": "value"},
                            "checkpoint": {
                                "kind": "choose_direction|confirm_sources_indexed|select_evidence|approve_plan|custom",
                                "prompt": "Question or instruction for the user.",
                                "options": ["optional choices"]
                            }
                        },
                        "memory_update": "Compact durable update for the agent session memory.",
                        "manifest_suggestions": {"optional_key": "optional project manifest additions"}
                    },
                    llm_options={"temperature": 0.2, "num_predict": 700},
                    ui_format="silent",
                    ui_target="research_agent",
                    required_context=[]
                )
            ]
        )

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
                required_context=[] 
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
                required_context=["manifest", "workspace"] 
            )
        ])
        
    @staticmethod
    def get_compare_outlines_blueprint(pm) -> AIActionBlueprint:
        return AIActionBlueprint(name="Compare Outlines", description="AI compares two document outlines.", steps=[
            ActionStep(
                step_id="compare", step_type="LLM_QUERY",
                system_prompt="{prompt:Compare Outlines System}",
                inputs={"query": "{prompt:Compare Outlines Query}"},
                ui_format="silent" 
            )
        ])

    @staticmethod
    def get_autopilot_injection_steps(pm, target_ui: str) -> list[ActionStep]:
        router_script = """
import json
plan = state.get('sys_autopilot_plan', '{}')
try: p = json.loads(plan)
except: p = {}

if not p.get('needs_project_manifest', True): state['project_manifest'] = "{}"
if not p.get('needs_workspace_graph', False): state['workspace_data'] = "{}"
if not p.get('needs_document_search', True): state['autopilot_disable_rag'] = True

result = "Auto-Pilot Routing Complete"
"""
        return [
            ActionStep(
                step_id="sys_autopilot_planner",
                step_type="LLM_QUERY",
                inputs={"query": pm.get_prompt("Autopilot Planner Query")},
                system_prompt=pm.get_prompt("Autopilot Planner System"),
                output_schema={"needs_document_search": True, "needs_project_manifest": True, "needs_workspace_graph": False},
                output_key="sys_autopilot_plan",
                ui_format="silent",
                ui_target=target_ui
            ),
            ActionStep(
                step_id="sys_autopilot_router",
                step_type="PYTHON_SCRIPT",
                inputs={"script": router_script},
                output_key="sys_route_status",
                ui_format="silent",
                ui_target=target_ui
            )
        ]

    @staticmethod
    def get_analysis_search_injection_steps(pm) -> list[ActionStep]:
        fetch_script = """
import sqlite3
db_path = state.get('__db_path__')
analyses_text = ""
if db_path:
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT doc_path, json_data FROM document_analyses")
        for r in cursor.fetchall():
            analyses_text += f"Doc: {r[0]}\\n{r[1]}\\n\\n"
        conn.close()
    except Exception:
        pass
result = analyses_text[:25000]
"""
        enhance_script = """
analysis = state.get('analysis_context', '')
intent = state.get('__analysis_intent__', '')
if analysis.strip().lower() in ['none', 'none.', '']:
    enhanced = intent
else:
    enhanced = f"{intent}. Context: {analysis[:300]}"
result = enhanced
"""
        return [
            ActionStep(
                step_id="sys_fetch_analyses", step_type="PYTHON_SCRIPT",
                inputs={"script": fetch_script}, output_key="raw_analyses",
                ui_format="silent", ui_target="floating"
            ),
            ActionStep(
                step_id="sys_search_analyses", step_type="LLM_QUERY",
                inputs={"query": pm.get_prompt("Analysis Search Query")},
                system_prompt=pm.get_prompt("Analysis Search System"),
                output_key="analysis_context", ui_format="silent", ui_target="floating"
            ),
            ActionStep(
                step_id="sys_enhance_intent", step_type="PYTHON_SCRIPT",
                inputs={"script": enhance_script}, output_key="enhanced_intent",
                ui_format="silent", ui_target="floating"
            )
        ]

    @staticmethod
    def get_inline_foreach_blueprint(inline_type: str, inputs: dict, llm_options: dict, ui_format: str) -> AIActionBlueprint:
        if inline_type == "LLM_QUERY":
            return AIActionBlueprint(name="inline_llm", description="", steps=[
                ActionStep(
                    step_id="inline", step_type="LLM_QUERY", 
                    inputs={"query": inputs.get("inline_prompt", "{item}")}, 
                    system_prompt=inputs.get("inline_system", ""), 
                    llm_options=llm_options
                )
            ])
        elif inline_type == "RAG_SEARCH":
            return AIActionBlueprint(name="inline_rag", description="", steps=[
                ActionStep(
                    step_id="inline", step_type="RAG_SEARCH", 
                    inputs={"queries": [inputs.get("inline_query", "{item}")]}, 
                    ui_format=ui_format
                )
            ])
        return AIActionBlueprint(name="empty", description="", steps=[])

    @staticmethod
    def get_blank_custom_tool(name: str) -> AIActionBlueprint:
        return AIActionBlueprint(name=name, description="A custom user tool.", steps=[
            ActionStep(
                step_id="query_llm", step_type="LLM_QUERY", 
                inputs={"query": "{user_input}"}, 
                ui_format="live_stream", ui_target="custom_tools_tab", 
                llm_options={"num_predict": 2048, "temperature": 0.7}
            )
        ])

    @staticmethod
    def get_blank_step(step_id: str) -> ActionStep:
        return ActionStep(
            step_id=step_id, step_type="LLM_QUERY", 
            llm_options={"num_predict": 2048, "temperature": 0.7}
        )
    @staticmethod
    def get_reword_blueprint(text_to_reword: str) -> AIActionBlueprint:
        return AIActionBlueprint(name="Reword Text", description="Rewrites text for clarity.", steps=[
            ActionStep(
                step_id="reword", step_type="LLM_QUERY",
                inputs={"query": f"\"{text_to_reword}\""},
                system_prompt="You are an expert editor. Rewrite the following text to make it easier to understand and follow, while keeping all crucial information intact. Respond ONLY with the reworded text. Do not include introductory phrases.",
                ui_format="nested_outline", 
                ui_target="floating",
                ui_title="📝 Reworded Text"
            )
        ])

    @staticmethod
    def get_similar_context_blueprint(text: str, allowed_docs: list) -> AIActionBlueprint:
        return AIActionBlueprint(name="Similar Context", description="Finds related chunks.", steps=[
            ActionStep(
                step_id="search", step_type="RAG_SEARCH",
                inputs={"queries": [text], "allowed_docs": allowed_docs, "n_results": 10}, # <--- ADDED N_RESULTS
                ui_format="results_dialog",
                ui_title="🔗 Similar Context Found",
                ui_target="floating"
            )
        ])

    @staticmethod
    def get_opposing_views_blueprint(text: str, allowed_docs: list) -> AIActionBlueprint:
        return AIActionBlueprint(name="Opposing Views", description="Finds counter-arguments.", steps=[
            ActionStep(
                step_id="fetch_context", step_type="RAG_SEARCH",
                inputs={"queries": [text], "allowed_docs": allowed_docs, "n_results": 30}, # <--- ADDED N_RESULTS
                output_key="rag_context", ui_format="silent"
            ),
            ActionStep(
                step_id="analyze_opposition", step_type="LLM_QUERY",
                inputs={"query": f"Original Text: '{text}'\n\nContext:\n{{rag_context}}"},
                system_prompt="Analyze the Context to find strong opposing views, counter-arguments, or contradictions to the Original Text. Extract the exact verbatim text that opposes it, along with its doc_name and page. ONLY output valid JSON.",
                output_schema=[{
                    "doc_name": "filename.pdf",
                    "page": 0,
                    "text": "exact verbatim opposing quote from the context"
                }],
                llm_options={"json_mode": True, "temperature": 0.3},
                ui_format="results_dialog",
                ui_title="⚖️ Opposing Views Found",
                ui_target="floating"
            )
        ])
