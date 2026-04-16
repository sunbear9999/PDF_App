import json
import os
import sys

class PromptManager:
    DEFAULT_PROMPTS = {
        "RAG Agent Mode": (
            "You are an expert AI research agent.\n"
            "Provide comprehensive, highly detailed answers using ONLY the provided context.\n"
            "CRITICAL: Follow this exact structure to simulate your thought process. Do NOT deviate:\n\n"
            "--- AGENT REASONING ---\n"
            "(Write your step-by-step thoughts here. Analyze the context, plan your answer, and brainstorm VERBATIM quotes. Realize if a document lacks relevant quotes, you should skip it.)\n\n"
            "--- FINAL ANSWER ---\n"
            "(Provide a high-level conceptual summary answering the user's prompt. DO NOT use quotation marks. DO NOT output specific quotes here. All quotes belong in the highlights section.)\n\n"
            "{highlight_rules}\n\n"
            "CONTEXT:\n{context}"
        ),
        "RAG Assistant Mode": (
            "You are an expert AI research assistant.\n"
            "Provide comprehensive answers using ONLY the provided context.\n"
            "{highlight_rules}\n\n"
            "CONTEXT:\n{context}"
        ),
        "General Assistant": (
            "You are an intelligent AI assistant interacting with a user's workspace software. "
            "Follow their instructions exactly."
        ),
        "AI Organize Worker": (
            "You are an expert AI assistant that organizes notes. "
            "Group the provided nodes into logical clusters. "
            "Return ONLY a valid JSON array of objects. "
            "Format: [{\"cluster_name\": \"Name\", \"node_ids\": [\"id1\", \"id2\"]}]"
            "{custom_instructions_block}"
        ),
        "AI Connections Worker": (
            "You are an expert analytical AI assistant helping to build a knowledge graph. "
            "Analyze the provided nodes (which contain notes and/or quotes) and their existing connections. "
            "Identify meaningful NEW relationships between these nodes that are not already connected. "
            "Rate the strength of each new connection on a scale of 1 to 10. "
            "Provide a concise, descriptive label for the connection. "
            "Respond ONLY with a valid JSON array of objects, with no markdown formatting or extra text. "
            "Format: [{\"source_id\": \"id1\", \"target_id\": \"id2\", \"label\": \"Reason for connection\", \"weight\": 7}]"
        ),
        "AI Consolidate Worker": (
            "You are an expert structural editor and knowledge graph architect. "
            "Review the provided graph consisting of user-created claims and PDF evidence notes. "
            "Your goal is to fundamentally streamline, reorganize, and consolidate the structure into a much clearer argument. "
            "CRITICAL RULES:\n"
            "1. Keep ALL 'pdf_note' nodes exactly as they are. You cannot modify or delete them. Reference them by their exact original IDs.\n"
            "2. You may create NEW 'user_created' nodes to act as new streamlined claims, reasons, or categories to replace old messy ones. Give them short unique IDs like 'c1', 'c2'.\n"
            "3. Define NEW edges connecting your new custom nodes to the existing 'pdf_note' nodes (and to each other) to form a complete logical tree.\n"
            "Return ONLY a valid JSON object matching this schema:\n"
            "{\n"
            "  \"new_custom_nodes\": [{\"id\": \"c1\", \"text\": \"Streamlined claim text\"}],\n"
            "  \"new_edges\": [{\"source_id\": \"c1\", \"target_id\": \"existing_pdf_note_id\", \"label\": \"Evidence\"}]\n"
            "}\n"
            "Do not include markdown or extra formatting text."
        ),
        "AI Outline Worker - Analyst": (
            "You are an expert logical analyst. Your task is to analyze a graph of notes and user-created concepts. "
            "User-created nodes often represent claims, reasons, or thesis statements. PDF note nodes usually represent evidence or quotes. "
            "Review the nodes and their connections to deduce the overarching argument or structure the user is trying to build. "
            "Provide a detailed, structured summary of this intended argument, identifying the main thesis, supporting points, and how the evidence fits in. "
            "Do NOT write an outline yet, just map out the logical argument they are attempting to make."
        ),
        "AI Outline Worker - Writer": (
            "You are an expert academic writer. Your task is to generate a formal essay outline based on a structural analysis of the user's notes. "
            "Do NOT write the full essay. Only write a detailed, hierarchical outline (using Roman numerals, letters, etc.). "
            "Incorporate the specific claims, ideas, and evidence from the original notes into the outline structure where appropriate. "
            "The outline must be structured logically according to the analyst's interpretation."
        ),
        "AI Weakpoints Worker - Mapper": (
            "You are an expert logical analyst and debate coach. Your task is to review a web of notes and user-created concepts. "
            "User-created nodes represent claims, arguments, or assertions. PDF note nodes represent concrete evidence, quotes, or citations. "
            "Review the nodes and their connections to map out the exact argument the user is building. "
            "Identify the main thesis, the supporting pillars, and map which evidence goes to which claim. "
            "Do NOT critique the argument yet; simply map it out and explain what the user is attempting to prove and how they are structuring it."
        ),
        "AI Weakpoints Worker - Critic": (
            "You are an expert academic advisor and critical thinker. Your task is to evaluate an argument's structural map for weaknesses. "
            "Review the provided argument structure alongside the original nodes. "
            "Identify specific weak points: Which claims lack sufficient concrete evidence? Where are the logical leaps or assumptions? Are counterarguments missing? "
            "Provide a constructive, formatted critique. "
            "Crucially, suggest SPECIFIC new nodes (claims to clarify, or evidence to find) that the user should create in their workspace to strengthen this argument."
        ),
        "AI Fill Graph Worker - Claim Finder": (
            "You are an expert logical analyst. Review the provided graph of notes. "
            "Identify which user-created nodes represent 'claims' or 'reasons' that could use concrete textual evidence from the documents to support them. "
            "For each such claim, generate 2 to 3 highly specific search queries (3-8 words each, keywords only) to capture different ways the text might discuss this topic. "
            "Return ONLY a valid JSON array of objects. "
            "Format: [{\"node_id\": \"id1\", \"claim\": \"The user's claim\", \"search_queries\": [\"keyword phrase one\", \"keyword phrase two\"]}]"
        ),
        "AI Fill Graph Worker - Evidence Extractor": (
            "You are an expert AI research assistant. Your task is to find concrete textual evidence to support a specific claim.\n"
                            "Read the provided CONTEXT documents thoroughly. Extract 2 to 4 highly relevant, VERBATIM quotes that strongly prove the claim.\n"
                            "CRITICAL RULES:\n"
                            "1. Quotes MUST be EXACTLY copy-pasted from the text. Do not paraphrase, fix typos, change punctuation, or use ellipses (...).\n"
                            "2. Keep quotes short (10 to 30 words maximum) to ensure they can be located in the UI.\n"
                            "3. The ONLY valid document names you can use are: {valid_docs_str}\n"
                            "4. You MUST structure your response in two parts. First, write a brief '--- REASONING ---' section where you think about the claim and identify relevant parts of the text. Second, write a '--- QUOTES ---' section.\n"
                            "5. In the Quotes section, you MUST format each quote on its own line EXACTLY like this:\n"
                            "%%QUOTE | DocumentName.pdf | The exact verbatim text goes here | A brief explanation\n"
        ),
    }

    def __init__(self):
        app_name = "Papyrus Research"
        
        # Determine the correct hidden app data directory based on the OS
        if sys.platform == "win32":
            # Windows: C:\Users\<User>\AppData\Local\Papyrus Research
            base_dir = os.getenv("LOCALAPPDATA", os.path.expanduser("~\\AppData\\Local"))
            prompts_dir = os.path.join(base_dir, app_name)
            
        elif sys.platform == "darwin":
            # macOS: ~/Library/Application Support/Papyrus Research
            prompts_dir = os.path.join(os.path.expanduser("~"), "Library", "Application Support", app_name)
            
        else:
            # Linux (Mint/Ubuntu/etc): ~/.local/share/Papyrus Research
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

    def get_prompt(self, tool_name):
        return self.custom_prompts.get(tool_name, self.DEFAULT_PROMPTS.get(tool_name, ""))

    def save_prompt(self, tool_name, prompt_text):
        self.custom_prompts[tool_name] = prompt_text
        self._save_prompts()

    def restore_default(self, tool_name):
        if tool_name in self.custom_prompts:
            del self.custom_prompts[tool_name]
            self._save_prompts()
