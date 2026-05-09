from abc import ABC, abstractmethod
from typing import Dict, Any, List

class GraphState(dict):
    """A shared memory dictionary that flows through the DAG."""
    def __init__(self, initial_data: Dict[str, Any] = None):
        super().__init__()
        if initial_data:
            self.update(initial_data)

class BaseNode(ABC):
    """The base protocol for all Execution Nodes."""
    def __init__(self, node_id: str, config: Dict[str, Any]):
        self.node_id = node_id
        self.config = config

    @abstractmethod
    def execute(self, state: GraphState, job: 'LLMJob', callbacks: Dict[str, Any]) -> GraphState:
        """
        Executes node logic, mutating and returning the GraphState.
        Takes the current LLMJob (for abort_events) and UI callbacks (for live streaming).
        """
        pass

# --- Archetype Examples ---

class LLMAgentNode(BaseNode):
    def execute(self, state: GraphState, job, callbacks) -> GraphState:
        llm_manager = self.config.get('llm_manager')
        model = self.config.get('model', 'llama3')
        prompt_template = self.config.get('system_prompt', '')
        
        # Pull variables from upstream nodes (e.g., {{ trigger.selected_text }})
        # In a real implementation, you'd use a regex template renderer here
        formatted_prompt = prompt_template.format(**state)
        
        callbacks['status_update'](f"Agent {self.node_id} is thinking...")
        
        response = llm_manager.query(
            question=self.config.get('user_query', ''),
            selected_model=model,
            custom_system_prompt=formatted_prompt,
            abort_event=job.abort_event,
            callback=callbacks.get('stream_chunk')
        )
        
        # Write output to state so downstream nodes can use it
        state[f"{self.node_id}_output"] = response
        return state

class RAGSearchNode(BaseNode):
    def execute(self, state: GraphState, job, callbacks) -> GraphState:
        llm_manager = self.config.get('llm_manager')
        query = self.config.get('search_query', '').format(**state)
        
        callbacks['status_update'](f"Searching database for: {query}")
        
        # Fetch directly from Chroma
        emb = llm_manager.get_embedding(query)
        results = llm_manager.query_by_raw_embedding(emb, n_results=5)
        
        # Package and write to state
        context = "\n".join(results['documents'][0]) if results else "No context found."
        state[f"{self.node_id}_context"] = context
        return state