from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

@dataclass
class ActionStep:
    step_id: str
    step_type: str  
    
    inputs: Dict[str, Any] = field(default_factory=dict)
    output_key: str = "result"
    
    model: str = "{selected_model}"
    prompt_key: Optional[str] = None          
    system_prompt: Optional[str] = None       
    llm_options: Dict[str, Any] = field(default_factory=dict)
    
    permissions: List[str] = field(default_factory=lambda: ["all"])
    output_mode: str = "workspace_update" 
    
    ui_format: str = "silent"  
    ui_target: str = "floating" 
    ui_title: str = "AI Result"
    
    # NEW: For Custom HTML Dashboard Rendering
    html_template: Optional[str] = None
    
    output_schema: Optional[Dict[str, Any]] = None
    workspace_filters: List[str] = field(default_factory=lambda: ["text", "color", "edges", "layout"])

@dataclass
class AIActionBlueprint:
    name: str
    description: str
    active_contexts: List[str] = field(default_factory=list)
    
    # NEW: Defines the UI elements required to run this tool
    expected_inputs: List[Dict[str, Any]] = field(default_factory=list) 
    
    steps: List[ActionStep] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict):
        steps = [ActionStep(**step_data) for step_data in data.get('steps', [])]
        return cls(
            name=data.get('name', 'Unnamed Tool'),
            description=data.get('description', ''),
            active_contexts=data.get('active_contexts', []),
            expected_inputs=data.get('expected_inputs', []), # Load inputs
            steps=steps
        )