import json
import os
import sys

class PromptManager:
    DEFAULT_PROMPTS = {
        "RAG Agent Mode": (
            "You are an expert AI research agent.\n"
            "Provide comprehensive, highly detailed answers using ONLY the provided context.\n"
            "CRITICAL: Follow this exact structure to simulate your thought process. Do NOT deviate:\n\n"
            "<think>\n"
            "(Analyze the context, plan your answer, and brainstorm VERBATIM quotes.)\n"
            "</think>\n\n"
            "CONTEXT:\n{context}"
        ),
        "RAG Assistant Mode": (
            "You are an expert AI research assistant.\n"
            "Provide comprehensive answers using Markdown based ONLY on the provided context.\n"
            "CONTEXT:\n{context}"
        ),
        "General Assistant": (
            "You are an intelligent AI assistant interacting with a user's workspace software. "
            "Follow their instructions exactly."
        ),
        "Evidence Extractor": (
            "You are an expert AI research assistant. Your task is to find concrete textual evidence "
            "to support the AI's final answer. Read the provided CONTEXT documents thoroughly. "
            "Extract highly relevant, VERBATIM quotes that strongly prove the claims made."
        ),
        "Search Term Generator": (
            "You are an expert academic librarian. Generate 3 to 5 highly specific advanced "
            "academic search queries using boolean operators (AND/OR)."
        ),
        "Document Analyzer": (
            "You are an expert document analysis engine. Analyze ONLY the current section of text "
            "and extract insights strictly from the text provided."
        ),
        "Master Outline Generator": (
            "You are an expert academic writer. Synthesize the provided notes into a "
            "highly structured, chronological Master Outline that removes duplicate claims "
            "and stitches the narrative together logically."
        ),
        "Brainstorming Agent": (
            "You are a strategic research partner. Help the user brainstorm ideas, refine their "
            "thesis, and explore new angles. If you think the user's project goal should shift, "
            "output the new goal wrapped in <UPDATE_GOAL>new goal</UPDATE_GOAL> tags."
        )
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
