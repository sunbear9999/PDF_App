# core/llm_manager.py
import subprocess
import requests
import json
import fitz
import os
import time
import chromadb
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
            requests.get("http://localhost:11434/", timeout=1)
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
            requests.post(f"{self.api_base}/generate", json=payload, timeout=10)
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
            response = requests.post(f"{self.api_base}/embeddings", json=payload, timeout=120)
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
                    blocks = page.get_text("blocks") 
                    
                    for block in blocks:
                        if block[6] == 0: 
                            text = block[4].strip()
                            if len(text) > 50: 
                                chunks.append(text)
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

    def query(self, question, selected_model, allowed_docs=None, callback=None, rag_enabled=True):
        context = ""
        system_prompt = ""

        if not selected_model:
            err = "\n[Generation Error: No AI model selected. Please select a model in the LLM Chat tab.]"
            if callback: callback(err)
            return err

        if rag_enabled:
            if not self.collection or self.collection.count() == 0:
                if callback: callback("Please build the search index first.")
                return ""

            try:
                question_emb = self.get_embedding(question)
                
                where_clause = None
                if allowed_docs:
                    if len(allowed_docs) == 1:
                        where_clause = {"doc_name": allowed_docs[0]}
                    else:
                        where_clause = {"doc_name": {"$in": allowed_docs}}

                results = self.collection.query(
                    query_embeddings=[question_emb],
                    n_results=10, 
                    where=where_clause
                )
                
                # --- NEW SAFETY CHECK: ABORT IF NO CONTEXT IS FOUND ---
                if not results.get('documents') or not results['documents'][0]:
                    err_msg = "\n[System Warning: No readable text was found in the selected document. If you recently ran OCR on a scanned PDF, you must click 'Build / Rebuild Search Index' to update the AI's memory.]"
                    if callback: callback(err_msg)
                    return err_msg

                context_pieces = []
                for idx, doc_text in enumerate(results['documents'][0]):
                    meta = results['metadatas'][0][idx]
                    context_pieces.append(f"--- DOCUMENT: {meta['doc_name']} | PAGE {meta['page'] + 1} ---\n{doc_text}")
                
                context = "\n\n".join(context_pieces)

            except Exception as e:
                if callback: callback(f"\n[System Error: {str(e)}]")
                return f"[System Error: {str(e)}]"

            # --- UPDATED PROMPT: STRICTLY FORBIDS HALLUCINATION ---
            system_prompt = (
                "You are an expert AI research assistant analyzing documents.\n"
                "Provide comprehensive, intelligent, and deeply analytical answers using ONLY the provided context.\n"
                "CRITICAL: If the context doesn't contain the exact answer, you MUST state 'I cannot answer this based on the provided documents.' DO NOT invent hypothetical examples, facts, or documents under any circumstances.\n\n"
                "--- AUTONOMOUS HIGHLIGHTING ---\n"
                "When you cite specific evidence, you MUST highlight it in the user's UI using EXACTLY this XML format:\n"
                "<highlight>\n"
                "<doc>Exact Document Name Here</doc>\n"
                "<quote>Exact continuous phrase from the text (5-15 words)</quote>\n"
                "<note>Your commentary on why this is relevant</note>\n"
                "</highlight>\n\n"
                "CRITICAL RULES for highlighting:\n"
                "1. NO markdown formatting around the XML. Output the raw tags.\n"
                "2. ALL THREE inner tags (<doc>, <quote>, <note>) MUST be present inside the <highlight> block.\n"
                "3. Create a separate <highlight> block for EACH piece of evidence. Do not bundle multiple quotes in one tag.\n\n"
                f"CONTEXT:\n{context}"
            )
        else:
            system_prompt = "You are a highly capable AI assistant interacting with a user's workspace software. Follow their instructions exactly."

        # Reduced temperature to 0.0 to completely eliminate creative writing/hallucinations
        payload = {"model": selected_model, "prompt": question, "system": system_prompt, "stream": True, "keep_alive": "60m", "options": {"temperature": 0.0, "top_p": 0.8}}
        
        full_response = ""
        try:
            with requests.post(f"{self.api_base}/generate", json=payload, stream=True, timeout=120) as response:
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
        except Exception as e:
            err = f"\n[Generation Error: {str(e)}]"
            if callback: callback(err)
            full_response += err
        
        return full_response