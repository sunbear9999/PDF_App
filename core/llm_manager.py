# core/llm_manager.py
import subprocess
import requests
import json
import fitz
import os
import time
import chromadb
import re
from chromadb.config import Settings

class LocalLLMManager:
    def __init__(self):
        self.api_base = "http://localhost:11434/api"
        self.embedding_model = "nomic-embed-text"
        self.chroma_client = None
        self.collection = None
        self.ensure_server_running()

    def ensure_server_running(self):
        try:
            requests.get("http://localhost:11434/", timeout=2)
        except requests.exceptions.ConnectionError:
            subprocess.Popen(['ollama', 'serve'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(2) 

    def set_project_database(self, project_filepath):
        if not project_filepath:
            self.collection = None
            return
            
        db_path = project_filepath + "_chroma_db"
        os.makedirs(db_path, exist_ok=True)
        
        self.chroma_client = chromadb.PersistentClient(path=db_path, settings=Settings(anonymized_telemetry=False))
        self.collection = self.chroma_client.get_or_create_collection(name="pdf_workspace")

    def preload_model(self, model_name="llama3"):
        if not model_name: return
        try:
            payload = {"model": model_name, "keep_alive": "60m"}
            requests.post(f"{self.api_base}/generate", json=payload, timeout=60)
            print(f"[System] Successfully preloaded {model_name} into memory.")
        except Exception as e:
            print(f"[System] Failed to preload model: {e}")

    def get_available_models(self):
        try:
            response = requests.get(f"{self.api_base}/tags", timeout=3)
            if response.status_code == 200:
                models = response.json().get("models", [])
                found_models = [m["name"] for m in models if m["name"] != self.embedding_model]
                if found_models:
                    return found_models
        except Exception as e:
            print(f"[System] Error fetching models: {e}")

        return ["qwen2.5:7b", "llama3", "mistral"] 

    def get_embedding(self, text):
        payload = {"model": self.embedding_model, "prompt": text, "keep_alive": "60m"}
        try:
            response = requests.post(f"{self.api_base}/embeddings", json=payload, timeout=(10, 300))
            if response.status_code == 200:
                return response.json().get("embedding")
            raise Exception(f"Server returned {response.status_code}: {response.text}")
        except Exception as e:
            raise Exception(f"Embedding failed. Error: {str(e)}")

    def index_documents(self, pdf_paths, progress_callback=None):
        if not self.collection:
            raise Exception("Vector Database not initialized. Please save the project first.")

        existing_ids = self.collection.get()["ids"]
        if existing_ids:
            self.collection.delete(ids=existing_ids)
        
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
                    if progress_callback: 
                        progress_callback(f"[{doc_name}] Extracting Page {page_num+1}/{total_pages}...")
                    
                    page = doc.load_page(page_num)
                    text = page.get_text("text").replace('\n', ' ').strip()
                    text = re.sub(r'\s+', ' ', text) 
                    
                    words = text.split(' ')
                    
                    for i in range(0, len(words), chunk_word_size - overlap_words):
                        chunk_text = ' '.join(words[i:i + chunk_word_size])
                        if len(chunk_text) > 50: 
                            chunks.append(chunk_text)
                            metadatas.append({"doc_name": doc_name, "page": page_num})
                            ids.append(f"{doc_name}_p{page_num}_c{chunk_counter}")
                            chunk_counter += 1
                doc.close()
            except Exception as e: 
                print(f"Failed to index {doc_name}: {e}")
                    
        total_chunks = len(chunks)
        batch_size = 50 
        
        for i in range(0, total_chunks, batch_size):
            batch_texts = chunks[i:i+batch_size]
            batch_metadatas = metadatas[i:i+batch_size]
            batch_ids = ids[i:i+batch_size]
            
            if progress_callback: 
                progress_callback(f"Embedding batch {i//batch_size + 1} of {(total_chunks//batch_size) + 1}...")
                
            batch_embeddings = [self.get_embedding(text) for text in batch_texts]
            
            self.collection.upsert(
                documents=batch_texts,
                embeddings=batch_embeddings,
                metadatas=batch_metadatas,
                ids=batch_ids
            )

    def query(self, question, selected_model, allowed_docs=None, callback=None, rag_enabled=True, use_agents=True):
        context = ""
        system_prompt = ""

        if not selected_model:
            err = "\n[Generation Error: No AI model selected.]"
            if callback: callback(err)
            return err

        if rag_enabled:
            if not self.collection or self.collection.count() == 0:
                if callback: callback("Please build the search index first.")
                return ""

            try:
                search_queries = [question]
                
                if use_agents:
                    if callback: callback("\n[🤖 Agent Planner] Formulating search strategy...\n")
                    
                    search_prompt = (
                        "You are an NLP keyword extractor. Ignore all action verbs in the user's prompt. "
                        "Identify the core factual subjects and generate exactly 2 short search phrases (2-4 words each).\n"
                        "Output ONLY a bulleted list using a dash (-). Do not output any other text.\n\n"
                        "User: Highlight quotes about the foundation of the Roman empire\n"
                        "- foundation of Rome\n"
                        "- roman empire origins\n\n"
                        f"User: {question}\n"
                    )
                    
                    exp_payload = {"model": selected_model, "prompt": search_prompt, "stream": False, "options": {"temperature": 0.1, "num_predict": 40}}
                    try:
                        exp_resp = requests.post(f"{self.api_base}/generate", json=exp_payload, timeout=10)
                        if exp_resp.status_code == 200:
                            raw_resp = exp_resp.json().get("response", "").strip()
                            for line in raw_resp.split('\n'):
                                clean_q = re.sub(r'^[-*0-9.\s]+', '', line).strip(' "\'')
                                if clean_q and len(clean_q) > 3 and clean_q.lower() not in question.lower():
                                    search_queries.append(clean_q)
                    except Exception:
                        pass 
                    
                    search_queries = list(dict.fromkeys(search_queries))[:3] 

                    if callback: 
                        clean_qs = " | ".join(f"'{q}'" for q in search_queries)
                        callback(f"[🔍 Agent Retriever] Scanning knowledge base: {clean_qs}\n")

                where_clause = None
                if allowed_docs:
                    if len(allowed_docs) == 1:
                        where_clause = {"doc_name": allowed_docs[0]}
                    else:
                        where_clause = {"doc_name": {"$in": allowed_docs}}

                aggregated_docs = {}
                
                for sq in search_queries:
                    try:
                        sq_emb = self.get_embedding(sq)
                        results = self.collection.query(
                            query_embeddings=[sq_emb],
                            n_results=7 if use_agents else 12, 
                            where=where_clause
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
                    except Exception:
                        continue

                if not aggregated_docs:
                    err_msg = "\n[System Warning: No readable text was found in the selected documents.]\n"
                    if callback: callback(err_msg)
                    return err_msg

                sorted_docs = sorted(aggregated_docs.values(), key=lambda x: (x['doc_name'], x['page']))
                context_pieces = [f"--- DOCUMENT: {d['doc_name']} | PAGE {d['page'] + 1} ---\n{d['text']}" for d in sorted_docs]

                context = "\n\n".join(context_pieces)
                if use_agents and callback: callback("[🧠 Agent Synthesizer] Analyzing gathered context...\n\n")

            except Exception as e:
                if callback: callback(f"\n[System Error: {str(e)}]\n")
                return f"[System Error: {str(e)}]"

            # NEW: More robust rules preventing the LLM from appending extra symbols or messing up the extraction strings
            highlight_rules = (
                "--- AUTONOMOUS HIGHLIGHTING ---\n"
                "To highlight a quote, you MUST use this exact single-line format at the VERY END of your response. Separate the three parts with a pipe (|).\n\n"
                "%%QUOTE | Document_Name.pdf | The exact phrase from the text | Your explanation\n\n"
                "CRITICAL RULES:\n"
                "1. Start the line EXACTLY with %%QUOTE | \n"
                "2. DO NOT use closing tags. The highlight ends at the end of the line.\n"
                "3. DO NOT wrap the quote in quotation marks unless they are in the original text.\n"
                "4. DO NOT REPEAT YOURSELF. Find MULTIPLE, DISTINCT quotes.\n"
            )

            if use_agents:
                system_prompt = (
                    "You are an expert AI research agent.\n"
                    "Provide comprehensive, highly detailed answers using ONLY the provided context.\n"
                    "CRITICAL: Follow this exact structure to simulate your thought process. Do NOT deviate:\n\n"
                    "--- AGENT REASONING ---\n"
                    "(Write your step-by-step thoughts here. Analyze the context and plan your answer.)\n\n"
                    "--- FINAL ANSWER ---\n"
                    "(Provide your final answer here. DO NOT put %%QUOTE lines in this section.)\n\n"
                    f"{highlight_rules}"
                    f"CONTEXT:\n{context}"
                )
            else:
                system_prompt = (
                    "You are an expert AI research assistant.\n"
                    "Provide comprehensive answers using ONLY the provided context.\n"
                    f"{highlight_rules}"
                    f"CONTEXT:\n{context}"
                )
        else:
            system_prompt = "You are an intelligent AI assistant interacting with a user's workspace software. Follow their instructions exactly."

        payload = {
            "model": selected_model, 
            "prompt": question, 
            "system": system_prompt, 
            "stream": True, 
            "keep_alive": "60m", 
            "options": {"temperature": 0.2 if use_agents else 0.0, "top_p": 0.9}
        }
        
        full_response = ""
        try:
            with requests.post(f"{self.api_base}/generate", json=payload, stream=True, timeout=(10, 300)) as response:
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