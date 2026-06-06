import copy
import re

from PySide6.QtCore import QObject
from core.events.event_bus import EventBus
from core.models.prompt_models import BlueprintPromptUsage
from core.events.domains.metadata_events import PromptIntent, PromptPayload
class PromptAppService(QObject):
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

    def _extract_step_prompts(self, step) -> list:
        explicit = set()
        implicit = set()

        prompt_key = getattr(step, "prompt_key", None)
        if prompt_key:
            if prompt_key == "{chat_persona}":
                explicit.update(["General Assistant", "RAG Agent Mode"])
            elif "{" not in str(prompt_key):
                explicit.add(prompt_key)

        texts_to_scan = [getattr(step, "system_prompt", "")]
        if isinstance(getattr(step, "inputs", None), dict):
            texts_to_scan.extend([str(value) for value in step.inputs.values()])

        for text in texts_to_scan:
            if text:
                explicit.update(re.findall(r"\{prompt:(.*?)\}", text))

        if getattr(step, "step_type", "") == "LLM_QUERY":
            opts = getattr(step, "llm_options", {}) or {}
            if getattr(step, "output_schema", None) or opts.get("json_mode"):
                implicit.add("JSON Schema Enforcer")

            ui_fmt = getattr(step, "ui_format", "")
            if ui_fmt == "chat_widgets":
                implicit.add("Format Enforcer - Chat Widgets")
            elif ui_fmt == "data_table":
                implicit.add("Format Enforcer - Data Table")
            elif ui_fmt == "card_grid":
                implicit.add("Format Enforcer - Card Grid")

            if getattr(step, "inline_citations", False):
                implicit.add("Inline Citation Directive")

            req_context = getattr(step, "required_context", [])
            if "manifest" in req_context:
                implicit.add("Manifest Update Directive")
                implicit.add("Context Inject - Manifest")
            if "workspace" in req_context:
                implicit.add("Context Inject - Workspace")
            if "selected_nodes" in req_context:
                implicit.add("Context Inject - Selected")
            if "analyses" in req_context:
                implicit.add("Context Inject - Analyses")

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
        if explicit or implicit:
            result.append(BlueprintPromptUsage(
                step_id=getattr(step, "step_id", "Unknown Step"),
                step_type=getattr(step, "step_type", "UNKNOWN"),
                explicit=sorted(explicit),
                implicit=sorted(implicit),
            ))
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
