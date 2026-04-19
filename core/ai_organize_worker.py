import json
import re
from PySide6.QtCore import QThread, Signal

class AIOrganizeWorker(QThread):
    finished = Signal(list, str)

    def __init__(self, llm_manager, model, nodes_data, custom_instructions="", parent=None):
        super().__init__(parent)
        self.llm_manager = llm_manager
        self.model = model
        self.nodes_data = nodes_data
        self.custom_instructions = custom_instructions

    def run(self):
        if not self.nodes_data:
            self.finished.emit([], "No nodes available.")
            return

        try:
            custom_instructions_block = ""
            if self.custom_instructions:
                custom_instructions_block = f" User specific instruction: {self.custom_instructions}"

            system_prompt = self.llm_manager.get_system_prompt(
                "AI Organize Worker",
                (
                    "You are an expert AI assistant that organizes notes. "
                    "Group the provided nodes into logical clusters. "
                    "Return ONLY a valid JSON array of objects. "
                    # 🔥 ESCAPED JSON BRACES HERE
                    "Format: [{{\"cluster_name\": \"Name\", \"node_ids\": [\"id1\", \"id2\"]}}]"
                    "{custom_instructions_block}"
                ),
                custom_instructions_block=custom_instructions_block,
            )

            prompt = f"Nodes Data:\n{json.dumps(self.nodes_data, indent=2)}\n\nGroup these nodes."

            response = ""
            def handle_chunk(chunk):
                nonlocal response
                response += chunk

            self.llm_manager.query(
                prompt,
                self.model,
                allowed_docs=[],
                callback=handle_chunk,
                rag_enabled=False,
                use_agents=False,
                custom_system_prompt=system_prompt
            )

            if "[Generation Error" in response or "[System Error" in response:
                self.finished.emit([], f"AI Organization Failed:\n{response.strip()}")
                return

            cleaned_response = response.strip()
            
            if "```json" in cleaned_response.lower():
                cleaned_response = cleaned_response.split("```json", 1)[-1].split("```")[0].strip()
            elif "```" in cleaned_response:
                cleaned_response = cleaned_response.split("```", 1)[-1].split("```")[0].strip()

            match = re.search(r'\[\s*\{.*?\}\s*\]', cleaned_response, re.DOTALL)
            if match:
                cleaned_response = match.group(0)

            clusters = json.loads(cleaned_response)
            self.finished.emit(clusters, "")

        except json.JSONDecodeError as e:
            self.finished.emit([], f"Failed to parse AI response as JSON.\nResponse: {response}")
        except Exception as e:
            self.finished.emit([], f"An unexpected error occurred: {str(e)}")