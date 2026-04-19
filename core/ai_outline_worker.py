import json
from PySide6.QtCore import QThread, Signal

class AIOutlineWorker(QThread):
    finished = Signal(str, str) # Emits (outline_text, error_msg)

    def __init__(self, llm_manager, model, nodes_data, edges_data, parent=None):
        super().__init__(parent)
        self.llm_manager = llm_manager
        self.model = model
        self.nodes_data = nodes_data
        self.edges_data = edges_data

    def run(self):
        if not self.nodes_data:
            self.finished.emit("", "No nodes available to generate an outline. Please add or select some nodes.")
            return

        try:
            # =================================================================
            # AGENT 1: Structural & Argument Analysis
            # =================================================================
            agent1_system = self.llm_manager.get_system_prompt(
                "AI Outline Worker - Analyst",
                (
                    "You are an expert logical analyst. Your task is to analyze a graph of notes and user-created concepts. "
                    "User-created nodes often represent claims, reasons, or thesis statements. PDF note nodes usually represent evidence or quotes. "
                    "Review the nodes and their connections to deduce the overarching argument or structure the user is trying to build. "
                    "Provide a detailed, structured summary of this intended argument, identifying the main thesis, supporting points, and how the evidence fits in. "
                    "Do NOT write an outline yet, just map out the logical argument they are attempting to make."
                ),
            )
            
            agent1_prompt = (
                f"Nodes Data (type indicates if it's a user claim or PDF evidence):\n{json.dumps(self.nodes_data, indent=2)}\n\n"
                f"Connections:\n{json.dumps(self.edges_data, indent=2)}\n\n"
                "Analyze the logical structure and intended argument."
            )

            analysis_result = ""
            def handle_chunk1(chunk):
                nonlocal analysis_result
                analysis_result += chunk

            self.llm_manager.query(
                agent1_prompt,
                self.model,
                allowed_docs=[],
                callback=handle_chunk1,
                rag_enabled=False,
                use_agents=False,
                custom_system_prompt=agent1_system
            )

            # 🚨 ERROR CHECK: Abort immediately if the LLM timed out or failed
            if "[Generation Error" in analysis_result or "[System Error" in analysis_result:
                self.finished.emit("", f"AI Analysis Failed:\n{analysis_result.strip()}")
                return

            # =================================================================
            # AGENT 2: Outline Generation
            # =================================================================
            agent2_system = self.llm_manager.get_system_prompt(
                "AI Outline Worker - Writer",
                (
                    "You are an expert academic writer. Your task is to generate a formal essay outline based on a structural analysis of the user's notes. "
                    "Do NOT write the full essay. Only write a detailed, hierarchical outline (using Roman numerals, letters, etc.). "
                    "Incorporate the specific claims, ideas, and evidence from the original notes into the outline structure where appropriate. "
                    "The outline must be structured logically according to the analyst's interpretation."
                ),
            )
            
            agent2_prompt = (
                f"Analyst's Structural Interpretation:\n{analysis_result}\n\n"
                f"Original Nodes Data:\n{json.dumps(self.nodes_data, indent=2)}\n\n"
                "Generate the detailed essay outline based strictly on this information."
            )

            outline_result = ""
            def handle_chunk2(chunk):
                nonlocal outline_result
                outline_result += chunk

            self.llm_manager.query(
                agent2_prompt,
                self.model,
                allowed_docs=[],
                callback=handle_chunk2,
                rag_enabled=False,
                use_agents=False,
                custom_system_prompt=agent2_system
            )

            # 🚨 ERROR CHECK: Abort immediately if the LLM timed out or failed
            if "[Generation Error" in outline_result or "[System Error" in outline_result:
                self.finished.emit("", f"AI Outline Generation Failed:\n{outline_result.strip()}")
                return

            # Final Output
            self.finished.emit(outline_result.strip(), "")

        except Exception as e:
            # Guarantees the overlay is hidden even if a catastrophic python error occurs
            self.finished.emit("", f"An unexpected error occurred: {str(e)}")