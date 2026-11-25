"""Processing policy decisions for intelligence jobs."""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from core.models.intelligence_models import (
    ProcessingMode,
    ProcessingSelection,
    ProcessingStage,
)


PROJECT_SETTINGS_KEY = "intelligence_project_settings"


DEFAULT_PROJECT_SETTINGS: Dict[str, Any] = {
    "default_processing_mode": ProcessingMode.BALANCED.value,
    "background_level": "normal",
    "background_processing_enabled": True,
    # Stages run one at a time through ProcessRegistry; there is no
    # concurrency setting because nothing could honour it.
    "pause_background_while_answering": True,
    "auto_transcribe": "ask",
    "auto_embed_sources": True,
    "auto_extract_entities": True,
    "auto_extract_dates": True,
    "auto_extract_citations": True,
    "auto_extract_tables": True,
    "auto_classify_sentences": True,
    # Label heuristic-"unknown" sentences with the installed zero-shot
    # enhancer (no effect until it is installed in AI Setup).
    "use_sentence_role_enhancer": True,
    # Optional Entities evidence installed in AI Setup; off until each passes its gold gate.
    "use_gliner": False,
    "use_gazetteer": False,
    "use_language_model": False,
    "auto_summarize_small_documents": False,
    "auto_create_graph_seeds": False,
    "auto_run_vision": False,
    "store_transcripts": True,
    "store_embeddings": True,
    "store_summaries": True,
    "store_claims": True,
    "store_graph_seeds": True,
    "generative_policy": "on_demand",
    "ask_processing_on_import": True,
}


class ProcessingPolicyService:
    def __init__(
        self, project_manager=None, source_format_registry=None, stage_registry=None
    ):
        self.project_manager = project_manager
        self.source_format_registry = source_format_registry
        self.stage_registry = stage_registry

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

    def set_source_selection(
        self, source_id: str, selection: ProcessingSelection | Dict[str, Any]
    ) -> None:
        if isinstance(selection, dict):
            selection = ProcessingSelection(**{
                key: selection.get(key)
                for key in ProcessingSelection.__dataclass_fields__
                if key in selection
            })
        db = getattr(self.project_manager, "db_intelligence", None)
        if not db:
            return
        settings = db.get_source_settings(source_id)
        settings.update({
            "processing_mode": ProcessingMode(selection.preset).value,
            "stage_overrides": dict(selection.stage_overrides or {}),
            "transcription_policy": selection.transcription_policy,
            # This is deliberately fixed to explicit/on-demand. The field is
            # stored for transparent inspection and future compatible readers.
            "generative_policy": "on_demand",
        })
        db.set_source_settings(source_id, settings)
        if selection.remember_for_project:
            self.set_project_settings({
                "default_processing_mode": selection.preset,
                "auto_transcribe": selection.transcription_policy,
                "generative_policy": "on_demand",
            })

    def selection_for_source(self, source: Dict[str, Any]) -> ProcessingSelection:
        source_id = source.get("id") or source.get("source_id") or ""
        project = self.get_project_settings()
        stored = self.get_source_settings(source_id)
        return ProcessingSelection(
            preset=stored.get("processing_mode")
            or project.get("default_processing_mode", ProcessingMode.BALANCED.value),
            stage_overrides=dict(stored.get("stage_overrides") or {}),
            transcription_policy=stored.get("transcription_policy")
            or project.get("auto_transcribe", "ask"),
            generative_policy="on_demand",
        )

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
        selection = self.selection_for_source(source)
        category = self._category_for_type(source.get("source_type") or "unknown")
        stages = self.stages_for_preset(
            selection.preset, category,
            transcribe=self.should_auto_transcribe(source), settings=settings,
        )
        overrides = dict(selection.stage_overrides or {})
        stages = [stage for stage in stages if overrides.get(stage, True)]
        stages += [stage for stage, on in overrides.items() if on and stage not in stages]
        return self._supported(stages, category)

    def stages_for_preset(self, preset: str, category: str = "document", *,
                          transcribe: bool = True,
                          settings: Optional[Dict[str, Any]] = None,
                          expand: bool = True) -> List[str]:
        """Stages a preset selects for a source category (before overrides).

        ``expand=False`` omits prerequisites so a plan can explain which
        stages were chosen and which were pulled in as dependencies.
        """
        settings = settings if settings is not None else self.get_project_settings()
        if preset == ProcessingMode.MANUAL.value:
            return []
        stages = [ProcessingStage.SOURCE_REGISTERED.value]
        stages += _base_stages(category, transcribe)
        if preset in {ProcessingMode.BALANCED.value, ProcessingMode.DEEP.value}:
            stages += _balanced_stages(category, settings)
        if preset == ProcessingMode.DEEP.value:
            stages += _deep_stages(category, settings)
        if self.stage_registry is not None:        # plugin stages that opted into this preset
            stages += [spec.id for spec in self.stage_registry.all() if preset in (spec.default_presets or [])]
        return self._supported(_dedupe(stages), category, expand=expand)

    def _supported(self, stages: List[str], category: str, expand: bool = True) -> List[str]:
        if not self.stage_registry:
            return _dedupe(stages)
        stages = [
            stage for stage in stages
            if (spec := self.stage_registry.get(stage))
            and spec.enabled and category in spec.source_categories
        ]
        if expand:
            stages = self.stage_registry.expand_dependencies(stages, category)
        return _dedupe(stages)

    def should_auto_transcribe(self, source: Dict[str, Any]) -> bool:
        policy = self.selection_for_source(source).transcription_policy
        return str(policy).lower() in {"always", "auto"}

    def is_small_source(self, source: Dict[str, Any]) -> bool:
        path = source.get("path") or source.get("source_path") or ""
        try:
            size_mb = os.path.getsize(path) / (1024 * 1024)
        except OSError:
            size_mb = 0
        return size_mb <= 10

    def category_for_type(self, source_type: str) -> str:
        return self._category_for_type(source_type)

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


