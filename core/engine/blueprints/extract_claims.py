"""
core/engine/blueprints/extract_claims.py

Extract Claims workflow:
  1. LLM_SCHEMA_QUERY  → extract structured claims from selected text
  2. SHOW_ITEM_SELECTOR → user reviews and checks which claims to keep
  3. WORKSPACE_WRITE   → selected claims written as 'claim' nodes to workspace

Registered as a DockActionSpec at SOURCE_VIEWER_TEXT_SELECTION so it appears
in the PDF viewer right-click menu whenever text is highlighted.
"""
from __future__ import annotations

from core.engine.action_model import AIActionBlueprint, ActionStep

# Low temperature: claim extraction is a precision task, not a creative one.
CLAIM_EXTRACTION_TEMPERATURE = 0.1


def get_extract_claims_blueprint(model: str = "{selected_model}") -> AIActionBlueprint:
    return AIActionBlueprint(
        name="Extract Claims",
        description="LLM extracts structured claims from highlighted text, user selects which to save.",
        mount_points=["source_viewer:context_menu:text_selection"],
        expected_inputs=[
            {"key": "selected_text", "type": "text_area", "label": "Selected Text"},
        ],
        steps=[
            ActionStep(
                step_id="extract_claims",
                step_type="LLM_SCHEMA_QUERY",
                inputs={"query": "{prompt:Extract Claims Query}\n\n{selected_text}"},
                system_prompt="{prompt:Extract Claims System}",
                output_schema={"claims": [{"claim": "", "type": "", "confidence": "", "evidence": ""}]},
                output_key="raw_claims",
                model=model,
                ui_format="silent",
                llm_options={"temperature": CLAIM_EXTRACTION_TEMPERATURE},
            ),
            ActionStep(
                step_id="parse_claims",
                step_type="PYTHON_SCRIPT",
                inputs={"script": """
import json
raw = state.get('raw_claims', '{}')
if isinstance(raw, str):
    try:
        raw = json.loads(raw)
    except Exception:
        raw = {}
claims = raw.get('claims', []) if isinstance(raw, dict) else raw if isinstance(raw, list) else []
result = json.dumps(claims)
"""},
                output_key="claims_list",
                ui_format="silent",
            ),
            ActionStep(
                step_id="select_claims",
                step_type="SHOW_ITEM_SELECTOR",
                inputs={
                    "items": "{claims_list}",
                    "title": "Select Claims to Save",
                    "action_label": "Save Selected Claims",
                    "item_display_key": "claim",
                    "item_subtitle_key": "type",
                },
                output_key="selected_claims",
                ui_format="silent",
                ui_target="custom_tools_tab",
            ),
            ActionStep(
                step_id="save_claims",
                step_type="WORKSPACE_WRITE",
                inputs={
                    "data": "{selected_claims}",
                    "node_type": "claim",
                    "source_path": "{source_path}",
                },
                output_key="save_result",
                ui_format="live_stream",
                ui_target="custom_tools_tab",
                ui_title="Claims Saved",
            ),
        ],
    )
