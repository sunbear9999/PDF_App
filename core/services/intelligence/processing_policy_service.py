"""Processing policy decisions for intelligence jobs."""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from core.models.intelligence_models import ProcessingMode, ProcessingStage


PROJECT_SETTINGS_KEY = "intelligence_project_settings"


DEFAULT_PROJECT_SETTINGS: Dict[str, Any] = {
    "default_processing_mode": ProcessingMode.BALANCED.value,
    "background_level": "normal",
    "background_processing_enabled": True,
    "max_deterministic_jobs": 2,
    "max_model_jobs": 1,
    "pause_background_while_answering": True,
    "auto_transcribe": "ask",
    "auto_embed_sources": True,
    "auto_extract_entities": True,
    "auto_extract_dates": True,
    "auto_extract_citations": True,
    "auto_extract_tables": True,
    "auto_summarize_small_documents": False,
    "auto_create_graph_seeds": False,
    "auto_run_vision": False,
    "store_transcripts": True,
    "store_embeddings": True,
    "store_summaries": True,
    "store_claims": True,
    "store_graph_seeds": True,
}


class ProcessingPolicyService:
    def __init__(self, project_manager=None, source_format_registry=None):
        self.project_manager = project_manager
        self.source_format_registry = source_format_registry

    def get_project_settings(self) -> Dict[str, Any]:
        pm = self.project_manager
        settings = dict(DEFAULT_PROJECT_SETTINGS)
        if not pm:
            return settings
        raw = pm.get_metadata(PROJECT_SETTINGS_KEY, "{}")
        try:
            loaded = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(loaded, dict):
                settings.update(loaded)
        except Exception:
            pass
        return settings

    def set_project_settings(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        settings = self.get_project_settings()
        settings.update(updates or {})
        pm = self.project_manager
        if pm:
            pm.set_metadata(PROJECT_SETTINGS_KEY, json.dumps(settings, ensure_ascii=False))
        return settings

    def get_source_settings(self, source_id: str) -> Dict[str, Any]:
        db = getattr(self.project_manager, "db_intelligence", None)
        if not db:
            return {}
        return db.get_source_settings(source_id)

    def set_source_processing_mode(self, source_id: str, mode: str) -> None:
        mode = ProcessingMode(mode).value
        db = getattr(self.project_manager, "db_intelligence", None)
        if not db:
            return
        settings = db.get_source_settings(source_id)
        settings["processing_mode"] = mode
        db.set_source_settings(source_id, settings)

    def processing_mode_for_source(self, source: Dict[str, Any]) -> str:
        source_id = source.get("id") or source.get("source_id") or ""
        per_source = self.get_source_settings(source_id)
        if per_source.get("processing_mode"):
            return per_source["processing_mode"]
        return self.get_project_settings().get(
            "default_processing_mode", ProcessingMode.BALANCED.value
        )

    def stages_for_source(self, source: Dict[str, Any]) -> List[str]:
        settings = self.get_project_settings()
        if not settings.get("background_processing_enabled", True):
            return []
        mode = self.processing_mode_for_source(source)
        if mode == ProcessingMode.MANUAL.value:
            return []

        source_type = source.get("source_type") or "unknown"
        category = self._category_for_type(source_type)
        stages = [ProcessingStage.SOURCE_REGISTERED.value]

        if mode in (ProcessingMode.MINIMAL.value, ProcessingMode.BALANCED.value, ProcessingMode.DEEP.value):
            if category in {"document", "image"}:
                stages.extend([
                    ProcessingStage.PARSED_BLOCKS.value,
                    ProcessingStage.SECTIONS.value,
                    ProcessingStage.FULL_TEXT_INDEX.value,
                ])
            if category in {"audio", "video"} and self.should_auto_transcribe(source):
                stages.append(ProcessingStage.TRANSCRIPT.value)

        if mode == ProcessingMode.MINIMAL.value:
            return _dedupe(stages)

        if category in {"document", "image"}:
            if settings.get("auto_extract_entities", True):
                stages.append(ProcessingStage.ENTITIES.value)
            if settings.get("auto_extract_dates", True):
                stages.append(ProcessingStage.DATES.value)
            if settings.get("auto_extract_citations", True):
                stages.append(ProcessingStage.CITATIONS.value)
            if settings.get("auto_extract_tables", True):
                stages.append(ProcessingStage.TABLES.value)
        if settings.get("auto_embed_sources", True) and settings.get("store_embeddings", True):
            stages.append(ProcessingStage.EMBEDDINGS.value)

        if mode == ProcessingMode.DEEP.value:
            if settings.get("auto_summarize_small_documents", False) and self.is_small_source(source):
                stages.append(ProcessingStage.SUMMARIES.value)
            if settings.get("auto_create_graph_seeds", False):
                stages.append(ProcessingStage.GRAPH_SEEDS.value)
            if settings.get("auto_run_vision", False) and category == "image":
                stages.append(ProcessingStage.FIGURES.value)
            stages.append(ProcessingStage.CLAIMS.value)

        return _dedupe(stages)

    def should_auto_transcribe(self, source: Dict[str, Any]) -> bool:
        policy = self.get_project_settings().get("auto_transcribe", "ask")
        return str(policy).lower() == "always"

    def is_small_source(self, source: Dict[str, Any]) -> bool:
        path = source.get("path") or source.get("source_path") or ""
        try:
            size_mb = os.path.getsize(path) / (1024 * 1024)
        except OSError:
            size_mb = 0
        return size_mb <= 10

    def _category_for_type(self, source_type: str) -> str:
        if self.source_format_registry:
            spec = self.source_format_registry.get(source_type)
            if spec:
                return spec.category
        if source_type in {"video", "audio"}:
            return source_type
        if source_type in {"image"}:
            return "image"
        return "document"


def _dedupe(items: List[str]) -> List[str]:
    seen = set()
    result = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