_TEXT_CATEGORIES = {"document", "image"}


def _base_stages(category: str, transcribe: bool) -> List[str]:
    if category in _TEXT_CATEGORIES:
        return [ProcessingStage.PARSED_BLOCKS.value, ProcessingStage.SECTIONS.value,
                ProcessingStage.FULL_TEXT_INDEX.value]
    if category == "data":
        return [ProcessingStage.TABLES.value, ProcessingStage.FULL_TEXT_INDEX.value]
    if category in {"audio", "video"} and transcribe:
        return [ProcessingStage.TRANSCRIPT.value]
    return []


def _balanced_stages(category: str, settings: Dict[str, Any]) -> List[str]:
    stages: List[str] = []
    if category in _TEXT_CATEGORIES:
        for key, stage in (
            ("auto_extract_entities", ProcessingStage.ABBREVIATIONS),
            ("auto_extract_entities", ProcessingStage.ENTITIES),
            ("auto_extract_entities", ProcessingStage.ENTITY_LINKING),
            ("auto_extract_dates", ProcessingStage.DATES),
            ("auto_extract_citations", ProcessingStage.CITATIONS),
            ("auto_extract_citations", ProcessingStage.CROSS_REFERENCES),
            ("auto_extract_tables", ProcessingStage.TABLES),
            ("auto_classify_sentences", ProcessingStage.CLAIMS),
        ):
            if settings.get(key, True):
                stages.append(stage.value)
    if settings.get("auto_embed_sources", True) and settings.get("store_embeddings", True):
        stages.append(ProcessingStage.EMBEDDINGS.value)
    return stages


def _deep_stages(category: str, settings: Dict[str, Any]) -> List[str]:
    if category == "data":
        return []
    stages: List[str] = []
    if settings.get("store_summaries", True):
        stages.append(ProcessingStage.SUMMARIES.value)
    if settings.get("store_graph_seeds", True):
        stages.append(ProcessingStage.GRAPH_SEEDS.value)
    if settings.get("auto_run_vision", False) and category == "image":
        stages.append(ProcessingStage.FIGURES.value)
    stages.append(ProcessingStage.CLAIMS.value)
    return stages


def _dedupe(items: List[str]) -> List[str]:
    seen = set()
    result = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
