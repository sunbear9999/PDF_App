"""
core/engine/steps/gather_registry_context_step.py

Reads the BlueprintNodeTypeRegistry at runtime and serialises all registered step
types (especially plugin-contributed ones) into a human-readable documentation
block that is injected into the Blueprint Architect system prompt via the
{plugin_step_docs} state variable.

This gives the AI Builder full, up-to-date knowledge of every step type that is
available when a workflow is built — including steps added by plugins after boot.
"""
from __future__ import annotations

from core.engine.steps.base_step import BaseStep
from core.plugins.plugin_step_protocol import StepContext

# Step types that are implementation details of the analysis pipeline; not
# directly usable in user-authored blueprints, so we exclude them from the
# architect's context to avoid confusion.
_ANALYSIS_INTERNALS = {
    "ANALYSIS_CONTRACT", "DOCUMENT_CHUNK", "ANALYSIS_COMPACT",
    "ANALYSIS_FINALIZE", "ANALYSIS_SEND_TO_WORKSPACE", "GRAPH_VALIDATOR",
    "GATHER_REGISTRY_CONTEXT",
}


def _format_input_schema(step_cls) -> str:
    """Return a compact, text-only field list from a step class's input_schema."""
    schema = getattr(step_cls, "input_schema", {}) or {}
    if not schema:
        return ""
    lines = []
    for key, meta in schema.items():
        req = " (required)" if meta.get("required") else ""
        type_str = meta.get("type", "string")
        label = meta.get("label", key)
        lines.append(f"    {key}: {type_str}{req} — {label}")
    return "\n" + "\n".join(lines)


class GatherRegistryContextStep(BaseStep):
    step_type = "GATHER_REGISTRY_CONTEXT"
    label = "Gather Registry Context"
    category = "Meta"
    description = (
        "Builds a dynamic doc string of all registered step types (including plugin ones) "
        "for injection into the Blueprint Architect system prompt via {plugin_step_docs}."
    )
    input_schema: dict = {}

    def execute(self, context: StepContext, inputs: dict):
        registry = getattr(context, "node_type_registry", None)
        if not registry:
            return self.build_result("(registry unavailable)")

        core_lines: list[str] = []
        plugin_sections: list[str] = []

        for nt in registry.all():
            if nt.step_type in _ANALYSIS_INTERNALS:
                continue

            field_docs = _format_input_schema(nt.step_cls) if nt.step_cls else ""
            entry = (
                f"  {nt.step_type} [{nt.category}] — {nt.description or nt.label}"
                f"{field_docs}"
            )

            if nt.plugin_id:
                plugin_sections.append(
                    f"  Plugin: {nt.plugin_id}\n{entry}"
                )
            else:
                core_lines.append(entry)

        parts: list[str] = []
        if core_lines:
            parts.append("REGISTERED CORE STEP TYPES:\n" + "\n".join(core_lines))
        if plugin_sections:
            parts.append("PLUGIN-CONTRIBUTED STEP TYPES:\n" + "\n".join(plugin_sections))

        if not parts:
            return self.build_result("(no step types registered)")

        return self.build_result("\n\n".join(parts))
