import subprocess
import requests
import json
import fitz
import math

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
        except:
            pass
        return ["llama3", "mistral"] # Fallbacks

    def get_embedding(self, text):
        payload = {"model": self.embedding_model, "prompt": text, "keep_alive": "60m"}
        response = requests.post(f"{self.api_base}/embeddings", json=payload)
        if response.status_code == 200:
            return response.json().get("embedding")
        raise Exception(f"Failed to generate embedding. Did you run 'ollama pull {self.embedding_model}'?")

    def index_document(self, pdf_path, progress_callback=None):
        self.document_chunks = []
        self.document_embeddings = []
        
        doc = fitz.open(pdf_path)
        total_pages = len(doc)
        
        # 1. Page-Aware Extraction
        for page_num in range(total_pages):
            if progress_callback: progress_callback(f"Extracting Page {page_num+1}/{total_pages}...")
            text = doc.load_page(page_num).get_text()
            words = text.split()
            chunk_size = 250
            overlap = 50
            
            for i in range(0, max(1, len(words)), chunk_size - overlap):
                chunk = " ".join(words[i:i + chunk_size])
                if chunk.strip():
                    self.document_chunks.append({"text": chunk, "page": page_num})
        doc.close()
                    
        # 2. Embedding Generation
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

    def query(self, question, selected_model, callback=None):
        if not self.document_chunks:
            if callback: callback("Please load and index a document first.")
            return ""

        # Find top 3 relevant chunks
        question_emb = self.get_embedding(question)
        similarities = [self._cosine_similarity(question_emb, doc_emb) for doc_emb in self.document_embeddings]
        top_indices = sorted(range(len(similarities)), key=lambda i: similarities[i], reverse=True)[:3]
        
        context_pieces = []
        for i in top_indices:
            chunk = self.document_chunks[i]
            context_pieces.append(f"--- PAGE {chunk['page'] + 1} ---\n{chunk['text']}")
        context = "\n\n".join(context_pieces)

        system_prompt = (
            "You are an AI research assistant analyzing a document. "
            "Use the provided context to answer the user's question.\n\n"
            "AUTONOMOUS HIGHLIGHTING:\n"
            "If you find specific evidence in the context that perfectly supports your answer, "
            "you can highlight it in the user's PDF and leave a note. "
            "To do this, include the following XML tag anywhere in your response:\n"
            '<mark quote="EXACT_SHORT_QUOTE">Your explanation here</mark>\n'
            "Rules: The quote MUST be an exact verbatim match from the context. Keep the quote under 10 words.\n\n"
            f"CONTEXT:\n{context}"
        )

        payload = {
            "model": selected_model,
            "prompt": question,
            "system": system_prompt,
            "stream": True,
            "keep_alive": "60m"
        }

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