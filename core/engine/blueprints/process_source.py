"""Registered local-only source processing through the normal workflow runner."""
from core.engine.action_model import AIActionBlueprint, ActionStep


def get_source_processing_blueprint(**_):
    return AIActionBlueprint(name="Process source intelligence", description="Prepare reusable local artifacts using the saved source plan and registered producers.",
        mount_points=["intelligence_processing"], requires_model_setup=False, steps=[ActionStep(
            step_id="process_source", step_type="SOURCE_STAGE", output_key="source_processing_result",
            inputs={"action": "process_plan", "source_id": "{source_id}", "mode": "{processing_mode}",
                    "stages": "{processing_stages}", "expected_project": "{processing_project}"},
            ui_format="silent", status_text="Processing reusable source artifacts")])
