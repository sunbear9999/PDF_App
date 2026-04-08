import json
from core.base_ai_worker import BaseAIWorker
from core.prompts import Prompts


class AIConsolidateWorker(BaseAIWorker):
    """[REFACTOR] Consolidate graph structure using BaseAIWorker.
    
    [AI OPTIMIZATION] Features:
    - Temperature=0.0 for deterministic restructuring
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
        """[REFACTOR] Execute consolidation task with optimized settings."""
        if not self.nodes_data:
            raise ValueError("No nodes available to consolidate.")

        self.emit_progress("✨ AI is fundamentally restructuring your notes...")

        # [REFACTOR] Use centralized prompt with few-shot examples
        system_prompt = Prompts.get_system_prompt('consolidate')

        prompt = (
            f"Nodes Data:\n{json.dumps(self.nodes_data, indent=2)}\n\n"
            f"Connections:\n{json.dumps(self.edges_data, indent=2)}\n\n"
            "Restructure the graph and return the JSON object."
        )

        result_text = ""
        def handle_chunk(chunk):
            nonlocal result_text
            result_text += chunk

        # [AI OPTIMIZATION] Query with temperature=0.0 for deterministic extraction
        self.llm_manager.query(
            prompt,
            self.model,
            allowed_docs=[],
            callback=handle_chunk,
            rag_enabled=False,
            use_agents=False,
            custom_system_prompt=system_prompt,
            temperature=0.0  # [AI OPTIMIZATION] Deterministic restructuring
        )

        # [REFACTOR] Error checking
        if "[Generation Error" in result_text or "[System Error" in result_text:
            raise Exception(f"AI Consolidation Failed:\n{result_text.strip()}")

        self.emit_progress("Parsing consolidation results...")

        result_dict = self.safe_parse_json(result_text.strip(), default={}, json_mode=True)

        return result_dict