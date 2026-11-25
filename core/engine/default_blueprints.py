# core/engine/default_blueprints.py
import copy
import json
from core.models.analysis_patch_schema import analysis_patch_schema
from core.engine.action_model import AIActionBlueprint, ActionStep

class DefaultBlueprints:
    @staticmethod
    def get_citation_coverage_steps(answer_key="final_answer", context_key="rag_context", ui_target="chat_dock") -> list[ActionStep]:
        """Validate answer-to-evidence coverage without another model call."""
        return [ActionStep(
            step_id="validate_citation_coverage",
            step_type="CITATION_VALIDATE",
            inputs={"answer": f"{{{answer_key}}}", "context": f"{{{context_key}}}"},
            output_key="citations",
            ui_format="chat_widgets",
            ui_target=ui_target,
            status_text="Validating source support…",
        )]

    @staticmethod
    def get_universal_citation_step(answer_key="final_answer", context_key="rag_context", ui_target="floating") -> ActionStep:
        # System prompt lives in PromptManager ("Widget - Citation Step") so it is user-editable.
        # Query is compact: just the content blocks, no repeated instruction text.
        query_text = f"SOURCE:\n{{{context_key}}}\n\nANSWER:\n{{{answer_key}}}"
        return ActionStep(
            step_id="generate_citation_coverage",
            step_type="LLM_QUERY",
            inputs={"query": query_text},
            system_prompt="{prompt:Widget - Citation Step}",
            output_schema={
                "citations": [{
                    "doc_name": "filename.pdf",
                    "quote": "exact verbatim sentence from source",
                    "note": "specific answer claim this quote proves",
                    "source": "label of the passage, e.g. S3",
                }]
            },
            llm_options={"temperature": 0.05, "num_predict": 4096, "num_ctx": 32768},
            output_key="citations",
            citation_source_key=context_key,
            ui_format="chat_widgets",
            ui_target=ui_target,
            status_text="Generating source citations...",
            required_context=[],
        )

    @staticmethod
    def get_manifest_update_step(user_key="user_query", answer_key="final_answer", ui_target="chat_dock") -> ActionStep:
        return ActionStep(
            step_id="update_project_manifest",
            step_type="LLM_QUERY",
            inputs={
                "query": (
                    "{prompt:Manifest Structured Update Query}\n\n"
                    f"USER REQUEST:\n{{{user_key}}}\n\nCOMPLETED ANSWER:\n{{{answer_key}}}"
                )
            },
            prompt_key="General Assistant",
            model="{fast_worker_model}",
            model_role="fast_worker_model",
            required_context=["manifest"],
            allow_manifest_updates=True,
            manifest_update_protocol="structured",
            llm_retry_attempts=1,
            output_schema={
                "action": "update | no_change",
                "changes": {
                    "Core Thesis": "<the project's actual thesis in one specific sentence, grounded in the completed answer; omit this key entirely if the answer adds none>",
                    "Major Topics": {"<a real topic name from the answer>": ["<a real subtopic from the answer>"]},
                    "Open Questions": ["<a specific open question the answer actually raises>"],
                },
                "reason": "<one concrete sentence naming what durable content changed>",
            },
            llm_options={
                "temperature": 0.1,
                "json_mode": True,
                "num_predict": 1600,
                "num_ctx": 32768,
            },
            output_key="manifest_update_result",
            ui_format="silent",
            ui_target=ui_target,
        )

    @staticmethod
    def get_project_manifest_update_blueprint(
        pm=None, ui_target: str = "chat_dock",
    ) -> AIActionBlueprint:
        """Small registered workflow used after an answer has completed.

        Keeping the manifest mutation in its own master-runner job prevents an
        optional second model call (and its retry) from delaying the answer.
        """
        return AIActionBlueprint(
            name="Project Manifest Update",
            description="Review a completed answer for durable project changes.",
            steps=[DefaultBlueprints.get_manifest_update_step(
                user_key="manifest_user_query",
                answer_key="manifest_answer",
                ui_target=ui_target,
            )],
        )

    @staticmethod
    def get_manifest_update_dispatch_step(
        user_key="user_query",
        answer_key="final_answer",
        ui_target="chat_dock",
    ) -> ActionStep:
        """Queue the registered manifest workflow without blocking this run."""
        return ActionStep(
            step_id="queue_project_manifest_update",
            step_type="DISPATCH_EVENT",
            inputs={
                "signal_name": "workflow_action_requested",
                "intent": "RUN_BLUEPRINT",
                "payload": {
                    "blueprint_id": "project_manifest_update",
                    "target_id": ui_target,
                    "job_name": "Project Manifest Update",
                    "job_type": "Background Intelligence",
                    "initial_state": {
                        "manifest_user_query": f"{{{user_key}}}",
                        "manifest_answer": f"{{{answer_key}}}",
                        "include_manifest": True,
                        "allow_manifest_updates": True,
                    },
                },
            },
            output_key="manifest_update_job",
            ui_format="silent",
            ui_target=ui_target,
            status_text="Queueing project manifest review…",
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
            llm_options={"temperature": 0.1, "json_mode": True, "num_predict": 6000, "num_ctx": 32768},
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
                llm_options={"temperature": 0.15, "json_mode": True, "num_predict": 6500, "num_ctx": 32768},
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
                model_role="fast_worker_model",
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
                    model_role="main_answer_model",
                    prompt_key="{chat_persona}",
                    required_context=["manifest"],
                    ui_format="live_stream",
                    ui_target="chat_dock",
                    output_key="final_answer",
                    inline_citations=False,
                    conversation_role="assistant",
                    show_reasoning=True,
                    llm_options={"temperature": 0.6, "num_predict": 7000, "num_ctx": 32768},
                ),
                *DefaultBlueprints.get_citation_coverage_steps(
                    answer_key="final_answer", context_key="rag_context", ui_target="chat_dock"
                ),
                ActionStep(
                    step_id="manifest_update_router",
                    step_type="BRANCH",
                    inputs={"logic": "state.get('allow_manifest_updates', False) == True"},
                    if_true=[DefaultBlueprints.get_manifest_update_dispatch_step(
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

    # ── Intelligence analysis modes (artifact-aware, retrieve→synthesize) ──
    @staticmethod
    def _intel_retrieve_steps(query_key: str, source_ids_expr: str = "{allowed_source_ids}",
                              max_results: int = 10, prefix: str = "intel") -> list[ActionStep]:
        """Shared retrieve primitive: plan → retrieve → pack (citation-ready)."""
        return [
            ActionStep(
                step_id=f"{prefix}_build_plan", step_type="BUILD_RETRIEVAL_PLAN",
                inputs={"query": f"{{{query_key}}}", "allowed_source_ids": source_ids_expr,
                        "max_results": max_results},
                output_key="retrieval_plan", ui_format="silent",
            ),
            ActionStep(
                step_id=f"{prefix}_retrieve", step_type="ARTIFACT_RETRIEVE",
                inputs={"plan": "{retrieval_plan}"}, output_key="retrieved_artifacts", ui_format="silent",
            ),
            ActionStep(
                step_id=f"{prefix}_pack", step_type="PACK_ARTIFACT_CONTEXT",
                inputs={"query": f"{{{query_key}}}", "results": "{retrieved_artifacts}"},
                output_key="packed_context", ui_format="silent",
            ),
        ]

    @staticmethod
    def get_intelligence_qa_blueprint(pm=None, model: str = "{selected_model}") -> AIActionBlueprint:
        return AIActionBlueprint(
            name="Q&A (Intelligence)",
            description="Artifact-aware Q&A: multi-signal retrieve (semantic + entity + keyword) → synthesize with segment-id citations.",
            mount_points=["analysis_mode", "research_tab"],
            expected_inputs=[{"key": "user_query", "type": "text", "label": "Question"}],
            steps=[
                *DefaultBlueprints._intel_retrieve_steps("user_query", max_results=10, prefix="qa"),
                ActionStep(
                    step_id="qa_answer", step_type="LLM_QUERY",
                    inputs={"query": "{prompt:Intelligence QA Query}\n\nCONTEXT:\n{packed_context}\n\nQUESTION: {user_query}"},
                    system_prompt="{prompt:Intelligence QA System}",
                    model=model, model_role="main_answer_model",
                    output_key="final_answer", citation_source_key="packed_context",
                    required_prompt_keys=["Intelligence QA System", "Intelligence QA Query"],
                    ui_format="live_stream", ui_target="chat_dock",
                    conversation_role="assistant",
                    llm_options={"temperature": 0.2, "num_predict": 4000, "num_ctx": 32768},
                ),
                *DefaultBlueprints.get_citation_coverage_steps(
                    answer_key="final_answer", context_key="packed_context", ui_target="chat_dock"),
                ActionStep(
                    step_id="qa_persist", step_type="ANALYSIS_OUTPUT_WRITE",
                    inputs={
                        "mode": "{analysis_mode_id}", "result_kind": "narrative",
                        "artifact_type": "analysis_answer", "output": "{final_answer}",
                        "evidence": "{citations}", "source_id": "{source_id}",
                        "params_hash": "{params_hash}", "doc_hash": "{doc_hash}",
                        "model_role": "main_answer_model",
                    },
                    output_key="analysis_result_envelope", ui_format="silent",
                ),
            ],
        )

    @staticmethod
    def get_intelligence_claim_tracking_blueprint(pm=None, model: str = "{selected_model}") -> AIActionBlueprint:
        return DefaultBlueprints.get_intelligence_mode_graph_blueprint(
            pm, model=model, name="Claim Tracking (Intelligence)"
        )

    @staticmethod
    def get_intelligence_analysis_mode_blueprint(
        pm=None,
        analysis_spec=None,
        model: str = "{selected_model}",
    ) -> AIActionBlueprint:
        """The single registered blueprint factory for every analysis mode.

        AnalysisModeSpec is data: it selects the appropriate reusable step
        composition without requiring a separately registered blueprint.
        User/plugin modes therefore gain the same preparation, runner,
        provenance, cancellation, and output contract automatically.
        """
        mode_id = str(getattr(analysis_spec, "id", "") or "custom_graph")
        result_kind = str(
            getattr(analysis_spec, "result_kind", "") or "narrative"
        )
        label = str(getattr(analysis_spec, "label", "") or "Analysis")
        if result_kind in {"graph", "composite"}:
            return DefaultBlueprints.get_intelligence_mode_graph_blueprint(
                pm,
                model=model,
                name=f"{label} (Universal Analysis)",
                required_stages=list(
                    getattr(analysis_spec, "required_stages", None) or []
                ),
            )
        if result_kind == "timeline":
            return DefaultBlueprints.get_intelligence_timeline_blueprint(pm)
        if mode_id == "qa":
            return DefaultBlueprints.get_intelligence_qa_blueprint(pm, model=model)
        if mode_id == "extraction" or result_kind == "table":
            return DefaultBlueprints.get_intelligence_schema_extraction_blueprint(
                pm, model=model
            )
        if mode_id == "summaries":
            return DefaultBlueprints.get_intelligence_summaries_blueprint(
                pm, model=model
            )
        # A user-created narrative mode gets the artifact-aware Q&A composition
        # and its AnalysisModeSpec description/parameters remain available in
        # state as the mode contract.
        return DefaultBlueprints.get_intelligence_qa_blueprint(pm, model=model)

    @staticmethod
    def get_intelligence_mode_graph_blueprint(
        pm=None,
        model: str = "{selected_model}",
        name: str = "Argument Map (Intelligence)",
        required_stages=None,
    ) -> AIActionBlueprint:
        """Build a grounded baseline, then enhance and repair it through the runner."""
        preparation_stages = [
            stage for stage in list(required_stages if required_stages is not None else ["parsed_blocks", "sections", "claims"])
            if stage != "graph_seeds"
        ]
        return AIActionBlueprint(
            name=name,
            description=(
                "Prepare section-aware evidence packets from reusable intelligence "
                "artifacts, then use prompt-managed model calls to enhance and, only "
                "when validation fails, repair the typed graph."
            ),
            mount_points=["analysis_mode", "document_analysis"],
            expected_inputs=[
                {"key": "analysis_focus", "type": "text", "label": "Analysis focus"},
                {"key": "allowed_source_ids", "type": "list", "label": "Sources"},
            ],
            steps=[
                ActionStep(
                    step_id="prepare_analysis_artifacts",
                    step_type="SOURCE_STAGE",
                    inputs={
                        "action": "ensure_analysis",
                        "source_id": "{source_id}",
                        "source_ids": "{allowed_source_ids}",
                        "stages": preparation_stages,
                    },
                    output_key="analysis_preparation",
                    ui_format="status",
                    ui_title="Preparing reusable intelligence",
                ),
                ActionStep(
                    step_id="project_complete_artifact_graph",
                    step_type="DETERMINISTIC_GRAPH_ANALYSIS",
                    inputs={
                        "doc_path": "{doc_path}",
                        "source_id": "{source_id}",
                        "allowed_source_ids": "{allowed_source_ids}",
                        "mode_id": "{analysis_mode_id}",
                        "analysis_focus": "{analysis_focus}",
                    },
                    output_key="deterministic_graph_envelope",
                    ui_format="silent",
                ),
                ActionStep(
                    step_id="build_mode_graph",
                    step_type="LLM_QUERY",
                    inputs={
                        "context_items_key": "analysis_model_context_items",
                        "query": (
                            "{prompt:Intelligence Universal Graph Query v6}\n\n"
                            "MODE: {analysis_mode_label}\n"
                            "FOCUS OR CLAIM: {analysis_focus}\n"
                            "MODE GRAPH CONTRACT:\n{analysis_graph_contract}"
                        ),
                    },
                    system_prompt="{prompt:Intelligence Universal Graph System v6}",
                    required_prompt_keys=[
                        "Intelligence Universal Graph System v6",
                        "Intelligence Universal Graph Query v6",
                        "Intelligence Mode Graph Output Enforcer v4",
                    ],
                    model=model,
                    model_role="main_answer_model",
                    output_schema=analysis_patch_schema(),
                    output_schema_prompt_key=(
                        "Intelligence Mode Graph Output Enforcer v4"
                    ),
                    output_key="mode_graph_raw",
                    llm_options={
                        "json_mode": True, "temperature": 0.1,
                        # The model returns a small patch; the app retains and
                        # validates the complete deterministic graph locally.
                        "num_predict": 1200, "num_ctx": 32768,
                    },
                    ui_format="silent",
                ),
                ActionStep(
                    step_id="hydrate_mode_graph",
                    step_type="GRAPH_ENRICH_VALIDATE",
                    inputs={
                        "graph_state_key": "deterministic_graph_envelope",
                        "enrichment_state_key": "mode_graph_raw",
                        "source_id": "{source_id}",
                        "attempt": "initial",
                        "packing_receipt_key": "mode_graph_raw",
                    },
                    output_key="validated_mode_graph",
                    ui_format="silent",
                ),
                ActionStep(
                    step_id="repair_mode_graph_if_needed",
                    step_type="BRANCH",
                    inputs={
                        "logic": (
                            "state.get('analysis_graph_needs_repair', False) == True "
                            "and state.get('analysis_allow_repair', True) == True"
                        )
                    },
                    if_true=[
                        ActionStep(
                            step_id="repair_mode_graph",
                            step_type="LLM_QUERY",
                            inputs={
                                "context_items_key": "analysis_model_context_items",
                                "query": (
                                    "{prompt:Intelligence Universal Graph Repair Query v4}\n\n"
                                    "MODE: {analysis_mode_label}\n"
                                    "FOCUS OR CLAIM: {analysis_focus}\n"
                                    "MODE GRAPH CONTRACT:\n{analysis_graph_contract}\n\n"
                                    "VALIDATION DIAGNOSTICS:\n{analysis_graph_validation}"
                                ),
                            },
                            system_prompt="{prompt:Intelligence Universal Graph System v6}",
                            required_prompt_keys=[
                                "Intelligence Universal Graph System v6",
                                "Intelligence Universal Graph Repair Query v4",
                                "Intelligence Mode Graph Output Enforcer v4",
                            ],
                            model=model,
                            model_role="main_answer_model",
                            output_schema=analysis_patch_schema(),
                            output_schema_prompt_key=(
                                "Intelligence Mode Graph Output Enforcer v4"
                            ),
                            output_key="mode_graph_repair_raw",
                            llm_options={
                                "json_mode": True, "temperature": 0.05,
                                "num_predict": 1200, "num_ctx": 32768,
                            },
                            ui_format="silent",
                        ),
                        ActionStep(
                            step_id="hydrate_repaired_mode_graph",
                            step_type="GRAPH_ENRICH_VALIDATE",
                            inputs={
                                "graph_state_key": "deterministic_graph_envelope",
                                "enrichment_state_key": "mode_graph_repair_raw",
                                "source_id": "{source_id}",
                                "attempt": "repair",
                                "packing_receipt_key": "mode_graph_repair_raw",
                            },
                            output_key="validated_mode_graph",
                            ui_format="silent",
                        ),
                    ],
                    if_false=[],
                ),
                ActionStep(
                    step_id="persist_mode_graph",
                    step_type="ANALYSIS_OUTPUT_WRITE",
                    inputs={
                        "mode": "{analysis_mode_id}",
                        "result_kind": "graph",
                        "artifact_type": "analysis_result",
                        "output_state_key": "validated_mode_graph",
                        "source_id": "{source_id}",
                        "params_hash": "{params_hash}",
                        "doc_hash": "{doc_hash}",
                        "model_role": "main_answer_model",
                    },
                    output_key="analysis_result_envelope",
                    ui_format="silent",
                ),
            ],
        )

    @staticmethod
    def get_intelligence_timeline_blueprint(pm=None) -> AIActionBlueprint:
        return AIActionBlueprint(
            name="Timeline (Intelligence)",
            description="Prepare dates and save a deterministic timeline through the master runner.",
            mount_points=["analysis_mode"],
            steps=[
                ActionStep(
                    step_id="prepare_timeline",
                    step_type="SOURCE_STAGE",
                    inputs={
                        "action": "ensure_analysis",
                        "source_id": "{source_id}",
                        "source_ids": "{allowed_source_ids}",
                        "stages": ["dates"],
                    },
                    output_key="analysis_preparation",
                    ui_format="status",
                ),
                ActionStep(
                    step_id="project_timeline",
                    step_type="DETERMINISTIC_GRAPH_ANALYSIS",
                    inputs={
                        "doc_path": "{doc_path}",
                        "source_id": "{source_id}",
                        "allowed_source_ids": "{allowed_source_ids}",
                        "mode_id": "timeline",
                    },
                    output_key="deterministic_timeline",
                    ui_format="silent",
                ),
                ActionStep(
                    step_id="persist_timeline",
                    step_type="ANALYSIS_OUTPUT_WRITE",
                    inputs={
                        "mode": "{analysis_mode_id}",
                        "result_kind": "timeline",
                        "artifact_type": "analysis_result",
                        "output": "{deterministic_graph_output}",
                        "source_id": "{source_id}",
                        "params_hash": "{params_hash}",
                        "doc_hash": "{doc_hash}",
                    },
                    output_key="analysis_result_envelope",
                    ui_format="silent",
                ),
            ],
        )

    @staticmethod
    def get_intelligence_schema_extraction_blueprint(pm=None, model: str = "{selected_model}") -> AIActionBlueprint:
        return AIActionBlueprint(
            name="Schema Extraction (Intelligence)",
            description="Schema-driven field extraction over retrieved context; the LLM fills only ambiguous fields, structured JSON.",
            mount_points=["analysis_mode"],
            expected_inputs=[
                {"key": "extraction_query", "type": "text", "label": "What to extract"},
                {"key": "extraction_schema", "type": "text", "label": "Field schema (JSON)"},
            ],
            steps=[
                *DefaultBlueprints._intel_retrieve_steps("extraction_query", max_results=15, prefix="ex"),
                ActionStep(
                    step_id="ex_extract", step_type="LLM_QUERY",
                    inputs={"query": "{prompt:Intelligence Schema Extraction Query}\n\nSCHEMA:\n{extraction_schema}\n\nCONTEXT:\n{packed_context}"},
                    system_prompt="{prompt:Intelligence Schema Extraction System}",
                    model=model, model_role="fast_worker_model",
                    output_key="extraction_result",
                    required_prompt_keys=["Intelligence Schema Extraction System", "Intelligence Schema Extraction Query"],
                    llm_options={"json_mode": True, "temperature": 0.1, "num_predict": 3000, "num_ctx": 32768},
                    ui_format="data_table", ui_target="custom_tools_tab",
                ),
                ActionStep(
                    step_id="ex_persist", step_type="ANALYSIS_OUTPUT_WRITE",
                    inputs={
                        "mode": "{analysis_mode_id}", "result_kind": "table",
                        "artifact_type": "schema_extraction",
                        "output": "{extraction_result}", "source_id": "{source_id}",
                        "params_hash": "{params_hash}", "doc_hash": "{doc_hash}",
                        "model_role": "fast_worker_model",
                    },
                    output_key="analysis_result_envelope", ui_format="silent",
                ),
            ],
        )

    @staticmethod
    def get_intelligence_graph_enrichment_blueprint(pm=None, model: str = "{selected_model}") -> AIActionBlueprint:
        return AIActionBlueprint(
            name="Explain / Enrich Graph with AI",
            description="One explicit main-model call over compact artifact IDs; suggestions remain unverified.",
            mount_points=["analysis_mode"],
            expected_inputs=[{"key": "deterministic_graph", "type": "json", "label": "Deterministic graph"}],
            steps=[
                ActionStep(
                    step_id="graph_enrich", step_type="LLM_QUERY",
                    inputs={
                        "query": "{prompt:Intelligence Graph Enrichment Query}\n\n"
                                 "GRAPH:\n{deterministic_graph}",
                    },
                    system_prompt="{prompt:Intelligence Graph Enrichment System}",
                    required_prompt_keys=[
                        "Intelligence Graph Enrichment System",
                        "Intelligence Graph Enrichment Query",
                    ],
                    model=model, model_role="main_answer_model",
                    output_schema={
                        "themes": [{"name": "theme", "node_ids": ["artifact id"]}],
                        "proposed_relations": [{
                            "source_id": "artifact id", "target_id": "artifact id",
                            "type": "suggested relation", "evidence_ids": ["artifact id"],
                            "explanation": "short explanation",
                        }],
                    },
                    output_key="graph_enrichment_raw",
                    llm_options={
                        "json_mode": True, "temperature": 0.1,
                        "num_predict": 2500, "num_ctx": 32768,
                    },
                    ui_format="silent",
                ),
                ActionStep(
                    step_id="graph_enrich_validate",
                    step_type="GRAPH_ENRICH_VALIDATE",
                    inputs={
                        "graph": "{deterministic_graph}",
                        "enrichment": "{graph_enrichment_raw}",
                        "source_id": "{source_id}",
                    },
                    output_key="graph_enrichment",
                    ui_format="data_table",
                    ui_target="analysis_tab",
                    ui_title="Suggested graph enrichment",
                ),
                ActionStep(
                    step_id="graph_enrich_persist", step_type="ANALYSIS_OUTPUT_WRITE",
                    inputs={
                        "mode": "{analysis_mode_id}", "result_kind": "composite",
                        "output": "{graph_enrichment}", "source_id": "{source_id}",
                        "params_hash": "{params_hash}", "doc_hash": "{doc_hash}",
                        "model_role": "main_answer_model", "persist_artifact": False,
                    },
                    output_key="analysis_result_envelope", ui_format="silent",
                ),
            ],
        )

    @staticmethod
    def get_intelligence_summaries_blueprint(pm=None, model: str = "{selected_model}") -> AIActionBlueprint:
        # Shape the LLM summary into an ARTIFACT_WRITE payload (created_by="model").
        shape_script = (
            "import json\n"
            "raw = state.get('abstractive_summary', '{}')\n"
            "try:\n    obj = json.loads(raw) if isinstance(raw, str) else (raw or {})\nexcept Exception:\n    obj = {}\n"
            "sid = str(state.get('source_id') or '')\n"
            "summary = str(obj.get('summary') or '')\n"
            "points = obj.get('key_points') or []\n"
            "text = summary + ('\\n- ' + '\\n- '.join(str(p) for p in points) if points else '')\n"
            "result = json.dumps([{ 'ref':'absum','artifact_type':'summary','storage_ref_type':'abstractive_summary',\n"
            "    'storage_ref_id': sid + ':abstractive','source_id': sid,'exact_text': text,\n"
            "    'metadata': {'scope':'document','method':'abstractive'},'confidence':0.7,'model_role':'main_answer_model'}])\n"
        )
        return AIActionBlueprint(
            name="Abstractive Summaries (Intelligence)",
            description="Optional LLM abstractive summary of a source's content, persisted as a model-produced knowledge artifact.",
            mount_points=["analysis_mode"],
            expected_inputs=[{"key": "source_id", "type": "text", "label": "Source"}],
            steps=[
                ActionStep(
                    step_id="sum_retrieve", step_type="ARTIFACT_RETRIEVE",
                    inputs={"query": "{summary_focus}", "allowed_source_ids": ["{source_id}"], "max_results": 25},
                    output_key="retrieved_artifacts", ui_format="silent",
                ),
                ActionStep(
                    step_id="sum_pack", step_type="PACK_ARTIFACT_CONTEXT",
                    inputs={"query": "{summary_focus}", "results": "{retrieved_artifacts}"},
                    output_key="packed_context", ui_format="silent",
                ),
                ActionStep(
                    step_id="sum_llm", step_type="LLM_QUERY",
                    inputs={"query": "{prompt:Intelligence Abstractive Summary Query}\n\nCONTENT:\n{packed_context}"},
                    system_prompt="{prompt:Intelligence Abstractive Summary System}",
                    model=model, model_role="main_answer_model",
                    output_schema={"summary": "one-paragraph abstractive summary", "key_points": ["point"]},
                    output_key="abstractive_summary",
                    required_prompt_keys=["Intelligence Abstractive Summary System", "Intelligence Abstractive Summary Query"],
                    llm_options={"json_mode": True, "temperature": 0.2, "num_predict": 2000, "num_ctx": 32768},
                    ui_format="silent",
                ),
                ActionStep(
                    step_id="sum_shape", step_type="PYTHON_SCRIPT",
                    inputs={"script": shape_script}, output_key="abstractive_summary_artifacts", ui_format="silent",
                ),
                ActionStep(
                    step_id="sum_write", step_type="ARTIFACT_WRITE",
                    inputs={"created_by": "model",
                            "artifacts": "{abstractive_summary_artifacts}"},
                    output_key="summary_write", ui_format="silent",
                ),
                ActionStep(
                    step_id="sum_persist", step_type="ANALYSIS_OUTPUT_WRITE",
                    inputs={
                        "mode": "{analysis_mode_id}", "result_kind": "narrative",
                        "artifact_type": "analysis_answer",
                        "output": "{abstractive_summary}", "source_id": "{source_id}",
                        "params_hash": "{params_hash}", "doc_hash": "{doc_hash}",
                        "model_role": "main_answer_model",
                    },
                    output_key="analysis_result_envelope", ui_format="silent",
                ),
            ],
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

        has_rag = "RAG" in prompt_key
        steps.append(
            ActionStep(
                step_id="brainstorm_reply", step_type="LLM_QUERY",
                inputs={"query": query_text},
                model=model, prompt_key=prompt_key,
                model_role="main_answer_model",
                required_context=required_context,
                ui_format="live_stream", ui_target="brainstorm_dock", output_key="final_answer",
                inline_citations=False,
                conversation_role="assistant",
                show_reasoning=True,
                llm_options={"temperature": 0.75, "num_predict": 7000, "num_ctx": 32768},
            )
        )
        if has_rag:
            steps.extend(DefaultBlueprints.get_citation_coverage_steps(
                answer_key="final_answer",
                context_key="context",
                ui_target="brainstorm_dock",
            ))
        steps.append(ActionStep(
            step_id="manifest_update_router",
            step_type="BRANCH",
            inputs={"logic": "state.get('allow_manifest_updates', False) == True"},
            if_true=[DefaultBlueprints.get_manifest_update_dispatch_step(
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
                model_role="fast_worker_model",
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
                    model_role="fast_worker_model",
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
                model_role="fast_worker_model",
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
                model_role="main_answer_model",
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
                model_role="main_answer_model",
                llm_options={"json_mode": True, "num_predict": "{analysis_limits.master_num_predict}", "temperature": 0.08, "num_ctx": "{analysis_limits.num_ctx}"},
                output_key="master_diagram",
                ui_format="silent",
                ui_target="analysis_tab",
            ),
            ActionStep("finalize_analysis", "ANALYSIS_FINALIZE", inputs={"doc_path": doc_path, "template_id": "{analysis_template_id}", "template": "{analysis_template}", "contract": "{analysis_contract}", "chunks": "{final_analysis}", "synthesis": "{argument_synthesis}", "master": "{master_diagram}", "run_id": "{analysis_run_id}"}, output_key="analysis_result", ui_format="silent"),
        ])

    @staticmethod
    def get_analysis_blueprint(pm, doc_path: str = "{analysis_doc_path}") -> AIActionBlueprint:
        """Return the zero-generative, complete artifact graph projection.

        Candidate selection, relation evidence, communities, centrality,
        chronology, and claim/evidence chains are materialized locally. Optional
        one-call AI enrichment is a separate explicitly selected analysis mode.
        """
        return AIActionBlueprint(
            name="Document Analysis",
            description="Complete evidence-backed artifact graph; zero generative calls by default.",
            mount_points=["analysis_tab", "analysis_mode"],
            steps=[
                ActionStep(
                    step_id="deterministic_document_graph",
                    step_type="DETERMINISTIC_GRAPH_ANALYSIS",
                    inputs={
                        "doc_path": doc_path,
                        "source_id": "{source_id}",
                        "allowed_source_ids": "{allowed_source_ids}",
                        "mode_id": "argument_map",
                    },
                    output_key="analysis_result",
                    ui_format="silent",
                    ui_target="analysis_tab",
                ),
            ],
        )
        # ── Sub-blueprint 1: per-chunk evidence extraction ──────────────────
        evidence_sub = AIActionBlueprint(name="Extract Chunk Evidence", description="", steps=[
            ActionStep(
                step_id="rank_document_blocks",
                step_type="RANK_BLOCKS",
                inputs={
                    "doc_path": "{analysis_doc_path}",
                    "block_ids": "{item.block_ids}",
                    "passes": "{analysis_template.deterministic_passes}",
                    "fallback_text": "{item.text}",
                    "base_num_predict": "{analysis_limits.evidence_num_predict}",
                },
                output_key="rank_result",
                ui_format="silent",
                ui_target="analysis_tab",
            ),
            ActionStep(
                step_id="extract_chunk_evidence",
                step_type="LLM_QUERY",
                inputs={"query": "{analysis_evidence_query_prompt}"},
                system_prompt="{analysis_evidence_system_prompt}",
                llm_options={
                    "json_mode": True,
                    "num_predict": "{rank_result.suggested_evidence_num_predict}",
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
                    "total_chunks": "{analysis_total_chunks}",
                    "section_id": "{item.section_id}",
                    "section_number": "{item.section_number}",
                    "total_sections": "{analysis_total_sections}",
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
                    "num_predict": "{analysis_limits.section_num_predict}",
                    "temperature": 0.12,
                    "num_ctx": "{analysis_limits.section_num_ctx}",
                },
                output_key="section_synthesis_result",
                ui_format="status",
                ui_target="analysis_tab",
                status_text="Synthesizing section {item.section_number} of {item.total_sections}...",
            ),
            ActionStep(
                step_id="store_section_synthesis",
                step_type="SECTION_SYNTHESIS_STORE",
                inputs={
                    "section_synthesis_result": "{section_synthesis_result}",
                    "section_number": "{item.section_number}",
                    "total_sections": "{item.total_sections}",
                },
                output_key="section_synthesis_parsed",
                ui_format="silent",
                ui_target="analysis_tab",
            ),
        ])

        # ── Merge script: build section_syntheses_compact from synthesis results.
        # (top_quote_ids_compact is already built by SECTION_GROUP itself.)
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
                # 2. Chunk the document along real section boundaries (falls back to
                #    page-based chunking when structure extraction is unavailable)
                ActionStep(
                    "chunk_document", "SECTION_EXTRACT",
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
                    inputs={
                        "list": "{doc_chunks}",
                        "sub_blueprint": evidence_sub,
                        "inherit_state_keys": [
                            "selected_model", "target_id", "analysis_run_id", "analysis_doc_path",
                            "analysis_template_id", "analysis_limits", "analysis_evidence_query_prompt",
                            "analysis_evidence_system_prompt", "analysis_total_chunks",
                            "analysis_total_sections", "analysis_template",
                        ],
                    },
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
                    inputs={
                        "list": "{section_groups}",
                        "sub_blueprint": section_synthesis_sub,
                        "inherit_state_keys": [
                            "selected_model", "target_id", "analysis_limits",
                            "analysis_section_synthesis_query_prompt",
                            "analysis_section_synthesis_system_prompt",
                        ],
                    },
                    output_key="all_section_syntheses",
                    ui_format="silent",
                    status_text="Synthesizing sections...",
                ),
                # 6. Merge synthesis results → section_syntheses_compact state key
                #    (top_quote_ids_compact was already built by SECTION_GROUP).
                ActionStep(
                    "merge_syntheses", "PYTHON_SCRIPT",
                    inputs={"script": merge_syntheses_script},
                    output_key="section_syntheses_compact",
                    ui_format="status",
                    ui_target="analysis_tab",
                    status_text="Merging section syntheses...",
                ),
                # 7. Single graph-assembly pass: builds the full graph directly from
                #    section summaries + top evidence quote_ids. A per-section
                #    map-reduce subgraph design was tried and reliably produced
                #    sparse, low-quality graphs with small local models — a single
                #    well-specified full-graph call is more reliable, and still
                #    saves one LLM call versus the original plan+assembly design.
                ActionStep(
                    "graph_assembly_pass", "LLM_QUERY",
                    inputs={"query": "{analysis_graph_assembly_query_prompt}"},
                    system_prompt="{analysis_graph_assembly_system_prompt}",
                    model_role="main_answer_model",
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
                # 8. Hydrate quote_ids → exact text from DB
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
    def get_analysis_send_to_workspace_blueprint(
        result: dict | None = None,
        workspace_id: int | None = None,
    ) -> AIActionBlueprint:
        # When invoked from a result card, embed an immutable copy of that
        # exact result in the blueprint. A generic state key can otherwise be
        # replaced by an older analysis while the workflow waits in the queue.
        result_input = (
            copy.deepcopy(result)
            if isinstance(result, dict) else "{analysis_result}"
        )
        workspace_input = (
            int(workspace_id)
            if workspace_id not in (None, "")
            else "{analysis_workspace_id}"
        )
        return AIActionBlueprint(name="Send Analysis To Workspace", description="Emit analyzed graph artifacts into the workspace.", mount_points=["analysis_tab"], steps=[
            ActionStep(
                "send_analysis_to_workspace",
                "ANALYSIS_SEND_TO_WORKSPACE",
                inputs={"result": result_input, "workspace_id": workspace_input},
                output_key="analysis_workspace_summary",
                ui_format="silent",
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
                system_prompt="{prompt:Reword Text System}",
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
                system_prompt="{prompt:Opposing Views System}",
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
        return AIActionBlueprint(
            name="LIBRA Local Bias Assessment",
            description="Measures local bias with exact LIBRA causal-LM logprob metrics.",
            expected_inputs=[
                {"key": "dataset_filename", "type": "text", "label": "Dataset JSON Filename"},
                {"key": "target_model", "type": "text", "label": "Model to Test"}
            ],
            steps=[
                ActionStep(
                    step_id="run_libra_bias_evaluation",
                    step_type="LIBRA_BIAS_EVALUATION",
                    inputs={
                        "dataset_filename": "{dataset_filename}",
                        "target_model": "{target_model}",
                    },
                    output_key="final_bias_metrics",
                    ui_format="bias_metrics",
                    ui_target="bias_lab"
                )
            ]
        )
