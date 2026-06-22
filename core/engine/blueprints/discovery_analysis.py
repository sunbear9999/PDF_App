"""
core/engine/blueprints/discovery_analysis.py

Blueprint for the "Analyze with AI" feature in the Discovery tab.

The system prompt is registered in PromptManager as "Discovery Analysis System"
and the user query as "Discovery Analysis Query" — both fully editable in the
Prompts UI just like any other built-in prompt.

The {ontology_schema} placeholder inside those prompts is resolved at runtime
from the state dict, so it always reflects the live OntologyRegistry (including
any types added by plugins).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from core.engine.action_model import AIActionBlueprint, ActionStep

if TYPE_CHECKING:
    from core.ontology.registry import OntologyRegistry


# ---------------------------------------------------------------------------
# Ontology schema builder — injected into runner state at call time
# ---------------------------------------------------------------------------

def build_ontology_schema_text(
    ontology_registry: Optional["OntologyRegistry"],
) -> str:
    """Return a compact text block listing all entity types, their fields,
    and all relation types with valid source→target constraints.

    This text is injected into runner state as `ontology_schema` so that the
    {ontology_schema} placeholder in the PromptManager templates resolves
    to the live registry — including plugin-contributed types.
    """
    if not ontology_registry:
        return (
            "ENTITY TYPES:\n"
            "  entity.person_org       — fields: name, role, description\n"
            "  entity.timeline_event   — fields: text, date, description\n"
            "  entity.source           — fields: title, authors, year, journal\n"
            "  entity.claim            — fields: text, certainty\n"
            "  entity.quote            — fields: text, quote\n"
            "  entity.finding          — fields: text\n"
            "  entity.text             — fields: text\n\n"
            "RELATION TYPES:\n"
            "  relation.basic          — any → any\n"
            "  relation.supports       — any → any\n"
            "  relation.references     — any → entity.source\n"
            "  relation.authored_by    — entity.source → entity.person_org\n"
            "  relation.before_after   — entity.timeline_event → entity.timeline_event\n"
            "  relation.causes         — any → any\n"
            "  relation.similar_to     — any → any\n"
        )

    lines = ["ENTITY TYPES (use EXACTLY these type_keys):"]
    for bp in ontology_registry.all_entities():
        field_names = [f.key for f in (bp.fields or [])]
        field_str = ", ".join(field_names) if field_names else "text"
        lines.append(f"  {bp.type_key}  ({bp.display_name})  — fields: {field_str}")

    lines.append(
        "\nRELATION TYPES (use EXACTLY these type_keys, respect source→target constraints):"
    )
    for rb in ontology_registry.all_relations():
        src = (
            ", ".join(rb.valid_source_types)
            if rb.valid_source_types != ["*"]
            else "any"
        )
        tgt = (
            ", ".join(rb.valid_target_types)
            if rb.valid_target_types != ["*"]
            else "any"
        )
        lines.append(f"  {rb.type_key}  ({rb.display_name})  — {src} → {tgt}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Import-graph script (embedded in blueprint, not user-editable)
# ---------------------------------------------------------------------------

_IMPORT_GRAPH_SCRIPT = """\
import json
from core.events.event_bus import EventBus
from core.events.domains.workspace_events import WorkspaceIntent, WorkspacePayload

raw = state.get("validated_graph", "{}")
try:
    graph_data = json.loads(raw) if isinstance(raw, str) else raw
except Exception:
    graph_data = {}

workspace_id = int(state.get("active_workspace_id", 1))
bus = EventBus.get_instance()
bus.workspace_action_requested.emit(
    WorkspaceIntent.IMPORT_GRAPH,
    WorkspacePayload(
        extra={
            "graph": graph_data,
            "workspace_id": workspace_id,
            "source": "discovery_analysis",
        }
    ),
)
n_ent = len(graph_data.get("entities", []))
n_rel = len(graph_data.get("relations", []))
result = f"Graph sent: {n_ent} nodes, {n_rel} edges"
"""


# ---------------------------------------------------------------------------
# Blueprint factory
# ---------------------------------------------------------------------------

def build_discovery_blueprint() -> AIActionBlueprint:
    """Return a ready-to-run blueprint for discovery LLM graph analysis.

    The caller must inject the following keys into MasterActionRunner's
    initial_state before starting:
        discovery_context  — from extractor_service.get_last_results_as_context()
        user_instructions  — from the user input dialog
        ontology_schema    — from build_ontology_schema_text(ontology_registry)
        selected_model     — active AI model string
        active_workspace_id — int workspace ID (default 1)

    The system prompt ("Discovery Analysis System") and user query template
    ("Discovery Analysis Query") are stored in PromptManager so users can
    edit them in the Prompts UI like any other built-in prompt.
    """
    steps = [
        ActionStep(
            step_id="generate_graph",
            step_type="LLM_QUERY",
            # System prompt → "Discovery Analysis System" in PromptManager (user-editable).
            # {ontology_schema} is resolved from runner state so it reflects the live registry.
            prompt_key="Discovery Analysis System",
            inputs={
                # User query → fetched from PromptManager via {prompt:...} syntax,
                # then {discovery_context} and {user_instructions} resolved from state.
                "query": "{prompt:Discovery Analysis Query}",
            },
            output_schema={
                "type": "object",
                "properties": {
                    "entities": {"type": "array"},
                    "relations": {"type": "array"},
                },
                "required": ["entities", "relations"],
            },
            output_key="raw_graph_json",
            ui_format="silent",
        ),
        ActionStep(
            step_id="validate_graph",
            step_type="GRAPH_VALIDATOR",
            inputs={"tuple_data": "{raw_graph_json}"},
            output_key="validated_graph",
            ui_format="silent",
        ),
        ActionStep(
            step_id="send_to_workspace",
            step_type="PYTHON_SCRIPT",
            inputs={"script": _IMPORT_GRAPH_SCRIPT},
            output_key="import_summary",
            ui_format="status",
            ui_title="Discovery Analysis Complete",
        ),
    ]

    return AIActionBlueprint(
        name="Discovery Graph Analysis",
        description=(
            "Analyses deterministically extracted entities and generates a workspace "
            "knowledge graph based on user instructions."
        ),
        mount_points=[],
        steps=steps,
    )
