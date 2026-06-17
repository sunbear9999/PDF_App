import copy
from PySide6.QtCore import QObject
from core.events.event_bus import EventBus
from core.models.prompt_models import ANALYSIS_STATE_PROMPTS, collect_step_prompt_usage
from core.events.domains.metadata_events import PromptIntent, PromptPayload
class PromptAppService(QObject):
    ANALYSIS_STATE_PROMPTS = ANALYSIS_STATE_PROMPTS

    def __init__(self, prompt_manager, blueprint_manager=None, blueprint_registry=None, step_manager=None):
        super().__init__()
        self.pm = prompt_manager
        self.blueprint_manager = blueprint_manager
        self.blueprint_registry = blueprint_registry
        self.step_manager = step_manager
        self.bus = EventBus.get_instance()
        self.bus.prompt_action_requested.connect(self._handle_intent)

    def _handle_intent(self, intent: PromptIntent, payload: PromptPayload):
        if intent == PromptIntent.SAVE:
            self.pm.save_prompt(payload["key"], payload["content"])
        elif intent == PromptIntent.DELETE:
            prompts = getattr(self.pm, 'prompts', getattr(self.pm, 'custom_prompts', {}))
            if payload["key"] in prompts:
                del prompts[payload["key"]]
                if hasattr(self.pm, '_save_custom_prompts'): self.pm._save_custom_prompts()
                elif hasattr(self.pm, 'save_custom_prompts'): self.pm.save_custom_prompts()
        elif intent == PromptIntent.RESTORE:
            self.pm.restore_default(payload["key"])

    def get_prompts_dict(self) -> dict:
        if hasattr(self.pm, "prompts"):
            return self.pm.prompts
        if hasattr(self.pm, "custom_prompts"):
            return self.pm.custom_prompts
        if hasattr(self.pm, "_prompts"):
            return self.pm._prompts
        return {}

    def get_prompt_categories(self) -> dict:
        return getattr(self.pm, "CATEGORIES", {}) or {}

    def is_core_prompt(self, prompt_key: str) -> bool:
        return any(prompt_key in keys for keys in self.get_prompt_categories().values())

    def list_blueprints(self) -> list:
        blueprints = []
        seen = set()

        if self.blueprint_manager and hasattr(self.blueprint_manager, "blueprints"):
            for name, blueprint in self.blueprint_manager.blueprints.items():
                blueprints.append({"id": name, "label": f"{name} (Custom)", "blueprint": copy.deepcopy(blueprint)})
                seen.add(name)

        if self.blueprint_registry:
            for definition in self.blueprint_registry.all():
                try:
                    blueprint = definition.create(pm=self.pm)
                except TypeError:
                    blueprint = definition.create(self.pm)
                except Exception as exc:
                    print(f"[Prompt Editor] Failed to instantiate blueprint '{definition.id}': {exc}")
                    continue
                label = definition.label or definition.id
                if label in seen:
                    label = f"{label} (Default)"
                blueprints.append({"id": definition.id, "label": label, "blueprint": blueprint})
                seen.add(label)

        return sorted(blueprints, key=lambda item: item["label"])

    def get_blueprint_prompt_usage(self, blueprint) -> dict:
        step_prompts = []
        for step in getattr(blueprint, "steps", []):
            step_prompts.extend(self._extract_step_prompts(self._resolve_library_step(step)))

        global_prompts = sorted({
            prompt_key
            for step_data in step_prompts
            for prompt_key in step_data.implicit
            if prompt_key.startswith("Context Inject") or prompt_key == "Manifest Update Directive"
        })

        return {
            "global_prompts": global_prompts,
            "steps": [usage.as_dict() for usage in step_prompts],
        }

    def get_analysis_template_prompt_usage(self, templates: list) -> list:
        usage = []
        for template in templates or []:
            if not isinstance(template, dict):
                continue
            rendered = self.render_analysis_template_prompts(template)
            usage.append({
                "id": template.get("id", ""),
                "title": template.get("title") or template.get("name") or "Unnamed Analysis Mode",
                "blueprint": "DefaultBlueprints.get_analysis_blueprint",
                "prompts": [
                    template.get("chunk_prompt_key") or "Graph Analysis Chunk Observations System",
                    template.get("synthesis_prompt_key") or "Graph Analysis Synthesis System",
                    template.get("master_prompt_key") or "Graph Analysis Master System",
                    template.get("chunk_query_prompt_key") or "Graph Analysis Chunk Observations Query",
                    template.get("synthesis_query_prompt_key") or "Graph Analysis Synthesis Query",
                    template.get("master_query_prompt_key") or "Graph Analysis Master Query",
                    "Graph Analysis Compact Output Contract",
                    "Graph Analysis Argument Chain Directive",
                ],
                "rendered": rendered,
                "node_types": list(template.get("node_types") or []),
                "relation_types": list(template.get("relation_types") or []),
            })
        return usage

    def render_analysis_template_prompts(self, template: dict) -> list:
        try:
            from core.engine.analysis_runtime import AnalysisRuntime
            runtime = AnalysisRuntime(None, self.pm, bus=object())
            contract = runtime.build_contract(template)
            contract["prompt_contract"] = runtime.build_prompt_contract(contract)
            return [
                {
                    "title": "Step analysis_contract: chunk quote-selection system prompt",
                    "content": runtime.chunk_system_prompt(template, contract),
                },
                {
                    "title": "Step analyze_chunk_graph: quote-selection query template",
                    "content": self._render_analysis_query_preview(runtime.chunk_query_prompt(template), chunk=True),
                },
                {
                    "title": "Step analysis_contract: synthesis system prompt",
                    "content": runtime.synthesis_system_prompt(template, contract),
                },
                {
                    "title": "Step argument_synthesis_pass: synthesis query template",
                    "content": self._render_analysis_query_preview(runtime.synthesis_query_prompt(template), chunk=False, synthesis=True),
                },
                {
                    "title": "Step analysis_contract: final graph system prompt",
                    "content": runtime.master_system_prompt(template, contract),
                },
                {
                    "title": "Step master_diagram_pass: final graph query template",
                    "content": self._render_analysis_query_preview(runtime.master_query_prompt(template), chunk=False),
                },
                {
                    "title": "Registry contract injected as template_schema",
                    "content": contract.get("prompt_contract", ""),
                },
            ]
        except Exception as exc:
            return [{
                "title": "Prompt render failed",
                "content": f"Could not render analysis prompts for this template: {exc}",
            }]

    def _render_analysis_query_preview(self, prompt: str, chunk: bool, synthesis: bool = False) -> str:
        if chunk:
            return (
                str(prompt or "")
                .replace("{item.page_range}", "[current chunk page range]")
                .replace("{item.text}", "[current chunk PDF text]")
                .replace("{item.template_instructions}", "[analysis mode instructions]")
                .replace("{item.template_schema}", "[rendered registry contract]")
                .replace("{analysis_limits.max_quotes_per_chunk}", "[template quote limit]")
                .replace("{analysis_limits.quote_words}", "[template target quote words]")
                .replace("{analysis_limits.max_quote_words}", "[template max quote words]")
                .replace("{analysis_limits.explanation_words}", "[template explanation words]")
            )
        rendered = str(prompt or "").replace("{master_input}", "[numbered compact quote JSON from all sections]")
        if synthesis:
            return rendered
        return rendered.replace("{argument_synthesis}", "[plain-text synthesis produced by argument_synthesis_pass]")

    def _extract_step_prompts(self, step) -> list:
        usage = collect_step_prompt_usage(step)

        sub_steps_data = []
        if getattr(step, "step_type", "") == "FOREACH":
            sub_bp = getattr(step, "inputs", {}).get("sub_blueprint")
            if sub_bp and hasattr(sub_bp, "steps"):
                for sub_step in sub_bp.steps:
                    sub_steps_data.extend(self._extract_step_prompts(self._resolve_library_step(sub_step)))

        for branch_attr in ["if_true", "if_false"]:
            for branch_step in getattr(step, branch_attr, []) or []:
                sub_steps_data.extend(self._extract_step_prompts(self._resolve_library_step(branch_step)))

        result = []
        if usage.explicit or usage.implicit:
            result.append(usage)
        return result + sub_steps_data

    def _resolve_library_step(self, step):
        if getattr(step, "step_ref", None) and self.step_manager:
            library_step = self.step_manager.get_step(step.step_ref)
            if library_step:
                base_dict = copy.deepcopy(library_step.__dict__)
                empty_step = type(step)(step_id="dummy")
                override_dict = {
                    key: value for key, value in step.__dict__.items()
                    if getattr(empty_step, key, None) != value and value is not None and value != "LIBRARY_REF"
                }
                base_dict.update(override_dict)
                return type(step)(**base_dict)
        return step
