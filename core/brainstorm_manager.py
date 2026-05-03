# core/brainstorm_manager.py
import re

class BrainstormManager:
    def __init__(self, llm_manager, prompt_manager, max_history=4):
        self.llm_manager = llm_manager
        self.prompt_manager = prompt_manager
        self.max_history = max_history
        self.history = []

    def clear_history(self):
        self.history = []

    def _build_history_string(self):
        if not self.history:
            return ""
        history_lines = ["--- PREVIOUS CHAT HISTORY ---"]
        for turn in self.history:
            history_lines.append(f"User: {turn['user']}")
            history_lines.append(f"Assistant: {turn['ai']}\n")
        history_lines.append("--- END HISTORY ---\n")
        return "\n".join(history_lines)

    def _fetch_context(self, query):
        if not self.llm_manager.collection or self.llm_manager.collection.count() == 0:
            return ""
        
        try:
            sq_emb = self.llm_manager.get_embedding(query)
            results = self.llm_manager.collection.query(
                query_embeddings=[sq_emb],
                n_results=5
            )
            
            if results.get('documents') and results['documents'][0]:
                context_pieces = []
                for idx, doc_text in enumerate(results['documents'][0]):
                    meta = results['metadatas'][0][idx]
                    context_pieces.append(f"--- Document: {meta['doc_name']} ---\n{doc_text}")
                return "\n\n".join(context_pieces)
        except Exception as e:
            print(f"Brainstorm RAG Error: {e}")
        return ""

    def generate_response(self, query, mode, selected_model, current_goal, callback=None):
        context_str = ""
        if mode in ["RAG Enabled", "RAG Only"]:
            if callback: callback("*(Analyzing goal to formulate optimal search query...)*\n")
            
            # --- UPDATED: Fetching the Prompt from the Manager ---
            search_prompt_template = self.prompt_manager.get_prompt("RAG Search Query Generator")
            search_prompt = search_prompt_template.replace("{project_goal}", current_goal).replace("{query}", query)
            
            optimized_query = self.llm_manager.query(
                question=search_prompt,
                selected_model=selected_model,
                rag_enabled=False, 
                use_agents=False,
                custom_system_prompt="You are a strict query generation algorithm. Output ONLY the raw search string."
            ).strip()
            
            # Strip out any quotes the LLM might stubbornly try to include
            optimized_query = optimized_query.replace('"', '').replace("'", "").strip()
            
            if callback: callback(f"*(Searching indexed documents for: \"{optimized_query}\")...*\n\n")
            
            # Execute the RAG search using the SMART query
            context_str = self._fetch_context(optimized_query)
            
            if mode == "RAG Only" and not context_str.strip():
                msg = "I couldn't find any relevant concepts in your indexed documents to suggest a direction. Please try a different query or add more sources."
                if callback: callback(msg)
                return msg, None

        # --- 2. Fetch and format the Main Base Prompt ---
        if mode == "RAG Enabled":
            base_prompt = self.prompt_manager.get_prompt("Brainstorm - RAG Enabled").replace("{context}", context_str)
        elif mode == "RAG Only":
            base_prompt = self.prompt_manager.get_prompt("Brainstorm - RAG Only").replace("{context}", context_str)
        else:
            base_prompt = self.prompt_manager.get_prompt("Brainstorm - Default")

        base_prompt = base_prompt.replace("{project_goal}", current_goal)

        history_str = self._build_history_string()
        
        # --- 3. Inject Structural & Citation Formatting Rules ---
        formatting_rule = (
            "\n\nCRITICAL FORMATTING & CITATION INSTRUCTIONS:\n"
            "1. STRUCTURE: You MUST structure your response into logical, dynamic categories using strict markdown headers (e.g., '### Questions to Explore'). Do not use headers smaller than ###.\n"
            "2. CITATIONS: IF a specific point is derived from the provided RAG context, you MUST append a citation footprint using the exact format: [[Document Name|short 3-5 word exact quote]].\n"
            "   Example: ...this altered traditional gender roles [[Trask Double Colonization.pdf|altered concepts of nurturance]].\n"
            "   Do NOT make up quotes. Do NOT cite general knowledge. Only cite when referencing specific text from the provided documents."
        )
        system_prompt = f"{base_prompt}\n\n{history_str}{formatting_rule}"

        # --- 4. Execute the Final Brainstorming Generation ---
        full_response = self.llm_manager.query(
            question=query,
            selected_model=selected_model,
            rag_enabled=False, # We already manually injected the context above
            use_agents=False,
            custom_system_prompt=system_prompt,
            callback=callback
        )

        new_goal = None
        cleaned_response = full_response

        match = re.search(r'<UPDATE_GOAL>(.*?)</UPDATE_GOAL>', full_response, re.DOTALL)
        if match:
            new_goal = match.group(1).strip()
            cleaned_response = re.sub(r'<UPDATE_GOAL>.*?</UPDATE_GOAL>', '', full_response, flags=re.DOTALL).strip()

        self.history.append({"user": query, "ai": cleaned_response})
        if len(self.history) > self.max_history:
            self.history.pop(0)

        return cleaned_response, new_goal