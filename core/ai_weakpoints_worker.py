import json
from PySide6.QtCore import QThread, Signal

class AIWeakpointsWorker(QThread):
    finished = Signal(str, str) # Emits (analysis_text, error_msg)

    def __init__(self, llm_manager, model, nodes_data, edges_data, parent=None):
        super().__init__(parent)
        self.llm_manager = llm_manager
        self.model = model
        self.nodes_data = nodes_data
        self.edges_data = edges_data

    def run(self):
        if not self.nodes_data:
            self.finished.emit("", "No nodes available to analyze. Please add or select some nodes.")
            return

        try:
            # =================================================================
            # AGENT 1: Structural Mapping & Argument Comprehension
            # =================================================================
            agent1_system = self.llm_manager.get_system_prompt(
                "AI Weakpoints Worker - Mapper",
                (
                    "You are an expert logical analyst and debate coach. Your task is to review a web of notes and user-created concepts. "
                    "User-created nodes represent claims, arguments, or assertions. PDF note nodes represent concrete evidence, quotes, or citations. "
                    "Review the nodes and their connections to map out the exact argument the user is building. "
                    "Identify the main thesis, the supporting pillars, and map which evidence goes to which claim. "
                    "Do NOT critique the argument yet; simply map it out and explain what the user is attempting to prove and how they are structuring it."
                ),
            )
            
            agent1_prompt = (
                f"Nodes Data (type indicates user claim vs PDF evidence):\n{json.dumps(self.nodes_data, indent=2)}\n\n"
                f"Connections:\n{json.dumps(self.edges_data, indent=2)}\n\n"
                "Provide a detailed breakdown of the logical argument structure."
            )

            structure_result = ""
            def handle_chunk1(chunk):
                nonlocal structure_result
                structure_result += chunk

            self.llm_manager.query(
                agent1_prompt,
                self.model,
                allowed_docs=[],
                callback=handle_chunk1,
                rag_enabled=False,
                use_agents=False,
                custom_system_prompt=agent1_system
            )

            # 🚨 ERROR CHECK
            if "[Generation Error" in structure_result or "[System Error" in structure_result:
                self.finished.emit("", f"AI Structural Analysis Failed:\n{structure_result.strip()}")
                return

            # =================================================================
            # AGENT 2: Weakpoint Identification & Suggestions
            # =================================================================
            agent2_system = self.llm_manager.get_system_prompt(
                "AI Weakpoints Worker - Critic",
                (
                    "You are an expert academic advisor and critical thinker. Your task is to evaluate an argument's structural map for weaknesses. "
                    "Review the provided argument structure alongside the original nodes. "
                    "Identify specific weak points: Which claims lack sufficient concrete evidence? Where are the logical leaps or assumptions? Are counterarguments missing? "
                    "Provide a constructive, formatted critique. "
                    "Crucially, suggest SPECIFIC new nodes (claims to clarify, or evidence to find) that the user should create in their workspace to strengthen this argument."
                ),
            )
            
            agent2_prompt = (
                f"Argument Structure Map:\n{structure_result}\n\n"
                f"Original Nodes Data:\n{json.dumps(self.nodes_data, indent=2)}\n\n"
                "Identify the weak points and suggest specific new nodes/evidence the user should find."
            )

            critique_result = ""
            def handle_chunk2(chunk):
                nonlocal critique_result
                critique_result += chunk

            self.llm_manager.query(
                agent2_prompt,
                self.model,
                allowed_docs=[],
                callback=handle_chunk2,
                rag_enabled=False,
                use_agents=False,
                custom_system_prompt=agent2_system
            )

            # 🚨 ERROR CHECK
            if "[Generation Error" in critique_result or "[System Error" in critique_result:
                self.finished.emit("", f"AI Critique Generation Failed:\n{critique_result.strip()}")
                return

            self.finished.emit(critique_result.strip(), "")

        except Exception as e:
            self.finished.emit("", f"An unexpected error occurred: {str(e)}")