# core/llm_manager.py
import subprocess
import requests
import json
import fitz
import os
import time
import chromadb
import re
import shutil     
import platform
from chromadb.config import Settings

try:
    from core.prompt_manager import PromptManager
except ImportError:
    from .prompt_manager import PromptManager

class LocalLLMManager:
    def __init__(self):
        self.api_base = "http://localhost:11434/api"
        self.embedding_model = "bge-m3"
        self.chroma_client = None
        self.collection = None
        self.ai_enabled = False  # Track if AI is available
        self.prompt_manager = PromptManager()
        self.audit_logger = None
        self.ensure_server_running()

    def _format_prompt_template(self, tool_name, fallback_template, **kwargs):
        template = self.prompt_manager.get_prompt(tool_name) or fallback_template
        try:
            return template.format(**kwargs)
        except Exception:
            return fallback_template.format(**kwargs)

    def get_system_prompt(self, tool_name, fallback_template, **kwargs):
        prompt = self._format_prompt_template(tool_name, fallback_template, **kwargs)
        return prompt
    def set_audit_logger(self, logger_func):
        """Injects the database logging function once at startup."""
        self.audit_logger = logger_func

    def ensure_server_running(self):
        # 1. Safely check if Ollama is actually installed on the system
        ollama_cmd = shutil.which("ollama")
        if not ollama_cmd:
            win_path = os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe")
            if os.path.exists(win_path):
                ollama_cmd = win_path
            else:
                print("[System] Ollama not found. Running in standard PDF mode.")
                self.ai_enabled = False
                return

        # 2. Check if the server is already running in the background
        try:
            requests.get("http://localhost:11434/", timeout=2)
            self.ai_enabled = True
        except requests.exceptions.ConnectionError:
            # 3. If it's not running, start it completely invisibly
            if platform.system() == "Windows":
                subprocess.Popen(
                    [ollama_cmd, 'serve'], 
                    stdout=subprocess.DEVNULL, 
                    stderr=subprocess.DEVNULL, 
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
            else:
                subprocess.Popen(
                    [ollama_cmd, 'serve'], 
                    stdout=subprocess.DEVNULL, 
                    stderr=subprocess.DEVNULL
                )
            time.sleep(3) # Give it a second to bind to the port
            self.ai_enabled = True

    def set_project_database(self, project_filepath):
        import gc
        if self.chroma_client is not None:
            # Attempt to clean up the previous chroma client and collection
            try:
                if hasattr(self.chroma_client, 'clear_system_cache'):
                    self.chroma_client.clear_system_cache()
            except Exception as e:
                print(f"Warning: Failed to clear chroma_client system cache: {e}")
            self.collection = None
            self.chroma_client = None
            gc.collect()

        if not project_filepath:
            self.collection = None
            return

        db_path = project_filepath + "_chroma_db"
        os.makedirs(db_path, exist_ok=True)

        self.chroma_client = chromadb.PersistentClient(path=db_path, settings=Settings(anonymized_telemetry=False))
        self.collection = self.chroma_client.get_or_create_collection(name="pdf_workspace")

    def unload_all_models(self):
        """Frees up system RAM/VRAM by explicitly asking Ollama to drop all active models."""
        try:
            resp = requests.get(f"{self.api_base}/ps", timeout=3)
            if resp.status_code == 200:
                models = resp.json().get("models", [])
                for m in models:
                    name = m.get("name")
                    # Sending an empty generate request with keep_alive=0 unloads it
                    requests.post(f"{self.api_base}/generate", json={"model": name, "keep_alive": 0}, timeout=3)
        except Exception as e:
            print(f"[System] Warning during model unload: {e}")

    def preload_model(self, model_name="gemma4:e2b"):
        if not model_name: return
        try:
            payload = {"model": model_name, "keep_alive": "60m"}
            requests.post(f"{self.api_base}/generate", json=payload, timeout=(15, 600))
            print(f"[System] Successfully preloaded {model_name} into memory.")
        except Exception as e:
            print(f"[System] Failed to preload model: {e}")

    def get_available_models(self):
        try:
            response = requests.get(f"{self.api_base}/tags", timeout=3)
            if response.status_code == 200:
                models = response.json().get("models", [])
                # Filter out the embedding model so it doesn't show up in your chat dropdowns
                found_models = [m["name"] for m in models if m["name"] != self.embedding_model and not m["name"].startswith(f"{self.embedding_model}:")]
                if found_models:
                    return found_models
        except Exception as e:
            print(f"[System] Error fetching models: {e}")

        return ["qwen2.5:7b", "llama3", "mistral"] 

    def check_and_pull_embedding_model(self, progress_callback=None):
        """Checks if the required embedding model is installed, and pulls it if missing."""
        try:
            response = requests.get(f"{self.api_base}/tags", timeout=3)
            if response.status_code == 200:
                models = response.json().get("models", [])
                model_names = [m["name"] for m in models]
                if self.embedding_model not in model_names and f"{self.embedding_model}:latest" not in model_names:
                    if progress_callback:
                        progress_callback(f"Downloading required embedding model '{self.embedding_model}'... (This takes a few minutes)")
                    # Ask Ollama to download the model (with a generous 30 min timeout)
                    requests.post(f"{self.api_base}/pull", json={"name": self.embedding_model}, timeout=(10, 1800)) 
        except Exception as e:
            print(f"[System] Warning: Could not verify/pull embedding model: {e}")

    def get_embedding(self, text):
        payload = {"model": self.embedding_model, "prompt": text, "keep_alive": "60m"}
        try:
            response = requests.post(f"{self.api_base}/embeddings", json=payload, timeout=(10, 300))
            if response.status_code == 200:
                return response.json().get("embedding")
            raise Exception(f"Server returned {response.status_code}: {response.text}")
        except Exception as e:
            raise Exception(f"Embedding failed. Error: {str(e)}")

    def get_batch_embeddings(self, texts):
        """Uses Ollama's newer /api/embed endpoint to process multiple chunks at lightning speed."""
        payload = {"model": self.embedding_model, "input": texts, "keep_alive": "60m"}
        try:
            response = requests.post(f"{self.api_base}/embed", json=payload, timeout=(15, 600))
            if response.status_code == 200:
                return response.json().get("embeddings")
        except Exception as e:
            print(f"[System] Batch embedding failed, falling back to sequential: {e}")
            
        # Fallback sequentially if the user has an older version of Ollama without /api/embed
        return [self.get_embedding(t) for t in texts]
    
    def remove_document_from_index(self, pdf_path):
        """Purges a specific document's embeddings from ChromaDB."""
        if not self.collection: return
        try:
            # Find all chunks associated with this doc_id
            results = self.collection.get(where={"doc_id": pdf_path})
            ids_to_delete = results.get("ids", [])
            
            # Fallback for older project databases
            if not ids_to_delete:
                fallback_name = os.path.basename(pdf_path)
                results = self.collection.get(where={"doc_name": fallback_name})
                ids_to_delete = results.get("ids", [])
                
            if ids_to_delete:
                self.collection.delete(ids=ids_to_delete)
                print(f"[System] Purged {len(ids_to_delete)} vectors for {os.path.basename(pdf_path)}.")
        except Exception as e:
            print(f"[System] Failed to remove document from index: {e}")

    def rename_document_in_index(self, old_path, new_path):
        """Wipes the old embeddings. The next indexing pass will automatically re-embed the new path."""
        self.remove_document_from_index(old_path)

    def index_documents(self, pdf_paths, progress_callback=None):
        if not self.collection:
            raise Exception("Vector Database not initialized. Please save the project first.")

        if progress_callback:
            progress_callback("Clearing VRAM to prioritize embedding engine...")
            
        self.unload_all_models()
        self.check_and_pull_embedding_model(progress_callback)

        # DELTA FIX: Grab all existing chunk IDs so we don't re-embed them
        existing_data = self.collection.get(include=["metadatas"])
        existing_ids = set(existing_data.get("ids", []))

        chunks = []
        metadatas = []
        ids = []
        
        chunk_word_size = 150
        overlap_words = 30

        for doc_idx, pdf_path in enumerate(pdf_paths):
            doc_name = os.path.basename(pdf_path)
            try:
                doc = fitz.open(pdf_path)
                total_pages = len(doc)
                
                chunk_counter = 0
                for page_num in range(total_pages):
                    page = doc.load_page(page_num)
                    text = page.get_text("text").replace('\n', ' ').strip()
                    text = re.sub(r'\s+', ' ', text) 
                    
                    words = text.split(' ')
                    
                    for i in range(0, len(words), chunk_word_size - overlap_words):
                        chunk_id = f"{doc_name}_p{page_num}_c{chunk_counter}"
                        
                        # DELTA FIX: Only process if the chunk isn't already in ChromaDB
                        if chunk_id not in existing_ids:
                            chunk_text = ' '.join(words[i:i + chunk_word_size])
                            if len(chunk_text) > 50: 
                                chunks.append(chunk_text)
                                metadatas.append({"doc_name": doc_name, "doc_id": pdf_path, "page": page_num})
                                ids.append(chunk_id)
                        chunk_counter += 1
                doc.close()
            except Exception as e: 
                print(f"Failed to index {doc_name}: {e}")
                
        total_chunks = len(chunks)
        if total_chunks == 0:
            if progress_callback: progress_callback("Search index is already up to date!")
            return

        batch_size = 100 
        total_batches = (total_chunks // batch_size) + (1 if total_chunks % batch_size != 0 else 0)
        
        for i in range(0, total_chunks, batch_size):
            current_batch_num = (i // batch_size) + 1
            if progress_callback: 
                progress_callback(f"Embedding and saving batch {current_batch_num} of {total_batches}...")
                
            batch_texts = chunks[i:i+batch_size]
            batch_metadatas = metadatas[i:i+batch_size]
            batch_ids = ids[i:i+batch_size]
            
            batch_embs = self.get_batch_embeddings(batch_texts)
            
            self.collection.upsert(
                documents=batch_texts,
                embeddings=batch_embs,
                metadatas=batch_metadatas,
                ids=batch_ids
            )
            
        if progress_callback:
            progress_callback("Releasing embedding model from memory...")
        self.unload_all_models()

    def sync_doc_tags(self, doc_id, current_tags):
        """Sync SQLite document tags into flat Chroma metadata keys (tag_<name>: True)."""
        if not self.collection or not doc_id:
            return

        try:
            fetched = self.collection.get(where={"doc_id": doc_id}, include=["metadatas"])
            ids = fetched.get("ids") or []
            metadatas = fetched.get("metadatas") or []

            # Backward compatibility for projects indexed before doc_id metadata existed.
            if not ids:
                fallback_name = os.path.basename(doc_id)
                fetched = self.collection.get(where={"doc_name": fallback_name}, include=["metadatas"])
                ids = fetched.get("ids") or []
                metadatas = fetched.get("metadatas") or []

            if not ids:
                return

            new_tag_keys = {}
            for tag in (current_tags or []):
                tag_name = str(tag.get("name", "")).strip()
                if tag_name:
                    new_tag_keys[f"tag_{tag_name}"] = True

            updated_metadatas = []
            for meta in metadatas:
                base_meta = dict(meta or {})
                cleaned = {k: v for k, v in base_meta.items() if not str(k).startswith("tag_")}
                cleaned.update(new_tag_keys)
                updated_metadatas.append(cleaned)

            self.collection.update(ids=ids, metadatas=updated_metadatas)
        except Exception as e:
            print(f"[System] Failed to sync tags for doc '{doc_id}': {e}")

    def query(self, question, selected_model, allowed_docs=None, callback=None, rag_enabled=True, use_agents=True, custom_system_prompt=None, existing_highlights=None, tag_filters=None):
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
            if not self.collection or self.collection.count() == 0:
                if callback: callback("Please build the search index first.")
                return ""

            try:
                search_queries = [question]
                tag_filters = [str(t).strip() for t in (tag_filters or []) if str(t).strip()]
                
                # 🔥 FIX: Build the global where_clause safely without empty or single-item $and operators
                global_conditions = []
                if allowed_docs:
                    base_names = [os.path.basename(d) for d in allowed_docs]
                    if len(base_names) == 1:
                        global_conditions.append({"doc_name": base_names[0]})
                    else:
                        global_conditions.append({"doc_name": {"$in": base_names}})
                                        
                for t in tag_filters:
                    global_conditions.append({f"tag_{t}": True})
                    
                where_clause = None
                if len(global_conditions) == 1:
                    where_clause = global_conditions[0]
                elif len(global_conditions) > 1:
                    where_clause = {"$and": global_conditions}

                if use_agents:
                    if callback: callback("@@AGENT@@🔍 Performing initial scan based on your query...")
                    
                    try:
                        sq_emb = self.get_embedding(question)
                        initial_results = self.collection.query(
                            query_embeddings=[sq_emb],
                            n_results=3, 
                            where=where_clause
                        )
                        
                        initial_context = ""
                        if initial_results.get('documents') and initial_results['documents'][0]:
                            initial_context = "\n\n".join(initial_results['documents'][0])
                        
                        if callback: callback("@@AGENT@@🤖 Analyzing context to formulate advanced search strategy...")
                        
                        search_prompt = (
                            "You are an expert researcher. A user asked the following question:\n"
                            f"USER QUERY: '{question}'\n\n"
                            "I performed an initial database search and retrieved this preliminary context:\n"
                            "--- START INITIAL CONTEXT ---\n"
                            f"{initial_context}\n"
                            "--- END INITIAL CONTEXT ---\n\n"
                            "Based on the user's query and the preliminary context, generate exactly 3 highly specific search phrases (2-6 words each) "
                            "that will help me find the most relevant and complete information across the documents to properly answer the user. Focus on key entities, core concepts, or missing details.\n"
                            "Output ONLY a bulleted list using a dash (-). Do not output any other text or reasoning.\n"
                        )
                        
                        exp_payload = {"model": selected_model, "prompt": search_prompt, "stream": False, "options": {"temperature": 0.2, "num_predict": 100, "num_ctx": 4096}}
                        try:
                            exp_resp = requests.post(f"{self.api_base}/generate", json=exp_payload, timeout=15)
                            if exp_resp.status_code == 200:
                                raw_resp = exp_resp.json().get("response", "").strip()
                                for line in raw_resp.split('\n'):
                                    clean_q = re.sub(r'^[-*0-9.\s]+', '', line).strip(' "\'')
                                    if clean_q and len(clean_q) > 3 and clean_q.lower() not in question.lower():
                                        search_queries.append(clean_q)
                        except Exception:
                            pass 
                    except Exception:
                        pass
                    
                    search_queries = list(dict.fromkeys(search_queries))[:4] 

                    if callback: 
                        clean_qs = ", ".join(f"'{q}'" for q in search_queries[1:])
                        if clean_qs:
                            callback(f"@@AGENT@@📚 Exploring deeper across project using terms: <b>{clean_qs}</b>")

                aggregated_docs = {}
                
                # Iterate through EACH allowed document individually to guarantee balanced context retrieval.
                docs_to_search = allowed_docs if allowed_docs else [None]
                
                for sq in search_queries:
                    try:
                        sq_emb = self.get_embedding(sq)
                        for doc in docs_to_search:
                            local_conditions = []
                            if doc:
                                local_conditions.append({"doc_name": os.path.basename(doc)})
                                
                            final_doc_where = None
                            if len(local_conditions) == 1:
                                final_doc_where = local_conditions[0]
                            elif len(local_conditions) > 1:
                                final_doc_where = {"$and": local_conditions}

                            results = self.collection.query(
                                query_embeddings=[sq_emb],
                                n_results=3 if use_agents else 6,
                                where=final_doc_where
                            )
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
                    except Exception as e:
                        print(f"[System] Search error for query '{sq}': {e}") # Prints to console so it no longer fails silently!
                        continue

                if not aggregated_docs:
                    err_msg = "\n[System Warning: No readable text was found in the selected documents.]\n"
                    if callback: callback(err_msg)
                    return err_msg

                sorted_docs = sorted(aggregated_docs.values(), key=lambda x: (x['doc_name'], x['page']))
                context_pieces = [f"--- DOCUMENT: {d['doc_name']} | PAGE {d['page'] + 1} ---\n{d['text']}" for d in sorted_docs]

                context = "\n\n".join(context_pieces)

                # Explicitly inject the strictly valid document names to prevent name hallucinations
                available_docs_list = list(set([d['doc_name'] for d in sorted_docs]))
                if available_docs_list:
                    highlight_rules += f"\nCRITICAL: The ONLY VALID DOCUMENT NAMES you can use for %%QUOTE are: {', '.join(available_docs_list)}\n"

            except Exception as e:
                if callback: callback(f"\n[System Error: {str(e)}]\n")
                return f"[System Error: {str(e)}]"

            if use_agents:
                system_prompt = self._format_prompt_template(
                    "RAG Agent Mode",
                    (
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
                    highlight_rules=highlight_rules,
                    context=context,
                )
            else:
                system_prompt = self._format_prompt_template(
                    "RAG Assistant Mode",
                    (
                        "You are an expert AI research assistant.\n"
                        "Provide comprehensive answers using ONLY the provided context.\n"
                        "{highlight_rules}\n\n"
                        "CONTEXT:\n{context}"
                    ),
                    highlight_rules=highlight_rules,
                    context=context,
                )
        else:
            system_prompt = custom_system_prompt or self.prompt_manager.get_prompt("General Assistant")

        payload = {
            "model": selected_model, 
            "prompt": question, 
            "system": system_prompt, 
            "stream": True, 
            "keep_alive": "60m", 
            "options": {
                "temperature": 0.1, 
                "top_p": 0.7,
                "num_ctx": 16384 # Critical fix: ensures the model reads the full context without truncation
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
        if self.audit_logger and full_response and "[System Error" not in full_response:
            # Fire and forget; the thread-safe DB method handles the rest
            self.audit_logger(question, full_response, selected_model)
        return full_response