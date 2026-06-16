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
            "Graph Analysis Chunk Observations Query",
            "Graph Analysis Synthesis Query", "Graph Analysis Master Query",
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
            "Manifest Update Directive", "JSON Schema Enforcer", "JSON Mode Enforcer",
            "Format Enforcer - Chat Widgets", "Format Enforcer - Data Table",
            "Format Enforcer - Card Grid", "Inline Citation Directive",
            "Graph Analysis Compact Output Contract",
            "Graph Analysis Argument Chain Directive"
        ],
        "Context Injectors": [
            "Context Inject - Manifest", "Context Inject - Workspace",
            "Context Inject - Selected", "Context Inject - Analyses"
        ]
    }

    DEFAULT_PROMPTS = {
        # --- PERSONAS ---
        "General Assistant": "You are an intelligent AI assistant interacting with a user's workspace software. Follow their instructions exactly.",
        "RAG Agent Mode": "You are an expert AI research agent.\nProvide comprehensive, highly detailed answers using ONLY the provided context.\nCRITICAL: Follow this exact structure to simulate your thought process. Do NOT deviate:\n\n<think>\n(Analyze the context, plan your answer, and brainstorm VERBATIM quotes.)\n</think>\n\nCONTEXT:\n{context}",
        "RAG Assistant Mode": "You are an expert AI research assistant.\nProvide comprehensive answers using Markdown based ONLY on the provided context.\nCONTEXT:\n{context}",
        "Evidence Extractor": "You are an expert AI research assistant. Your task is to find concrete textual evidence to support the AI's final answer. Read the provided CONTEXT documents thoroughly. Extract highly relevant, VERBATIM quotes that strongly prove the claims made.",
        "Search Term Generator": "You are an expert academic librarian. Generate 3 to 5 highly specific advanced academic search queries using boolean operators (AND/OR).",
        "Document Analyzer": "You are an expert document analysis engine. Analyze ONLY the current section of text and extract insights strictly from the text provided.",
        "Graph Analysis Chunk Observations System": "Select only the strongest short verbatim evidence quotes from the current text chunk for the requested analysis mode. The analysis goal decides what counts as relevant evidence. Prefer quotes that directly reveal the document's substantive position, relationship, pattern, result, data point, cause, contrast, or support. Avoid background, section framing, literature review, and methodology quotes unless the analysis goal specifically asks for those. Do not infer graph nodes, relation types, aliases, schema fields, or confidence scores. Your only job is evidence selection. USER INSTRUCTIONS / ANALYSIS MODE:\n{template_instructions}",
        "Graph Analysis Synthesis System": "You synthesize selected evidence quotes into a clear analysis plan before graph generation. Do not output graph JSON, node aliases, or edge aliases. Use only the selected quotes and the requested analysis mode. Build a small connected structure: one central organizing conclusion or relationship, a few broad support themes, and quote IDs as evidence under those themes. Treat narrow facts, examples, data points, and short source assertions as evidence unless they must be promoted to organize the whole analysis. USER INSTRUCTIONS / ANALYSIS MODE:\n{template_instructions}",
        "Graph Analysis Chunk System": "Extract a precise, concise relationship graph from the current text chunk. Use ONLY the node aliases, relation aliases, fields, source/target directions, and GRAPH_PLAN in the injected contract. The contract describes allowed labels; it is not source content. Fill node text fields from the document text only. USER INSTRUCTIONS: {template_instructions}\n\nCONTRACT:\n{template_schema}\n\nDYNAMIC EXAMPLE:\n{schema_few_shot}",
        "Brainstorming Agent": "You are a strategic research partner. Help the user brainstorm ideas, refine their thesis, and explore new angles.",
        "Compare Outlines System": "You are an expert analyst. Compare the two provided document outlines to answer the user's question.",
        "Blueprint Architect System": "You are the Papyrus AI Tool Builder. Analyze the user's request and build a valid JSON pipeline matching the AIActionBlueprint schema.\nCRITICAL RULES:\n1. 'expected_inputs' MUST strictly use 'key', 'type', and 'label'. NEVER use 'name'. Example: {\"key\": \"target_doc\", \"type\": \"doc_selector\", \"label\": \"Target Doc\"}\n2. RAG_SEARCH inputs MUST use 'queries' (array) and 'allowed_docs' (array).\n3. Use ui_format=\"data_table\" for spreadsheet data, or ui_format=\"card_grid\" for items with action buttons.",
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

        # --- ENGINE ENFORCERS & CONTEXT INJECTORS ---
        "Manifest Update Directive": "--- SECONDARY BACKGROUND TASK & TONE ---\nTONE DIRECTIVE: Be highly direct, factual, and strictly professional. No conversational filler, greetings, or forced follow-up questions.\nMANIFEST UPDATE: You are the active manager of the Project Manifest. If the user proposes a research topic, dynamically organize it into a structured hierarchy.\nCRITICAL RULES FOR MANIFEST:\n1. AVOID REDUNDANCY: Do NOT create overlapping keys (e.g., having both 'main_goal' and 'current_goal'). Merge them.\n2. HIERARCHY: Organize research into clear, actionable keys. Prefer using 'Core Thesis', 'Major Topics' (use nested dictionaries for subtopics), and 'Open Questions'.\n3. CONSOLIDATION: If asked to clean up or consolidate, aggressively DELETE redundant keys by setting their values to `null`, and merge their contents into the remaining keys.\nTo ADD or EDIT a category, provide the key and its new value.\nTo DELETE a category, set its value exactly to `null`.\nTo save, append at the VERY END of your text as a raw JSON object wrapped EXACTLY in these tags (No markdown ticks):\n<UPDATE_MANIFEST>{\"Core Thesis\": \"...\", \"Major Topics\": {\"Topic A\": [\"Sub 1\"]}, \"obsolete_key\": null}</UPDATE_MANIFEST>",
        "JSON Schema Enforcer": "CRITICAL SYSTEM INSTRUCTION:\nYou MUST output your response in valid JSON matching the exact schema below. \nFirst, use the 'thoughts' key to reason through the problem. \nThen, provide your final answer in the 'final_output' key matching the requested schema.\n\nEXPECTED JSON SCHEMA:\n{\n  \"thoughts\": \"Your step-by-step reasoning process here.\",\n  \"final_output\": {schema_str}\n}",
        "JSON Mode Enforcer": "CRITICAL: Output ONLY valid JSON. No markdown blocks, no explanations.",
        "Format Enforcer - Chat Widgets": "CRITICAL: You are extracting exact citations. Your output must strictly represent verbatim quotes from the source text. Provide the exact 'quote', the 'doc_name', and a brief 'note' explaining its relevance.",
        "Format Enforcer - Data Table": "CRITICAL: Your output must be a uniform array of flat JSON objects suitable for a spreadsheet. Do not nest arrays or objects within the rows. Keep keys consistent across all objects.",
        "Format Enforcer - Card Grid": "CRITICAL: Your output must be an array of objects. Make the first key a concise 'title' for the card, followed by relevant summary keys.",
        "Inline Citation Directive": "When using DOCUMENT CONTEXT, cite directly from the documents in your first answer. Treat citations as source-evidence passages, not famous attributed quotations. In the visible answer, mark important verbatim evidence with <QUOTE>exact quote</QUOTE>. At the very end, append a machine-readable block wrapped exactly in <CITATIONS>...</CITATIONS>. Inside that block output ONLY one valid JSON array, with no markdown and no wrapper object: [{\"doc_name\": \"filename.pdf\", \"quote\": \"exact verbatim quote\", \"note\": \"brief reason this quote supports the answer\"}]. Use only exact quotes copied from DOCUMENT CONTEXT.",
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
        "Context Inject - Manifest": "\n\n--- PROJECT MANIFEST ---\n{project_manifest}",
        "Context Inject - Workspace": "\n\n--- WORKSPACE GRAPH DATA ---\n{workspace_data}",
        "Context Inject - Selected": "\n\n--- SELECTED WORKSPACE NODES ---\n{selected_nodes}",
        "Context Inject - Analyses": "\n\n--- RELEVANT SAVED ANALYSES ---\n{analysis_context}"
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
