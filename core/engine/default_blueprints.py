import json
from core.engine.action_model import AIActionBlueprint, ActionStep

class DefaultBlueprints:
    @staticmethod
    def get_universal_citation_step(answer_key="final_answer", context_key="rag_context", ui_target="floating") -> ActionStep:
        return ActionStep(
            step_id="extract_citations", 
            step_type="LLM_QUERY",
            inputs={
                "query": f"Analyze the SOURCE TEXT and the AI ANSWER. You MUST extract 3 to 5 highly relevant, VERBATIM quotes from the text that directly support the claims made in the answer.\n\nSOURCE TEXT:\n{{{context_key}}}\n\nAI ANSWER:\n{{{answer_key}}}"
            },
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
            output_key="citations"
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

        query_text = f"Analyze the workspace data and return the updated graph.\n\nCRITICAL RULE: You MUST output ONLY valid JSON matching this exact schema shape. Do not add keys outside this schema.\n\nSCHEMA:\n{schema_str}\n\nWORKSPACE DATA:\n{{workspace_data}}"
        if additional_context:
            query_text += f"\n\n--- ARCHITECTURAL PLAN TO EXECUTE ---\n{additional_context}"

        return ActionStep(
            step_id=step_id, step_type="LLM_QUERY", prompt_key=prompt_key,
            permissions=permissions, output_mode=output_mode,
            inputs={"query": query_text},
            llm_options={"json_mode": True},
            model="{selected_model}", output_key="ai_graph"
        )

    @staticmethod
    def get_workspace_organize_blueprint() -> AIActionBlueprint:
        return AIActionBlueprint(name="Organize Workspace", description="Organize nodes in the workspace", steps=[
            DefaultBlueprints._build_modular_workspace_step("organize_nodes", "AI Organize Worker", permissions=["layout"])
        ])

    @staticmethod
    def get_workspace_consolidate_blueprint() -> AIActionBlueprint:
        perms = ["layout", "edit_color", "edit_text", "create_edges", "delete_nodes"]
        return AIActionBlueprint(name="Consolidate Nodes", description="Restructure the workspace", steps=[
            ActionStep(
                step_id="plan_consolidation", step_type="LLM_QUERY", prompt_key="AI Consolidate Worker - Planner", 
                output_mode="silent",
                inputs={"query": "Analyze the following workspace data. Draft a step-by-step structural plan to reorganize it into a cohesive argument map. Identify which nodes should group together, what new overarching hub nodes should be created, and EXACTLY how they should connect to the original evidence nodes. Do not write JSON, just write the plan in plain text.\n\nWORKSPACE DATA:\n{workspace_data}"},
                llm_options={"json_mode": False, "temperature": 0.3},
                output_key="consolidation_plan" 
            ),
            DefaultBlueprints._build_modular_workspace_step(
                step_id="execute_consolidation", prompt_key="AI Consolidate Worker - Executor", permissions=perms,
                additional_context="{consolidation_plan}" 
            )
        ])

    @staticmethod
    def get_chat_blueprint(prompt_key: str, model: str = "{selected_model}") -> AIActionBlueprint:
        steps = []
        if "RAG" in prompt_key:
            steps.append(
                ActionStep(
                    step_id="gather_context", step_type="RAG_SEARCH",
                    inputs={"queries": ["{user_query}"]}, output_key="context", 
                    ui_format="silent", ui_target="chat_dock"
                )
            )
        steps.append(
            ActionStep(
                step_id="chat_reply", step_type="LLM_QUERY",
                inputs={"query": "USER QUERY: {user_query}\n\nWORKSPACE DATA:\n{workspace_data}\n\nCONTEXT:\n{context}"},
                model=model, prompt_key=prompt_key,
                ui_format="live_stream", ui_target="chat_dock", output_key="final_answer"
            )
        )
        if "RAG" in prompt_key: 
            steps.append(DefaultBlueprints.get_universal_citation_step("final_answer", "context", "chat_dock"))
        return AIActionBlueprint(name="Chat Interaction", description="", steps=steps)

    @staticmethod
    def get_brainstorm_blueprint(prompt_key: str, model: str = "{selected_model}") -> AIActionBlueprint:
        steps = []
        if "RAG" in prompt_key:
            steps.append(ActionStep(step_id="gather_context", step_type="RAG_SEARCH", inputs={"queries": ["{query}"]}, output_key="context", ui_format="silent", ui_target="brainstorm_dock"))
            
        steps.append(
            ActionStep(
                step_id="brainstorm_reply", step_type="LLM_QUERY",
                inputs={"query": "USER QUERY: {query}\n\nPROJECT GOAL: {project_goal}\n\nCONTEXT: {context}"},
                model=model, prompt_key=prompt_key if prompt_key in ["Brainstorm - Default", "Brainstorm - RAG Enabled"] else "Brainstorming Agent",
                ui_format="live_stream", ui_target="brainstorm_dock", output_key="final_answer"
            )
        )
        
        # FIX: The citations MUST happen after the brainstorm reply, not before!
        if "RAG" in prompt_key:
            steps.append(DefaultBlueprints.get_universal_citation_step("final_answer", "context", "brainstorm_dock"))
            
        return AIActionBlueprint(name="Brainstorming", description="", steps=steps)

    @staticmethod
    def get_search_terms_blueprint(model: str = "{selected_model}") -> AIActionBlueprint:
        return AIActionBlueprint(name="Generate Search Terms", description="AI generates advanced boolean search queries.", steps=[
            ActionStep(
                step_id="generate_queries", step_type="LLM_QUERY",
                inputs={"query": "USER GOAL: {goal}"}, model=model, prompt_key="Search Term Generator",
                output_schema={
                    "search_terms": [{
                        "title": "boolean search string", 
                        "description": "Why it helps",
                        "actions": [
                            {"label": "🏛️ Search JSTOR", "url": "https://www.jstor.org/action/doBasicSearch?Query={title}"},
                            {"label": "🎓 Search Scholar", "url": "https://scholar.google.com/scholar?q={title}"}
                        ]
                    }]
                },
                ui_format="card_grid", ui_target="search_tab", output_key="search_array"
            )
        ])

    @staticmethod
    def get_python_example_blueprint() -> AIActionBlueprint:
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
    def get_analysis_blueprint(chunks: list) -> AIActionBlueprint:
        sub_blueprint = AIActionBlueprint(name="Analyze Chunk", description="", steps=[
            ActionStep(
                step_id="analyze_chunk", step_type="LLM_QUERY",
                inputs={"query": "INSTRUCTIONS: {item[template_instructions]}\nREQUIRED JSON SCHEMA:\n{item[template_schema]}\n\n--- TEXT TO ANALYZE (Pages: {item[page_range]}) ---\n{item[text]}"},
                prompt_key="Document Analyzer", llm_options={"json_mode": True, "num_predict": 1000},
                ui_format="analysis_chunk", ui_target="analysis_tab", output_key="chunk_json"
            )
        ])
        return AIActionBlueprint(name="Document Analysis", description="", steps=[
            ActionStep(step_id="process_all_chunks", step_type="FOREACH", inputs={"list": chunks, "sub_blueprint": sub_blueprint}, output_key="final_analysis")
        ])

    @staticmethod
    def get_master_outline_blueprint(doc_name: str) -> AIActionBlueprint:
        return AIActionBlueprint(name="Master Project Outline", description="", steps=[
            ActionStep(
                step_id="write_outline", step_type="LLM_QUERY", inputs={"query": "NOTES:\n{combined_text}"},
                prompt_key="Master Outline Generator", ui_format="static_document", ui_target="floating", ui_title=f"Master Outline: {doc_name}"
            )
        ])

    # ... (Keep existing workspace blueprints exactly as they are) ...

    @staticmethod
    def get_blueprint_architect(model: str = "{selected_model}") -> AIActionBlueprint:
        sys_prompt = """You are the Papyrus AI Tool Builder. 
        Analyze the user's request and build a valid JSON pipeline matching the AIActionBlueprint schema.
        CRITICAL RULES:
        1. 'expected_inputs' MUST strictly use 'key', 'type', and 'label'. NEVER use 'name'. Example: {"key": "target_doc", "type": "doc_selector", "label": "Target Doc"}
        2. RAG_SEARCH inputs MUST use 'queries' (array) and 'allowed_docs' (array).
        3. Use ui_format="data_table" for spreadsheet data, or ui_format="card_grid" for items with action buttons.
        """
        return AIActionBlueprint(name="Blueprint Architect", description="AI Assistant to build custom tools", steps=[
            ActionStep(
                step_id="build_blueprint", step_type="LLM_QUERY",
                inputs={"query": "CURRENT TOOL JSON:\n{current_json}\n\nUSER REQUEST:\n{user_text}"},
                system_prompt=sys_prompt, model=model, ui_format="silent", 
                output_key="architect_response", llm_options={"temperature": 0.3}
            )
        ])