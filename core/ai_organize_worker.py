import json
from core.base_ai_worker import BaseAIWorker
from core.prompts import Prompts


class AIOrganizeWorker(BaseAIWorker):
    """[REFACTOR] Organize notes into logical clusters using BaseAIWorker.
    
    [AI OPTIMIZATION] Features:
    - Temperature=0.0 for deterministic clustering
    - Few-shot examples from centralized prompts
    - JSON mode enforcement for structured output
    """

    def __init__(self, llm_manager, model, nodes_data, custom_instructions="", parent=None):
        super().__init__()
        self.llm_manager = llm_manager
        self.model = model
        self.nodes_data = nodes_data
        self.custom_instructions = custom_instructions

    def execute_task(self):
        """[REFACTOR] Execute organization task with optimized settings."""
        if not self.nodes_data:
            raise ValueError("No nodes available.")

        self.emit_progress("Analyzing node relationships...")

        # [REFACTOR] Use centralized prompt with few-shot examples
        system_prompt = Prompts.get_system_prompt('organize', self.custom_instructions)

        prompt = f"Nodes Data:\n{json.dumps(self.nodes_data, indent=2)}\n\nGroup these nodes."

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
            temperature=0.0  # [AI OPTIMIZATION] Deterministic clustering
        )

        # [REFACTOR] Error checking
        if "[Generation Error" in response or "[System Error" in response:
            raise Exception(f"AI Organization Failed:\n{response.strip()}")

        self.emit_progress("Parsing organization results...")

        clusters = self.safe_parse_json(response.strip(), default=[], json_mode=True)

        return clusters