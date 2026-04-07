# core/ai_organize_worker.py - REFACTORED EXAMPLE
# [REFACTOR] Demonstrates new AI worker pattern using BaseAIWorker

import json
from base_ai_worker import BaseAIWorker
from prompts import Prompts


class AIOrganizeWorker(BaseAIWorker):
    """[REFACTOR] Organize notes into logical clusters.
    
    Demonstrates:
    - Inheriting from BaseAIWorker (signals pre-defined)
    - Using centralized prompts from Prompts class
    - Using inherited JSON parsing utilities
    - [AI OPTIMIZATION] Temperature=0.0 for deterministic extraction
    """

    def __init__(self, llm_manager, model, nodes_data, custom_instructions="", parent=None):
        """Initialize organizer worker.
        
        Args:
            llm_manager: LocalLLMManager instance
            model: Selected AI model name
            nodes_data: List of node dicts with 'id' and 'text'
            custom_instructions: Optional user instructions
        """
        super().__init__()
        self.llm_manager = llm_manager
        self.model = model
        self.nodes_data = nodes_data
        self.custom_instructions = custom_instructions

    def execute_task(self):
        """[REFACTOR] Execute organization task.
        
        [AI OPTIMIZATION] Features:
        - Temperature=0.0 for deterministic clustering
        - JSON mode enforcement for structured output
        - Few-shot examples in system prompt
        """
        if not self.nodes_data:
            raise ValueError("No nodes available.")

        self.emit_progress("Analyzing node relationships...")
        
        # [REFACTOR] Get centralized prompt with few-shot examples
        system_prompt = Prompts.get_system_prompt('organize', self.custom_instructions)

        prompt = f"Nodes Data:\n{json.dumps(self.nodes_data, indent=2)}\n\nGroup these nodes."

        response = ""
        def handle_chunk(chunk):
            nonlocal response
            response += chunk

        # [AI OPTIMIZATION] Query with:
        # - temperature=0.0 for deterministic output (extraction task)
        # - json_mode=True for structured response format
        self.llm_manager.query(
            prompt,
            self.model,
            allowed_docs=[],
            callback=handle_chunk,
            rag_enabled=False,
            use_agents=False,
            custom_system_prompt=system_prompt,
            temperature=0.0  # [AI OPTIMIZATION] Deterministic extraction
        )

        # [REFACTOR] Error checking
        if "[Generation Error" in response or "[System Error" in response:
            raise Exception(f"AI Organization Failed:\n{response.strip()}")

        self.emit_progress("Parsing organization results...")
        
        # [REFACTOR] Use inherited JSON cleaning utility
        cleaned_response = BaseAIWorker.clean_and_parse_json(response.strip())
        clusters = self.safe_parse_json(cleaned_response, default=[], json_mode=True)
        
        return clusters
