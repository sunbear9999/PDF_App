# core/engine/step_manager.py
import json
import os
import sys
import copy
import dataclasses
from core.engine.action_model import ActionStep

class StepManager:
    def __init__(self):
        # Determine save directory based on OS
        app_name = "Papyrus Research"
        if sys.platform == "win32":
            base_dir = os.getenv("LOCALAPPDATA", os.path.expanduser("~\\AppData\\Local"))
        elif sys.platform == "darwin":
            base_dir = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
        else:
            base_dir = os.getenv("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
            
        self.library_path = os.path.join(base_dir, app_name, "step_library.json")
        self.library = {}
        self._load_library()

    def _load_library(self):
        # 1. Load your existing file if it exists
        if os.path.exists(self.library_path):
            try:
                with open(self.library_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for key, step_data in data.items():
                        self.library[key] = ActionStep(**step_data)
            except Exception as e:
                print(f"Failed to load Step Library: {e}")
                
        # 2. THE FIX: ALWAYS force-inject the core steps! 
        # This guarantees your local file is updated with the newest tools.
        self._inject_core_steps()

    def _inject_core_steps(self):
        """Pre-populates the library with universal tools using the latest blueprints."""
        from core.engine.default_blueprints import DefaultBlueprints
        
        # Pull the absolute latest, perfectly-schema'd versions from your blueprints file!
        core_steps = {
            "core_extract_citations": DefaultBlueprints.get_universal_citation_step(),
            "core_build_graph": DefaultBlueprints.get_auto_build_graph_step()
        }
        
        self.library.update(core_steps)
        self.save_library()

    def save_library(self):
        out_data = {k: dataclasses.asdict(v) for k, v in self.library.items()}
        os.makedirs(os.path.dirname(self.library_path), exist_ok=True)
        with open(self.library_path, 'w', encoding='utf-8') as f:
            json.dump(out_data, f, indent=4)

    def save_step(self, step_ref: str, step: ActionStep):
        """Persist a reusable workflow step definition."""
        clean_ref = self._safe_ref(step_ref)
        saved_step = copy.deepcopy(step)
        saved_step.step_id = clean_ref
        saved_step.step_ref = None
        if not saved_step.node_type_id:
            from core.engine.workflow_graph_service import STEP_TYPE_TO_NODE_TYPE
            saved_step.node_type_id = STEP_TYPE_TO_NODE_TYPE.get(saved_step.step_type, "workflow.llm_query")
        self.library[clean_ref] = saved_step
        self.save_library()
        return clean_ref

    def get_step(self, step_ref: str) -> ActionStep:
        """Returns a copy of the requested step to prevent reference mutation."""
        step = self.library.get(step_ref)
        return copy.deepcopy(step) if step else None

    def list_steps(self):
        return [(key, copy.deepcopy(step)) for key, step in sorted(self.library.items())]

    def _safe_ref(self, text: str) -> str:
        value = "".join(ch.lower() if ch.isalnum() else "_" for ch in (text or "").strip()).strip("_")
        return value or "custom_step"
