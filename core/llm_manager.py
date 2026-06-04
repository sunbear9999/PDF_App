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
        self.embedding_model = "nomic-embed-text"
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
   
    def query_by_raw_embedding(self, embedding_vector, n_results=5, allowed_docs=None,tag_filters=None):
        """Directly queries ChromaDB using a pre-calculated mathematical vector."""
       
        if not self.collection or self.collection.count() == 0:
            return None

       
        where_clause = {}
        tag_filters = [str(t).strip() for t in (tag_filters or []) if str(t).strip()]
        
        if allowed_docs:
            base_names = [os.path.basename(d) for d in allowed_docs]
            if len(base_names) == 1:
                where_clause["doc_name"] = base_names[0]
            else:
                where_clause["doc_name"] = {"$in": base_names}
                                
        for t in tag_filters:
            where_clause[f"tag_{t}"] = True
            
        if not where_clause:
            where_clause = None

        try:
            results = self.collection.query(
                query_embeddings=[embedding_vector],
                n_results=n_results,
                where=where_clause
            )
            return results
        except Exception as e:
            print(f"[System] Centroid query failed: {e}")
            return None

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
            response = requests.post(f"{self.api_base}/generate", json=payload, timeout=(15, 600))
            
            # Actually check if Ollama succeeded
            if response.status_code == 200:
                print(f"[System] Successfully preloaded {model_name} into memory.")
            else:
                print(f"[System] Failed to preload model: Ollama returned {response.status_code} - {response.text}")
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
        payload = {
            "model": self.embedding_model, 
            "prompt": text, 
            "keep_alive": "60m",
            "options": {"num_ctx": 8192} # Force max context window
        }
        try:
            response = requests.post(f"{self.api_base}/embeddings", json=payload, timeout=(10, 300))
            if response.status_code == 200:
                return response.json().get("embedding")
            raise Exception(f"Server returned {response.status_code}: {response.text}")
        except Exception as e:
            raise Exception(f"Embedding failed. Error: {str(e)}")

    def get_batch_embeddings(self, texts):
        payload = {
            "model": self.embedding_model, 
            "input": texts, 
            "keep_alive": "60m",
            "options": {"num_ctx": 8192} # Force max context window
        }
        try:
            response = requests.post(f"{self.api_base}/embed", json=payload, timeout=(15, 600))
            if response.status_code == 200:
                return response.json().get("embeddings")
        except Exception as e:
            print(f"[System] Batch embedding failed, falling back to sequential: {e}")
            
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
                    # Clean the text but don't rely on spaces for chunking
                    text = page.get_text("text").replace('\n', ' ').strip()
                    text = re.sub(r'\s+', ' ', text) 
                    
                    # Hard character limits (1500 chars is roughly 300 tokens, 100% safe)
                    char_limit = 1500
                    overlap = 250
                    
                    for i in range(0, len(text), char_limit - overlap):
                        chunk_id = f"{doc_name}_p{page_num}_c{chunk_counter}"
                        
                        if chunk_id not in existing_ids:
                            chunk_text = text[i:i + char_limit].strip()
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

        batch_size = 50 # Dropped from 100 to prevent cumulative batch overflows
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
        """Sync SQLite document tags into flat Chroma metadata keys. Explicitly overwrites removed tags."""
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
                cleaned = {}
                
                # 🔥 FIX 2: Explicitly set all old tags to False. 
                # Because ChromaDB merges metadata, omitting a key doesn't delete it.
                # Setting it to False ensures it is completely overwritten and won't trigger false positives.
                for k, v in base_meta.items():
                    if str(k).startswith("tag_"):
                        cleaned[k] = False 
                    else:
                        cleaned[k] = v
                        
                cleaned.update(new_tag_keys)
                updated_metadatas.append(cleaned)

            self.collection.update(ids=ids, metadatas=updated_metadatas)
        except Exception as e:
            print(f"[System] Failed to sync tags for doc '{doc_id}': {e}")
    

    def query(self, question, selected_model, allowed_docs=None, callback=None, rag_enabled=False, custom_system_prompt=None, existing_highlights=None, tag_filters=None, abort_event=None, **kwargs):
        """A lean, standard inference function. Advanced agent routing is now handled by MasterRunner Blueprints."""
         
        # --- THE FIX: Filter embedding models out of the fallback array ---
        try:
            resp = requests.get(f"{self.api_base}/tags", timeout=2)
            if resp.status_code == 200:
                # Filter out embedding models entirely from text generation queries
                installed_models = [
                    m["name"] for m in resp.json().get("models", [])
                    if m["name"] != self.embedding_model and not m["name"].startswith(f"{self.embedding_model}:")
                ]
                if installed_models and selected_model not in installed_models:
                    old_model = selected_model
                    partial_match = next((m for m in installed_models if old_model and old_model.lower().strip() in m.lower()), None)
                    selected_model = partial_match if partial_match else installed_models[0]
        except Exception as e:
            print(f"[LLM Manager] Warning: Could not verify installed models: {e}")

        context = ""
        system_prompt = ""
        
        if not selected_model:
            err = "\n[Generation Error: No AI model selected.]"
            if callback: callback(err)
            return err

        # Legacy Highlight Rules for standard single-pass queries
        
        # --- FETCH RAG DATA (Standard Single-Pass Only) ---
        if rag_enabled:
            if not self.collection or self.collection.count() == 0:
                if callback: callback("Please build the search index first.")
                return ""

            try:
                tag_filters = [str(t).strip() for t in (tag_filters or []) if str(t).strip()]
                
                top_level_and = []
                if allowed_docs:
                    base_names = [os.path.basename(d) for d in allowed_docs]
                    if len(base_names) == 1:
                        top_level_and.append({"doc_name": base_names[0]})
                    else:
                        top_level_and.append({"doc_name": {"$in": base_names}})
                                        
                if tag_filters:
                    tag_conditions = [{f"tag_{t}": True} for t in tag_filters]
                    tag_logic = kwargs.get("tag_logic", "AND") 
                    
                    if tag_logic == "OR":
                        if len(tag_conditions) > 1:
                            top_level_and.append({"$or": tag_conditions})
                        else:
                            top_level_and.append(tag_conditions[0])
                    else:
                        top_level_and.extend(tag_conditions) 
                    
                where_clause = None
                if len(top_level_and) == 1:
                    where_clause = top_level_and[0]
                elif len(top_level_and) > 1:
                    where_clause = {"$and": top_level_and}

                sq_emb = self.get_embedding(question)
                results = self.collection.query(
                    query_embeddings=[sq_emb],
                    n_results=10, 
                    where=where_clause
                )

                aggregated_docs = {}
                if results.get('documents') and results['documents'][0]:
                    for idx, doc_text in enumerate(results['documents'][0]):
                        doc_id = results['ids'][0][idx]
                        meta = results['metadatas'][0][idx]
                        aggregated_docs[doc_id] = {
                            "text": doc_text,
                            "doc_name": meta['doc_name'],
                            "page": meta['page']
                        }

                if not aggregated_docs:
                    err_msg = "\n[System Warning: No readable text was found in the selected documents.]\n"
                    if callback: callback(err_msg)
                    return err_msg

                sorted_docs = sorted(aggregated_docs.values(), key=lambda x: (x['doc_name'], x['page']))
                context_pieces = [f"--- DOCUMENT: {d['doc_name']} | PAGE {d['page'] + 1} ---\n{d['text']}" for d in sorted_docs]
                context += "\n\n".join(context_pieces) 

            except Exception as e:
                if callback: callback(f"\n[System Error: {str(e)}]\n")
                return f"[System Error: {str(e)}]"

        # --- PROMPT COMPILATION ---
        if custom_system_prompt:
            # We completely trust the Blueprint's system prompt now. No forced injections.
            system_prompt = custom_system_prompt.replace("{context}", context)
        else:
            system_prompt = self._format_prompt_template(
                "RAG Assistant Mode",
                "You are a helpful assistant. Use this context: {context}", 
                context=context
            )
        # --- STREAMING EXECUTION ---
        payload = {
            "model": selected_model, 
            "prompt": question, 
            "system": system_prompt, 
            "stream": True, 
            "keep_alive": "60m", 
            "options": {
                "temperature": kwargs.get("temperature", 0.1), 
                "top_p": kwargs.get("top_p", 0.7),
                "num_ctx": kwargs.get("num_ctx", 16384) 
            }
        }
        
        if "num_predict" in kwargs:
            payload["options"]["num_predict"] = kwargs["num_predict"]
            
        if kwargs.get("json_mode"):
            payload["format"] = "json"
            
        full_response = ""
        try:
            with requests.post(f"{self.api_base}/generate", json=payload, stream=True, timeout=(15, 600)) as response:
                if response.status_code != 200:
                    # EXTRACT THE REAL ERROR: Read Ollama's explanation
                    err_detail = response.text
                    try:
                        err_detail = response.json().get("error", response.text)
                    except: pass
                    raise Exception(f"Ollama Error ({response.status_code}): {err_detail}")

                for line in response.iter_lines():
                    # --- THE KILL SWITCH ---
                    if abort_event and abort_event.is_set():
                        abort_msg = "\n[Process Aborted by User]"
                        if callback: callback(abort_msg)
                        full_response += abort_msg
                        break

                    if line:
                        chunk = json.loads(line)
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
            self.audit_logger(question, full_response, selected_model)
            
        return full_response