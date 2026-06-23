from __future__ import annotations

from core.engine.action_model import ActionStep, AIActionBlueprint


CHART_ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "category_title": {"type": "string"},
        "value_axis_title": {"type": "string"},
        "series": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "points": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string"},
                                "value": {"type": "number"},
                                "evidence_status": {"type": "string", "enum": ["exact", "estimated"]},
                                "source_text": {"type": "string"},
                            },
                            "required": ["label", "value", "evidence_status"],
                        },
                    },
                },
                "required": ["name", "points"],
            },
        },
    },
    "required": ["title", "series"],
}


def build_chart_analysis_blueprint() -> AIActionBlueprint:
    keys = [
        "Chart Analysis System v1", "Chart Evidence Policy v1",
        "Chart Structured Output v1", "Vision JSON Repair v1",
    ]
    return AIActionBlueprint(
        name="Analyze Selected Chart",
        description="Prompt-managed conversion of a chart image to structured data.",
        steps=[ActionStep(
            step_id="analyze_chart",
            step_type="LLM_QUERY",
            inputs={"query": "{prompt:Chart Structured Output v1}", "images": "{selection_images}"},
            output_key="chart_analysis_result",
            prompt_key="Chart Analysis System v1",
            system_prompt="{prompt:Chart Evidence Policy v1}\n\n{prompt:Vision JSON Repair v1}",
            required_prompt_keys=keys,
            required_model_capabilities=["vision"],
            output_schema=CHART_ANALYSIS_SCHEMA,
            llm_options={"temperature": 0.0, "num_predict": 4096},
            ui_format="silent",
        )],
    )
