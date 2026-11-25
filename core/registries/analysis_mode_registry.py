"""
AnalysisModeRegistry — the single registry of analysis modes.

An analysis mode (Q&A, argument map, claim tracking, extraction, or a custom
user-defined one) is a *registered blueprint* plus a small descriptor. Running
a mode dispatches ``WorkflowIntent.RUN_BLUEPRINT`` through WorkflowRunnerService
— it never calls the LLM directly (rules.md §1.1) — so modes stay traceable,
prompt-managed, and plugin-addable via ``api.intelligence.register_analysis_mode``.

Mirrors the DeterministicPassRegistry convention (instance-based, plugin_id
cleanup). Modes are discovered at runtime from this registry and filtered by
mount point, never hardcoded (rules.md §13.3).
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional

from core.models.intelligence_models import ModelCallBudget
from core.models.analysis_contract import normalize_graph_contract
from core.models.analysis_requirements import normalize_requirements, template_prerequisites
from core.services.intelligence.analysis_contract_compiler import AnalysisContractCompiler


@dataclass
class AnalysisModeSpec:
    id: str
    label: str
    description: str
    blueprint_id: str = "intelligence_analysis_mode"    # universal analysis blueprint
    params_schema: Dict[str, Any] = field(default_factory=dict)
    mount_points: List[str] = field(default_factory=lambda: ["analysis_mode"])
    required_stages: List[str] = field(default_factory=list)
    required_artifact_types: List[str] = field(default_factory=list)
    artifact_requirements: Optional[List[Dict[str, Any]]] = None
    scope_options: List[str] = field(default_factory=lambda: ["source", "project"])
    result_kind: str = "narrative"
    execution_class: str = "generative_blueprint"
    model_call_budget: ModelCallBudget = field(default_factory=ModelCallBudget)
    cache_policy: str = "content_and_provenance"
    output_artifact_types: List[str] = field(default_factory=list)
    renderer_id: str = "analysis.default"
    empty_state: str = "Run the required local intelligence stages to create this view."
    # Data-driven graph semantics consumed by the one universal analysis
    # blueprint.  Plugins and user-created modes extend this contract instead
    # of registering a mode-specific LLM blueprint.
    graph_contract: Dict[str, Any] = field(default_factory=dict)
    plugin_id: Optional[str] = None


class AnalysisModeRegistry:
    def __init__(self, compiler=None, stage_registry=None) -> None:
        self._modes: Dict[str, AnalysisModeSpec] = {}
        self.compiler = compiler or AnalysisContractCompiler()
        self.stage_registry = stage_registry
        self.template_errors = {}

    def register(self, spec: AnalysisModeSpec) -> None:
        self._modes[spec.id] = self.validate(spec)

    def validate(self, spec: AnalysisModeSpec) -> AnalysisModeSpec:
        if not spec.id:
            raise ValueError("AnalysisModeSpec requires a stable id.")
        for key in ('max_calls', 'max_input_tokens', 'max_output_tokens'):
            value = getattr(spec.model_call_budget, key, None)
            if type(value) is not int or value < 0:
                raise ValueError(f"Analysis budget '{key}' must be a non-negative integer.")
        if not isinstance(spec.required_stages, list):
            raise ValueError('Required stages must be a list.')
        stages = list(spec.required_stages)
        if any(not isinstance(stage, str) or not stage for stage in stages):
            raise ValueError('Required stages must be non-empty registered names.')
        if self.stage_registry:
            for stage in stages:
                if not self.stage_registry.get(stage) or not self.stage_registry.get(stage).enabled:
                    raise ValueError(f"Unknown required intelligence stage '{stage}'.")
        contract = spec.graph_contract
        if spec.result_kind in {"graph", "composite"}:
            contract = self.compiler.compile(contract)
            if int(spec.model_call_budget.max_calls or 0) < 2 or not spec.model_call_budget.allow_repair_calls:
                contract['allow_repair_call'] = False
        spec = replace(spec, graph_contract=contract, required_stages=stages,
                       artifact_requirements=normalize_requirements(
                           spec.artifact_requirements, spec.required_artifact_types))
        return spec

    def unregister(self, mode_id: str) -> None:
        self._modes.pop(mode_id, None)

    def get(self, mode_id: str) -> Optional[AnalysisModeSpec]:
        return self._modes.get(mode_id)

    def all(self) -> List[AnalysisModeSpec]:
        return list(self._modes.values())

    def for_mount(self, mount_point: str) -> List[AnalysisModeSpec]:
        return [m for m in self._modes.values() if mount_point in m.mount_points]

    def remove_by_plugin(self, plugin_id: str) -> None:
        for mid in [k for k, v in self._modes.items() if v.plugin_id == plugin_id]:
            self._modes.pop(mid, None)

    def sync_saved_templates(self, templates: List[Dict[str, Any]]) -> None:
        """One invalid saved mode must not hide all the valid modes."""
        self.remove_by_plugin('__user_analysis_templates__')
        self.template_errors = {}
        for template in templates or []:
            if not isinstance(template, dict) or not template.get('id'):
                continue
            try:
                self.register(self.saved_template_spec(template))
            except (ValueError, TypeError) as exc:
                self.template_errors[str(template['id'])] = str(exc)

    def saved_template_spec(self, template):
        prerequisites = template_prerequisites(template)
        return AnalysisModeSpec(
            id=f"custom:{template['id']}",
            label=str(template.get('name') or template.get('title') or 'Custom Analysis'),
            description='Saved user analysis contract',
            params_schema={'doc_path': {'type': 'string', 'label': 'Document', 'required': True}},
            mount_points=['analysis_mode', 'document_analysis'],
            required_stages=prerequisites['required_stages'],
            artifact_requirements=prerequisites['artifact_requirements'],
            result_kind='graph', execution_class='prepared_blueprint',
            model_call_budget=ModelCallBudget(allowed_roles=['main_answer_model'], max_calls=2,
                max_input_tokens=28000, max_output_tokens=3800, allow_repair_calls=True),
            output_artifact_types=['analysis_result'], renderer_id='intelligence.graph',
            graph_contract=self.compiler.compile(template=template),
            plugin_id='__user_analysis_templates__')


def builtin_analysis_modes() -> List[AnalysisModeSpec]:
    """The built-in modes. Each composes shared primitives via its blueprint.

    The blueprint_id names the AIActionBlueprint the mode runs; Q&A composes
    the retrieve primitive (BUILD_RETRIEVAL_PLAN → ARTIFACT_RETRIEVE →
    PACK_ARTIFACT_CONTEXT) + an LLM synthesis step, argument-map reuses the
    hierarchical analysis template blueprint, etc.
    """
    return [
        AnalysisModeSpec(
            id="qa",
            label="Q&A",
            description="Ask a question; the answer is synthesized from a tiered, "
                        "multi-signal retrieval package and must cite segment IDs.",
            blueprint_id="intelligence_analysis_mode",
            params_schema={
                "query": {"type": "string", "label": "Question", "required": True},
                "allowed_source_ids": {"type": "array", "label": "Limit to sources"},
                "max_results": {"type": "integer", "label": "Max passages", "default": 10},
            },
            mount_points=["analysis_mode", "research_tab"],
            required_artifact_types=["sentence", "transcript_segment"],
            artifact_requirements=[{"artifact_types": ["sentence", "transcript_segment"], "match": "any", "scope": "selection"}],
            result_kind="narrative",
            model_call_budget=ModelCallBudget(
                allowed_roles=["main_answer_model"], max_calls=1,
                max_input_tokens=24000, max_output_tokens=2500,
            ),
            output_artifact_types=["analysis_answer"],
            renderer_id="analysis.narrative",
        ),
        AnalysisModeSpec(
            id="argument_map",
            label="Argument Map",
            description="Build a complete grounded graph, then optionally organize it "
                        "with one model call and one targeted repair call.",
            blueprint_id="intelligence_analysis_mode",
            params_schema={
                "doc_path": {"type": "string", "label": "Document", "required": True},
                "analysis_focus": {"type": "string", "label": "Research focus"},
            },
            mount_points=["analysis_mode", "document_analysis"],
            required_stages=["parsed_blocks", "sections", "claims"],
            required_artifact_types=["argument_unit"],
            artifact_requirements=[{"artifact_types": ["argument_unit"], "match": "all", "scope": "selection"}],
            result_kind="graph",
            execution_class="prepared_blueprint",
            model_call_budget=ModelCallBudget(
                allowed_roles=["main_answer_model"], max_calls=2,
                max_input_tokens=28000, max_output_tokens=3500,
                allow_repair_calls=True,
            ),
            output_artifact_types=["analysis_result"],
            renderer_id="intelligence.graph",
            empty_state="Run local parsing, section detection, and claim extraction first.",
            graph_contract={
                "purpose": (
                    "Map the argument about the focus as a compact chain from the "
                    "overall claim or methodology, through the reasons it is expected "
                    "to work, to exact textual evidence. Preserve objections and limits."
                ),
                "node_types": [
                    "methodology", "claim", "reasoning", "evidence",
                    "counterclaim", "limitation",
                ],
                "relation_types": [
                    "supports", "reasons", "refutes", "contradicts",
                    "critiques", "part_of",
                ],
                "required_shape": [
                    "At least one claim or methodology node directly answering the focus.",
                    "Reasoning nodes explain why the claim or methodology works.",
                    "Every reasoning branch ends in one or more evidence nodes.",
                    "Evidence nodes are verbatim text hydrated from artifact IDs.",
                    "Use evidence --supports--> reasoning --reasons--> claim.",
                ],
                "density": "standard",
                "root_roles": ["methodology", "claim"],
                "required_roles": ["claim", "reasoning", "evidence"],
                "min_branches": 4,
                "min_sections": 4,
                "min_proposition_nodes": 20,
                "max_proposition_nodes": 40,
                "min_evidence_nodes": 8,
                "max_evidence_nodes": 24,
                "max_nodes": 64,
                "max_relations": 96,
                "requires_exact_quotes": True,
                "require_connected": True,
                "allow_repair_call": True,
                "evidence_selection": {
                    "quotes_per_section": 3,
                    "max_sections": 4,
                    "max_quotes": 12,
                },
            },
        ),
        AnalysisModeSpec(
            id="timeline",
            label="Timeline",
            description="Build contextual event candidates with calendar uncertainty and source evidence, using the same event query as Intelligence Timeline. No LLM runs.",
            blueprint_id="intelligence_analysis_mode",
            params_schema={"allowed_source_ids": {"type": "array", "label": "Limit to sources"}},
            required_stages=["dates"],
            required_artifact_types=[],  # A completed Dates pass may legitimately find no observations.
            artifact_requirements=[],
            result_kind="timeline",
            execution_class="prepared_blueprint",
            model_call_budget=ModelCallBudget(max_calls=0),
            output_artifact_types=["analysis_result"],
            renderer_id="intelligence.timeline",
        ),
        AnalysisModeSpec(
            id="argument_map_enriched",
            label="Explain / Enrich Graph with AI",
            description="Build the complete local graph, then use a bounded model overlay "
                        "with one optional repair to propose evidence-bound structure.",
            blueprint_id="intelligence_analysis_mode",
            params_schema={"doc_path": {"type": "string", "label": "Document", "required": True}},
            mount_points=["analysis_mode", "document_analysis"],
            required_stages=["parsed_blocks", "sections", "claims"],
            required_artifact_types=["argument_unit"],
            artifact_requirements=[{"artifact_types": ["argument_unit"], "match": "all", "scope": "selection"}],
            result_kind="composite",
            execution_class="prepared_blueprint",
            model_call_budget=ModelCallBudget(
                allowed_roles=["main_answer_model"], max_calls=2,
                max_input_tokens=24000, max_output_tokens=2500,
                allow_repair_calls=True,
            ),
            output_artifact_types=["analysis_result", "graph_suggestion"],
            renderer_id="intelligence.graph",
        ),
        AnalysisModeSpec(
            id="claim_tracking",
            label="Claim Tracking",
            description="Pin a claim; reuse project artifacts and create a typed graph "
                        "of supporting, contradicting, qualifying, and repeated evidence.",
            blueprint_id="intelligence_analysis_mode",
            params_schema={
                "claim": {"type": "string", "label": "Claim", "required": True},
                "scope": {"type": "string", "label": "Scope (document|project)", "default": "project"},
            },
            mount_points=["analysis_mode"],
            required_stages=["parsed_blocks", "sections", "claims"],
            required_artifact_types=["argument_unit"],
            artifact_requirements=[{"artifact_types": ["argument_unit"], "match": "all", "scope": "selection"}],
            result_kind="graph",
            execution_class="prepared_blueprint",
            model_call_budget=ModelCallBudget(
                allowed_roles=["main_answer_model"], max_calls=2,
                max_input_tokens=24000, max_output_tokens=2500,
                allow_repair_calls=True,
            ),
            output_artifact_types=["analysis_result"],
            renderer_id="intelligence.graph",
            graph_contract={
                "purpose": (
                    "Track the supplied claim across the selected sources and distinguish "
                    "support, contradiction, qualification, repetition, and uncertainty."
                ),
                "node_types": [
                    "tracked_claim", "supporting_claim", "contradicting_claim",
                    "qualification", "reasoning", "evidence", "uncertainty",
                ],
                "relation_types": [
                    "supports", "contradicts", "critiques", "reasons",
                    "similar_to", "refutes",
                ],
                "required_shape": [
                    "Exactly one tracked_claim root states the user's claim.",
                    "Separate supporting, contradicting, and qualifying branches.",
                    "Each branch is grounded by exact evidence nodes where available.",
                ],
                "root_roles": ["tracked_claim"],
                "required_roles": ["tracked_claim", "evidence"],
                "min_branches": 3,
                "max_nodes": 42,
                "max_relations": 56,
            },
        ),
        AnalysisModeSpec(
            id="custom_graph",
            label="Custom Graph Analysis",
            description="Apply a user-authored analysis contract to reusable intelligence artifacts.",
            blueprint_id="intelligence_analysis_mode",
            params_schema={
                "analysis_focus": {"type": "string", "label": "Research focus", "required": True},
                "custom_instructions": {"type": "string", "label": "Graph contract", "required": True},
            },
            mount_points=["analysis_mode", "document_analysis"],
            required_stages=["parsed_blocks", "sections", "claims", "summaries"],
            required_artifact_types=["argument_unit"],
            artifact_requirements=[{"artifact_types": ["argument_unit"], "match": "all", "scope": "selection"}],
            result_kind="graph",
            execution_class="prepared_blueprint",
            model_call_budget=ModelCallBudget(
                allowed_roles=["main_answer_model"], max_calls=2,
                max_input_tokens=28000, max_output_tokens=3500,
                allow_repair_calls=True,
            ),
            output_artifact_types=["analysis_result"],
            renderer_id="intelligence.graph",
            graph_contract={
                "purpose": (
                    "Apply the user's graph instructions to the supplied artifacts while "
                    "remaining evidence-bound and analytically selective."
                ),
                "node_types": [
                    "claim", "methodology", "reasoning", "evidence",
                    "finding", "counterclaim", "limitation", "concept",
                ],
                "relation_types": [
                    "supports", "contradicts", "critiques", "reasons",
                    "implies", "derived_from", "part_of", "similar_to",
                ],
                "required_shape": [
                    "Follow the custom instructions.",
                    "Every synthesized node cites source artifact IDs.",
                    "Evidence nodes are exact text hydrated from an artifact.",
                ],
                "max_nodes": 48,
                "max_relations": 64,
            },
        ),
        AnalysisModeSpec(
            id="extraction",
            label="Schema Extraction",
            description="Schema-driven field extraction; regex/NER first, LLM only "
                        "for ambiguous fields, batched structured JSON.",
            blueprint_id="intelligence_analysis_mode",
            params_schema={"schema": {"type": "json", "label": "Field schema", "required": True}},
            mount_points=["analysis_mode"],
            required_artifact_types=["sentence", "entity"],
            artifact_requirements=[{"artifact_types": ["sentence", "entity"], "match": "any", "scope": "selection"}],
            result_kind="table",
            model_call_budget=ModelCallBudget(
                allowed_roles=["fast_worker_model"], max_calls=1,
                max_input_tokens=16000, max_output_tokens=2500,
            ),
            output_artifact_types=["schema_extraction"],
            renderer_id="analysis.table",
        ),
        AnalysisModeSpec(
            id="summaries",
            label="Abstractive Summary",
            description="Optional LLM abstractive summary of the source, persisted as a "
                        "model-produced knowledge artifact (created_by=model).",
            blueprint_id="intelligence_analysis_mode",
            params_schema={"source_id": {"type": "string", "label": "Source", "required": True}},
            mount_points=["analysis_mode"],
            required_artifact_types=["summary"],
            model_call_budget=ModelCallBudget(
                allowed_roles=["main_answer_model"], max_calls=1,
                max_input_tokens=24000, max_output_tokens=2500,
            ),
            output_artifact_types=["summary"],
            renderer_id="analysis.narrative",
        ),
    ]

