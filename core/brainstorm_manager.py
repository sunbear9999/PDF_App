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

    def _fetch_context(self, query, allowed_docs, tag_filters, tag_logic):
        if not self.llm_manager.collection or self.llm_manager.collection.count() == 0:
            return ""
        
        try:
            import os
            sq_emb = self.llm_manager.get_embedding(query)
            
            # ---> FIX: Apply the Global Dock Filters to Brainstorm RAG
            global_conditions = []
            if allowed_docs:
                base_names = [os.path.basename(d) for d in allowed_docs]
                if len(base_names) == 1:
                    global_conditions.append({"doc_name": base_names[0]})
                else:
                    global_conditions.append({"doc_name": {"$in": base_names}})
                                    
            tag_filters = [str(t).strip() for t in (tag_filters or []) if str(t).strip()]
            if tag_filters:
                tag_conditions = [{f"tag_{t}": True} for t in tag_filters]
                if tag_logic == "OR":
                    if len(tag_conditions) > 1:
                        global_conditions.append({"$or": tag_conditions})
                    else:
                        global_conditions.append(tag_conditions[0])
                else:
                    global_conditions.extend(tag_conditions)
                
            where_clause = None
            if len(global_conditions) == 1:
                where_clause = global_conditions[0]
            elif len(global_conditions) > 1:
                where_clause = {"$and": global_conditions}

            results = self.llm_manager.collection.query(
                query_embeddings=[sq_emb],
                n_results=10, # Pull top 10 chunks across filtered scope
                where=where_clause
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

    def generate_response(self, query, mode, selected_model, current_goal, allowed_docs=None, tag_filters=None, tag_logic="AND", callback=None, graph_mode=False):
        context_str = ""
        if mode in ["RAG Enabled", "RAG Only"]:
            # ---> FIX: Added Markdown spacing \n\n> so it formats nicely in the UI
            if callback: callback("\n\n> *(Analyzing goal to formulate optimal search query...)*\n\n")
            
            search_prompt_template = self.prompt_manager.get_prompt("RAG Search Query Generator")
            search_prompt = search_prompt_template.replace("{project_goal}", current_goal).replace("{query}", query)
            
            optimized_query = self.llm_manager.query(
                question=search_prompt,
                selected_model=selected_model,
                rag_enabled=False, 
                use_agents=False,
                custom_system_prompt="You are a strict query generation algorithm. Output ONLY the raw search string."
            ).strip()
            
            optimized_query = optimized_query.replace('"', '').replace("'", "").strip()
            
            if callback: callback(f"\n\n> *(Searching indexed documents for: \"{optimized_query}\")...*\n\n")
            
            # ---> FIX: Pass the filters into the fetcher!
            context_str = self._fetch_context(optimized_query, allowed_docs, tag_filters, tag_logic)
            
            if not context_str.strip():
                if mode == "RAG Only":
                    msg = "\n\nI couldn't find any relevant concepts in your indexed documents matching these filters. Please adjust your filters, expand your query, or add more sources."
                    if callback: callback(msg)
                    return msg, None
                else:
                    if callback: callback("\n\n> *(No exact context found, falling back to general methodology...)*\n\n")

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
        
       
        formatting_rule = (
            "\n\nCRITICAL FORMATTING & CITATION INSTRUCTIONS:\n"
            "1. REASONING: Wrap your internal planning/reasoning strictly in <think> and </think> tags.\n"
            "2. STRUCTURE: You MUST structure your response into logical categories using markdown headers (e.g., '### Category Name').\n"
            "3. INLINE CITATIONS: When citing provided documents, use inline numbered footnotes like [1], [2].\n"
            "4. FOOTNOTE DEFINITIONS: At the VERY END of EACH category (before the next ###), you MUST define the footnotes used in that category exactly like this:\n"
            "%%QUOTE | Document_Name.pdf | The exact verbatim text from the document | Your explanatory note | 1\n"
            "Do NOT use quotation marks inside the %%QUOTE formatting."
        )
        if graph_mode:
            formatting_rule += (
                "\n\n5. WORKSPACE GRAPH GENERATION:\n"
                "The user has requested a Workspace Node Graph. You MUST outline the concepts in a JSON block wrapped EXACTLY in <workspace_graph> and </workspace_graph> tags.\n"
                "Format Example:\n"
                "<workspace_graph>\n"
                "{\n"
                "  \"nodes\": [\n"
                "    {\"id\": \"n1\", \"text\": \"Main Idea: Climate Change\", \"color\": \"#005577\"},\n"
                "    {\"id\": \"n2\", \"text\": \"Sub-point: Sea Level Rise\", \"color\": \"#007755\"}\n"
                "  ],\n"
                "  \"edges\": [\n"
                "    {\"source\": \"n1\", \"target\": \"n2\", \"label\": \"causes\"}\n"
                "  ]\n"
                "}\n"
                "</workspace_graph>\n"
                "Keep node text concise. Return this block at the end of your response."
            )
        # ----------------------------------

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