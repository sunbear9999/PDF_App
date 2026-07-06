from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional


LLM_SETTING_KEYS = ("temperature", "num_predict", "num_ctx", "json_mode", "stop_sequences")
OVERRIDES_METADATA_KEY = "blueprint_llm_overrides"


@dataclass(frozen=True)
class LLMSettingsStep:
    blueprint_id: str
    blueprint_label: str
    step_id: str
    step_path: str
    step_type: str
    defaults: Dict[str, Any]
    overrides: Dict[str, Any]
    effective: Dict[str, Any]

    def as_dict(self) -> dict:
        return {
            "blueprint_id": self.blueprint_id,
            "blueprint_label": self.blueprint_label,
            "step_id": self.step_id,
            "step_path": self.step_path,
            "step_type": self.step_type,
            "defaults": copy.deepcopy(self.defaults),
            "overrides": copy.deepcopy(self.overrides),
            "effective": copy.deepcopy(self.effective),
        }


class LLMSettingsService:
    """Project-scoped LLM option discovery and override resolution.

    Blueprints remain immutable templates.  Overrides are stored in project
    metadata and applied at runtime using blueprint registry id + nested step
    path, which makes the feature work for built-ins, custom blueprints, and
    plugin-registered blueprints.
    """

    ENGINE_DEFAULTS: Dict[str, Any] = {
        "temperature": 0.7,
        "num_predict": 2048,
        "num_ctx": 16384,
        "json_mode": False,
    }

    def __init__(
        self,
        *,
        project_manager=None,
        blueprint_registry=None,
        blueprint_manager=None,
        prompt_manager=None,
        node_type_registry=None,
    ):
        self.project_manager = project_manager
        self.blueprint_registry = blueprint_registry
        self.blueprint_manager = blueprint_manager
        self.prompt_manager = prompt_manager
        self.node_type_registry = node_type_registry

    # ------------------------------------------------------------------
    # Public catalog API
    # ------------------------------------------------------------------

    def list_blueprints(self, include_empty: bool = True) -> List[dict]:
        items = []
        seen: set[str] = set()
        seen_labels: set[str] = set()
        for bp_id, label, blueprint in self._iter_blueprint_instances():
            if bp_id in seen:
                continue
            normalized_label = str(label or bp_id).strip().lower()
            if normalized_label in seen_labels:
                continue
            seen.add(bp_id)
            seen_labels.add(normalized_label)
            steps = self.discover_steps(bp_id, blueprint, label)
            if include_empty or steps:
                items.append({
                    "id": bp_id,
                    "label": label,
                    "description": getattr(blueprint, "description", ""),
                    "step_count": len(steps),
                    "has_llm_steps": bool(steps),
                })
        return sorted(items, key=lambda item: item["label"].lower())

    def list_blueprint_steps(self, blueprint_id: str) -> List[dict]:
        found = self._create_blueprint(blueprint_id)
        if not found:
            return []
        label, blueprint = found
        return [item.as_dict() for item in self.discover_steps(blueprint_id, blueprint, label)]

    def discover_steps(self, blueprint_id: str, blueprint, blueprint_label: Optional[str] = None) -> List[LLMSettingsStep]:
        label = blueprint_label or getattr(blueprint, "name", blueprint_id) or blueprint_id
        overrides = self._overrides_for_blueprint(blueprint_id)
        result: List[LLMSettingsStep] = []
        for step in getattr(blueprint, "steps", []) or []:
            result.extend(self._discover_step(blueprint_id, label, step, "", overrides))
        deduped: List[LLMSettingsStep] = []
        seen_paths: set[str] = set()
        for item in result:
            if item.step_path in seen_paths:
                continue
            seen_paths.add(item.step_path)
            deduped.append(item)
        return deduped

    def resolve_options(
        self,
        *,
        blueprint_id: Optional[str],
        step_path: Optional[str],
        step=None,
        base_options: Optional[dict] = None,
    ) -> Dict[str, Any]:
        defaults = self.step_defaults(step, base_options)
        if not blueprint_id or not step_path:
            return defaults
        overrides = self._overrides_for_blueprint(blueprint_id).get(step_path, {})
        merged = dict(defaults)
        merged.update({k: v for k, v in (overrides or {}).items() if k in LLM_SETTING_KEYS})
        if self._step_forces_json(step):
            merged["json_mode"] = True
        return merged

    def set_step_overrides(self, blueprint_id: str, step_path: str, overrides: dict) -> None:
        all_overrides = self._read_overrides()
        bp_overrides = all_overrides.setdefault(blueprint_id, {})
        cleaned = self._clean_override_dict(overrides)
        if cleaned:
            bp_overrides[step_path] = cleaned
        else:
            bp_overrides.pop(step_path, None)
        if not bp_overrides:
            all_overrides.pop(blueprint_id, None)
        self._write_overrides(all_overrides)

    def reset_step_overrides(self, blueprint_id: str, step_path: str) -> None:
        self.set_step_overrides(blueprint_id, step_path, {})

    def step_defaults(self, step=None, base_options: Optional[dict] = None) -> Dict[str, Any]:
        defaults = dict(self.ENGINE_DEFAULTS)
        options = base_options if base_options is not None else getattr(step, "llm_options", {})
        if isinstance(options, dict):
            defaults.update({k: v for k, v in options.items() if k in LLM_SETTING_KEYS})
        if self._step_forces_json(step):
            defaults["json_mode"] = True
        return defaults

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def _discover_step(self, blueprint_id: str, label: str, step, prefix: str, overrides: dict) -> List[LLMSettingsStep]:
        path = f"{prefix}.{step.step_id}" if prefix else str(step.step_id)
        items: List[LLMSettingsStep] = []
        if self._is_llm_capable_step(step):
            step_overrides = overrides.get(path, {})
            defaults = self.step_defaults(step)
            effective = dict(defaults)
            effective.update({k: v for k, v in step_overrides.items() if k in LLM_SETTING_KEYS})
            if self._step_forces_json(step):
                effective["json_mode"] = True
            items.append(LLMSettingsStep(
                blueprint_id=blueprint_id,
                blueprint_label=label,
                step_id=step.step_id,
                step_path=path,
                step_type=step.step_type,
                defaults=defaults,
                overrides=copy.deepcopy(step_overrides),
                effective=copy.deepcopy(effective),
            ))

        sub_bp = getattr(step, "inputs", {}).get("sub_blueprint") if getattr(step, "step_type", "") == "FOREACH" else None
        if sub_bp and hasattr(sub_bp, "steps"):
            for sub_step in sub_bp.steps:
                items.extend(self._discover_step(blueprint_id, label, sub_step, f"{path}.sub", overrides))

        for branch_name in ("if_true", "if_false"):
            for branch_step in getattr(step, branch_name, []) or []:
                items.extend(self._discover_step(blueprint_id, label, branch_step, f"{path}.{branch_name}", overrides))
        return items

    def _is_llm_capable_step(self, step) -> bool:
        step_type = getattr(step, "step_type", "")
        if step_type == "LLM_QUERY":
            return True
        if not self.node_type_registry:
            return False
        node_type = self.node_type_registry.get_by_step_type(step_type)
        step_cls = getattr(node_type, "step_cls", None) if node_type else None
        if not step_cls:
            return False
        try:
            from core.engine.steps.llm_query_step import LLMQueryStep
            return issubclass(step_cls, LLMQueryStep)
        except Exception:
            return False

    @staticmethod
    def _step_forces_json(step) -> bool:
        if step is None:
            return False
        return bool(getattr(step, "output_schema", None)) or getattr(step, "step_type", "") == "LLM_SCHEMA_QUERY"

    def _iter_blueprint_instances(self) -> Iterable[tuple[str, str, Any]]:
        seen: set[str] = set()
        if self.blueprint_manager and hasattr(self.blueprint_manager, "blueprints"):
            for bp_id, blueprint in self.blueprint_manager.blueprints.items():
                seen.add(bp_id)
                yield bp_id, f"{getattr(blueprint, 'name', bp_id) or bp_id} (Custom)", copy.deepcopy(blueprint)

        if self.blueprint_registry:
            for definition in self.blueprint_registry.all():
                if definition.id in seen:
                    continue
                try:
                    blueprint = definition.create(pm=self.prompt_manager)
                except TypeError:
                    try:
                        blueprint = definition.create()
                    except Exception:
                        continue
                except Exception:
                    continue
                yield definition.id, definition.label or definition.id, blueprint

    def _create_blueprint(self, blueprint_id: str) -> Optional[tuple[str, Any]]:
        if self.blueprint_manager and blueprint_id in getattr(self.blueprint_manager, "blueprints", {}):
            bp = copy.deepcopy(self.blueprint_manager.blueprints[blueprint_id])
            return f"{getattr(bp, 'name', blueprint_id) or blueprint_id} (Custom)", bp
        if self.blueprint_registry:
            definition = self.blueprint_registry.get(blueprint_id)
            if definition:
                try:
                    return definition.label or definition.id, definition.create(pm=self.prompt_manager)
                except TypeError:
                    return definition.label or definition.id, definition.create()
                except Exception:
                    return None
        return None

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def _read_overrides(self) -> dict:
        pm = self.project_manager
        if not pm:
            return {}
        raw = pm.get_metadata(OVERRIDES_METADATA_KEY, "{}")
        try:
            data = json.loads(raw or "{}") if isinstance(raw, str) else raw
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _write_overrides(self, data: dict) -> None:
        if self.project_manager:
            self.project_manager.set_metadata(OVERRIDES_METADATA_KEY, json.dumps(data, sort_keys=True))

    def _overrides_for_blueprint(self, blueprint_id: str) -> dict:
        value = self._read_overrides().get(blueprint_id, {})
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _clean_override_dict(overrides: dict) -> dict:
        cleaned = {}
        for key, value in (overrides or {}).items():
            if key not in LLM_SETTING_KEYS or value in (None, ""):
                continue
            cleaned[key] = value
        return cleaned

    @classmethod
    def _coerce_options(cls, options: dict) -> dict:
        coerced = dict(options or {})
        for key in ("num_predict", "num_ctx"):
            if key in coerced and coerced[key] not in (None, ""):
                try:
                    coerced[key] = max(1, int(coerced[key]))
                except Exception:
                    coerced[key] = cls.ENGINE_DEFAULTS[key]
        if "temperature" in coerced and coerced["temperature"] not in (None, ""):
            try:
                coerced["temperature"] = max(0.0, min(2.0, float(coerced["temperature"])))
            except Exception:
                coerced["temperature"] = cls.ENGINE_DEFAULTS["temperature"]
        if "json_mode" in coerced:
            raw = coerced["json_mode"]
            coerced["json_mode"] = raw if isinstance(raw, bool) else str(raw).strip().lower() in {"1", "true", "yes", "on"}
        if "stop_sequences" in coerced and isinstance(coerced["stop_sequences"], str):
            text = coerced["stop_sequences"].strip()
            if text:
                try:
                    parsed = json.loads(text)
                    coerced["stop_sequences"] = parsed if isinstance(parsed, list) else [text]
                except Exception:
                    coerced["stop_sequences"] = [line.strip() for line in text.splitlines() if line.strip()]
            else:
                coerced.pop("stop_sequences", None)
        return coerced
