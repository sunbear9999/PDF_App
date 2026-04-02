# core/ai_organize_worker.py
import json
import re
from PyQt6.QtCore import QThread, pyqtSignal

class AIOrganizeWorker(QThread):
    finished = pyqtSignal(object, str)

    def __init__(self, llm_manager, model, nodes_data, custom_instructions="", parent=None):
        super().__init__(parent)
        self.llm_manager = llm_manager
        self.model = model
        self.nodes_data = nodes_data
        self.custom_instructions = custom_instructions

    def run(self):
        custom_rule = f"CRITICAL USER RULE FOR CATEGORIES: '{self.custom_instructions}'\n" if self.custom_instructions else "Group by general semantic similarity into logical topics.\n"
        
        prompt = (
            "You are a strict JSON data processing API.\n"
            f"{custom_rule}"
            "INSTRUCTIONS:\n"
            "1. You MUST strictly follow the user rule above for how to name and define the categories.\n"
            "2. You MUST include EVERY SINGLE 'id' from the Input Data. If a note naturally fits into multiple categories, you MAY include its 'id' in more than one category.\n"
            "3. DO NOT alter the 'id' strings. Copy them exactly as provided.\n"
            "4. Respond ONLY with a valid, raw JSON array. DO NOT wrap the output in markdown blockquotes (e.g. ```json).\n"
            "Schema:\n"
            "[\n"
            "  {\n"
            "    \"cluster_name\": \"Category Name\",\n"
            "    \"node_ids\": [\"id1\", \"id2\"]\n"
            "  }\n"
            "]\n\n"
            f"Input Data:\n{json.dumps(self.nodes_data, indent=2)}\n\n"
            "OUTPUT STRICTLY JSON:"
        )
        
        response_text = ""
        def callback(chunk):
            nonlocal response_text
            response_text += chunk

        try:
            self.llm_manager.query(prompt, self.model, allowed_docs=None, callback=callback, rag_enabled=False)
            
            if "[Generation Error" in response_text:
                self.finished.emit(None, f"The AI engine encountered an error:\n{response_text}")
                return
                
            if not response_text.strip():
                self.finished.emit(None, "The AI returned an empty response. Make sure your local model is working correctly.")
                return
            
            cleaned = response_text.replace("```json", "").replace("```", "").strip()
            
            start_idx = cleaned.find('[')
            end_idx = cleaned.rfind(']')
            
            if start_idx != -1 and end_idx != -1:
                cleaned = cleaned[start_idx:end_idx+1]
            else:
                raise ValueError("No JSON array found in the AI response.")
                
            cleaned = re.sub(r',\s*([\]}])', r'\1', cleaned) 
            
            clusters = json.loads(cleaned)
            self.finished.emit(clusters, "")
            
        except Exception as e:
            err = f"Failed to parse LLM Output.\nError: {str(e)}\n\nRaw Output:\n{response_text[:300]}..."
            self.finished.emit(None, err)