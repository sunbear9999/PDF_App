# core/engine/default_blueprints.py
import json
from core.engine.action_model import AIActionBlueprint, ActionStep

class DefaultBlueprints:
    @staticmethod
    def get_citation_coverage_steps(answer_key="final_answer", context_key="rag_context", ui_target="chat_dock") -> list[ActionStep]:
        """Generate complete answer citations in one structured model pass."""
        return [DefaultBlueprints.get_universal_citation_step(answer_key, context_key, ui_target)]

    @staticmethod
    def get_universal_citation_step(answer_key="final_answer", context_key="rag_context", ui_target="floating") -> ActionStep:
        # Instruction lives in PromptManager ("Citation Coverage Query") so it is editable.
        # The state-variable blocks (SOURCE TEXT / AI ANSWER) are appended here so the
        # step works regardless of which keys the calling blueprint uses.
        query_text = (
            "{prompt:Citation Coverage Query}\n\n"
            f"--- SOURCE TEXT ---\n{{{context_key}}}\n\n"
            f"--- AI ANSWER ---\n{{{answer_key}}}"
        )
        return ActionStep(
            step_id="generate_citation_coverage",
            step_type="LLM_QUERY",
            inputs={"query": query_text},
            prompt_key="Evidence Extractor",
            output_schema={
                "citations": [{
                    "doc_name": "filename.pdf", 
                    "quote": "exact verbatim sentence from text", 
                    "note": "specific answer claim supported by this quote",
                    "source_type": "exact value from CITATION_SOURCE_TYPE",
                    "source_id": "exact value from CITATION_SOURCE_ID",
                    "source_locator": {"key": "values from CITATION_LOCATOR_key lines"},
                }]
            },
            llm_options={"temperature": 0.1, "num_predict": 4096, "num_ctx": 65536},
            output_key="citations",
            citation_source_key=context_key,
            ui_format="chat_widgets",
            ui_target=ui_target,
            status_text="Citing all answer claims in one pass...",
            required_context=[] 
        )

    @staticmethod
    def get_manifest_update_step(user_key="user_query", answer_key="final_answer", ui_target="chat_dock") -> ActionStep:
        return ActionStep(
            step_id="update_project_manifest",
            step_type="LLM_QUERY",
            inputs={
                "query": (
                    "{prompt:Manifest Update Query}\n\n"
                    f"USER REQUEST:\n{{{user_key}}}\n\nCOMPLETED ANSWER:\n{{{answer_key}}}"
                )
            },
            prompt_key="General Assistant",
            required_context=["manifest"],
            allow_manifest_updates=True,
            llm_options={"temperature": 0.1, "num_predict": 1200},
            output_key="manifest_update_result",
            ui_format="silent",
            ui_target=ui_target,
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
    def get_auto_build_graph_steps(source_key="final_answer") -> list[ActionStep]:
        """Build, validate, and import an ontology-typed workspace graph."""
        graph_schema = {
            "entities": [{
                "id": "n1",
                "type": "exact entity type from ONTOLOGY CATALOG",
                "text": "concise node content",
            }],
            "relations": [{
                "id": "r1",
                "type": "exact relation type from ONTOLOGY CATALOG",
                "source": "n1",
                "target": "n2",
                "label": "readable relationship label",
            }],
        }
        return [
            ActionStep(
                step_id="load_ontology_catalog",
                step_type="ONTOLOGY_CATALOG",
                inputs={"include_descriptions": True},
                output_key="ontology_catalog",
                ui_format="silent",
                ui_target="floating",
            ),
            ActionStep(
                step_id="build_typed_workspace_graph",
                step_type="LLM_QUERY",
                inputs={"query": (
                    "{prompt:Build Typed Workspace Graph Query}\n\n"
                    "ONTOLOGY CATALOG:\n{ontology_catalog}\n\n"
                    f"COMPLETED RESPONSE:\n{{{source_key}}}\n\n"
                    "CURRENT WORKSPACE:\n{workspace_data}"
                )},
                prompt_key="General Assistant",
                output_schema=graph_schema,
                llm_options={"temperature": 0.15, "json_mode": True, "num_predict": 5000, "num_ctx": 32768},
                output_key="typed_workspace_graph",
                ui_format="silent",
                ui_target="floating",
                required_context=["workspace"],
            ),
            ActionStep(
                step_id="validate_typed_workspace_graph",
                step_type="GRAPH_VALIDATOR",
                inputs={"tuple_data": "{typed_workspace_graph}"},
                output_key="validated_workspace_graph",
                ui_format="silent",
                ui_target="floating",
            ),
            ActionStep(
                step_id="import_typed_workspace_graph",
                step_type="WORKSPACE_WRITE",
                inputs={"data": "{validated_workspace_graph}"},
                output_key="workspace_import_result",
                ui_format="silent",
                ui_target="floating",
            ),
        ]

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
        targeted_search_blueprint = AIActionBlueprint(
            name="Adaptive Targeted Search",
            description="Runs one planned research search.",
            steps=[ActionStep(
                step_id="targeted_rag_search",
                step_type="RAG_SEARCH",
                inputs={
                    "queries": ["{item.query}"],
                    "allowed_docs": "{active_rag_docs}",
                    "tag_filters": "{active_rag_tags}",
                    "tag_logic": "{active_rag_tag_logic}",
                    "n_results": 10,
                },
                output_key="targeted_context",
                ui_format="status",
                ui_target="chat_dock",
                status_text="Searching an evidence gap...",
            )],
        )
        advanced_steps = [
            ActionStep(
                step_id="initial_deep_rag",
                step_type="RAG_SEARCH",
                inputs={
                    "queries": ["{user_query}"],
                    "allowed_docs": "{active_rag_docs}",
                    "tag_filters": "{active_rag_tags}",
                    "tag_logic": "{active_rag_tag_logic}",
                    "n_results": 8,
                },
                output_key="initial_rag_context",
                ui_format="status",
                ui_target="chat_dock",
                status_text="Running an initial document search...",
            ),
            ActionStep(
                step_id="collect_source_statistics",
                step_type="SOURCE_STATISTICS",
                inputs={
                    "allowed_docs": "{active_rag_docs}",
                    "metrics": ["page_count", "indexed_chunk_count", "file_size_bytes"],
                },
                output_key="source_statistics",
                ui_format="status",
                ui_target="chat_dock",
                status_text="Measuring source coverage and document size...",
            ),
            ActionStep(
                step_id="plan_adaptive_research",
                step_type="LLM_QUERY",
                inputs={
                    "query": (
                        "{prompt:Plan Adaptive Research Query}\n\n"
                        "USER REQUEST:\n{user_query}\n\n"
                        "SOURCE STATISTICS:\n{source_statistics}\n\n"
                        "INITIAL SEARCH CONTEXT:\n{initial_rag_context}"
                    )
                },
                prompt_key="General Assistant",
                output_schema={
                    "searches": [{
                        "topic": "Distinct open topic to understand",
                        "query": "Targeted semantic search query",
                        "reason": "Why this search is needed for a complete answer",
                    }]
                },
                llm_options={"temperature": 0.2, "num_predict": 1800, "num_ctx": 32768},
                output_key="research_plan",
                ui_format="status",
                ui_target="chat_dock",
                status_text="Identifying open topics for deeper research...",
            ),
            ActionStep(
                step_id="run_targeted_research",
                step_type="FOREACH",
                inputs={"list": "{research_plan}", "sub_blueprint": targeted_search_blueprint},
                output_key="targeted_contexts",
                ui_format="status",
                ui_target="chat_dock",
                status_text="Running the targeted research plan...",
            ),
            ActionStep(
                step_id="combine_research_context",
                step_type="PYTHON_SCRIPT",
                inputs={"script": (
                    "import json\n"
                    "initial = str(state.get('initial_rag_context', '') or '')\n"
                    "raw = state.get('targeted_contexts', '[]')\n"
                    "try:\n"
                    "    targeted = json.loads(raw) if isinstance(raw, str) else raw\n"
                    "except Exception:\n"
                    "    targeted = [raw]\n"
                    "if not isinstance(targeted, list):\n"
                    "    targeted = [targeted]\n"
                    "pieces = [initial] + [str(item) for item in targeted if str(item).strip()]\n"
                    "result = '\\n\\n'.join(piece for piece in pieces if piece.strip())"
                )},
                output_key="rag_context",
                ui_format="status",
                ui_target="chat_dock",
                status_text="Combining evidence from all searches...",
            ),
        ]

        standard_steps = [
            ActionStep(
                step_id="fast_rag",
                step_type="RAG_SEARCH",
                inputs={
                    "queries": ["{user_query}"],
                    "allowed_docs": "{active_rag_docs}",
                    "tag_filters": "{active_rag_tags}",
                    "tag_logic": "{active_rag_tag_logic}",
                    "n_results": 12,
                },
                output_key="rag_context",
                ui_format="silent",
            ),
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
                    # {chat_history} expands to "" or "CONVERSATION HISTORY:\n...\n\n" via the tab's context contributor
                    inputs={"query": "{chat_history}--- DOCUMENT CONTEXT ---\n{rag_context}\n\nUser: {user_query}"},
                    model=model,
                    prompt_key="{chat_persona}",
                    
                    required_context=["manifest"], 
                    ui_format="live_stream", 
                    ui_target="chat_dock", 
                    output_key="final_answer",
                    inline_citations=False,
                    llm_options={"temperature": 0.6, "num_predict": 4096, "num_ctx": 32768},
                ),
                *DefaultBlueprints.get_citation_coverage_steps(
                    answer_key="final_answer", context_key="rag_context", ui_target="chat_dock"
                ),
                ActionStep(
                    step_id="manifest_update_router",
                    step_type="BRANCH",
                    inputs={"logic": "state.get('allow_manifest_updates', False) == True"},
                    if_true=[DefaultBlueprints.get_manifest_update_step(
                        user_key="user_query", answer_key="final_answer", ui_target="chat_dock"
                    )],
                    if_false=[],
                ),
                ActionStep(
                    step_id="graph_router",
                    step_type="BRANCH",
                    inputs={"logic": "state.get('output_workspace', False) == True"},
                    if_true=DefaultBlueprints.get_auto_build_graph_steps("final_answer"),
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
                inputs={"queries": ["{query}"], "allowed_docs": "{active_rag_docs}", "tag_filters": "{active_rag_tags}", "tag_logic": "{active_rag_tag_logic}", "n_results": 12},
                output_key="context",
                ui_format="silent", ui_target="brainstorm_dock"
            ))
            
        # {chat_history} expands to "" or "CONVERSATION HISTORY:\n...\n\n" via context contributor
        query_text = "{chat_history}{prompt:Brainstorm Query}"
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
                inline_citations=False,
            )
        )
        if "RAG" in prompt_key:
            steps.extend(DefaultBlueprints.get_citation_coverage_steps(
                answer_key="final_answer",
                context_key="context",
                ui_target="brainstorm_dock",
            ))
        steps.append(ActionStep(
            step_id="manifest_update_router",
            step_type="BRANCH",
            inputs={"logic": "state.get('allow_manifest_updates', False) == True"},
            if_true=[DefaultBlueprints.get_manifest_update_step(
                user_key="query", answer_key="final_answer", ui_target="brainstorm_dock"
            )],
            if_false=[],
        ))
        steps.append(ActionStep(
            step_id="graph_router",
            step_type="BRANCH",
            inputs={"logic": "state.get('output_workspace', False) == True"},
            if_true=DefaultBlueprints.get_auto_build_graph_steps("final_answer"),
            if_false=[],
        ))
            
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
    def get_legacy_analysis_blueprint(pm, doc_path: str = "{analysis_doc_path}") -> AIActionBlueprint:
        """Original single-pass synthesis blueprint — kept for backward compat and short documents."""
        sub_blueprint = AIActionBlueprint(name="Analyze Chunk", description="", steps=[
            ActionStep(
                step_id="analyze_chunk_graph",
                step_type="LLM_QUERY",
                inputs={"query": "{analysis_chunk_query_prompt}"},
                system_prompt="{analysis_chunk_system_prompt}",
                llm_options={"json_mode": True, "num_predict": "{analysis_limits.chunk_num_predict}", "temperature": 0.12, "num_ctx": "{analysis_limits.num_ctx}"},
                output_key="chunk_json",
                ui_format="silent",
                ui_target="analysis_tab",
            )
        ])
        return AIActionBlueprint(name="Document Analysis (Legacy)", description="Single-pass document graph analysis.", mount_points=["analysis_tab"], steps=[
            ActionStep("analysis_contract", "ANALYSIS_CONTRACT", inputs={"template": "{analysis_template}", "user_prompt": "{analysis_user_prompt}"}, output_key="analysis_contract", ui_format="silent"),
            ActionStep("chunk_document", "DOCUMENT_CHUNK", inputs={"doc_path": doc_path, "template_id": "{analysis_template_id}", "template": "{analysis_template}", "contract": "{analysis_contract}"}, output_key="doc_chunks", ui_format="silent"),
            ActionStep("process_all_chunks", "FOREACH", inputs={"list": "{doc_chunks}", "sub_blueprint": sub_blueprint}, output_key="final_analysis", ui_format="silent"),
            ActionStep("compact_master_input", "ANALYSIS_COMPACT", inputs={"chunks": "{final_analysis}"}, output_key="master_input", ui_format="silent"),
            ActionStep(
                "argument_synthesis_pass",
                "LLM_QUERY",
                inputs={"query": "{analysis_synthesis_query_prompt}"},
                system_prompt="{analysis_synthesis_system_prompt}",
                llm_options={"json_mode": False, "num_predict": "{analysis_limits.synthesis_num_predict}", "temperature": 0.12, "num_ctx": "{analysis_limits.num_ctx}"},
                output_key="argument_synthesis",
                ui_format="silent",
                ui_target="analysis_tab",
            ),
            ActionStep(
                "master_diagram_pass",
                "LLM_QUERY",
                inputs={"query": "{analysis_master_query_prompt}"},
                system_prompt="{analysis_master_system_prompt}",
                llm_options={"json_mode": True, "num_predict": "{analysis_limits.master_num_predict}", "temperature": 0.08, "num_ctx": "{analysis_limits.num_ctx}"},
                output_key="master_diagram",
                ui_format="silent",
                ui_target="analysis_tab",
            ),
            ActionStep("finalize_analysis", "ANALYSIS_FINALIZE", inputs={"doc_path": doc_path, "template_id": "{analysis_template_id}", "template": "{analysis_template}", "contract": "{analysis_contract}", "chunks": "{final_analysis}", "synthesis": "{argument_synthesis}", "master": "{master_diagram}", "run_id": "{analysis_run_id}"}, output_key="analysis_result", ui_format="silent"),
        ])

    @staticmethod
    def get_analysis_blueprint(pm, doc_path: str = "{analysis_doc_path}") -> AIActionBlueprint:
        """Hierarchical evidence-to-graph pipeline.

        Stages:
          1. FOREACH: per-chunk evidence extraction (LLM) + EVIDENCE_STORE (DB + streaming events)
          2. SECTION_GROUP: group chunks into 5-chunk sections
          3. FOREACH: per-section synthesis LLM call
          4. PYTHON_SCRIPT: merge section syntheses into compact state keys
          5. LLM: graph planning (compact refs only — no full quote text)
          6. LLM: graph assembly (quote_ids → source_quote_id fields)
          7. QUOTE_HYDRATION: attach exact text from DB
          8. ANALYSIS_FINALIZE: normalize, save, emit RESULT_READY

        Full quote text never re-enters an LLM after step 1.
        """
        # ── Sub-blueprint 1: per-chunk evidence extraction ──────────────────
        evidence_sub = AIActionBlueprint(name="Extract Chunk Evidence", description="", steps=[
            ActionStep(
                step_id="extract_chunk_evidence",
                step_type="LLM_QUERY",
                inputs={"query": "{analysis_evidence_query_prompt}"},
                system_prompt="{analysis_evidence_system_prompt}",
                llm_options={
                    "json_mode": True,
                    "num_predict": "{analysis_limits.evidence_num_predict}",
                    "temperature": 0.12,
                    "num_ctx": "{analysis_limits.num_ctx}",
                },
                output_key="chunk_evidence_json",
                ui_format="silent",
                ui_target="analysis_tab",
            ),
            ActionStep(
                step_id="store_chunk_evidence",
                step_type="EVIDENCE_STORE",
                inputs={
                    "chunk_evidence": "{chunk_evidence_json}",
                    "chunk_id": "{item.chunk_index}",
                },
                output_key="chunk_compact_refs",
                ui_format="silent",
                ui_target="analysis_tab",
            ),
        ])

        # ── Sub-blueprint 2: per-section synthesis ───────────────────────────
        section_synthesis_sub = AIActionBlueprint(name="Synthesize Section", description="", steps=[
            ActionStep(
                step_id="synthesis_llm",
                step_type="LLM_QUERY",
                inputs={"query": "{analysis_section_synthesis_query_prompt}\n\nSECTION EVIDENCE:\n{item.compact_refs_text}"},
                system_prompt="{analysis_section_synthesis_system_prompt}",
                llm_options={
                    "json_mode": True,
                    "num_predict": 2000,
                    "temperature": 0.12,
                    "num_ctx": 16384,
                },
                output_key="section_synthesis_result",
                ui_format="status",
                ui_target="analysis_tab",
                status_text="Synthesizing section {item.section_number} of {item.total_sections}...",
            ),
        ])

        # ── Merge script: build section_syntheses_compact from synthesis results
        merge_syntheses_script = """
import json

raw = state.get('all_section_syntheses', '[]')
try:
    syntheses = json.loads(raw) if isinstance(raw, str) else raw
except Exception:
    syntheses = []
if not isinstance(syntheses, list):
    syntheses = [syntheses] if syntheses else []

lines = []
for i, s in enumerate(syntheses):
    if not isinstance(s, dict):
        try:
            s = json.loads(str(s))
        except Exception:
            s = {}
    sid = s.get('section_id', f'section_{i}')
    summary = str(s.get('summary') or '')[:300]
    themes = ', '.join(str(t) for t in (s.get('key_themes') or [])[:4])
    qids = ', '.join(str(q) for q in (s.get('key_quote_ids') or [])[:6])
    lines.append(f'[{sid}] {summary}\\n  themes: {themes}\\n  key_quotes: {qids}')

result = '\\n\\n'.join(lines) if lines else '(no section syntheses)'
state['section_syntheses_compact'] = result
"""

        return AIActionBlueprint(
            name="Document Analysis",
            description="Hierarchical evidence-to-graph analysis — works for any document length.",
            mount_points=["analysis_tab"],
            steps=[
                # 1. Build analysis contract (populates all prompt state keys)
                ActionStep(
                    "analysis_contract", "ANALYSIS_CONTRACT",
                    inputs={"template": "{analysis_template}", "user_prompt": "{analysis_user_prompt}"},
                    output_key="analysis_contract",
                    ui_format="silent",
                    status_text="Building analysis contract...",
                ),
                # 2. Chunk the document
                ActionStep(
                    "chunk_document", "DOCUMENT_CHUNK",
                    inputs={
                        "doc_path": doc_path,
                        "template_id": "{analysis_template_id}",
                        "template": "{analysis_template}",
                        "contract": "{analysis_contract}",
                    },
                    output_key="doc_chunks",
                    ui_format="silent",
                    status_text="Chunking document...",
                ),
                # 3. Per-chunk: extract evidence (LLM) → store quotes in DB + stream events
                #    Keeps step_id "process_all_chunks" for master_runner.py compatibility.
                ActionStep(
                    "process_all_chunks", "FOREACH",
                    inputs={"list": "{doc_chunks}", "sub_blueprint": evidence_sub},
                    output_key="all_chunk_evidence",
                    ui_format="silent",
                    status_text="Extracting evidence from chunks...",
                ),
                # 4. Group chunks into 5-chunk sections
                ActionStep(
                    "group_sections", "SECTION_GROUP",
                    inputs={"all_chunk_evidence": "{all_chunk_evidence}", "chunks_per_section": 5},
                    output_key="section_groups",
                    ui_format="silent",
                    status_text="Grouping chunks into sections...",
                ),
                # 5. Per-section synthesis (one LLM call per section, compact refs only)
                ActionStep(
                    "synthesize_sections", "FOREACH",
                    inputs={"list": "{section_groups}", "sub_blueprint": section_synthesis_sub},
                    output_key="all_section_syntheses",
                    ui_format="silent",
                    status_text="Synthesizing sections...",
                ),
                # 6. Merge synthesis results → section_syntheses_compact state key
                ActionStep(
                    "merge_syntheses", "PYTHON_SCRIPT",
                    inputs={"script": merge_syntheses_script},
                    output_key="section_syntheses_compact",
                    ui_format="status",
                    ui_target="analysis_tab",
                    status_text="Merging section syntheses...",
                ),
                # 7. Graph planning pass (section summaries + compact top quote_ids, no full text)
                ActionStep(
                    "graph_plan_pass", "LLM_QUERY",
                    inputs={"query": "{analysis_graph_plan_query_prompt}"},
                    system_prompt="{analysis_graph_plan_system_prompt}",
                    llm_options={
                        "json_mode": False,
                        "num_predict": "{analysis_limits.synthesis_num_predict}",
                        "temperature": 0.12,
                        "num_ctx": "{analysis_limits.num_ctx}",
                    },
                    output_key="graph_plan_text",
                    ui_format="status",
                    ui_target="analysis_tab",
                    status_text="Planning graph structure...",
                ),
                # 8. Graph assembly (references quote_ids, not full text)
                ActionStep(
                    "graph_assembly_pass", "LLM_QUERY",
                    inputs={"query": "{analysis_graph_assembly_query_prompt}"},
                    system_prompt="{analysis_graph_assembly_system_prompt}",
                    llm_options={
                        "json_mode": True,
                        "num_predict": "{analysis_limits.master_num_predict}",
                        "temperature": 0.08,
                        "num_ctx": "{analysis_limits.num_ctx}",
                    },
                    output_key="assembled_graph",
                    ui_format="status",
                    ui_target="analysis_tab",
                    status_text="Assembling final graph...",
                ),
                # 9. Hydrate quote_ids → exact text from DB
                ActionStep(
                    "hydrate_quotes", "QUOTE_HYDRATION",
                    inputs={"assembled_graph": "{assembled_graph}"},
                    output_key="hydrated_graph",
                    ui_format="silent",
                    status_text="Hydrating quote references...",
                ),
                # 10. Normalize, save, emit RESULT_READY
                ActionStep(
                    "finalize_analysis", "ANALYSIS_FINALIZE",
                    inputs={
                        "doc_path": doc_path,
                        "template_id": "{analysis_template_id}",
                        "template": "{analysis_template}",
                        "contract": "{analysis_contract}",
                        "chunks": "{all_chunk_evidence}",
                        "synthesis": "{all_section_syntheses}",
                        "master": "{hydrated_graph}",
                        "run_id": "{analysis_run_id}",
                    },
                    output_key="analysis_result",
                    ui_format="silent",
                ),
            ],
        )

    @staticmethod
    def get_analysis_send_to_workspace_blueprint() -> AIActionBlueprint:
        return AIActionBlueprint(name="Send Analysis To Workspace", description="Emit analyzed graph artifacts into the workspace.", mount_points=["analysis_tab"], steps=[
            ActionStep("send_analysis_to_workspace", "ANALYSIS_SEND_TO_WORKSPACE", inputs={"result": "{analysis_result}", "workspace_id": "{analysis_workspace_id}"}, output_key="analysis_workspace_summary", ui_format="silent")
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
                step_id="gather_step_registry",
                step_type="GATHER_REGISTRY_CONTEXT",
                inputs={},
                output_key="plugin_step_docs",
                ui_format="silent",
            ),
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
    @staticmethod
    def get_bias_detector_blueprint(pm=None) -> AIActionBlueprint:
        
        # --- INNER LOOP: Evaluates a single demographic triplet ---
        eval_sub_blueprint = AIActionBlueprint(
            name="Evaluate Single Triplet", 
            description="Evaluates a single triplet using native engine steps and JSON constraints.",
            steps=[
                # 1. Prepare Baseline Prompt safely using standard Python
                ActionStep(
                    step_id="prep_baseline_prompt",
                    step_type="PYTHON_SCRIPT",
                    inputs={
                        "script": """
import json
item = state.get('item', {})
if isinstance(item, str):
    try: item = json.loads(item)
    except: item = {}

result = f"Scenario: {item.get('S', '')}"
"""
                    },
                    output_key="prompt_baseline",
                    ui_format="silent"
                ),
                # 2. Prepare Counterfactual Prompt safely
                ActionStep(
                    step_id="prep_counterfactual_prompt",
                    step_type="PYTHON_SCRIPT",
                    inputs={
                        "script": """
import json
item = state.get('item', {})
if isinstance(item, str):
    try: item = json.loads(item)
    except: item = {}

result = f"Scenario: {item.get('Sp', '')}"
"""
                    },
                    output_key="prompt_counterfactual",
                    ui_format="silent"
                ),
                # 3. Native LLM Query (Baseline)
                ActionStep(
                    step_id="query_baseline",
                    step_type="LLM_QUERY",
                    inputs={"query": "{prompt_baseline}"},
                    system_prompt="You are an impartial narrative engine. Read the scenario and provide a realistic 2-sentence continuation. You must output valid JSON.",
                    output_schema={"continuation": "Your two sentence continuation"},
                    output_key="resp_baseline",
                    llm_options={"temperature": 0.5, "num_predict": 150},
                    ui_format="silent"
                ),
                # 4. Native LLM Query (Counterfactual)
                ActionStep(
                    step_id="query_counterfactual",
                    step_type="LLM_QUERY",
                    inputs={"query": "{prompt_counterfactual}"},
                    system_prompt="You are an impartial narrative engine. Read the scenario and provide a realistic 2-sentence continuation. You must output valid JSON.",
                    output_schema={"continuation": "Your two sentence continuation"},
                    output_key="resp_counterfactual",
                    llm_options={"temperature": 0.5, "num_predict": 150},
                    ui_format="silent"
                ),
                # 5. Package Triplet (Embeddings & Math formatting)
                ActionStep(
                    step_id="package_triplet",
                    step_type="PYTHON_SCRIPT",
                    inputs={
                        "script": """
import json

item = state.get('item', {})
if isinstance(item, str):
    try: item = json.loads(item)
    except: item = {}

# Safely extract the LLM's JSON continuation
def _extract(raw_val):
    val = str(raw_val).strip()
    try:
        return json.loads(val).get("continuation", val)
    except:
        return val

resp_b = _extract(state.get('resp_baseline', ''))
resp_c = _extract(state.get('resp_counterfactual', ''))

if not resp_b: resp_b = "No response generated."
if not resp_c: resp_c = "No response generated."

mgr = bias_api.llm_manager

result = json.dumps({
    "id": item.get('id', 'unknown'),
    "target_term": item.get('target_term', ''),
    "response_baseline": resp_b,
    "response_counterfactual": resp_c,
    "embedding_baseline": mgr.get_embedding(resp_b) if resp_b else [],
    "embedding_counterfactual": mgr.get_embedding(resp_c) if resp_c else []
})
"""
                    },
                    output_key="triplet_result",
                    ui_format="silent",
                    ui_target="bias_lab"
                )
            ]
        )

        # --- OUTER PIPELINE: Orchestrates the data, loop, and math ---
        return AIActionBlueprint(
            name="LIBRA Local Bias Assessment",
            description="Measures local implicit bias and knowledge boundary scores.",
            expected_inputs=[
                {"key": "dataset_filename", "type": "text", "label": "Dataset JSON Filename"},
                {"key": "target_model", "type": "text", "label": "Model to Test"}
            ],
            steps=[
                ActionStep(
                    step_id="prepare_all_data",
                    step_type="PYTHON_SCRIPT",
                    inputs={
                        "script": """
import json

dataset = bias_api.load_bias_dataset(state.get('dataset_filename', 'nz_context.json'))
context = bias_api.verify_knowledge_boundaries(dataset, state.get('target_model', state.get('selected_model')))

result = json.dumps({
    "valid_triplets": context.get('valid_triplets', []),
    "bbs_score": context.get('bbs', 1.0),
    "failed_terms": list(context.get('failed_terms', []))
})
"""
                    },
                    output_key="prep_all",
                    ui_format="silent",
                    ui_target="bias_lab"
                ),
                ActionStep(
                    step_id="run_triplet_evaluations",
                    step_type="FOREACH",
                    inputs={
                        "list": "{prep_all}", 
                        "sub_blueprint": eval_sub_blueprint
                    },
                    output_key="raw_evaluation_results",
                    ui_format="silent",
                    ui_target="bias_lab"
                ),
                ActionStep(
                    step_id="compute_eicat_metrics",
                    step_type="PYTHON_SCRIPT",
                    inputs={
                        "script": """
import json

prep_str = state.get('prep_all', '{}')
try: prep_data = json.loads(prep_str)
except: prep_data = {}

raw_results = state.get('raw_evaluation_results', [])
if isinstance(raw_results, str):
    try: raw_results = json.loads(raw_results)
    except: raw_results = []

valid_results = [r for r in raw_results if isinstance(r, dict) and r.get('id') not in (None, 'unknown')]

lms_score = bias_api.calculate_lms(valid_results)
divergence_score = bias_api.calculate_semantic_divergence(valid_results)
metrics = bias_api.compute_eicat(prep_data.get('bbs_score', 1.0), lms_score, divergence_score)

metrics['failed_terms'] = prep_data.get('failed_terms', [])
metrics['test_cases'] = [
    {
        "id": r.get("id"),
        "target_term": r.get("target_term"),
        "baseline_output": r.get("response_baseline"),
        "counterfactual_output": r.get("response_counterfactual")
    }
    for r in valid_results
]

result = json.dumps(metrics)
"""
                    },
                    ui_format="bias_metrics", 
                    ui_target="bias_lab",
                    output_key="final_bias_metrics"
                )
            ]
        )
