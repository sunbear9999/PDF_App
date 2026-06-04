# core/api/manifest_ai.py
import json
from core.utils.json_utils import extract_json_from_tags

class ManifestAIApi:
    def __init__(self, project_manager):
        self.pm = project_manager

    def parse_and_apply_update(self, raw_llm_response: str) -> tuple[bool, dict]:
        """
        Scans LLM output for <UPDATE_MANIFEST> tags, parses it safely, 
        and updates the SQLite database.
        Returns (True, new_data_dict) if updated, or (False, {}) if no update found.
        """
        success, parsed_data = extract_json_from_tags(raw_llm_response, "UPDATE_MANIFEST")
        
        if not success or not isinstance(parsed_data, dict):
            return False, {}

        # Fetch current manifest from DB
        current_manifest_str = self.pm.get_metadata("project_manifest", "{}")
        try:
            current_manifest = json.loads(current_manifest_str)
        except json.JSONDecodeError:
            current_manifest = {}

        # Apply updates: Delete if null, otherwise update/add
        for key, value in parsed_data.items():
            if value is None:
                current_manifest.pop(key, None)
            else:
                current_manifest[key] = value

        # Save back to database
        self.pm.set_metadata("project_manifest", json.dumps(current_manifest))
        
        return True, parsed_data