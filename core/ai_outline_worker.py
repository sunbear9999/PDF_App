import json
from core.base_ai_worker import BaseAIWorker
from core.prompts import Prompts


class AIOutlineWorker(BaseAIWorker):
    """[REFACTOR] Generate essay outline using BaseAIWorker.
    
    [AI OPTIMIZATION] Features:
    - Two-phase process: analysis then generation
    - Temperature=0.4 for creative outline writing
    - Centralized prompts for both phases
    """

    def __init__(self, llm_manager, model, nodes_data, edges_data, parent=None):
        super().__init__()
        self.llm_manager = llm_manager
        self.model = model
        self.nodes_data = nodes_data
        self.edges_data = edges_data

    def execute_task(self):
        """[REFACTOR] Execute outline generation task with optimized settings."""
        if not self.nodes_data:
            raise ValueError("No nodes available to generate an outline. Please add or select some nodes.")

        self.emit_progress("Analyzing argument structure...")

        # =================================================================
        # PHASE 1: Structural & Argument Analysis
        # =================================================================
        # [REFACTOR] Use centralized prompt
        agent1_system = Prompts.get_system_prompt('outline_analysis')

        agent1_prompt = (
            f"Nodes Data (type indicates if it's a user claim or PDF evidence):\n{json.dumps(self.nodes_data, indent=2)}\n\n"
            f"Connections:\n{json.dumps(self.edges_data, indent=2)}\n\n"
            "Analyze the logical structure and intended argument."
        )

        analysis_result = ""
        def handle_chunk1(chunk):
            nonlocal analysis_result
            analysis_result += chunk

        # [AI OPTIMIZATION] Query with temperature=0.4 for creative analysis
        self.llm_manager.query(
            agent1_prompt,
            self.model,
            allowed_docs=[],
            callback=handle_chunk1,
            rag_enabled=False,
            use_agents=False,
            custom_system_prompt=agent1_system,
            temperature=0.4  # [AI OPTIMIZATION] Creative analysis
        )

        # [REFACTOR] Error checking
        if "[Generation Error" in analysis_result or "[System Error" in analysis_result:
            raise Exception(f"AI Analysis Failed:\n{analysis_result.strip()}")

        self.emit_progress("Generating formal outline...")

        # =================================================================
        # PHASE 2: Outline Generation
        # =================================================================
        # [REFACTOR] Use centralized prompt
        agent2_system = Prompts.get_system_prompt('outline_generation')

        agent2_prompt = (
            f"Analyst's Structural Interpretation:\n{analysis_result}\n\n"
            f"Original Nodes Data:\n{json.dumps(self.nodes_data, indent=2)}\n\n"
            "Generate the detailed essay outline based strictly on this information."
        )

        outline_result = ""
        def handle_chunk2(chunk):
            nonlocal outline_result
            outline_result += chunk

        # [AI OPTIMIZATION] Query with temperature=0.4 for creative outline writing
        self.llm_manager.query(
            agent2_prompt,
            self.model,
            allowed_docs=[],
            callback=handle_chunk2,
            rag_enabled=False,
            use_agents=False,
            custom_system_prompt=agent2_system,
            temperature=0.4  # [AI OPTIMIZATION] Creative outline generation
        )

        # [REFACTOR] Error checking
        if "[Generation Error" in outline_result or "[System Error" in outline_result:
            raise Exception(f"AI Outline Generation Failed:\n{outline_result.strip()}")

        return outline_result.strip()