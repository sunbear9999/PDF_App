# core/blueprint_manager.py
import os
import json
import copy
import sys
from core.engine.action_model import AIActionBlueprint
from core.engine.registries import BlueprintDefinition

class BlueprintManager:
    def __init__(self, registry=None):
        self.registry = registry
        app_name = "Papyrus Research"
        
        # Keep config in the same OS-specific hidden folder as prompts
        if sys.platform == "win32":
            base_dir = os.getenv("LOCALAPPDATA", os.path.expanduser("~\\AppData\\Local"))
            config_dir = os.path.join(base_dir, app_name)
        elif sys.platform == "darwin":
            config_dir = os.path.join(os.path.expanduser("~"), "Library", "Application Support", app_name)
        else:
            base_dir = os.getenv("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
            config_dir = os.path.join(base_dir, app_name)

        os.makedirs(config_dir, exist_ok=True)
        self.blueprint_file = os.path.join(config_dir, "custom_blueprints.json")
        self.blueprints = self._load()
        self._registered_custom_ids = set()
        self._register_custom_blueprints()

    def _load(self):
        if os.path.exists(self.blueprint_file):
            try:
                with open(self.blueprint_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    return {k: AIActionBlueprint.from_dict(v) for k, v in data.items()}
            except Exception as e:
                print(f"Error loading custom blueprints: {e}")
        return {}

    def _register_custom_blueprints(self):
        if not self.registry:
            return
        for key in list(self._registered_custom_ids):
            if key not in self.blueprints and hasattr(self.registry, "unregister"):
                self.registry.unregister(key)
                self._registered_custom_ids.discard(key)
        for key, blueprint in self.blueprints.items():
            self.registry.register(BlueprintDefinition(
                id=key,
                label=blueprint.name or key,
                description=blueprint.description,
                factory=lambda bp=blueprint: copy.deepcopy(bp),
                mount_points=blueprint.mount_points,
                capabilities=["custom_workflow"],
                required_inputs=copy.deepcopy(blueprint.expected_inputs),
                produced_outputs=["workflow_result"],
                human_checkpoints=["review_custom_tool_output"],
            ))
            self._registered_custom_ids.add(key)

    def get_blueprint(self, key_name, fallback_func, *args, **kwargs):
        """Fetches the user-edited blueprint. If none exists, runs the fallback."""
        if key_name in self.blueprints:
            # Deep copy ensures the engine state doesn't mutate saved persistent templates!
            return copy.deepcopy(self.blueprints[key_name])

        # Call factory fallback initialization
        fallback_blueprint = fallback_func(*args, **kwargs)
        if fallback_blueprint:
            fallback_blueprint.name = key_name
            return copy.deepcopy(fallback_blueprint)

        if self.registry:
            registered = self.registry.create(key_name, *args, **kwargs)
            if registered:
                return registered
        return None
