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
        try:
            response = requests.post(f"{self.api_base}/embeddings", json=payload, timeout=120)
            if response.status_code == 200:
                return response.json().get("embedding")
            raise Exception(f"Server returned {response.status_code}: {response.text}")
        except Exception as e:
            raise Exception(f"Embedding failed. Error: {str(e)}")

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

    def query(self, question, selected_model, allowed_docs=None, callback=None, rag_enabled=True):
        context = ""
        system_prompt = ""

        if rag_enabled:
            if not self.document_chunks:
                if callback: callback("Please load and index a document first.")
                return ""

            try:
                question_emb = self.get_embedding(question)
                similarities = [self._cosine_similarity(question_emb, doc_emb) for doc_emb in self.document_embeddings]
                
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

            except Exception as e:
                if callback: callback(f"\n[System Error: {str(e)}]")
                return f"[System Error: {str(e)}]"

            system_prompt = (
                "You are an AI research assistant analyzing a set of documents.\n"
                "Use the provided context to answer the user's question.\n\n"
                "AUTONOMOUS HIGHLIGHTING:\n"
                "When you find evidence in the context, you MUST highlight it in the user's PDF.\n"
                "To highlight, you MUST use this exact XML format for EACH piece of evidence:\n"
                "<highlight>\n"
                "<doc>exact filename from context</doc>\n"
                "<quote>exact continuous sentence or phrase from context</quote>\n"
                "<note>Your commentary or explanation here</note>\n"
                "</highlight>\n\n"
                "CRITICAL RULES:\n"
                "1. The text inside <quote> MUST be an exact copy-paste of a continuous phrase (5-15 words) from the CONTEXT.\n"
                "2. You MUST create MULTIPLE <highlight> blocks if the evidence spans multiple documents or locations.\n"
                "3. Put all your regular text/commentary inside the <note> tags.\n\n"
                f"CONTEXT:\n{context}"
            )
        else:
            # Bypass RAG completely for structural/system queries
            system_prompt = "You are a highly capable AI assistant interacting with a user's workspace software. Follow their instructions exactly."

        payload = {"model": selected_model, "prompt": question, "system": system_prompt, "stream": True, "keep_alive": "60m"}
        full_response = ""
        try:
            with requests.post(f"{self.api_base}/generate", json=payload, stream=True, timeout=120) as response:
                for line in response.iter_lines():
                    if line:
                        chunk = json.loads(line)
                        txt = chunk.get("response", "")
                        full_response += txt
                        if callback: callback(txt)
        except Exception as e:
            if callback: callback(f"\n[Generation Error: {str(e)}]")
            full_response += f"\n[Generation Error: {str(e)}]"
        
        return full_response