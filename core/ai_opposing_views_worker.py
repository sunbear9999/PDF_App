import json
import re
import requests
from PySide6.QtCore import QThread, Signal

class AIOpposingViewsWorker(QThread):
    progress = Signal(str)
    finished = Signal(list, str) 

    # --- Added audit_logger to the initialization ---
    def __init__(self, api_base, embed_model, active_chat_model, target_quote, documents, metadatas, search_mode="opposing", audit_logger=None, parent=None):
        super().__init__(parent)
        self.api_base = api_base
        self.embed_model = embed_model
        self.active_chat_model = active_chat_model
        self.fast_model = "llama3.2" 
        self.target_quote = target_quote
        self.documents = documents
        self.metadatas = metadatas
        self.search_mode = search_mode
        self.audit_logger = audit_logger # Save the reference

    def run(self):
        try:
            self.progress.emit("🧹 Freeing up VRAM...")
            
            try:
                ps_resp = requests.get(f"{self.api_base}/ps", timeout=5)
                if ps_resp.status_code == 200:
                    running_models = ps_resp.json().get("models", [])
                    for m in running_models:
                        model_tag = m.get("name")
                        if model_tag:
                            is_fast = self.fast_model in model_tag or model_tag in self.fast_model
                            is_embed = self.embed_model in model_tag or model_tag in self.embed_model
                            if not (is_fast or is_embed):
                                requests.post(
                                    f"{self.api_base}/generate", 
                                    json={"model": model_tag, "keep_alive": 0},
                                    timeout=5
                                )
            except Exception as e:
                print(f"[Worker Warning] Model unload failed (continuing anyway): {e}")

            scored_matches = []
            total_docs = len(self.documents)
            
            if self.search_mode == "opposing":
                system_prompt = (
                    "You are a strict logical scoring engine. "
                    "Rate how much the EXCERPT CONTRADICTS or OPPOSES the TARGET STATEMENT. "
                    "1 = Completely agrees or supports. "
                    "5 = Unrelated or neutral. "
                    "10 = Direct logical contradiction or strong counter-argument. "
                    "Respond ONLY with a single integer between 1 and 10. No text, no explanation."
                )
            else:
                system_prompt = "..." 

            with requests.Session() as session:
                for i, doc_text in enumerate(self.documents):
                    self.progress.emit(f"⚖️ Scoring excerpt {i + 1} of {total_docs}...")
                    
                    clean_doc = doc_text.replace('\n', ' ').strip()
                    prompt = f"TARGET STATEMENT: '{self.target_quote}'\n\nEXCERPT:\n{clean_doc}"
                    
                    payload = {
                        "model": self.fast_model,
                        "prompt": prompt,
                        "system": system_prompt,
                        "stream": False,
                        "options": {"temperature": 0.0, "num_ctx": 1024} 
                    }
                    
                    try:
                        resp = session.post(f"{self.api_base}/generate", json=payload, timeout=15)
                        if resp.status_code == 200:
                            raw_output = resp.json().get("response", "").strip()
                            
                            # --- CRITICAL ADDITION: Send to the LLM Log ---
                            if self.audit_logger:
                                try:
                                    # Log the interaction exactly like the main chat does
                                    self.audit_logger(prompt, raw_output, self.fast_model)
                                except Exception as log_e:
                                    print(f"[Audit Log Error] Failed to log background task: {log_e}")
                            # ----------------------------------------------
                                    
                            match = re.search(r'\b(10|[1-9])\b', raw_output)
                            if match:
                                score = int(match.group(1))
                                if score >= 6: 
                                    scored_matches.append({
                                        "score": score,
                                        "text": clean_doc,
                                        "doc_name": self.metadatas[i].get("doc_name", "Unknown Document"),
                                        "page": self.metadatas[i].get("page", 0)
                                    })
                    except Exception as loop_e:
                        print(f"[Worker Warning] Failed to score excerpt {i}: {loop_e}")
                        continue 

            if not scored_matches:
                self.finished.emit([], "")
                return
                
            scored_matches.sort(key=lambda x: x['score'], reverse=True)
            final_matches = scored_matches[:5]
            
            clean_matches = [{"text": m["text"], "doc_name": m["doc_name"], "page": m["page"]} for m in final_matches]
            self.finished.emit(clean_matches, "")

        except Exception as e:
            self.finished.emit([], f"Worker Thread Failure: {str(e)}")