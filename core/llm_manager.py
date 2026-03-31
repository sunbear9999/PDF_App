import subprocess
import requests
import json
import os
import math

class LocalLLMManager:
    def __init__(self):
        self.api_base = "http://localhost:11434/api"
        self.embedding_model = "nomic-embed-text"
        self.document_chunks = []
        self.document_embeddings = []
        self.ensure_server_running()

    def ensure_server_running(self):
        """Silently starts the Ollama background server if it isn't already running."""
        try:
            requests.get("http://localhost:11434/", timeout=2)
        except requests.exceptions.ConnectionError:
            subprocess.Popen(['ollama', 'serve'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def get_available_models(self):
        """Fetches a list of models you have installed in Ollama."""
        try:
            response = requests.get(f"{self.api_base}/tags")
            if response.status_code == 200:
                models = response.json().get("models", [])
                return [m["name"] for m in models if m["name"] != self.embedding_model]
            return []
        except:
            return []

    def chunk_text(self, text, chunk_size=400, overlap=100):
        """Splits a massive document into smaller overlapping paragraphs for the LLM."""
        words = text.split()
        chunks = []
        for i in range(0, len(words), chunk_size - overlap):
            chunk = " ".join(words[i:i + chunk_size])
            chunks.append(chunk)
        return chunks

    def get_embedding(self, text):
        """Converts text into a mathematical vector using Ollama."""
        payload = {"model": self.embedding_model, "prompt": text, "keep_alive": "60m"}
        response = requests.post(f"{self.api_base}/embeddings", json=payload)
        if response.status_code == 200:
            return response.json().get("embedding")
        raise Exception(f"Failed to generate embedding. Did you run 'ollama pull {self.embedding_model}'?")

    def index_document(self, text, progress_callback=None):
        """Processes the PDF text so it can be searched instantly."""
        self.document_chunks = self.chunk_text(text)
        self.document_embeddings = []
        
        total = len(self.document_chunks)
        for i, chunk in enumerate(self.document_chunks):
            if progress_callback:
                progress_callback(f"Indexing chunk {i+1} of {total}...")
            
            emb = self.get_embedding(chunk)
            self.document_embeddings.append(emb)

    def _cosine_similarity(self, v1, v2):
        """Pure Python math to find the most relevant text chunks."""
        dot_product = sum(a * b for a, b in zip(v1, v2))
        magnitude = math.sqrt(sum(a * a for a in v1)) * math.sqrt(sum(b * b for b in v2))
        if not magnitude: return 0
        return dot_product / magnitude

    def query(self, question, selected_model, callback=None):
        """Searches the document and streams the LLM response back to the UI."""
        if not self.document_chunks:
            # FIX: Replaced 'yield' with the proper callback mechanism
            if callback:
                callback("Please load a document first.")
            return

        # 1. Find the most relevant chunks of the PDF
        question_emb = self.get_embedding(question)
        similarities = [self._cosine_similarity(question_emb, doc_emb) for doc_emb in self.document_embeddings]
        
        # Get the top 3 most relevant chunks
        top_indices = sorted(range(len(similarities)), key=lambda i: similarities[i], reverse=True)[:3]
        context = "\n\n".join([self.document_chunks[i] for i in top_indices])

        # 2. Build the Prompt
        system_prompt = (
            "You are a helpful assistant analyzing a document. "
            "Use the provided context to answer the user's question. "
            "If the answer is not in the context, say 'I cannot find the answer in the provided document.'\n\n"
            f"CONTEXT:\n{context}"
        )

        payload = {
            "model": selected_model,
            "prompt": question,
            "system": system_prompt,
            "stream": True,
            "keep_alive": "60m"
        }

        # 3. Stream the response back
        with requests.post(f"{self.api_base}/generate", json=payload, stream=True) as response:
            for line in response.iter_lines():
                if line:
                    chunk = json.loads(line)
                    if callback:
                        callback(chunk.get("response", ""))