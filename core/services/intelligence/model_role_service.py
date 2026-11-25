"""Model-role resolution for project intelligence tasks."""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from core.models.intelligence_models import ModelRole
from core.services.intelligence.model_role_defaults import (
    BALANCED_HARDWARE_RECOMMENDATIONS,
    HIGH_HARDWARE_RECOMMENDATIONS,
    LOW_HARDWARE_RECOMMENDATIONS,
    ROLE_RECOMMENDATIONS,
)


MODEL_ROLE_METADATA_KEY = "intelligence_model_roles"


class ModelRoleService:
    ROLE_CAPABILITIES = {
        ModelRole.EMBEDDING.value: "embedding",
        ModelRole.VISION.value: "vision",
    }

    def __init__(self, project_manager=None, llm_manager=None, ai_setup_service=None, bus=None):
        self.project_manager = project_manager
        self.llm_manager = llm_manager
        self.ai_setup_service = ai_setup_service
        self.bus = bus

    def list_roles(self) -> Dict[str, Dict[str, Any]]:
        models = self._available_models()
        return {
            role.value: self.describe_role(role.value, installed_models=models)
            for role in ModelRole
        }

    def describe_role(
        self,
        role: str,
        *,
        installed_models: Optional[list[str]] = None,
    ) -> Dict[str, Any]:
        model = self.resolve_role(role)
        installed = installed_models if installed_models is not None else self._available_models()
        capability = self.ROLE_CAPABILITIES.get(role)
        available = bool(model and (not installed or model in installed))
        capability_ok = True
        if model and capability and self.llm_manager:
            try:
                capability_ok = bool(self.llm_manager.supports_model_capability(model, capability))
            except Exception:
                capability_ok = False
        return {
            "role": role,
            "model": model,
            "available": available,
            "required_capability": capability or "",
            "capability_ok": capability_ok,
            "fallback": self._fallback_for_role(role),
            "recommendations": self.recommendations_for_role(role),
            "hardware_tier": self.hardware_tier(),
            "max_context_tokens": self.max_context_tokens_for_model(model, role=role),
            "guide": ROLE_RECOMMENDATIONS.get(role, {}).get("guide", ""),
        }

    def recommendations_for_role(self, role: str) -> list[str]:
        role = self._normalize_role(role)
        tier = self.hardware_tier()
        if tier == "low":
            choices = list(LOW_HARDWARE_RECOMMENDATIONS.get(role, []))
        elif tier == "balanced":
            choices = list(BALANCED_HARDWARE_RECOMMENDATIONS.get(role, []))
        else:
            choices = list(HIGH_HARDWARE_RECOMMENDATIONS.get(role, []))
        fallback = list(ROLE_RECOMMENDATIONS.get(role, {}).get("choices", []))
        for item in fallback:
            if item not in choices:
                choices.append(item)
        return choices[:3]

    def hardware_tier(self) -> str:
        info = self.hardware_profile()
        ram = float(info.get("ram_gb") or 0)
        vram = float(info.get("vram_gb") or 0)
        if vram >= 8 or ram >= 24:
            return "high"
        if vram >= 4 or ram >= 12:
            return "balanced"
        return "low"

    def hardware_profile(self) -> Dict[str, Any]:
        svc = self.ai_setup_service
        if svc and hasattr(svc, "get_hardware_info"):
            try:
                return dict(svc.get_hardware_info() or {})
            except Exception:
                pass
        return {"ram_gb": 0, "gpu_name": "Not detected", "vram_gb": 0}

    def max_context_tokens_for_model(self, model_name: str, *, role: str = "") -> int:
        model = str(model_name or "").lower()
        tier = self.hardware_tier()
        if role == ModelRole.FAST_WORKER.value or role == ModelRole.EXTRACTION_REPAIR.value:
            base = 4096 if tier == "low" else 8192
        elif role == ModelRole.MAIN_ANSWER.value:
            base = 8192 if tier == "low" else 16384 if tier == "balanced" else 32768
        else:
            base = 8192 if tier == "low" else 16384
        if any(token in model for token in ("0.5b", "1b", "tinyllama", "lfm2", "liquid")):
            base = min(base, 4096)
        elif any(token in model for token in ("e2b", "2b", "3b")):
            base = min(base, 8192 if tier != "low" else 6144)
        elif any(token in model for token in ("7b", "8b", "9b")):
            base = min(base, 16384 if tier != "low" else 8192)
        reported = None
        if self.llm_manager and model_name:
            try:
                reported = self.llm_manager.model_context_limit(model_name)
            except Exception:
                pass
        if isinstance(reported, int) and reported > 0:
            base = min(base, reported)
        return max(256, int(base))

    def resolve_role(self, role: str, fallback: str = "") -> str:
        role = self._normalize_role(role)
        stored = self._read_roles()
        if stored.get(role):
            return stored[role]
        return fallback or self._fallback_for_role(role)

    def set_role(self, role: str, model_name: str) -> None:
        role = self._normalize_role(role)
        roles = self._read_roles()
        old_embedding = roles.get(ModelRole.EMBEDDING.value) or self._fallback_for_role(ModelRole.EMBEDDING.value)
        if model_name:
            roles[role] = model_name
        else:
            roles.pop(role, None)
        self._write_roles(roles)
        if self.bus:
            try:
                self.bus.active_model_changed.emit(role, model_name)
            except Exception:
                pass
        if role == ModelRole.EMBEDDING.value:
            new_embedding = self.resolve_role(role)
            if new_embedding != old_embedding:
                self.mark_embedding_dependents_stale()

    def reset_role(self, role: str) -> None:
        self.set_role(role, "")

    def mark_embedding_dependents_stale(self) -> int:
        pm = self.project_manager
        db = getattr(pm, "db_intelligence", None)
        if not db or not getattr(pm, "_conn", None):
            return 0
        try:
            cur = pm._conn.execute(
                "UPDATE intelligence_artifacts SET status='stale', updated_at=CURRENT_TIMESTAMP "
                "WHERE model_role=? OR artifact_type='chunk'",
                (ModelRole.EMBEDDING.value,),
            )
            try:
                pm._conn.execute(
                    "UPDATE intelligence_artifact_embeddings SET status='stale', updated_at=CURRENT_TIMESTAMP"
                )
                pm._conn.execute(
                    "UPDATE intelligence_stage_status SET status='stale', updated_at=CURRENT_TIMESTAMP "
                    "WHERE stage='embeddings'"
                )
            except Exception:
                pass
            return cur.rowcount or 0
        except Exception:
            return 0

    def _read_roles(self) -> Dict[str, str]:
        roles: Dict[str, str] = {}
        svc = self.ai_setup_service
        if svc and hasattr(svc, "get_model_roles"):
            try:
                loaded = svc.get_model_roles()
                if isinstance(loaded, dict):
                    roles.update({str(k): str(v) for k, v in loaded.items() if v})
            except Exception:
                pass
        pm = self.project_manager
        if not pm:
            return roles
        raw = pm.get_metadata(MODEL_ROLE_METADATA_KEY, "{}")
        try:
            decoded = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(decoded, dict):
                roles.update({str(k): str(v) for k, v in decoded.items() if v})
        except Exception:
            pass
        return roles

    def _write_roles(self, roles: Dict[str, str]) -> None:
        svc = self.ai_setup_service
        if svc and hasattr(svc, "save_model_roles"):
            try:
                svc.save_model_roles(roles)
            except Exception:
                pass
        pm = self.project_manager
        if pm:
            pm.set_metadata(MODEL_ROLE_METADATA_KEY, json.dumps(roles, ensure_ascii=False))

    def _fallback_for_role(self, role: str) -> str:
        svc = self.ai_setup_service
        if role == ModelRole.MAIN_ANSWER.value:
            if svc and hasattr(svc, "get_default_chat_model"):
                return svc.get_default_chat_model() or ""
            return self._first_installed_recommendation(role)
        if role in {
            ModelRole.FAST_WORKER.value,
            ModelRole.RERANKER.value,
            ModelRole.EXTRACTION_REPAIR.value,
        }:
            helper = self._first_installed_recommendation(role)
            if helper:
                return helper
            if svc and hasattr(svc, "get_default_chat_model"):
                return svc.get_default_chat_model() or ""
        if role == ModelRole.EMBEDDING.value:
            if svc and hasattr(svc, "get_default_embedding_model"):
                return svc.get_default_embedding_model() or "nomic-embed-text"
            return "nomic-embed-text"
        if role == ModelRole.TRANSCRIPTION.value:
            return "small"
        return ""

    def _available_models(self) -> list[str]:
        if not self.llm_manager:
            return []
        try:
            return list(self.llm_manager.get_available_models() or [])
        except Exception:
            try:
                return list(self.llm_manager.available_models() or [])
            except Exception:
                return []

    def _first_installed_recommendation(self, role: str) -> str:
        installed = set(self._available_models())
        for model in self.recommendations_for_role(role):
            if model in installed:
                return model
        return ""

    @staticmethod
    def _normalize_role(role: str) -> str:
        if isinstance(role, ModelRole):
            return role.value
        role = str(role or "").strip()
        valid = {item.value for item in ModelRole}
        if role not in valid:
            raise ValueError(f"Unknown model role: {role}")
        return role
