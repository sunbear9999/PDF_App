import json
import re
from PyQt6.QtCore import QThread, pyqtSignal

class AIFindConnectionsWorker(QThread):
    finished = pyqtSignal(list, str)

    def __init__(self, llm_manager, model, nodes_data, edges_data, parent=None):
        super().__init__(parent)
        self.llm_manager = llm_manager
        self.model = model
        self.nodes_data = nodes_data
        self.edges_data = edges_data

    def run(self):
        if not self.nodes_data or len(self.nodes_data) < 2:
            self.finished.emit([], "Not enough nodes to find connections. Select at least 2 nodes.")
            return

        try:
            system_prompt = (
                "You are an expert analytical AI assistant helping to build a knowledge graph. "
                "Analyze the provided nodes (which contain notes and/or quotes) and their existing connections. "
                "Identify meaningful NEW relationships between these nodes that are not already connected. "
                "Rate the strength of each new connection on a scale of 1 to 10. "
                "Provide a concise, descriptive label for the connection. "
                "Respond ONLY with a valid JSON array of objects, with no markdown formatting or extra text. "
                "Format: [{\"source_id\": \"id1\", \"target_id\": \"id2\", \"label\": \"Reason for connection\", \"weight\": 7}]"
            )

            prompt = (
                f"Nodes:\n{json.dumps(self.nodes_data, indent=2)}\n\n"
                f"Existing Connections:\n{json.dumps(self.edges_data, indent=2)}\n\n"
                "Find new logical connections between the nodes. Return JSON ONLY."
            )

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

            # 🚨 ERROR CHECK
            if "[Generation Error" in response or "[System Error" in response:
                self.finished.emit([], f"AI Processing Failed:\n{response.strip()}")
                return

            cleaned_response = response.strip()
            match = re.search(r'\[\s*\{.*?\}\s*\]', cleaned_response, re.DOTALL)
            if match:
                cleaned_response = match.group(0)

            new_connections = json.loads(cleaned_response)
            self.finished.emit(new_connections, "")

        except json.JSONDecodeError as e:
            self.finished.emit([], f"Failed to parse AI response as JSON. The model may have hallucinated.\nResponse: {response}")
        except Exception as e:
            self.finished.emit([], f"An unexpected error occurred: {str(e)}")