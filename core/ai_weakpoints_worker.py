import json
from core.base_ai_worker import BaseAIWorker
from core.prompts import Prompts


class AIWeakpointsWorker(BaseAIWorker):
    """[REFACTOR] Identify weak points in argument using BaseAIWorker.
    
    [AI OPTIMIZATION] Features:
    - Two-phase process: analysis then critique
    - Temperature=0.4 for constructive feedback
    - Centralized prompts for both phases
    """

    def __init__(self, llm_manager, model, nodes_data, edges_data, parent=None):
        super().__init__()
        self.llm_manager = llm_manager
        self.model = model
        self.nodes_data = nodes_data
        self.edges_data = edges_data

    def execute_task(self):
        """[REFACTOR] Execute weakpoints analysis task with optimized settings."""
        if not self.nodes_data:
            raise ValueError("No nodes available to analyze. Please add or select some nodes.")

        self.emit_progress("Analyzing argument structure...")

        # =================================================================
        # PHASE 1: Structural Analysis
        # =================================================================
        # [REFACTOR] Use centralized prompt
        agent1_system = Prompts.get_system_prompt('weakpoints_analysis')

        agent1_prompt = (
            f"Nodes Data (type indicates user claim vs PDF evidence):\n{json.dumps(self.nodes_data, indent=2)}\n\n"
            f"Connections:\n{json.dumps(self.edges_data, indent=2)}\n\n"
            "Provide a detailed breakdown of the logical argument structure."
        )

        analysis_result = ""
        def handle_chunk1(chunk):
            nonlocal analysis_result
            analysis_result += chunk

        # [AI OPTIMIZATION] Query with temperature=0.4 for analytical thinking
        self.llm_manager.query(
            agent1_prompt,
            self.model,
            allowed_docs=[],
            callback=handle_chunk1,
            rag_enabled=False,
            use_agents=False,
            custom_system_prompt=agent1_system,
            temperature=0.4  # [AI OPTIMIZATION] Analytical critique
        )

        # [REFACTOR] Error checking
        if "[Generation Error" in analysis_result or "[System Error" in analysis_result:
            raise Exception(f"AI Structural Analysis Failed:\n{analysis_result.strip()}")

        self.emit_progress("Generating constructive feedback...")

        # =================================================================
        # PHASE 2: Constructive Critique
        # =================================================================
        # [REFACTOR] Use centralized prompt
        agent2_system = Prompts.get_system_prompt('weakpoints_critique')

        agent2_prompt = (
            f"Argument Structure Map:\n{analysis_result}\n\n"
            f"Original Nodes Data:\n{json.dumps(self.nodes_data, indent=2)}\n\n"
            "Identify the weak points and suggest specific new nodes/evidence the user should find."
        )

        critique_result = ""
        def handle_chunk2(chunk):
            nonlocal critique_result
            critique_result += chunk

        # [AI OPTIMIZATION] Query with temperature=0.4 for constructive feedback
        self.llm_manager.query(
            agent2_prompt,
            self.model,
            allowed_docs=[],
            callback=handle_chunk2,
            rag_enabled=False,
            use_agents=False,
            custom_system_prompt=agent2_system,
            temperature=0.4  # [AI OPTIMIZATION] Constructive critique
        )

        # [REFACTOR] Error checking
        if "[Generation Error" in critique_result or "[System Error" in critique_result:
            raise Exception(f"AI Critique Generation Failed:\n{critique_result.strip()}")

        return critique_result.strip()