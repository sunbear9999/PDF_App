import json
import os
import sys

class PromptManager:
    CATEGORIES = {
        "Agent Personas": [
            "General Assistant", "RAG Agent Mode", "RAG Assistant Mode",
            "Evidence Extractor", "Search Term Generator", "Document Analyzer",
            "Master Outline Generator", "Brainstorming Agent", "Comparison Agent",
            "Blueprint Architect System", "Auto Build Graph System", 
            "Analysis Search System", "Autopilot Planner System"
        ],
        "Tool & Feature Queries": [
            "Brainstorm Query", "Search Terms Query", "Advanced RAG Optimize Query",
            "Universal Citation Query", "Analyze Chunk Query", "Master Outline Query",
            "Compare Outlines Query", "Blueprint Architect Query", "Auto Build Graph Query",
            "Modular Workspace Query", "Consolidate Workspace Plan Query",
            "Autopilot Planner Query", "Analysis Search Query", "Async Teardown Buffer Query"
        ],
        "Brainstorming Configurations": [
            "Brainstorm System - RAG Only", "Brainstorm System - RAG Enabled",
            "Brainstorm System - Default"
        ],
        "System & Engine Enforcers": [
            "Manifest Update Directive", "JSON Schema Enforcer",
            "Format Enforcer - Chat Widgets", "Format Enforcer - Data Table",
            "Format Enforcer - Card Grid"
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
        "Master Outline Generator": "You are an expert academic writer. Synthesize the provided notes into a highly structured, chronological Master Outline that removes duplicate claims and stitches the narrative together logically.",
        "Brainstorming Agent": "You are a strategic research partner. Help the user brainstorm ideas, refine their thesis, and explore new angles.",
        "Comparison Agent": "You are an expert analyst. Compare the two provided document outlines to answer the user's question.",
        "Blueprint Architect System": "You are the Papyrus AI Tool Builder. Analyze the user's request and build a valid JSON pipeline matching the AIActionBlueprint schema.\nCRITICAL RULES:\n1. 'expected_inputs' MUST strictly use 'key', 'type', and 'label'. NEVER use 'name'. Example: {\"key\": \"target_doc\", \"type\": \"doc_selector\", \"label\": \"Target Doc\"}\n2. RAG_SEARCH inputs MUST use 'queries' (array) and 'allowed_docs' (array).\n3. Use ui_format=\"data_table\" for spreadsheet data, or ui_format=\"card_grid\" for items with action buttons.",
        "Auto Build Graph System": "You are a strict graph architect. Convert the provided text into a logical diagram. Output ONLY valid, fully closed JSON matching the exact schema. Do not truncate the JSON output.",
        "Analysis Search System": "You are an analytical assistant extracting relevant context from saved document analyses.",
        "Autopilot Planner System": "You are an autonomous routing agent. Output ONLY a JSON object evaluating context needs.",

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
        "Master Outline Query": "NOTES:\n{combined_text}",
        "Compare Outlines Query": "USER QUESTION: {query}\n\n--- DOCUMENT A OUTLINE ---\n{doc_a}\n\n--- DOCUMENT B OUTLINE ---\n{doc_b}",
        "Blueprint Architect Query": "CURRENT TOOL JSON:\n{current_json}\n\nUSER REQUEST:\n{user_text}",
        "Auto Build Graph Query": "Translate the preceding AI Response into a spatial workspace graph map. Extract the main ideas as separate nodes, and link them logically with edges.\n\nCRITICAL RULES:\n1. If an idea is an original brainstorm concept, put the text in 'note' and leave 'quote' and 'doc_name' EMPTY.\n2. ONLY use the 'quote' and 'doc_name' fields if the AI Response contains an explicit, verbatim quote from a source document.\n\nAI RESPONSE TO MAP:\n{{{source_key}}}\n\nCURRENT WORKSPACE GRAPH MAP (Do not duplicate these):\n{workspace_data}",
        "Modular Workspace Query": "Analyze the workspace data and return the updated graph.\n\nCRITICAL RULE: You MUST output ONLY valid JSON matching this exact schema shape. Do not add keys outside this schema.\n\nSCHEMA:\n{schema_str}\n\nWORKSPACE DATA:\n{workspace_data}",
        "Consolidate Workspace Plan Query": "Analyze the following workspace data. Draft a step-by-step structural plan to reorganize it into a cohesive argument map. Identify which nodes should group together, what new overarching hub nodes should be created, and EXACTLY how they should connect to the original evidence nodes. Do not write JSON, just write the plan in plain text.\n\nWORKSPACE DATA:\n{workspace_data}",
        "Autopilot Planner Query": "USER INTENT: {__autopilot_intent__}\n\nAnalyze this goal. Decide if answering it requires: 1) Searching document text (RAG). 2) Reading the Project Manifest. 3) Reading the Workspace Map.",
        "Analysis Search Query": "USER INTENT: {__analysis_intent__}\n\nRAW DOCUMENT ANALYSES:\n{raw_analyses}\n\nExtract and summarize any information from the analyses that is directly relevant to the user intent. If nothing is relevant or analyses are empty, output 'None'.",
        "Async Teardown Buffer Query": "Acknowledge process completion with the word 'done'.",

        # --- ENGINE ENFORCERS & CONTEXT INJECTORS ---
        "Manifest Update Directive": "--- SECONDARY BACKGROUND TASK & TONE ---\nTONE DIRECTIVE: Be highly direct, factual, and strictly professional. No conversational filler, greetings, or forced follow-up questions.\nMANIFEST UPDATE: You are the active manager of the Project Manifest. If the user proposes a research topic, dynamically organize it into a structured hierarchy.\nCRITICAL RULES FOR MANIFEST:\n1. AVOID REDUNDANCY: Do NOT create overlapping keys (e.g., having both 'main_goal' and 'current_goal'). Merge them.\n2. HIERARCHY: Organize research into clear, actionable keys. Prefer using 'Core Thesis', 'Major Topics' (use nested dictionaries for subtopics), and 'Open Questions'.\n3. CONSOLIDATION: If asked to clean up or consolidate, aggressively DELETE redundant keys by setting their values to `null`, and merge their contents into the remaining keys.\nTo ADD or EDIT a category, provide the key and its new value.\nTo DELETE a category, set its value exactly to `null`.\nTo save, append at the VERY END of your text as a raw JSON object wrapped EXACTLY in these tags (No markdown ticks):\n<UPDATE_MANIFEST>{\"Core Thesis\": \"...\", \"Major Topics\": {\"Topic A\": [\"Sub 1\"]}, \"obsolete_key\": null}</UPDATE_MANIFEST>",
        "JSON Schema Enforcer": "CRITICAL SYSTEM INSTRUCTION:\nYou MUST output your response in valid JSON matching the exact schema below. \nFirst, use the 'thoughts' key to reason through the problem. \nThen, provide your final answer in the 'final_output' key matching the requested schema.\n\nEXPECTED JSON SCHEMA:\n{\n  \"thoughts\": \"Your step-by-step reasoning process here.\",\n  \"final_output\": {schema_str}\n}",
        "Format Enforcer - Chat Widgets": "CRITICAL: You are extracting exact citations. Your output must strictly represent verbatim quotes from the source text. Provide the exact 'quote', the 'doc_name', and a brief 'note' explaining its relevance.",
        "Format Enforcer - Data Table": "CRITICAL: Your output must be a uniform array of flat JSON objects suitable for a spreadsheet. Do not nest arrays or objects within the rows. Keep keys consistent across all objects.",
        "Format Enforcer - Card Grid": "CRITICAL: Your output must be an array of objects. Make the first key a concise 'title' for the card, followed by relevant summary keys.",
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