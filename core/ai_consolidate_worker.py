import json
import re
from PyQt6.QtCore import QThread, pyqtSignal

class AIConsolidateWorker(QThread):
    finished = pyqtSignal(dict, str) # Emits (result_dict, error_msg)
    progress = pyqtSignal(str)

    def __init__(self, llm_manager, model, nodes_data, edges_data, parent=None):
        super().__init__(parent)
        self.llm_manager = llm_manager
        self.model = model
        self.nodes_data = nodes_data
        self.edges_data = edges_data

    def run(self):
        if not self.nodes_data:
            self.finished.emit({}, "No nodes available to consolidate.")
            return

        try:
            self.progress.emit("✨ AI is fundamentally restructuring your notes...")
            
            system_prompt = (
                "You are an expert structural editor and knowledge graph architect. "
                "Review the provided graph consisting of user-created claims and PDF evidence notes. "
                "Your goal is to fundamentally streamline, reorganize, and consolidate the structure into a much clearer argument. "
                "CRITICAL RULES:\n"
                "1. Keep ALL 'pdf_note' nodes exactly as they are. You cannot modify or delete them. Reference them by their exact original IDs.\n"
                "2. You may create NEW 'user_created' nodes to act as new streamlined claims, reasons, or categories to replace old messy ones. Give them short unique IDs like 'c1', 'c2'.\n"
                "3. Define NEW edges connecting your new custom nodes to the existing 'pdf_note' nodes (and to each other) to form a complete logical tree.\n"
                "Return ONLY a valid JSON object matching this schema:\n"
                "{\n"
                "  \"new_custom_nodes\": [{\"id\": \"c1\", \"text\": \"Streamlined claim text\"}],\n"
                "  \"new_edges\": [{\"source_id\": \"c1\", \"target_id\": \"existing_pdf_note_id\", \"label\": \"Evidence\"}]\n"
                "}\n"
                "Do not include markdown or extra formatting text."
            )
            
            prompt = (
                f"Nodes Data:\n{json.dumps(self.nodes_data, indent=2)}\n\n"
                f"Connections:\n{json.dumps(self.edges_data, indent=2)}\n\n"
                "Restructure the graph and return the JSON object."
            )

            result_text = ""
            def handle_chunk(chunk):
                nonlocal result_text
                result_text += chunk

            self.llm_manager.query(
                prompt,
                self.model,
                allowed_docs=[],
                callback=handle_chunk,
                rag_enabled=False,
                use_agents=False,
                custom_system_prompt=system_prompt
            )

            if "[Generation Error" in result_text or "[System Error" in result_text:
                self.finished.emit({}, f"AI Consolidation Failed:\n{result_text.strip()}")
                return

            cleaned_result = result_text.strip()
            match = re.search(r'\{.*\}', cleaned_result, re.DOTALL)
            if match:
                cleaned_result = match.group(0)

            try:
                result_dict = json.loads(cleaned_result)
                self.finished.emit(result_dict, "")
            except json.JSONDecodeError:
                self.finished.emit({}, f"Failed to parse AI structure. The model may have hallucinated.\nResponse: {result_text}")

        except Exception as e:
            self.finished.emit({}, f"An unexpected error occurred: {str(e)}")