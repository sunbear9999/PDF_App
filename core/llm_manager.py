import subprocess
import requests
import json
import fitz
import math
import os

class LocalLLMManager:
    def __init__(self):
        self.api_base = "http://localhost:11434/api"
        self.embedding_model = "nomic-embed-text"
        self.document_chunks = []
        self.document_embeddings = []
        self.ensure_server_running()

    def ensure_server_running(self):
        try:
            requests.get("http://localhost:11434/", timeout=2)
        except requests.exceptions.ConnectionError:
            subprocess.Popen(['ollama', 'serve'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def get_available_models(self):
        try:
            response = requests.get(f"{self.api_base}/tags", timeout=2)
            if response.status_code == 200:
                models = response.json().get("models", [])
                return [m["name"] for m in models if m["name"] != self.embedding_model]
        except: pass
        return ["llama3", "mistral"] 

    def get_embedding(self, text):
        payload = {"model": self.embedding_model, "prompt": text, "keep_alive": "60m"}
        response = requests.post(f"{self.api_base}/embeddings", json=payload)
        if response.status_code == 200:
            return response.json().get("embedding")
        raise Exception(f"Failed to generate embedding. Did you run 'ollama pull {self.embedding_model}'?")

    def save_index(self, filepath):
        try:
            with open(filepath, 'w') as f:
                json.dump({"chunks": self.document_chunks, "embeddings": self.document_embeddings}, f)
        except Exception as e: print("Failed to save index:", e)

    def load_index(self, filepath):
        try:
            if os.path.exists(filepath):
                with open(filepath, 'r') as f:
                    data = json.load(f)
                    self.document_chunks = data["chunks"]
                    self.document_embeddings = data["embeddings"]
                return True
        except: pass
        return False

    def index_documents(self, pdf_paths, progress_callback=None):
        self.document_chunks = []
        self.document_embeddings = []
        
        for doc_idx, pdf_path in enumerate(pdf_paths):
            doc_name = os.path.basename(pdf_path)
            try:
                doc = fitz.open(pdf_path)
                total_pages = len(doc)
                
                for page_num in range(total_pages):
                    if progress_callback: 
                        progress_callback(f"[{doc_name}] Ext. Page {page_num+1}/{total_pages}...")
                    
                    text = doc.load_page(page_num).get_text()
                    words = text.split()
                    chunk_size = 250
                    overlap = 50
                    
                    for i in range(0, max(1, len(words)), chunk_size - overlap):
                        chunk = " ".join(words[i:i + chunk_size])
                        if chunk.strip():
                            self.document_chunks.append({"text": chunk, "page": page_num, "doc_name": doc_name})
                doc.close()
            except Exception as e: print(f"Failed to index {doc_name}: {e}")
                    
        total_chunks = len(self.document_chunks)
        for i, chunk_data in enumerate(self.document_chunks):
            if progress_callback: progress_callback(f"Embedding chunk {i+1} of {total_chunks}...")
            emb = self.get_embedding(chunk_data["text"])
            self.document_embeddings.append(emb)

    def _cosine_similarity(self, v1, v2):
        dot_product = sum(a * b for a, b in zip(v1, v2))
        magnitude = math.sqrt(sum(a * a for a in v1)) * math.sqrt(sum(b * b for b in v2))
        if not magnitude: return 0
        return dot_product / magnitude

    def query(self, question, selected_model, allowed_docs=None, callback=None):
        if not self.document_chunks:
            if callback: callback("Please load and index a document first.")
            return ""

        question_emb = self.get_embedding(question)
        similarities = [self._cosine_similarity(question_emb, doc_emb) for doc_emb in self.document_embeddings]
        
        # Filter strictly by the PDFs the user checked in the UI
        valid_indices = []
        for i, chunk in enumerate(self.document_chunks):
            if not allowed_docs or chunk['doc_name'] in allowed_docs:
                valid_indices.append(i)
                
        top_indices = sorted(valid_indices, key=lambda i: similarities[i], reverse=True)[:4]
        
        context_pieces = []
        for i in top_indices:
            chunk = self.document_chunks[i]
            context_pieces.append(f"--- DOCUMENT: {chunk['doc_name']} | PAGE {chunk['page'] + 1} ---\n{chunk['text']}")
        context = "\n\n".join(context_pieces)

        system_prompt = (
            "You are an AI research assistant analyzing a set of documents. Use the provided context to answer the user's question.\n\n"
            "AUTONOMOUS HIGHLIGHTING:\n"
            "If you find specific evidence in the context that supports your answer, you can highlight it in the user's PDF and leave a note. "
            "To do this, include the following XML tag anywhere in your response:\n"
            '<mark quote="Paste the exact sentence or phrase from the context here">Your original commentary explaining WHY you highlighted this.</mark>\n\n'
            "CRITICAL RULES FOR HIGHLIGHTING:\n"
            "1. The 'quote' MUST be an exact copy-paste from the context. Do not alter a single character.\n"
            "2. DO NOT use placeholders like 'EXACT_SHORT_QUOTE'. You must put the actual context text inside the quote attribute.\n"
            "3. The text INSIDE the tags MUST be your own insightful note or summary. DO NOT just repeat the quote inside the tag.\n\n"
            f"CONTEXT:\n{context}"
        )

        payload = {"model": selected_model, "prompt": question, "system": system_prompt, "stream": True, "keep_alive": "60m"}
        full_response = ""
        try:
            with requests.post(f"{self.api_base}/generate", json=payload, stream=True) as response:
                for line in response.iter_lines():
                    if line:
                        chunk = json.loads(line)
                        txt = chunk.get("response", "")
                        full_response += txt
                        if callback: callback(txt)
        except Exception as e:
            if callback: callback(f"\n[Error: {str(e)}]")
        
        return full_response