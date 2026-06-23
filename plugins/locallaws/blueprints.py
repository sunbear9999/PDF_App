"""
Blueprint factory for the Legal Research Query Assist workflow.
Creates a single LLM_QUERY step that generates boolean search parameters
from a plain-text legal issue description.
"""
from __future__ import annotations

from .prompts import LAW_QUERY_SYSTEM_KEY

_QUERY_OUTPUT_SCHEMA = {
    "type": "object",
    "description": (
        "Legal search parameters. Include only the keys matching the requested target. "
        "Omit 'caselaw' or 'municipal' entirely if not requested."
    ),
    "properties": {
        "caselaw": {
            "type": "object",
            "description": "Parameters for the CourtListener case law search API",
            "properties": {
                "court": {
                    "type": "string",
                    "description": (
                        "Exact CourtListener court ID (e.g. scotus, ca9, cadc, dcd). "
                        "Comma-separated for multiple courts. Empty string searches all courts."
                    ),
                },
                "query": {
                    "type": "string",
                    "description": (
                        "Full boolean search query using AND, OR, NOT, parentheses, "
                        "quoted phrases, and wildcards as supported by CourtListener."
                    ),
                },
                "date_from": {
                    "type": "string",
                    "description": "Lower bound filing date in YYYY-MM-DD format, or empty string.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Number of cases to retrieve (10–50).",
                },
            },
        },
        "municipal": {
            "type": "object",
            "description": "Parameters for LOCUS municipal code dataset search",
            "properties": {
                "state": {
                    "type": "string",
                    "description": "2-letter US state abbreviation (e.g. CO, CA, TX).",
                },
                "subject": {
                    "type": "string",
                    "description": (
                        "Boolean keyword query for filtering municipal law text/headers. "
                        "Supports AND, OR, NOT, and quoted phrases."
                    ),
                },
            },
        },
    },
}


def build_legal_query_blueprint():
    from core.engine.action_model import ActionStep, WorkflowBlueprint

    step = ActionStep(
        step_id="suggest_legal_query",
        step_type="LLM_QUERY",
        inputs={"query": "{legal_query_prompt}"},
        prompt_key=LAW_QUERY_SYSTEM_KEY,
        output_key="legal_query_suggestion",
        output_schema=_QUERY_OUTPUT_SCHEMA,
        llm_options={"temperature": 0.2, "num_predict": 1024, "num_ctx": 8192},
        ui_format="silent",
    )
    return WorkflowBlueprint(
        name="Legal Query Assist",
        description=(
            "Generates optimized boolean search parameters for legal research "
            "from a plain-text issue description."
        ),
        steps=[step],
    )
