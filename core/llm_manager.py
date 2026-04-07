# core/llm_manager.py
# [REFACTOR] Facade/Orchestrator for AI operations
# Delegates to OllamaClient for HTTP operations and VectorStore for RAG indexing/search

import requests
import json
import re
import logging
from core.ollama_client import OllamaClient
from core.vector_store import VectorStore
from core.prompts import Prompts

logger = logging.getLogger(__name__)


class LocalLLMManager:
    """[REFACTOR] Facade orchestrator for AI operations.
    
    Responsibilities:
    - Delegate Ollama API calls to OllamaClient
    - Delegate vector DB operations to VectorStore
    - Maintain public API for backward compatibility
    - Coordinate embeddings between client and DB
    """

    def __init__(self):
        """[REFACTOR] Initialize with OllamaClient and VectorStore."""
        self.ollama_client = OllamaClient()  # Handles Ollama API + model management
        self.vector_store = VectorStore()    # Handles ChromaDB + embeddings
        self.embedding_model = "nomic-embed-text"
        self.api_base = "http://localhost:11434/api"  # [COMPAT] Kept for backward compat
        
        # For backward compatibility with code accessing self.collection
        self.collection = None
        self.chroma_client = None

    def set_project_database(self, project_filepath):
        """[REFACTOR] Initialize vector store for project."""
        if not project_filepath:
            self.vector_store.collection = None
            self.collection = None
            return
        
        self.vector_store.initialize(project_filepath)
        # [COMPAT] Expose ChromaDB collection for backward compatibility
        self.collection = self.vector_store.collection
        self.chroma_client = self.vector_store.chroma_client

    def unload_all_models(self):
        """[REFACTOR] Delegate to OllamaClient."""
        self.ollama_client.unload_all_models()

    def preload_model(self, model_name="llama3"):
        """[REFACTOR] Delegate to OllamaClient."""
        self.ollama_client.preload_model(model_name)

    def get_available_models(self):
        """[REFACTOR] Delegate to OllamaClient."""
        models = self.ollama_client.list_models()
        if models:
            return [m for m in models if m != self.embedding_model and not m.startswith(f"{self.embedding_model}:")]
        return ["qwen2.5:7b", "llama3", "mistral"]

    def check_and_pull_embedding_model(self, progress_callback=None):
        """[REFACTOR] Delegate to OllamaClient."""
        self.ollama_client.pull_model(self.embedding_model, progress_callback)

    def get_embedding(self, text):
        """[REFACTOR] Delegate to OllamaClient."""
        return self.ollama_client.embed(self.embedding_model, text)

    def get_batch_embeddings(self, texts):
        """[REFACTOR] Delegate to OllamaClient."""
        return self.ollama_client.embed_batch(self.embedding_model, texts)

    def generate_response(self, prompt, selected_model, system_prompt=None, stream=False, options=None, json_mode=False):
        """[REFACTOR] Delegate to OllamaClient with backward-compatible signature."""
        return self.ollama_client.generate(
            model=selected_model,
            prompt=prompt,
            system_prompt=system_prompt,
            stream=stream,
            options=options,
            json_mode=json_mode
        )

    def _clean_json_response(self, text):
        """[REFACTOR] Backward compat wrapper - JSON cleanup now in BaseAIWorker."""
        from base_ai_worker import BaseAIWorker
        return BaseAIWorker.clean_and_parse_json(text)

    def _calculate_dynamic_context(self, prompt, base_ctx=2048):
        """[REFACTOR] Delegate to OllamaClient."""
        return self.ollama_client.calculate_dynamic_context(prompt, base_ctx)

    def route_query_intent(self, model_name, user_prompt):
        """[REFACTOR] Route query intent using centralized prompt."""
        if not model_name or not user_prompt:
            return "LOGICAL_ANALYSIS"

        classification_prompt = (
            "USER PROMPT:\n" + user_prompt + "\n\n" 
            "Respond with exactly one category: FACT_RETRIEVAL, LOGICAL_ANALYSIS, or GENERAL_CHAT."
        )

        try:
            system_prompt = Prompts.get_system_prompt('query_classifier')
            raw = self.generate_response(classification_prompt, model_name, system_prompt=system_prompt, stream=False)
            if not raw:
                return "LOGICAL_ANALYSIS"

            candidate = raw.strip().upper()
            for category in ["FACT_RETRIEVAL", "LOGICAL_ANALYSIS", "GENERAL_CHAT"]:
                if candidate.startswith(category) or f" {category} " in candidate or candidate == category:
                    return category

            if "GENERAL" in candidate or "CHAT" in candidate:
                return "GENERAL_CHAT"
            if "FACT" in candidate or "RETRIEVAL" in candidate:
                return "FACT_RETRIEVAL"
            if "LOGICAL" in candidate or "ANALYSIS" in candidate:
                return "LOGICAL_ANALYSIS"
        except Exception:
            pass

        return "LOGICAL_ANALYSIS"

    def index_documents(self, pdf_paths, progress_callback=None):
        """[REFACTOR] Delegate to VectorStore for document indexing."""
        if not self.vector_store.is_ready():
            raise Exception("Vector Database not initialized. Please save the project first.")
        
        if progress_callback:
            progress_callback("Clearing VRAM to prioritize embedding engine...")
        
        self.unload_all_models()
        self.check_and_pull_embedding_model(progress_callback)
        
        # [REFACTOR] Pass embedding function to VectorStore
        # VectorStore will chunk PDFs and call this function for embeddings
        def embedding_fn(texts):
            return self.get_batch_embeddings(texts)
        
        self.vector_store.index_documents(pdf_paths, embedding_fn, progress_callback)
        
        if progress_callback:
            progress_callback("Releasing embedding model from memory...")
        self.unload_all_models()

    def query(self, question, selected_model, allowed_docs=None, callback=None, rag_enabled=True, use_agents=True, custom_system_prompt=None, existing_highlights=None, document_map=None, temperature=0.1):
        """[REFACTOR] Query with RAG and agent support.
        
        [AI OPTIMIZATION] Temperature control:
        - temperature=0.1 for extraction/analysis (default)
        - temperature=0.4 for generative tasks
        """
        context = ""
        system_prompt = ""
        
        if existing_highlights is None:
            existing_highlights = []

        if not selected_model:
            err = "\n[Generation Error: No AI model selected.]"
            if callback: callback(err)
            return err

        highlight_rules = (
            "--- HIGHLIGHTS ---\n"
            "To autonomously highlight quotes in the PDF for the user, you MUST create a final section at the VERY END of your response exactly titled '--- HIGHLIGHTS ---'.\n"
            "Under this section, list your quotes using this exact single-line format:\n"
            "%%QUOTE | Document_Name.pdf | The exact phrase from the text | Your explanation\n\n"
            "CRITICAL RULES (FAILURE TO FOLLOW WILL BREAK THE SYSTEM):\n"
            "1. STRICT ANTI-HALLUCINATION: You MUST ONLY extract exact, verbatim phrases that physically appear in the provided CONTEXT text below. NEVER alter text, paraphrase, or invent 'implied' quotes.\n"
            "2. EXACT DOCUMENT NAMES: Use ONLY the exact document names provided in the CONTEXT headers. NEVER invent or repeat document names if they aren't in the context.\n"
            "3. NO FORCED QUOTAS: If a document does not contain highly relevant text, SKIP IT entirely. Do not force quotes.\n"
            "4. NO REPETITION: Every quote must be entirely distinct.\n"
            "5. ISOLATION: The '--- FINAL ANSWER ---' section MUST NOT contain any quotes, highlight tags, or direct document citations. It should only be a high-level conceptual summary. Reserve ALL quotes and explanations exclusively for the '--- HIGHLIGHTS ---' section.\n"
        )

        if existing_highlights:
            highlight_rules += "6. DO NOT highlight the following phrases as they are ALREADY highlighted by the user:\n"
            for h in set(existing_highlights):
                highlight_rules += f" - {h}\n"

        if rag_enabled:
            if not self.vector_store.is_ready():
                if callback: callback("Please build the search index first.")
                return ""

            try:
                search_queries = [question]
                where_clause = None
                
                if allowed_docs:
                    if len(allowed_docs) == 1:
                        where_clause = {"doc_name": allowed_docs[0]}
                    else:
                        where_clause = {"doc_name": {"$in": allowed_docs}}

                if use_agents:
                    if callback: callback("@@AGENT@@🔍 Performing initial scan based on your query...")
                    
                    try:
                        sq_emb = self.get_embedding(question)
                        initial_results = self.vector_store.search(sq_emb, where_clause, n_results=3)
                        
                        initial_context = ""
                        if initial_results.get('documents') and initial_results['documents'][0]:
                            initial_context = "\n\n".join(initial_results['documents'][0])
                        
                        if callback: callback("@@AGENT@@🤖 Analyzing context to formulate advanced search strategy...")
                        
                        search_prompt = Prompts.get_system_prompt('search_expansion')
                        search_prompt += f"\n\nUSER QUERY: '{question}'\n\nPreliminary context:\n{initial_context}"
                        
                        exp_resp = self.generate_response(search_prompt, selected_model, system_prompt="You are an expert researcher.", stream=False, options={"temperature": 0.2, "num_predict": 100, "num_ctx": 4096})
                        if exp_resp:
                            raw_resp = exp_resp.strip()
                            for line in raw_resp.split('\n'):
                                clean_q = re.sub(r'^[-*0-9.\s]+', '', line).strip(' "\'')
                                if clean_q and len(clean_q) > 3 and clean_q.lower() not in question.lower():
                                    search_queries.append(clean_q)
                    except Exception:
                        pass 
                    
                    search_queries = list(dict.fromkeys(search_queries))[:4] 

                    if callback: 
                        clean_qs = ", ".join(f"'{q}'" for q in search_queries[1:])
                        if clean_qs:
                            callback(f"@@AGENT@@📚 Exploring deeper across project using terms: <b>{clean_qs}</b>")

                aggregated_docs = {}
                
                # Iterate through each allowed document individually
                docs_to_search = allowed_docs if allowed_docs else [None]
                
                for sq in search_queries:
                    try:
                        sq_emb = self.get_embedding(sq)
                        for doc in docs_to_search:
                            doc_where = {"doc_name": doc} if doc else None
                            results = self.vector_store.search(sq_emb, doc_where, n_results=3 if use_agents else 6)
                            if results.get('documents') and results['documents'][0]:
                                for idx, doc_text in enumerate(results['documents'][0]):
                                    doc_id = results['ids'][0][idx]
                                    meta = results['metadatas'][0][idx]
                                    if doc_id not in aggregated_docs:
                                        aggregated_docs[doc_id] = {
                                            "text": doc_text,
                                            "doc_name": meta['doc_name'],
                                            "page": meta['page']
                                        }
                    except Exception:
                        continue

                if not aggregated_docs:
                    err_msg = "\n[System Warning: No readable text was found in the selected documents.]\n"
                    if callback: callback(err_msg)
                    return err_msg

                sorted_docs = sorted(aggregated_docs.values(), key=lambda x: (x['doc_name'], x['page']))
                context_pieces = [f"--- DOCUMENT: {d['doc_name']} | PAGE {d['page'] + 1} ---\n{d['text']}" for d in sorted_docs]

                context = "\n\n".join(context_pieces)
                if document_map:
                    context = f"DOCUMENT LOGIC MAP:\n{document_map}\n\n{context}"

                # Explicitly inject valid document names to prevent hallucinations
                available_docs_list = list(set([d['doc_name'] for d in sorted_docs]))
                if available_docs_list:
                    highlight_rules += f"\nCRITICAL: The ONLY VALID DOCUMENT NAMES you can use for %%QUOTE are: {', '.join(available_docs_list)}\n"

            except Exception as e:
                if callback: callback(f"\n[System Error: {str(e)}]\n")
                return f"[System Error: {str(e)}]"

            if use_agents:
                system_prompt = Prompts.get_system_prompt('rag_agent')
                system_prompt += f"\n\n{highlight_rules}\n\nCONTEXT:\n{context}"
            else:
                system_prompt = Prompts.get_system_prompt('rag_standard')
                system_prompt += f"\n\n{highlight_rules}\n\nCONTEXT:\n{context}"
        else:
            system_prompt = custom_system_prompt or "You are an intelligent AI assistant interacting with a user's workspace software. Follow their instructions exactly."

        # [AI OPTIMIZATION] Dynamic context + temperature
        dynamic_ctx = self._calculate_dynamic_context(question, base_ctx=2048)
        payload = {
            "model": selected_model, 
            "prompt": question, 
            "system": system_prompt, 
            "stream": True, 
            "keep_alive": "60m", 
            "options": {
                "temperature": temperature,
                "top_p": 0.7,
                "num_ctx": dynamic_ctx
            }
        }
        
        full_response = ""
        try:
            with requests.post(f"{self.api_base}/generate", json=payload, stream=True, timeout=(15, 600)) as response:
                if response.status_code != 200:
                    try: err_msg = response.json().get("error", response.text)
                    except: err_msg = response.text
                    raise Exception(f"Ollama API Error ({response.status_code}): {err_msg}")

                for line in response.iter_lines():
                    if line:
                        chunk = json.loads(line)
                        if "error" in chunk:
                            raise Exception(chunk["error"])
                        txt = chunk.get("response", "")
                        full_response += txt
                        if callback: callback(txt)
        except requests.exceptions.ReadTimeout:
            err = "\n[Generation Error: The AI took too long to respond. Hardware limits exceeded.]"
            if callback: callback(err)
            full_response += err
        except Exception as e:
            err = f"\n[Generation Error: {str(e)}]"
            if callback: callback(err)
            full_response += err
        
        return full_response