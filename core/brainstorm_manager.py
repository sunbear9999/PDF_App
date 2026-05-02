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
            if callback: callback("*(Scanning documents for brainstorming context...)*\n\n")
            context_str = self._fetch_context(query)
            if mode == "RAG Only" and not context_str.strip():
                msg = "I couldn't find any relevant concepts in your indexed documents to suggest a direction. Please try a different query or add more sources."
                if callback: callback(msg)
                return msg, None

        # Fetch and format the base prompt
        if mode == "RAG Enabled":
            base_prompt = self.prompt_manager.get_prompt("Brainstorm - RAG Enabled").replace("{context}", context_str)
        elif mode == "RAG Only":
            base_prompt = self.prompt_manager.get_prompt("Brainstorm - RAG Only").replace("{context}", context_str)
        else:
            base_prompt = self.prompt_manager.get_prompt("Brainstorm - Default")

        base_prompt = base_prompt.replace("{project_goal}", current_goal)

        # Inject sliding-window history
        history_str = self._build_history_string()
        system_prompt = f"{base_prompt}\n\n{history_str}"

        full_response = self.llm_manager.query(
            question=query,
            selected_model=selected_model,
            rag_enabled=False, 
            use_agents=False,
            custom_system_prompt=system_prompt,
            callback=callback
        )

        # Post-Processing: Extract Goal Updates
        new_goal = None
        cleaned_response = full_response

        # Look for the update tag
        match = re.search(r'<UPDATE_GOAL>(.*?)</UPDATE_GOAL>', full_response, re.DOTALL)
        if match:
            new_goal = match.group(1).strip()
            # Remove the XML tags from the text we save to history
            cleaned_response = re.sub(r'<UPDATE_GOAL>.*?</UPDATE_GOAL>', '', full_response, flags=re.DOTALL).strip()

        # Update History Window with the CLEANED response
        self.history.append({"user": query, "ai": cleaned_response})
        if len(self.history) > self.max_history:
            self.history.pop(0)

        return cleaned_response, new_goal