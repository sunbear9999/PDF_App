import json
from core.base_ai_worker import BaseAIWorker
from core.prompts import Prompts


class AIFindConnectionsWorker(BaseAIWorker):
    """[REFACTOR] Find connections between nodes using BaseAIWorker.
    
    [AI OPTIMIZATION] Features:
    - Temperature=0.0 for deterministic edge detection
    - Few-shot examples from centralized prompts
    - JSON mode enforcement for structured output
    """

    def __init__(self, llm_manager, model, nodes_data, edges_data, parent=None):
        super().__init__()
        self.llm_manager = llm_manager
        self.model = model
        self.nodes_data = nodes_data
        self.edges_data = edges_data

    def execute_task(self):
        """[REFACTOR] Execute connection finding task with optimized settings."""
        if not self.nodes_data or len(self.nodes_data) < 2:
            raise ValueError("Not enough nodes to find connections. Select at least 2 nodes.")

        self.emit_progress("Analyzing node relationships...")

        # [REFACTOR] Use centralized prompt with few-shot examples
        system_prompt = Prompts.get_system_prompt('connections')

        prompt = (
            f"Nodes:\n{json.dumps(self.nodes_data, indent=2)}\n\n"
            f"Existing Connections:\n{json.dumps(self.edges_data, indent=2)}\n\n"
            "Find new logical connections between the nodes. Return JSON ONLY."
        )

        response = ""
        def handle_chunk(chunk):
            nonlocal response
            response += chunk

        # [AI OPTIMIZATION] Query with temperature=0.0 for deterministic extraction
        self.llm_manager.query(
            prompt,
            self.model,
            allowed_docs=[],
            callback=handle_chunk,
            rag_enabled=False,
            use_agents=False,
            custom_system_prompt=system_prompt,
            temperature=0.0  # [AI OPTIMIZATION] Deterministic edge detection
        )

        # [REFACTOR] Error checking
        if "[Generation Error" in response or "[System Error" in response:
            raise Exception(f"AI Processing Failed:\n{response.strip()}")

        self.emit_progress("Parsing connection results...")

        # [REFACTOR] Use inherited JSON parsing utility
        cleaned_response = BaseAIWorker.clean_and_parse_json(response.strip())
        new_connections = self.safe_parse_json(cleaned_response, default=[], json_mode=True)

        return new_connections