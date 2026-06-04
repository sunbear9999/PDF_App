# core/engine/action_model.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

@dataclass
class ActionStep:
    step_id: str
    step_type: str = "LIBRARY_REF" 
    
    step_ref: Optional[str] = None
    
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
    
    html_template: Optional[str] = None
    output_schema: Optional[Dict[str, Any]] = None
    workspace_filters: List[str] = field(default_factory=lambda: ["text", "color", "edges", "layout"])
    required_context: List[str] = field(default_factory=list)

    if_true: List[ActionStep] = field(default_factory=list)
    if_false: List[ActionStep] = field(default_factory=list)

@dataclass
class AIActionBlueprint:
    name: str
    description: str
    mount_points: List[str] = field(default_factory=lambda: ["custom_tools_tab"])
    active_contexts: List[str] = field(default_factory=list)
    expected_inputs: List[Dict[str, Any]] = field(default_factory=list) 
    steps: List[ActionStep] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict):
        def parse_steps(step_list):
            parsed = []
            for s_data in step_list:
                s_data_copy = s_data.copy()
                if 'if_true' in s_data_copy:
                    s_data_copy['if_true'] = parse_steps(s_data_copy['if_true'])
                if 'if_false' in s_data_copy:
                    s_data_copy['if_false'] = parse_steps(s_data_copy['if_false'])
                parsed.append(ActionStep(**s_data_copy))
            return parsed

        steps = parse_steps(data.get('steps', []))
        
        return cls(
            name=data.get('name', 'Unnamed Tool'),
            description=data.get('description', ''),
            mount_points=data.get('mount_points', ["custom_tools_tab"]),
            active_contexts=data.get('active_contexts', []),
            expected_inputs=data.get('expected_inputs', []),
            steps=steps
        )