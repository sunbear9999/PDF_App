# core/ollama_client.py
# [REFACTOR] Extracted Ollama API client - handles all raw HTTP communication with Ollama
# Single responsibility: Raw API calls, timeouts, request formatting

import requests
import json
import subprocess
import time
import math

class OllamaClient:
    """[REFACTOR] Lightweight HTTP client for Ollama API.
    
    Handles:
    - Connection verification and server startup
    - Dynamic context window calculation
    - Raw API calls (generate, embed, pull)
    - Payload formatting with JSON mode support
    - Streaming vs non-streaming responses
    """

    def __init__(self, api_base="http://localhost:11434/api"):
        self.api_base = api_base
        self.ensure_server_running()

    def ensure_server_running(self):
        """[REFACTOR] Verify Ollama server is accessible, start if needed."""
        try:
            requests.get("http://localhost:11434/", timeout=2)
        except requests.exceptions.ConnectionError:
            print("[Ollama] Server not running, starting...")
            subprocess.Popen(['ollama', 'serve'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(2)

    def calculate_dynamic_context(self, prompt, base_ctx=2048):
        """[AI OPTIMIZATION] Calculate optimal context window based on prompt length.
        
        Estimates token count (words * 1.3) and selects nearest power of 2:
        - Short prompts (<3k tokens) → 2048 (saves VRAM)
        - Medium prompts (3k-6.5k) → 4096
        - Long prompts (6.5k-13k) → 8192
        - Very long (13k+) → 16384 (cap)
        
        Returns: Optimal num_ctx value
        """
        word_count = len(prompt.split())
        estimated_tokens = int(word_count * 1.3)
        
        context_options = [2048, 4096, 8192, 16384]
        for ctx in context_options:
            if estimated_tokens <= ctx:
                return ctx
        return 16384

    def generate(self, model, prompt, system_prompt=None, stream=False, options=None, 
                 json_mode=False, temperature=0.1):
        """[AI OPTIMIZATION] Generate text with optional JSON mode enforcement.
        
        Args:
            model: Model name (e.g., 'llama3')
            prompt: User prompt
            system_prompt: System context
            stream: Whether to stream response
            options: Dict of Ollama options (temperature, top_p, etc.)
            json_mode: If True, append "format": "json" to payload
            temperature: Response temperature (0.0=deterministic, 1.0=creative)
        
        Returns:
            Full response text or generator if streaming
        """
        if not model:
            raise ValueError("No model specified")

        payload = {
            "model": model,
            "prompt": prompt,
            "stream": stream,
            "keep_alive": "60m"
        }

        if system_prompt:
            payload["system"] = system_prompt

        # [AI OPTIMIZATION] Dynamic context window
        if not options:
            options = {}
        if "num_ctx" not in options:
            options["num_ctx"] = self.calculate_dynamic_context(prompt)
        
        # [AI OPTIMIZATION] Temperature control
        if "temperature" not in options:
            options["temperature"] = temperature

        # [AI OPTIMIZATION] JSON mode enforcement
        if json_mode:
            payload["format"] = "json"

        if options:
            payload["options"] = options

        try:
            if stream:
                return self._stream_generate(payload)
            else:
                return self._non_stream_generate(payload)
        except Exception as e:
            print(f"[Ollama] Generation failed: {e}")
            return ""

    def _stream_generate(self, payload):
        """[REFACTOR] Stream response from Ollama."""
        try:
            with requests.post(f"{self.api_base}/generate", json=payload, stream=True, 
                             timeout=(15, 600)) as response:
                if response.status_code != 200:
                    raise Exception(response.text)
                
                for line in response.iter_lines():
                    if line:
                        chunk = json.loads(line)
                        if "error" in chunk:
                            raise Exception(chunk["error"])
                        yield chunk.get("response", "")
        except requests.exceptions.ReadTimeout:
            yield "\n[Generation Error: The AI took too long to respond. Hardware limits exceeded.]"
        except Exception as e:
            yield f"\n[Generation Error: {str(e)}]"

    def _non_stream_generate(self, payload):
        """[REFACTOR] Non-streaming response from Ollama."""
        response = requests.post(f"{self.api_base}/generate", json=payload, timeout=(15, 600))
        if response.status_code == 200:
            return response.json().get("response", "")
        raise Exception(f"Ollama API Error ({response.status_code}): {response.text}")

    def embed(self, model, text):
        """[REFACTOR] Get embedding for a single text."""
        payload = {"model": model, "prompt": text, "keep_alive": "60m"}
        try:
            response = requests.post(f"{self.api_base}/embeddings", json=payload, 
                                   timeout=(10, 300))
            if response.status_code == 200:
                return response.json().get("embedding")
            raise Exception(f"Server returned {response.status_code}: {response.text}")
        except Exception as e:
            raise Exception(f"Embedding failed: {str(e)}")

    def embed_batch(self, model, texts):
        """[REFACTOR] Batch embedding - more efficient than sequential."""
        payload = {"model": model, "input": texts, "keep_alive": "60m"}
        try:
            response = requests.post(f"{self.api_base}/embed", json=payload, 
                                   timeout=(15, 600))
            if response.status_code == 200:
                return response.json().get("embeddings")
        except Exception as e:
            print(f"[Ollama] Batch embedding failed, falling back to sequential: {e}")
        
        # Fallback to sequential if batch fails
        return [self.embed(model, t) for t in texts]

    def pull_model(self, model_name, progress_callback=None):
        """[REFACTOR] Pull a model from Ollama registry."""
        try:
            response = requests.get(f"{self.api_base}/tags", timeout=3)
            if response.status_code == 200:
                models = response.json().get("models", [])
                model_names = [m["name"] for m in models]
                
                if model_name not in model_names and f"{model_name}:latest" not in model_names:
                    if progress_callback:
                        progress_callback(f"Downloading model '{model_name}'... (This takes a few minutes)")
                    
                    requests.post(f"{self.api_base}/pull", json={"name": model_name}, 
                                timeout=(10, 1800))
        except Exception as e:
            print(f"[Ollama] Warning: Could not pull model: {e}")

    def preload_model(self, model_name):
        """[REFACTOR] Preload model into VRAM."""
        if not model_name:
            return
        try:
            payload = {"model": model_name, "keep_alive": "60m"}
            requests.post(f"{self.api_base}/generate", json=payload, timeout=(15, 600))
            print(f"[Ollama] Successfully preloaded {model_name} into memory.")
        except Exception as e:
            print(f"[Ollama] Failed to preload model: {e}")

    def unload_all_models(self):
        """[REFACTOR] Free VRAM by unloading all active models."""
        try:
            resp = requests.get(f"{self.api_base}/ps", timeout=3)
            if resp.status_code == 200:
                models = resp.json().get("models", [])
                for m in models:
                    name = m.get("name")
                    requests.post(f"{self.api_base}/generate", json={"model": name, "keep_alive": 0}, 
                                timeout=3)
        except Exception as e:
            print(f"[Ollama] Warning during model unload: {e}")

    def list_models(self):
        """[REFACTOR] Get available models."""
        try:
            response = requests.get(f"{self.api_base}/tags", timeout=3)
            if response.status_code == 200:
                models = response.json().get("models", [])
                return [m["name"] for m in models]
        except Exception as e:
            print(f"[Ollama] Error fetching models: {e}")
        return ["qwen2.5:7b", "llama3", "mistral"]
