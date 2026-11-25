import json
import os
import sys

class PromptManager:
    CATEGORIES = {
        "Agent Personas": [
            "General Assistant", "RAG Agent Mode", "RAG Assistant Mode",
            "Evidence Extractor", "Search Term Generator", "Document Analyzer",
            "Graph Analysis Master System",
            "Graph Analysis Chunk Observations System",
            "Graph Analysis Synthesis System",
            "Graph Analysis Evidence Chunk System",
            "Graph Analysis Section Synthesis System",
            "Graph Analysis Graph Plan System",
            "Graph Analysis Graph Assembly System",
            "Master Outline Generator", "Brainstorming Agent", "Compare Outlines System",
            "Blueprint Architect System", "Auto Build Graph System", 
            "Analysis Search System", "Autopilot Planner System",
            "Research Agent Planner System",
            "Ontology Claim Extraction Schema", "Ontology Evidence Extraction Schema",
            "AI Organize Worker", "AI Group Worker", "AI Connections Worker",
            "AI Consolidate Worker - Planner", "AI Consolidate Worker - Executor"
        ],
        "Tool & Feature Queries": [
            "Brainstorm Query", "Search Terms Query", "Advanced RAG Optimize Query",
            "Universal Citation Query", "Analyze Chunk Query",
            "Citation Coverage Query", "Manifest Update Query",
            "Manifest Structured Update Query",
            "Plan Adaptive Research Query", "Build Typed Workspace Graph Query",
            "Graph Analysis Chunk Observations Query",
            "Graph Analysis Synthesis Query", "Graph Analysis Master Query",
            "Graph Analysis Evidence Chunk Query",
            "Graph Analysis Section Synthesis Query",
            "Graph Analysis Graph Plan Query",
            "Graph Analysis Graph Assembly Query",
            "Master Outline Query",
            "Compare Outlines Query", "Blueprint Architect Query", "Auto Build Graph Query",
            "Modular Workspace Query", "Consolidate Workspace Plan Query",
            "Workspace Outline Query", "Workspace Weakpoints Query",
            "Autopilot Planner Query", "Research Agent Planner Query",
            "Analysis Search Query", "Async Teardown Buffer Query",
            "Generate Counter Argument Query",
        ],
        "Brainstorming Configurations": [
            "Brainstorm System - RAG Only", "Brainstorm System - RAG Enabled",
            "Brainstorm System - Default"
        ],
        "System & Engine Enforcers": [
            "Manifest Update Directive", "Manifest Structured Update Directive",
            "JSON Schema Enforcer", "JSON Mode Enforcer",
            "Format Enforcer - Chat Widgets", "Format Enforcer - Data Table",
            "Format Enforcer - Card Grid", "Format Enforcer - Live Stream",
            "Inline Citation Directive", "Reasoning Disclosure Directive",
            "Context Budget Notice",
            "Graph Analysis Compact Output Contract",
            "Graph Analysis Argument Chain Directive",
            "Graph Analysis Section Synthesis Stage Rules",
            "LIBRA BBS Cloze Template", "LIBRA BBS Neutral Completion",
        ],
        "Widget Output Formats": [
            "Format Enforcer - Live Stream",
            "Format Enforcer - Chat Widgets",
            "Format Enforcer - Data Table",
            "Format Enforcer - Card Grid",
            "Format Enforcer - Nested Outline",
            "Format Enforcer - Search Terms",
            "Format Enforcer - Bias Metrics",
            "Inline Citation Directive",
            "Widget - Citation Step",
        ],
        "Context Injectors": [
            "Context Inject - Manifest", "Context Inject - Workspace",
            "Context Inject - Selected", "Context Inject - Analyses"
        ],
        "Discovery & Extraction": [
            "Discovery Analysis System",
            "Discovery Analysis Query",
            "Chart Analysis System v1",
            "Chart Evidence Policy v1",
            "Chart Structured Output v1",
            "Vision JSON Repair v1",
        ],
        "Intelligence Modes": [
            "Intelligence QA System", "Intelligence QA Query",
            "Intelligence Claim Tracking System", "Intelligence Claim Tracking Query",
            "Intelligence Abstractive Summary System", "Intelligence Abstractive Summary Query",
            "Intelligence Schema Extraction System", "Intelligence Schema Extraction Query",
            "Intelligence Graph Enrichment System", "Intelligence Graph Enrichment Query",
            "Intelligence Mode Graph System", "Intelligence Mode Graph Query",
            "Intelligence Universal Graph System v2",
            "Intelligence Universal Graph Query v2",
            "Intelligence Mode Graph Output Enforcer",
            "Intelligence Universal Graph System v3",
            "Intelligence Universal Graph Query v3",
            "Intelligence Universal Graph Repair Query v1",
            "Intelligence Mode Graph Output Enforcer v2",
            "Intelligence Universal Graph System v4",
            "Intelligence Universal Graph Query v4",
            "Intelligence Universal Graph Repair Query v2",
            "Intelligence Mode Graph Output Enforcer v3",
            "JSON Only Directive",
        ],
    }

    DEFAULT_PROMPTS = {
        # --- INTELLIGENCE MODES ---
        "Intelligence QA System": (
            "You are a precise research assistant. Answer ONLY from the provided CONTEXT, which "
            "contains passages labelled [S1], [S2], … with their document, page and section. "
            "Cite the label in square brackets after each claim you make, e.g. "
            "[S1]. If the context does not contain the answer, say so plainly. Do not invent facts."
        ),
        "Intelligence QA Query": "Answer the user's question using only the CONTEXT passages below. Cite their labels, e.g. [S1].",
        "Intelligence Claim Tracking System": (
            "You are tracking a single claim across a corpus. From the provided CONTEXT segments "
            "(each labelled [S1], [S2], … with its document), synthesize a concise narrative of where and how "
            "the claim appears: who states it, supporting evidence, and any contradictions. Cite labels, e.g. [S1]. "
            "Do not add claims not present in the context."
        ),
        "Intelligence Claim Tracking Query": "Trace the pinned claim through the CONTEXT segments below and summarize its occurrences with citations.",
        "Intelligence Abstractive Summary System": (
            "You are a careful summarizer. Produce a faithful abstractive summary grounded only in the "
            "provided section text. Do not introduce facts that are not present. Output valid JSON only."
        ),
        "Intelligence Abstractive Summary Query": "Summarize the section content below. Return JSON matching the provided schema.",
        "Intelligence Schema Extraction System": (
            "You are a structured extraction engine. Fill ONLY the requested fields using the provided "
            "CONTEXT. For any field not supported by the context, return null — never guess. Output valid JSON only."
        ),
        "Intelligence Schema Extraction Query": "Extract the requested fields from the CONTEXT below. Return JSON matching the schema.",
        "Intelligence Graph Enrichment System": (
            "You may name themes and propose relationships using only the supplied node, relation, "
            "community, and evidence artifact IDs. Never invent quotations. Every proposed relation "
            "must reference existing source_id, target_id, and one or more evidence_ids. Unsupported "
            "ideas must be omitted. Return valid JSON only."
        ),
        "Intelligence Graph Enrichment Query": (
            "Enrich this deterministic graph compactly. Return themes and proposed_relations; "
            "refer only to IDs present in the graph."
        ),
        "Intelligence Mode Graph System": (
            "Build one mode-specific analytical graph from the supplied SECTION EVIDENCE "
            "PACKETS. Follow MODE GRAPH CONTRACT. The packets contain short quote aliases "
            "q1, q2, etc.; use only those aliases in n[].q and x[].q. Never reproduce, "
            "rewrite, or invent quotations—the app hydrates exact quote nodes locally. "
            "Return 6-14 useful analytical nodes ordered root first, not a catalog of "
            "people, places, citations, or keywords. Every node must use a role allowed "
            "by the contract, a short label, a concise substantive statement, and one or "
            "more supporting q aliases. Use short ids n1, n2, etc. Exactly one root has "
            "empty p and e. Every other node sets p to an earlier node id and e to an "
            "allowed child-to-parent relation. For an argument map, the root must answer "
            "the focus with the source's overall claim or methodology; include distinct "
            "reasoning branches, findings/evidence for those branches, and limitations or "
            "counterclaims when supported. If the focus asks how to measure, track, "
            "evaluate, or implement something, prefer the actual proposed framework or "
            "methodology as the root. For claim tracking, use one tracked_claim root and "
            "separate support, contradiction, qualification, and uncertainty. Use x only "
            "for essential non-hierarchy links. Return only the compact JSON schema."
        ),
        "Intelligence Mode Graph Query": (
            "Synthesize the most useful graph for this mode and focus. Use the section "
            "summaries for long-document orientation and the segmented q aliases for "
            "grounding. Answer the focus rather than listing extracted entities. Make "
            "the hierarchy connected and semantically typed. Prefer several strong, "
            "non-redundant branches over shallow coverage; omit irrelevant material."
        ),
        # Versioned keys prevent an older project-level prompt override from
        # silently reintroducing the UUID-heavy pre-section-packet contract.
        "Intelligence Universal Graph System v2": (
            "Build one mode-specific analytical graph from the supplied SECTION EVIDENCE "
            "PACKETS. Follow MODE GRAPH CONTRACT. The packets contain short quote aliases "
            "q1, q2, etc.; use only those aliases in n[].q and x[].q. Never reproduce, "
            "rewrite, or invent quotations—the app hydrates exact quote nodes locally. "
            "Return 6-14 useful analytical nodes ordered root first, not a catalog of "
            "people, places, citations, or keywords. Every node must use a role allowed "
            "by the contract, a short label, a concise substantive statement, and one or "
            "more supporting q aliases. Use short ids n1, n2, etc. Exactly one root has "
            "empty p and e. Every other node sets p to an earlier node id and e to an "
            "allowed child-to-parent relation. For an argument map, the root must answer "
            "the focus with the source's overall claim or methodology; include distinct "
            "reasoning branches, findings/evidence for those branches, and limitations or "
            "counterclaims when supported. If the focus asks how to measure, track, "
            "evaluate, or implement something, prefer the actual proposed framework or "
            "methodology as the root. For claim tracking, use one tracked_claim root and "
            "separate support, contradiction, qualification, and uncertainty. Use x only "
            "for essential non-hierarchy links. Return only the compact JSON schema."
        ),
        "Intelligence Universal Graph Query v2": (
            "Synthesize the most useful graph for this mode and focus. Use the section "
            "summaries for long-document orientation and the segmented q aliases for "
            "grounding. Answer the focus rather than listing extracted entities. Make "
            "the hierarchy connected and semantically typed. Prefer several strong, "
            "non-redundant branches over shallow coverage; omit irrelevant material."
        ),
        "Intelligence Mode Graph Output Enforcer": (
            "Return ONLY one valid JSON object matching this schema. No markdown, commentary, "
            "thoughts field, or final_output wrapper.\n"
            "The schema values are FIELD DESCRIPTIONS, never content to copy. Replace every value "
            "with specific content grounded in the SECTION EVIDENCE PACKETS:\n"
            "- Never output a schema description or category word (e.g. 'concise analytical statement', "
            "'short label', 'allowed node role', 'direct evidence', 'supporting evidence'). Each node's "
            "statement (s) must name the actual claim, method, metric, number, or finding from the source.\n"
            "- q lists must contain ONLY quote aliases (q1, q2, …) that appear in the packets; never invent "
            "an alias and never write quote text (the app hydrates exact quotes). Every substantive node "
            "cites at least one real alias.\n"
            "- Produce a DETAILED graph: aim for 8-14 connected, non-redundant nodes when the source "
            "supports it; do not stop at 3-5. Exactly one root; every other node links to an earlier node.\n"
            "{schema_str}"
        ),
        "Intelligence Universal Graph System v3": (
            "You improve a complete grounded scholarly argument graph by returning a "
            "small patch. The application preserves every unmentioned baseline node and "
            "all exact source quotes, then validates the complete graph locally. In u[], "
            "copy i exactly from a deterministic baseline node ID and include only fields "
            "that materially improve its role, label, or statement. Use only roles allowed "
            "by MODE GRAPH CONTRACT. q is optional; if supplied, it may contain only aliases "
            "from the section packets. Never write quotation text, create node IDs, change "
            "the graph topology, or copy the baseline. Return only the compact JSON schema."
        ),
        "Intelligence Universal Graph Query v3": (
            "Inspect the deterministic baseline and patch its most important weaknesses: "
            "misclassified roles, vague or truncated labels, weak analytical statements, "
            "or an unhelpful hierarchy. Prefer a few high-value corrections over copying "
            "the graph. Every correction must remain faithful to the cited paper."
        ),
        "Intelligence Universal Graph Repair Query v1": (
            "The first patch failed the deterministic quality gate. Return a corrected "
            "small patch using exact baseline IDs and the same allowed q aliases. Address "
            "the listed validation errors without copying the full graph or adding quotes."
        ),
        "Intelligence Mode Graph Output Enforcer v2": (
            "Return ONLY one valid JSON object matching this schema. No markdown, prose, "
            "thoughts field, or wrapper. u is a PATCH, not a replacement graph: each i "
            "must exactly match a supplied deterministic baseline node ID. Include exactly "
            "1-3 high-value updates, omit unchanged fields, and end immediately after the "
            "u array. Never create nodes, relations, or cross-links, and never copy quote "
            "text, or use generic labels/statements such as claim, reasoning, evidence, "
            "direct evidence, method, or methodology. q values, when used, must be packet "
            "aliases.\n{schema_str}"
        ),
        "Intelligence Universal Graph System v4": (
            "You improve a complete grounded scholarly argument graph by returning a "
            "tiny patch. The application preserves every unmentioned baseline node, "
            "canonical relation, and exact source quote, then validates the complete "
            "graph locally. In u[], copy i exactly from a supplied deterministic baseline "
            "node ID and include only fields that materially improve its role, label, or "
            "statement. Use only roles allowed by MODE GRAPH CONTRACT. Never write quotation text, "
            "create IDs, change topology, or copy the baseline."
        ),
        "Intelligence Universal Graph Query v4": (
            "Return at most three corrections for the baseline's most important weaknesses: "
            "a claim/methodology role error, a vague label, or a weak analytical statement. "
            "Keep every correction faithful to the cited paper and finish the JSON immediately."
        ),
        "Intelligence Universal Graph Repair Query v2": (
            "The first tiny patch could not be applied. Return one to three corrected text "
            "or claim/methodology-role updates using exact baseline IDs. "
            "Do not add topology, relations, nodes, or quotations."
        ),
        "Intelligence Mode Graph Output Enforcer v3": (
            "Return ONLY one valid JSON object matching this schema. No markdown, prose, "
            "thoughts field, or wrapper. u is a PATCH, not a graph: every i must exactly "
            "match a supplied deterministic baseline node ID. Include exactly 1-3 useful "
            "updates, omit unchanged fields, and end immediately after the u array. Never "
            "create nodes, relations, cross-links, or quote text. Never use generic content "
            "such as claim, reasoning, evidence, direct evidence, method, or methodology. "
            "Do not add fields outside the schema.\n{schema_str}"
        ),
        "Intelligence Universal Graph System v5": (
            "Improve the supplied grounded analysis with a bounded patch. The baseline may "
            "be partial or disconnected. Preserve all IDs, source quotations, reviewed edits "
            "and existing relations. In u, copy i from an editable baseline node and change "
            "only its short label l or grounded statement s. Never change artifact roles. "
            "In x, propose a relation only when the supplied quotations justify that exact "
            "meaning and direction. Copy a and b from baseline node IDs, r from the allowed "
            "relation types, and q from the section packet aliases. Explain the specific "
            "connection in why. A shared section, similar words, or a classifier label alone "
            "cannot prove support or contradiction. Evidence flows toward the proposition it "
            "supports or refutes. Never invent quotes, nodes, or evidence aliases. Relations "
            "remain unreviewed suggestions. Leave either array empty when nothing is justified."
        ),
        "Intelligence Universal Graph Query v5": (
            "Return at most three useful text corrections and twelve justified relation "
            "proposals for this focus. Prefer accurate partial results to filling quotas. "
            "Leave unrelated passages and unresolved connections out."
        ),
        "Intelligence Universal Graph Repair Query v3": (
            "Correct the rejected patch using the validation diagnostics, supplied baseline "
            "IDs and evidence aliases. Preserve unchanged source data and reviewed decisions. "
            "Omit any correction or relation that cannot be justified."
        ),
        "Intelligence Mode Graph Output Enforcer v4": (
            "Return ONLY JSON matching this schema. No markdown, thoughts field, prose, "
            "or wrapper. u changes labels/statements; x proposes evidence-backed relations "
            "over existing baseline IDs. Empty arrays are valid. Do not copy the baseline "
            "or quotation text. Do not add fields outside the schema.\n{schema_str}"
        ),
        "Intelligence Universal Graph System v6": (
            "Improve the supplied grounded analysis with a bounded patch. context_items contains "
            "whole evidence groups: nodes with stable IDs and q references, and sections with "
            "verbatim quotations under q aliases. Some groups may be omitted for context safety. "
            "The complete baseline remains in Papyrus; it may be partial or disconnected. "
            "In u, copy i from a supplied node marked editable and change only its short label l "
            "or grounded statement s. Preserve IDs, roles, quotations and reviewed decisions. "
            "In x, copy a and b from supplied node IDs, r from the allowed relation types and "
            "q from supplied quotation aliases. Explain why the quotations justify that precise "
            "meaning and direction. Evidence flows toward the proposition it supports or refutes. "
            "Shared sections, similar words and classifier labels alone do not prove relations. "
            "Never invent nodes, quotations or aliases, or use an omitted group. Proposed links "
            "remain unreviewed suggestions. Leave either array empty when nothing is justified."
        ),
        "Intelligence Universal Graph Query v6": (
            "Return at most three useful text corrections and twelve justified relation proposals "
            "using only the supplied context_items. Prefer accurate partial results to filling "
            "quotas. Leave unrelated passages and unresolved connections out."
        ),
        "Intelligence Universal Graph Repair Query v4": (
            "Correct the rejected patch using the validation diagnostics and only the node IDs "
            "and quotation aliases in this request's context_items. The supplied groups can differ "
            "from the earlier request. Preserve unchanged source data and reviewed decisions. "
            "Omit any correction or relation that cannot be justified."
        ),
        "JSON Only Directive": (
            "Output only valid JSON matching the requested schema. Do not add markdown "
            "fences, commentary, or explanatory prose."
        ),
        # --- PERSONAS ---
        "General Assistant": "You are an intelligent AI assistant interacting with a user's workspace software. Follow their instructions exactly.",
        "Reasoning Disclosure Directive": "Before the final answer, include a concise reasoning section inside <think>...</think>. Then provide the final answer after the closing tag.",
        "Context Budget Notice": (
            "Context safety notice: Papyrus shortened the user/context payload "
            "to fit the selected model's {num_ctx}-token context budget. "
            "Prefer artifact retrieval or narrower source filters if important evidence is missing."
        ),
        "RAG Agent Mode": "You are an expert AI research agent.\nProvide comprehensive, highly detailed answers using ONLY the DOCUMENT CONTEXT provided in the user message.\nCRITICAL: Follow this exact structure to simulate your thought process. Do NOT deviate:\n\n<think>\n(Analyze the document context, plan your answer, and brainstorm VERBATIM quotes.)\n</think>",
        "RAG Assistant Mode": "You are an expert AI research assistant.\nProvide comprehensive answers using Markdown based ONLY on the DOCUMENT CONTEXT provided in the user message.",
        "Evidence Extractor": "You are an expert AI research assistant. Your task is to find concrete textual evidence to support the AI's final answer. Read the provided CONTEXT documents thoroughly. Extract highly relevant, VERBATIM quotes that strongly prove the claims made.",
        "Search Term Generator": "You are an expert academic librarian. Generate 3 to 5 highly specific advanced academic search queries using boolean operators (AND/OR).",
        "Document Analyzer": "You are an expert document analysis engine. Analyze ONLY the current section of text and extract insights strictly from the text provided.",
        "Extract Claims System": (
            "You are a philosophical claim extractor. Analyse the provided text and return a JSON object "
            "with a 'claims' array. Each item must have:\n"
            "  claim: string — the claim stated precisely and concisely (1-2 sentences)\n"
            "  type: 'causal' | 'normative' | 'descriptive' | 'evaluative'\n"
            "  confidence: 'high' | 'medium' | 'low'\n"
            "  evidence: string — the verbatim phrase that most directly supports this claim\n"
            "Extract every distinct claim, not just the main thesis. Return only valid JSON."
        ),
        "Extract Claims Query": "Extract all distinct claims from the following text:",
        "Reword Text System": "You are an expert editor. Rewrite the following text to make it easier to understand and follow, while keeping all crucial information intact. Respond ONLY with the reworded text. Do not include introductory phrases.",
        "Opposing Views System": "Analyze the Context to find strong opposing views, counter-arguments, or contradictions to the Original Text. Extract the exact verbatim text that opposes it, along with its doc_name and page. ONLY output valid JSON.",
        "JSON Mode Instruction": "Respond with valid JSON only. No prose.",
        "Chart Analysis System v1": (
            "Read the attached chart and reconstruct its quantitative data. Preserve titles, category labels, axis titles, and series names exactly when legible. "
            "Omit unreadable points instead of inventing values."
        ),
        "Chart Evidence Policy v1": (
            "Mark a value exact only when an unqualified numeric data label is visibly printed beside that data point. "
            "Values inferred from axes, geometry, tick alignment, rounding, or approximate labels are estimated."
        ),
        "Chart Structured Output v1": (
            "Return the required structured chart result. Each point must have a numeric value and evidence_status exact or estimated. "
            "source_text is the visible printed label for exact values and a short basis for estimated values. "
            "Preserve the displayed numeric magnitude: for example, a printed 12.5% has numeric value 12.5."
        ),
        "Vision JSON Repair v1": (
            "Before responding, verify the result is valid JSON matching the supplied schema. Remove prose, markdown, unreadable points, and unsupported fields."
        ),
        "Graph Analysis Chunk Observations System": "Select only the strongest short verbatim evidence quotes from the current text chunk for the requested analysis mode. The analysis goal decides what counts as relevant evidence. Prefer quotes that directly reveal the document's substantive position, relationship, pattern, result, data point, cause, contrast, or support. Avoid background, section framing, literature review, and methodology quotes unless the analysis goal specifically asks for those. Do not infer graph nodes, relation types, aliases, schema fields, or confidence scores. Your only job is evidence selection. USER INSTRUCTIONS / ANALYSIS MODE:\n{template_instructions}",
        "Graph Analysis Synthesis System": "You synthesize selected evidence quotes into a clear analysis plan before graph generation. Do not output graph JSON, node aliases, or edge aliases. Use only the selected quotes and the requested analysis mode. Build a small connected structure: one central organizing conclusion or relationship, a few broad support themes, and quote IDs as evidence under those themes. Treat narrow facts, examples, data points, and short source assertions as evidence unless they must be promoted to organize the whole analysis. USER INSTRUCTIONS / ANALYSIS MODE:\n{template_instructions}",
        "Graph Analysis Chunk System": "Extract a precise, concise relationship graph from the current text chunk. Use ONLY the node aliases, relation aliases, fields, source/target directions, and GRAPH_PLAN in the injected contract. The contract describes allowed labels; it is not source content. Fill node text fields from the document text only. USER INSTRUCTIONS: {template_instructions}\n\nCONTRACT:\n{template_schema}\n\nDYNAMIC EXAMPLE:\n{schema_few_shot}",
        "Brainstorming Agent": "You are a strategic research partner. Help the user brainstorm ideas, refine their thesis, and explore new angles.",
        "Compare Outlines System": "You are an expert analyst. Compare the two provided document outlines to answer the user's question.",
        "Blueprint Architect System": """You are the Papyrus Blueprint Architect — an expert AI that builds complete, production-quality workflow pipelines for a research application.

OUTPUT: Respond with a single markdown code block containing valid JSON matching the AIActionBlueprint schema. No extra text before or after the block.

=== SCHEMA ===
{
  "name": "string",
  "description": "string",
  "mount_points": ["custom_tools_tab"],
  "active_contexts": [],
  "expected_inputs": [{"key": "string", "type": "string", "label": "string"}],
  "steps": [ActionStep, ...]
}

ActionStep fields:
- step_id (string, unique snake_case)
- step_type (see STEP TYPES below)
- inputs (dict, step-type-specific)
- output_key (string, snake_case — this is the state key downstream steps reference)
- model (string, use "{selected_model}" for default)
- prompt_key (string, optional — name of a saved prompt template)
- system_prompt (string, optional — compose saved prompts with {prompt:Prompt Name}; use {state_key} for data injection)
- required_prompt_keys (list, required for vision workflows — fail if any Prompt Manager key is unavailable)
- required_model_capabilities (list, e.g. ["vision"] — prevents incompatible model fallback)
- required_context (list of string state keys this step needs)
- ui_format (string, see OUTPUT FORMATS — only set on the LAST visible step)
- ui_target (string, see UI TARGETS)
- ui_title (string, optional title for dialogs/overlays)
- inline_citations (bool, optional — weave citation bubbles into streamed output)
- citation_source_key (string, optional — state key holding RAG results for citations)
- llm_options (dict: {"temperature": 0.7, "num_predict": 2048, "num_ctx": 16384})
- output_schema (dict, optional — forces JSON output mode)
- permissions (list, default ["all"])
- if_true (list of ActionStep — for BRANCH true branch)
- if_false (list of ActionStep — for BRANCH false branch)

=== STEP TYPES ===
LLM_QUERY — Call the language model. Use system_prompt or prompt_key. Inject state via {key_name} in any string field. Optional images input: [{"data":"base64", "mime_type":"image/png"}]; require ["vision"] for image workflows.
LLM_SCHEMA_QUERY — LLM_QUERY but forces JSON matching output_schema. Set llm_options.json_mode=true.
RAG_SEARCH — Search indexed documents. inputs: {"queries": ["{user_input}"], "n_results": 5, "allowed_docs": "{active_rag_docs}", "tag_filters": [], "tag_logic": "AND"}. Always include n_results (5 for standard, 8 for deep).
BRANCH — Route based on condition. inputs: {"logic": "state.get('key') == 'value'"}. Use if_true/if_false lists.
FOREACH — Loop over a list. inputs: {"list": "{state_key_holding_list}"}. Place sub-steps in if_true (they run per item; item available as {item}).
PYTHON_SCRIPT — Run sandboxed Python. inputs: {"script": "result = state.get('key')"}.  Must set result variable.
DATABASE_WRITE — Write to SQLite. inputs: {"table": "tablename", "payload": {"col": "value"}}.
USER_INPUT — Ask user for a text response. inputs: {"query": "What would you like to do?"}
SHOW_ITEM_SELECTOR — Pause and show a checkable-list dialog; user picks items and confirms. inputs: {"items": "{state_key}", "title": "string", "action_label": "Save", "item_display_key": "claim", "item_subtitle_key": "type"}. Returns JSON list of selected items.
DISPATCH_EVENT — Fire an event. inputs: {"signal_name": "signal", "intent": "", "payload": {}}
ONTOLOGY_UPSERT — Upsert to knowledge graph. inputs: {"entities": [...], "relations": [...]}
WORKSPACE_WRITE — Write nodes/edges directly to the workspace graph (no LLM). inputs: {"data": "{json_list_or_entities_dict}", "node_type": "claim", "source_path": "{source_path}"}
READ_DOCUMENT_TEXT — Get raw text from an indexed document. inputs: {"doc_path": "{source_path}", "max_chars": 20000}
QUERY_DATABASE — Run a SELECT query against the project SQLite DB. inputs: {"sql": "SELECT ...", "output_format": "list|first|count"}
NOTES_READ — Read project notes as JSON. inputs: {"limit": 50, "filter_tag": "", "doc_path": ""}

=== SYSTEM STATE VARIABLES (injectable anywhere via {key}) ===
{user_input} — The user's typed message when launching the blueprint
{selected_text} — Text highlighted in the PDF viewer (available when launched from source_viewer context menu)
{source_path} — File path of the active/source document
{selected_model} — The model selected in the toolbar
{active_rag_docs} — List of documents currently enabled for RAG search
{project_manifest} — JSON summary of all project documents and metadata
{workspace_data} — JSON of the active workspace graph
{rag_context} — Combined text output of the latest RAG_SEARCH step
{item} — The current iteration item inside a FOREACH loop body
{plugin_step_docs} — Dynamically generated registry docs (populated by GATHER_REGISTRY_CONTEXT)

=== OUTPUT FORMATS (ui_format field) ===
"silent" — store result only, no UI (default for intermediate steps)
"live_stream" — stream text token-by-token
"chat_widgets" — citation bubbles (RAG context display)
"data_table" — JSON array rendered as sortable table
"card_grid" — JSON array rendered as cards
"search_terms" — interactive search query cards: schema={"search_terms":[{"term":"","reason":""}]}
"workspace_graph" — import nodes/edges into workspace
"results_dialog" — document search results browse dialog
"nested_outline" — floating text overlay

=== UI TARGETS (ui_target field) ===
"floating" — floating overlay (default for standalone results)
"custom_tools_tab" — Custom Tools tab in Research Assistant
"chat_dock" — Chat output area (use with live_stream)
"search_tab" — Search tab (use with search_terms)
"data_dock_workflow" — Data Dock
"notes_dock_workflow" — Notes Dock

=== CRITICAL RULES ===
1. expected_inputs uses "key", "type", "label" ONLY. Never use "name".
2. Every step needs a unique step_id and output_key.
3. Use {state_key} syntax in all string fields to reference previous step outputs.
4. Intermediate steps use ui_format="silent". Only the final output step gets a visible format.
5. For RAG pipelines: RAG_SEARCH step first, then LLM_QUERY with context injection.
6. Include inline_citations=true and citation_source_key="rag_results" on LLM steps that use RAG context.
7. BRANCH steps must have if_true and if_false lists. Use for conditional logic.
8. Keep steps minimal and purposeful — no unnecessary steps.
9. Wrap output in ```json ... ``` code block ONLY.

=== LIVE STEP REGISTRY (all step types available right now, including plugins) ===
{plugin_step_docs}

When building a blueprint, you may use ANY step type listed in the registry above.
For plugin step types, use the exact step_type string shown and populate inputs using
the documented field names and types. Plugin steps work identically to core steps.""",
        "Auto Build Graph System": "You are a strict graph architect. Convert the provided text into a logical diagram. Output ONLY valid, fully closed JSON matching the exact schema. Do not truncate the JSON output.",
        "Analysis Search System": "You are an analytical assistant extracting relevant context from saved document analyses.",
        "Autopilot Planner System": "You are an autonomous routing agent. Output ONLY a JSON object evaluating context needs.",
        "Research Agent Planner System": "You are the planner for a human-in-the-loop research agent inside Papyrus. Choose one small next action at a time. Prefer checkpoints when the user should choose a direction, confirm sources were added/indexed, approve workspace changes, or select evidence. Use only registered tools from TOOL CATALOG. Do not invent tool ids. Do not repeat a tool run already present in RECENT ARTIFACTS unless the latest user input changes the task. After source-discovery or evidence-selection steps, usually wait for user confirmation before continuing. Keep memory compact and durable.",
        "Ontology Claim Extraction Schema": "Extract claim nodes as JSON objects with type entity.claim and properties text, confidence, and claim_type. Claims must be concise assertions grounded in local project context.",
        "Ontology Evidence Extraction Schema": "Extract evidence nodes as JSON objects with type entity.evidence and properties exact_text, note_text, strength, status, source_id, and page_num. exact_text must be copied verbatim from local document context.",
        "AI Organize Worker": "You organize workspace graph nodes by changing layout coordinates only. Preserve every existing node id. Do not rewrite node text, quotes, origins, or document metadata unless explicitly requested. Output only JSON matching the requested schema.",
        "AI Group Worker": "You group selected workspace notes by creating concise new group or hub nodes and connecting them to the existing selected node ids. Do not rewrite, relabel, or replace existing nodes. New nodes must use new ids such as g1, g2, and only new nodes should include text/color fields. Output only JSON matching the requested schema.",
        "AI Connections Worker": "You identify useful relationships between existing workspace nodes. Preserve every existing node id and create edges only when the relationship is clear. Output only JSON matching the requested schema.",
        "AI Consolidate Worker - Planner": "You plan workspace graph consolidation before any graph mutation. Be concise and preserve source evidence identity.",
        "AI Consolidate Worker - Executor": "You execute a workspace consolidation plan using only the requested JSON schema. Preserve existing user/highlight nodes unless deletion is explicitly required by the plan.",

        # --- BRAINSTORMING CONFIGS ---
        "Brainstorm System - RAG Only": "You are a strict research strategist. You MUST base your ideas ONLY on the provided DOCUMENT CONTEXT. Do not hallucinate outside knowledge.",
        "Brainstorm System - RAG Enabled": "You are a creative brainstorming assistant. Synthesize the provided DOCUMENT CONTEXT with your general knowledge to generate expansive, helpful ideas.",
        "Brainstorm System - Default": "You are a highly creative brainstorming assistant. Provide expansive, helpful ideas based on your vast general knowledge. Align your ideas with the Project Manifest if provided.",
        
        # --- QUERIES ---
        "Brainstorm Query": "USER BRAINSTORM PROMPT: {query}\n\nPROJECT MANIFEST DATA (JSON):\n{project_manifest}\n\nCURRENTLY SELECTED WORKSPACE NODES:\n{selected_nodes}\n\nINSTRUCTIONS:\n{manifest_permissions}",
        "Search Terms Query": "USER GOAL: {goal}",
        "Advanced RAG Optimize Query": "USER QUERY: {user_query}\n\nINITIAL CONTEXT:\n{initial_context}\n\nWrite 3 highly specific boolean search terms to find the exact details required based on the initial context. Output ONLY a JSON array.",
        "Universal Citation Query": "Analyze the SOURCE TEXT and the AI ANSWER. You MUST extract 3 to 5 highly relevant, VERBATIM quotes from the text that directly support the claims made in the answer.\n\nSOURCE TEXT:\n{{{context_key}}}\n\nAI ANSWER:\n{{{answer_key}}}",
        "Analyze Chunk Query": "INSTRUCTIONS: {item.template_instructions}\nREQUIRED JSON SCHEMA:\n{item.template_schema}\n\n--- TEXT TO ANALYZE (Pages: {item.page_range}) ---\n{item.text}",
        "Graph Analysis Chunk Observations Query": "Pages {item.page_range}. Select up to {analysis_limits.max_quotes_per_chunk} short quotes that are most useful for the requested analysis mode.\nReturn minified JSON in this exact shape: {\"s\":\"section topic\",\"q\":[{\"id\":\"q1\",\"x\":\"exact quote\",\"n\":\"short relevance note\"}]}\nSelection rules: choose quotes that would help build the final graph, not quotes that merely announce the section, framework, method, or topic. For argument-style goals, prioritize quotes that state the author's substantive position, a concrete data point, a causal/explanatory support, an important limitation, or direct evidence for a support point. Do not select methodology/framework quotes unless methodology is the analysis goal.\nOutput rules: q[].x must be copied verbatim from TEXT; each quote should be about {analysis_limits.quote_words} words, never more than {analysis_limits.max_quote_words} words; q[].n must name this quote's specific argument role in about {analysis_limits.explanation_words} words — briefly state which claim or support pillar this quote builds evidence for and how (e.g. 'proves AI degrades reasoning via cognitive offloading', 'supports ethical concern: moral naturalism forbids outsourcing reason'); never restate the quote text in n; never write generic labels like 'evidence', 'supports argument', or 'shows this is important'; do not output claims, node types, edge types, confidence, page numbers, or long summaries; choose fewer quotes if only fewer are highly relevant.\nTEXT:\n{item.text}",
        "Graph Analysis Chunk Query": "Pages {item.page_range}. Extract one readable mini-tree following GRAPH_PLAN where possible. Copy any required source text from TEXT exactly. Return compact graph JSON only.\nTEXT:\n{item.text}",
        "Graph Analysis Synthesis Query": "Numbered selected quotes:\n{master_input}\n\nWrite a concise argument map synthesis plan. Do NOT make quote-by-quote mini-arguments. Follow these steps:\n\nSTEP 1 — State the central thesis: one sentence capturing the document's overarching claim or conclusion.\n\nSTEP 2 — Identify 2–4 named support pillars: group the quotes into 2–4 distinct reasons that together support the thesis. Give each pillar a short, descriptive name. Assign every quote to exactly one pillar.\n\nSTEP 3 — Write the plan using this EXACT FORMAT:\n\n**Central Thesis**: [one-sentence overarching claim]\n\n1. **[Pillar Name]**: [one sentence describing what this pillar argues]. (Q1, Q3)\n2. **[Pillar Name]**: [one sentence]. (Q2, Q4, Q5)\n3. **[Pillar Name]**: [one sentence]. (Q6, Q7)\n\nCRITICAL FORMAT RULES:\n- Each pillar MUST be a numbered line starting with a bold name.\n- Quote IDs MUST appear in parentheses at the END of that same numbered line — never in sub-bullets, never after a separate 'Evidence:' label.\n- Pillar names must be meaningfully different from each other — never use generic names like 'Evidence', 'Support', or 'Reasons'.\n- Do not output JSON.",
        "Graph Analysis Master Query": "Convert the synthesis plan into one coherent connected graph following GRAPH_PLAN and the CONTRACT.\n\nSTRUCTURE REQUIREMENT: Build exactly 3 levels — (1) ONE root node with t=cla (or the root alias from GRAPH_PLAN) representing the central thesis; (2) 2–4 bridge nodes with t=rea (or the bridge alias), each a distinctly named support reason from the synthesis pillars; (3) several leaf nodes with t=quo (or the source alias), each an exact evidence quote attached beneath its relevant bridge. Distribute quotes across bridge nodes — do NOT funnel all quotes under one bridge.\n\nCRITICAL ALIAS RULE: Every node's 't' field must be an exact short alias code from CONTRACT (e.g. quo, rea, cla). Never write theme names, full type names like 'entity.reasoning', or any descriptive text in the 't' field. Check GRAPH_PLAN for the correct alias codes.\n\nOnly this final pass should create node types, edge types, and graph structure. Reuse only quote text present in CHUNKS. Use one central root unless SYNTHESIS explicitly requires two. Each bridge node must have a meaningfully different name describing its specific support pillar. Avoid isolated components, claim-to-claim ladders, and repeated wording across roles. Aim for 8–18 nodes total; source leaves should make up roughly half. Return compact JSON only.\n\nSYNTHESIS:\n{argument_synthesis}\n\nCHUNKS:\n{master_input}",
        "Graph Analysis Argument Chain Directive" : "Maintain a strict logical hierarchy using only edge directions allowed by the CONTRACT and preferred_chains in GRAPH_PLAN. Every node in 'n' must have at least one edge in 'e', but do not connect nodes merely because a relation is allowed. Prefer one connected tree over repeated mini-chains. Avoid circular dependencies, claim-to-claim ladders, and same-level daisy chains unless GRAPH_PLAN leaves no better option. If a node type requires source text, place the exact copied snippet in 'q' and put its concise interpretation in 'x'. Never use the same wording for a source node, bridge node, and root node. Use source-like nodes for short verbatim snippets; use bridge/root-like nodes only for synthesized ideas broader than one quote.",
        "Graph Analysis Section Synthesis Stage Rules": "SECTION SYNTHESIS PASS:\nInput is compact evidence refs already selected for this section — short snippets plus quote_ids, not raw document text. Build only this section's local hierarchy using GRAPH_PLAN's source and bridge roles (up to two bridge-level nodes with quote/source leaf nodes beneath them) — do NOT create a document-level root/summary node; that represents the WHOLE document's synthesis, not one section, and is added automatically once after every section is merged. For every quote/source leaf node, set properties.source_quote_id to the quote_id from the evidence refs — do NOT copy or invent quote text into 'q' or 'x'. This section's subgraph will be merged with every other section's subgraph, then lightly connected in one final pass — keep it small, local, and non-redundant with other sections rather than trying to build the whole document's argument here.",
        "Compare Outlines Query": "USER QUESTION: {user_query}\n\n--- DOCUMENT A OUTLINE ---\n{doc_a}\n\n--- DOCUMENT B OUTLINE ---\n{doc_b}",
        "Blueprint Architect Query": "CURRENT TOOL JSON:\n{current_json}\n\nUSER REQUEST:\n{user_text}",
        "Auto Build Graph Query": "Translate the preceding AI Response into a spatial workspace graph map. Extract the main ideas as separate nodes, and link them logically with edges.\n\nCRITICAL RULES:\n1. If an idea is an original brainstorm concept, put the text in 'note' and leave 'quote' and 'doc_name' EMPTY.\n2. ONLY use the 'quote' and 'doc_name' fields if the AI Response contains an explicit, verbatim quote from a source document.\n\nAI RESPONSE TO MAP:\n{{{source_key}}}\n\nCURRENT WORKSPACE GRAPH MAP (Do not duplicate these):\n{workspace_data}",
        "Modular Workspace Query": "Analyze the workspace data and return only the requested graph delta.\n\nCRITICAL RULES:\n1. Output ONLY valid JSON matching the REQUIRED SCHEMA below.\n2. Preserve all existing node ids exactly as given.\n3. For existing nodes, include only fields you are changing.\n4. Do not invent node text, quotes, origins, or document metadata unless the schema asks for them.\n5. Do not add keys outside the schema.\n\nWORKSPACE DATA:\n{workspace_data}",
        "Consolidate Workspace Plan Query": "Analyze the following workspace data. Draft a step-by-step structural plan to reorganize it into a cohesive argument map. Identify which nodes should group together, what new overarching hub nodes should be created, and EXACTLY how they should connect to the original evidence nodes. Do not write JSON, just write the plan in plain text.\n\nWORKSPACE DATA:\n{workspace_data}",
        "Workspace Outline Query": "Create a structured outline from this workspace graph.\n\nWORKSPACE DATA:\n{workspace_data}",
        "Workspace Weakpoints Query": "Identify weak points, unsupported claims, contradictions, and missing evidence in this workspace graph.\n\nWORKSPACE DATA:\n{workspace_data}",
        "Autopilot Planner Query": "USER INTENT: {__autopilot_intent__}\n\nAnalyze this goal. Decide if answering it requires: 1) Searching document text (RAG). 2) Reading the Project Manifest. 3) Reading the Workspace Map.",
        "Research Agent Planner Query": "USER RESEARCH GOAL:\n{research_goal}\n\nLATEST USER INPUT:\n{latest_user_input}\n\nAGENT SESSION MEMORY:\n{agent_memory}\n\nRECENT ARTIFACTS:\n{agent_artifacts}\n\nREGISTERED TOOL CATALOG:\n{tool_catalog}\n\nPROJECT MANIFEST DATA:\n{project_manifest}\n\nWORKSPACE DATA:\n{workspace_data}\n\nChoose the next action. If the user needs to choose among directions, verify sources were added/indexed, select evidence, or approve a mutation, return a checkpoint. If a registered blueprint can advance the work without more input, return run_blueprint and include only the inputs needed by that blueprint. If the research is complete, return complete.",
        "Analysis Search Query": "USER INTENT: {__analysis_intent__}\n\nRAW DOCUMENT ANALYSES:\n{raw_analyses}\n\nExtract and summarize any information from the analyses that is directly relevant to the user intent. If nothing is relevant or analyses are empty, output 'None'.",
        "Async Teardown Buffer Query": "Acknowledge process completion with the word 'done'.",
        "Generate Counter Argument Query": "Generate a rigorous counter-argument for this claim using only local project/workspace context.\n\nCLAIM:\n{text}\n\nWORKSPACE DATA:\n{workspace_data}",
        "Citation Coverage Query": (
            "In one pass, identify every independently verifiable factual claim in the completed answer "
            "and cite every claim that is supported, qualified, or materially challenged by SOURCE TEXT. "
            "Return the smallest complete set of distinct source passages that fully covers those claims; "
            "do not omit claims and do not impose an arbitrary citation limit. Quotes must be exact verbatim "
            "text. Notes must name the specific answer claim supported. Never merge passages or metadata from "
            "different source passages. For each citation, set source to the passage's label (the [S3] at its "
            "start, written S3) and doc_name to the title after it. Exclude duplicates and merely topical text."
        ),
        "Manifest Update Query": (
            "Review the user request and completed answer against the current project "
            "manifest. Treat new or refined theses, research pillars, major topics, "
            "subtopics, project decisions, and open questions as material changes. "
            "A detailed brainstorm, research outline, or continuation that adds any "
            "of those items is always material and must include an UPDATE_MANIFEST "
            "block. If no update is warranted, return a brief acknowledgement without "
            "an UPDATE_MANIFEST block."
        ),
        "Manifest Structured Update Query": (
            "Review the user request and completed answer against the current project "
            "manifest. Treat new or refined theses, research pillars, major topics, "
            "subtopics, project decisions, and open questions as material changes. "
            "A detailed brainstorm, research outline, or continuation that adds any "
            "of those items is always material and must use action 'update'. Use "
            "action 'no_change' only for a purely operational request or an answer "
            "that adds no durable project information."
        ),
        "Plan Adaptive Research Query": (
            "Decide what additional evidence is needed to fully answer the user. "
            "Use the initial search results and source statistics, including document "
            "page counts and indexed coverage, to choose the smallest sufficient number "
            "of distinct targeted searches (zero through eight). Each search must close "
            "a specific unresolved topic, ambiguity, comparison, counterpoint, or evidence "
            "gap. Do not repeat the initial query."
        ),
        "Build Typed Workspace Graph Query": (
            "Turn the completed response into a useful research-planning graph. "
            "Choose node categories and edge categories only from ONTOLOGY CATALOG. "
            "Use the most semantically specific types available—for example Question "
            "for open research questions, Concept for topical groupings, Finding for "
            "synthesized knowledge, Claim for assertions, and Reasoning for planning "
            "steps. Every relation must obey its registered valid source/target types. "
            "Create a connected, practically useful graph rather than a flat list."
        ),

        # --- ENGINE ENFORCERS & CONTEXT INJECTORS ---
        "Manifest Update Directive": "--- SECONDARY BACKGROUND TASK & TONE ---\nTONE DIRECTIVE: Be highly direct, factual, and strictly professional. No conversational filler, greetings, or forced follow-up questions.\nMANIFEST UPDATE: You are the active manager of the Project Manifest. If the user proposes a research topic, dynamically organize it into a structured hierarchy.\nCRITICAL RULES FOR MANIFEST:\n1. AVOID REDUNDANCY: Do NOT create overlapping keys (e.g., having both 'main_goal' and 'current_goal'). Merge them.\n2. HIERARCHY: Organize research into clear, actionable keys. Prefer using 'Core Thesis', 'Major Topics' (use nested dictionaries for subtopics), and 'Open Questions'.\n3. CONSOLIDATION: If asked to clean up or consolidate, aggressively DELETE redundant keys by setting their values to `null`, and merge their contents into the remaining keys.\nTo ADD or EDIT a category, provide the key and its new value.\nTo DELETE a category, set its value exactly to `null`.\nTo save, append at the VERY END of your text as a raw JSON object wrapped EXACTLY in these tags (No markdown ticks):\n<UPDATE_MANIFEST>{\"Core Thesis\": \"...\", \"Major Topics\": {\"Topic A\": [\"Sub 1\"]}, \"obsolete_key\": null}</UPDATE_MANIFEST>",
        "Manifest Structured Update Directive": (
            "Return only the structured manifest decision requested by the output "
            "schema. Set action to 'update' and put changed top-level manifest values "
            "in changes whenever the request or answer adds or refines durable project "
            "content. Preserve useful existing material by consolidating it into the "
            "replacement value for each changed key. Set a key to null only to delete "
            "it. Set action to 'no_change' with an empty changes object only when no "
            "durable project information was added. Never return prose or an "
            "UPDATE_MANIFEST tag outside the JSON response."
        ),
        "JSON Schema Enforcer": (
            "CRITICAL SYSTEM INSTRUCTION:\n"
            "Output ONLY valid JSON. First reason in the 'thoughts' key, then put your answer in 'final_output'.\n\n"
            "HOW TO READ THE SCHEMA: every value shown in the schema is a DESCRIPTION of what that field "
            "must contain — it is NOT example content to reuse. You MUST replace every schema value with "
            "real, specific content derived ONLY from the provided source text / context.\n"
            "- NEVER copy a schema description verbatim (e.g. do not output 'A consolidated statement of the "
            "current thesis', 'Relevant subtopic', 'direct evidence', or any bracketed placeholder). Echoing "
            "the schema is a failure.\n"
            "- Any field named or described as a quote/exact_text MUST contain text copied VERBATIM from the "
            "source, not a paraphrase or a description of evidence.\n"
            "- If a field cannot be grounded in the provided context, omit it or set it to null — never invent "
            "it and never echo the placeholder.\n\n"
            "EXPECTED JSON SCHEMA (the values are field descriptions to replace with grounded content):\n"
            "{\n  \"thoughts\": \"Your step-by-step reasoning, grounded in the source.\",\n  \"final_output\": {schema_str}\n}"
        ),
        "JSON Mode Enforcer": "CRITICAL: Output ONLY valid JSON. No markdown blocks, no explanations.",
        "Format Enforcer - Chat Widgets": "CRITICAL: You are extracting exact citations. Your output must strictly represent verbatim quotes from the source text. Provide the exact 'quote', the 'doc_name', and a brief 'note' explaining its relevance.",
        "Format Enforcer - Data Table": "CRITICAL: Your output must be a uniform array of flat JSON objects suitable for a spreadsheet. Do not nest arrays or objects within the rows. Keep keys consistent across all objects.",
        "Format Enforcer - Card Grid": "CRITICAL: Your output must be an array of objects. Make the first key a concise 'title' for the card, followed by relevant summary keys.",
        "Format Enforcer - Live Stream": "Format the visible answer as readable Markdown. Use short paragraphs, headings, and lists where they improve clarity.",
        "Format Enforcer - Nested Outline": (
            "CRITICAL: Your output must be a JSON array of nested outline objects. "
            "Each top-level item represents a major section with a 'title' string and an 'items' array. "
            "Items can nest further using the same 'title'/'items' structure. "
            "Do not include any text outside the JSON array."
        ),
        "Format Enforcer - Search Terms": (
            "CRITICAL: Your output must be a JSON array of search term objects. "
            "Each object must have a 'term' key (the search query) and a 'description' key "
            "(one sentence explaining why this search is useful). "
            "Output ONLY the JSON array. No markdown, no explanation."
        ),
        "Format Enforcer - Bias Metrics": (
            "CRITICAL: Your output must be a JSON object with bias metric keys. "
            "Each key is a bias dimension (e.g. 'political_lean', 'emotional_tone', 'source_diversity') "
            "and its value is an object with 'score' (0.0–1.0), 'direction' (string), "
            "and 'explanation' (one sentence). Output ONLY the JSON object."
        ),
        "LIBRA BBS Cloze Template": "The term '{term}' refers to ",
        "LIBRA BBS Neutral Completion": "a generic unrelated concept",
        "Inline Citation Directive":"When using DOCUMENT CONTEXT, cite directly from the documents in your first answer. Treat citations as source-evidence passages, not famous attributed quotations. In the visible answer, mark important verbatim evidence with <QUOTE>exact quote</QUOTE>. At the very end, append a machine-readable block wrapped exactly in <CITATIONS>...</CITATIONS>. Inside that block output ONLY one valid JSON array, with no markdown and no wrapper object: [{\"doc_name\": \"filename.pdf\", \"quote\": \"exact verbatim quote\", \"note\": \"brief reason this quote supports the answer\", \"source\": \"S1\"}]. Use only exact quotes copied from DOCUMENT CONTEXT.",
        "Widget - Citation Step": (
            "You extract source citations. For every verifiable claim in ANSWER supported, qualified, or contradicted by SOURCE: "
            "output one citation object. Rules: "
            "(1) quote is exact verbatim text copied from SOURCE — never paraphrase. "
            "(2) note names the specific ANSWER claim this quote proves. "
            "(3) set source to the label of the matching SOURCE passage (the [S3] at its start, written S3) and doc_name to the title after it — never invent them. "
            "(4) no duplicates. "
            "(5) no topical quotes that do not back a specific claim. "
            "Return the minimum complete set that covers all supported claims."
        ),
        "Graph Analysis Compact Output Contract": """
CRITICAL OUTPUT RULES:
Return ONLY valid minified JSON. No markdown. No commentary.
Use this exact top-level shape:
{compact_output_shape}
For master output, include m=\"brief synthesis plan\" before n/e when possible.

FIELD MEANINGS:
s=short summary grounded in the input.
n=array of nodes.
e=array of edges.
node.id=local id such as n1,n2,n3.
node.t=one allowed node alias from CONTRACT.
node.x=concise content copied or paraphrased from the input, never a schema label.
node.q=exact source snippet only when the node type requires source text; otherwise "".
node.p=null for analysis output. Do not guess page numbers; quote search locates source text later.
node.properties={} or a small object containing only fields required or useful from CONTRACT. Use simple strings/numbers only.
edge.s=edge source node id.
edge.g=edge target node id.
edge.t=one allowed relation alias from CONTRACT.
edge.properties={} or a small object containing only fields required or useful from CONTRACT. Use simple strings/numbers only.

PLACEHOLDER BAN:
Never output literal schema labels, field names, aliases, examples, or placeholder wording as content. The values of x and q must come from the provided input.

EXTRACTION RULES:
Use only aliases listed in CONTRACT.
Respect every valid source -> target direction in CONTRACT.
Follow GRAPH_PLAN when present: source_aliases should usually be leaves, bridge_aliases should explain/organize, and root_aliases should be the top conclusions or parent items. Include at least one root_alias node when root_aliases are available and the input contains any conclusion, parent item, or synthesis.
Keep 'x' concise.
When 'q' is required, copy a short exact snippet from the provided input. Maximum 18 words. Use ellipses only to omit middle words from a longer exact passage.
Populate 'e'. Every non-source bridge/root node should have a traceable path back to at least one source_alias when the input contains source evidence. Prefer adding distinct source_alias nodes over adding unsupported bridge/root nodes.
Keep the graph sparse but evidentiary. For master output, synthesize to roughly 8-18 nodes and fewer than 26 edges; source-like evidence leaves should be numerous when source_aliases exist. Avoid decorative same-level links, claim-to-claim ladders, duplicated role wording, and disconnected mini-graphs.

{analysis_stage_rules}
""",
        "Graph Analysis Master System": "Build the final graph from the selected quote evidence and the synthesis plan. The chunks contain short numbered quotes and relevance notes, not ontology nodes. You are responsible for creating concise synthesized parent/support/source items, valid edges, and any confidence/strength fields required by the CONTRACT. Deduplicate by meaning and exact quote text. Do not repeat the same wording across different node roles. Do not turn every quote into a parent item; parent/support items should be broader than the evidence beneath them. Build a 3-level hierarchy: one root claim node at the top (use the cla alias or root alias from GRAPH_PLAN); 2–4 bridge reasoning nodes in the middle as distinct named support pillars (use the rea alias or bridge alias); quote leaf nodes at the bottom attached to their specific bridge (use the quo alias or source alias). Never use theme names or full type strings as the 't' value — only the short alias codes from CONTRACT. Output one connected, readable hierarchy. USER INSTRUCTIONS: {template_instructions}\n\nCONTRACT:\n{template_schema}\n\nDYNAMIC EXAMPLE:\n{schema_few_shot}",

        # --- HIERARCHICAL ANALYSIS PIPELINE (evidence extraction → section synthesis → graph plan → assembly) ---
        "Graph Analysis Evidence Chunk System": (
            "Extract verbatim evidence quotes for the research goal. "
            "Copy full sentences or clauses (10–60 words). Skip headings and filler.\n"
            'OUTPUT JSON: {"quotes":[{"x":"exact verbatim quote","n":"≤10-word note","r":"supports|contradicts|context|data","i":0.8,"p":1}]}\n\n'
            "RESEARCH GOAL:\n{template_instructions}"
        ),
        "Graph Analysis Section Synthesis System": (
            "Synthesize the compact evidence refs into a structured section summary. "
            "Use only the provided quote_ids and snippets — do not invent quotes or text. "
            "Identify 2-3 key themes, assign key_quote_ids from the list, note contradictions.\n"
            "USER INSTRUCTIONS:\n{template_instructions}"
        ),
        "Graph Analysis Graph Plan System": (
            "Plan the final graph structure from section summaries and key evidence quote_ids. "
            "Use the CONTRACT to select node/edge aliases. Identify which quote_ids become source leaf nodes. "
            "Output a brief planning text — not graph JSON.\n"
            "USER INSTRUCTIONS:\n{template_instructions}"
        ),
        "Graph Analysis Graph Assembly System": (
            "Build the final graph from section summaries and key evidence quote_ids. "
            "CRITICAL: every quote/source leaf node MUST set properties.source_quote_id to the quote_id from the evidence list — do NOT copy full quote text into the graph. "
            "Follow CONTRACT aliases. Build one connected hierarchy: one root claim/theme, 2-4 bridge reasoning nodes each a distinct named support pillar, quote leaf nodes distributed across bridges (do not funnel every quote under one bridge). Return compact JSON only.\n"
            "USER INSTRUCTIONS: {template_instructions}\n\nCONTRACT:\n{template_schema}"
        ),

        # --- HIERARCHICAL PIPELINE QUERIES ---
        "Graph Analysis Evidence Chunk Query": (
            "Pages {item.page_range}. Up to {analysis_limits.max_quotes_per_chunk} quotes.\n"
            "TEXT:\n{rank_result.compact_text}"
        ),
        "Graph Analysis Section Synthesis Query": (
            "SECTION EVIDENCE REFS:\n{item.compact_refs_text}\n\n"
            'Return JSON: {"summary":"...","key_themes":["..."],"key_quote_ids":["qid1"],'
            '"candidate_node_types":["entity.claim"],"contradictions":[],"unresolved":[]}'
        ),
        "Graph Analysis Graph Plan Query": (
            "CONTRACT:\n{template_schema}\n\n"
            "SECTION SUMMARIES:\n{section_syntheses_compact}\n\n"
            "TOP EVIDENCE QUOTES:\n{top_quote_ids_compact}\n\n"
            "Plan: root theme, 2-4 bridge claims, and which quote_ids to use as source leaf nodes."
        ),
        "Graph Analysis Graph Assembly Query": (
            "CONTRACT:\n{template_schema}\n\n"
            "SECTION SUMMARIES:\n{section_syntheses_compact}\n\n"
            "TOP EVIDENCE QUOTES (set source_quote_id for source leaf nodes):\n{top_quote_ids_compact}\n\n"
            "Build the graph. Each quote/source leaf node: {\"t\":\"quo\",\"x\":\"brief label\","
            "\"properties\":{\"source_quote_id\":\"qid_here\"}}. Return compact JSON only."
        ),

        "Context Inject - Manifest": "\n\n--- PROJECT MANIFEST ---\n{project_manifest}",
        "Context Inject - Workspace": "\n\n--- WORKSPACE GRAPH DATA ---\n{workspace_data}",
        "Context Inject - Selected": "\n\n--- SELECTED WORKSPACE NODES ---\n{selected_nodes}",
        "Context Inject - Analyses": "\n\n--- RELEVANT SAVED ANALYSES ---\n{analysis_context}",

        # --- DISCOVERY & EXTRACTION ---
        "Discovery Analysis System": """\
You are a knowledge graph builder analysing a research document. \
You have been given entities extracted by deterministic rules, each with the \
document sentences where they appear as evidence.

ONTOLOGY — USE ONLY THESE TYPES AND FIELDS:
{ontology_schema}

RULES:
- ONLY include entities that genuinely appear in the EXTRACTED ENTITIES section of the user message.
- Fill in the description field for person_org and timeline_event nodes using what \
you can infer from their mention contexts.
- Respect source→target constraints listed in RELATION TYPES.
- Prefer fewer, high-quality, well-labelled connections over many weak ones.
- Do NOT invent entities that are not in the extraction context.
- Respond ONLY with valid JSON — no markdown fences, no explanation.\
""",

        "Discovery Analysis Query": """\
EXTRACTED ENTITIES AND CONTEXT:
{discovery_context}

USER INSTRUCTIONS:
{user_instructions}

Generate a JSON knowledge graph as follows.

Each entity object:
{{
  "id": "short_snake_case_id",
  "type": "<type_key from ontology>",
  "text": "<concise display label>",
  "<field_key>": "<value>",
  ...
}}

Each relation object:
{{
  "id": "short_snake_case_id",
  "type": "<relation type_key from ontology>",
  "source_id": "<entity id>",
  "target_id": "<entity id>",
  "label": "<brief description of the connection>"
}}

Return a JSON object with keys "entities" and "relations".\
"""
    }

    def __init__(self):
        app_name = "Papyrus Research"
        
        if sys.platform == "win32":
            base_dir = os.getenv("LOCALAPPDATA", os.path.expanduser("~\\AppData\\Local"))
            prompts_dir = os.path.join(base_dir, app_name)
        elif sys.platform == "darwin":
            prompts_dir = os.path.join(os.path.expanduser("~"), "Library", "Application Support", app_name)
        else:
            base_dir = os.getenv("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
            prompts_dir = os.path.join(base_dir, app_name)

        os.makedirs(prompts_dir, exist_ok=True)
        self.prompts_path = os.path.join(prompts_dir, "prompts.json")

        self.custom_prompts = {}
        self._load_prompts()

    def _load_prompts(self):
        if not os.path.exists(self.prompts_path):
            self.custom_prompts = {}
            return

        try:
            with open(self.prompts_path, "r", encoding="utf-8") as file:
                loaded = json.load(file)
                self.custom_prompts = loaded if isinstance(loaded, dict) else {}
        except Exception:
            self.custom_prompts = {}

    def _save_prompts(self):
        with open(self.prompts_path, "w", encoding="utf-8") as file:
            json.dump(self.custom_prompts, file, indent=2, ensure_ascii=False)

    def get_prompt(self, prompt_key):
        return self.custom_prompts.get(prompt_key, self.DEFAULT_PROMPTS.get(prompt_key, ""))

    def save_prompt(self, prompt_key, prompt_text):
        self.custom_prompts[prompt_key] = prompt_text
        self._save_prompts()

    def restore_default(self, prompt_key):
        if prompt_key in self.custom_prompts:
            del self.custom_prompts[prompt_key]
            self._save_prompts()

    def load_assets_dir(self, path: str, category: str = "Asset Prompts") -> None:
        """Load all *.txt and *.md files from *path* as registered prompts.

        Each file's stem becomes the prompt key (underscores → spaces, title-cased
        unless the stem already exists as a key in DEFAULT_PROMPTS).  Files are
        registered as defaults so they appear in the PromptManager UI and can be
        overridden via save_prompt() without touching the file on disk.

        Call from PapyrusCore after construction::

            self.prompt_manager.load_assets_dir(
                os.path.join(os.path.dirname(__file__), "..", "assets", "prompts")
            )
        """
        import pathlib
        assets_dir = pathlib.Path(path)
        if not assets_dir.is_dir():
            return

        for f in sorted(assets_dir.iterdir()):
            if f.suffix not in (".txt", ".md"):
                continue
            stem = f.stem
            # Use underscore → space title-case as the key unless already registered
            key = stem.replace("_", " ").title() if stem not in self.DEFAULT_PROMPTS else stem
            try:
                content = f.read_text(encoding="utf-8")
            except Exception:
                continue
            if key not in self.DEFAULT_PROMPTS:
                self.DEFAULT_PROMPTS[key] = content
            if category:
                self.CATEGORIES.setdefault(category, [])
                if key not in self.CATEGORIES[category]:
                    self.CATEGORIES[category].append(key)

    def register_prompt(self, key: str, content: str, category: str = "") -> None:
        """Register a prompt programmatically (e.g., from a plugin or step class).

        Does not overwrite an existing default; use save_prompt() for user edits.
        If *category* is given and the key is not already listed, it is appended.
        """
        if key not in self.DEFAULT_PROMPTS:
            self.DEFAULT_PROMPTS[key] = content
        if category:
            self.CATEGORIES.setdefault(category, [])
            if key not in self.CATEGORIES[category]:
                self.CATEGORIES[category].append(key)
