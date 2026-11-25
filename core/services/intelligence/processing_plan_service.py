"""One processing plan for previews and execution.

The same policy + stage registry that the scheduler executes produce the
plan users see: which stages a selection runs for each source category, why
each is included (preset, override, or required by another stage), each
stage's current state for an existing source (ready, stale, blocked, …) and
which method/model would run. Import dialogs, the Intelligence dock and
plugins read this instead of keeping their own stage tables.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from core.models.producer_models import ProducerIdentity
from core.services.intelligence.processing_controls import snapshot, save_selection, process_source

from core.models.intelligence_models import (
    ProcessingMode, ProcessingSelection, StageStatus,
)

NOT_SELECTED = "not_selected"
UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class StagePlanItem:
    stage: str
    label: str
    description: str = ""
    cost_hint: str = ""
    method_kind: str = ""
    selected: bool = False
    reason: str = ""
    status: str = NOT_SELECTED
    detail: str = ""
    provider: str = ""
    recorded_provider: str = ""
    dependencies: List[str] = field(default_factory=list)
    model_dependencies: List[str] = field(default_factory=list)
    readiness: str = ""
    readiness_detail: str = ""


@dataclass(frozen=True)
class SourcePlan:
    category: str
    preset: str
    items: List[StagePlanItem] = field(default_factory=list)
    source_id: str = ""
    title: str = ""

    @property
    def runnable(self) -> List[str]:
        return [item.stage for item in self.items if item.selected]

    def to_dict(self) -> Dict[str, Any]:
        return {**asdict(self), "runnable": self.runnable}


class ProcessingPlanService:
    def __init__(self, policy_service: Any = None, stage_registry: Any = None,
                 scheduler: Any = None, project_manager: Any = None,
                 model_dependency_service=None, model_role_service=None) -> None:
        self.policy = policy_service
        self.stages = stage_registry
        self.scheduler = scheduler
        self.project_manager = project_manager
        self.models, self.roles = model_dependency_service, model_role_service

    @property
    def db(self):
        return getattr(self.project_manager, "db_intelligence", None)

    def get_processing_snapshot(self, source_id="", selection=None):
        sources = self.project_manager.list_sources() if self.project_manager else []
        current = source_id and any(row.get("id") == source_id for row in sources)
        result = self.processing_snapshot(source_id, selection) if current else {"source_id": "", "items": [], "selection": None}
        return {**result, "sources": sources}

    # ---------------------------------------------------------- previews

    def preview(self, selection: Optional[Dict[str, Any]], category: str) -> SourcePlan:
        """Plan for a not-yet-imported source of ``category``."""
        selection = _selection(selection, self.policy)
        requested = self._requested(selection, category, transcribe=_transcribes(selection))
        return SourcePlan(category=category, preset=selection.preset,
                          items=self._items(requested, category))

    def plan_source(self, source_id: str, selection=None) -> Optional[SourcePlan]:
        """Plan plus live status for an existing source."""
        source = self._source(source_id)
        if not source or not self.policy:
            return None
        category = self.policy.category_for_type(source.get("source_type") or "")
        selection = _selection(selection, self.policy) if selection is not None else self.policy.selection_for_source(source)
        requested = self._requested(selection, category,
                                    transcribe=_transcribes(selection))
        items = [self._with_status(source, item)
                 for item in self._items(requested, category)]
        return SourcePlan(category=category, preset=selection.preset, items=items,
                          source_id=source.get("id") or source_id,
                          title=source.get("title") or source.get("path") or "")

    # ------------------------------------------------------------ helpers

    def _requested(self, selection: ProcessingSelection, category: str,
                   *, transcribe: bool) -> Dict[str, str]:
        """Selected stage → reason, before dependency expansion."""
        reasons = {stage: "preset" for stage in self.policy.stages_for_preset(
            selection.preset, category, transcribe=transcribe, expand=False)} if self.policy else {}
        for stage, enabled in (selection.stage_overrides or {}).items():
            if enabled:
                reasons[stage] = "your override"
            else:
                reasons.pop(stage, None)
        return reasons

    def _items(self, requested: Dict[str, str], category: str) -> List[StagePlanItem]:
        registry = self.stages
        if not registry:
            return []
        expanded = registry.expand_dependencies(
            [s for s in requested if _supported(registry, s, category)], category)
        items = []
        for spec in registry.all(include_disabled=True):
            supported = category in spec.source_categories
            selected = spec.id in expanded
            reason = (requested.get(spec.id) or self._required_by(spec.id, expanded, category)
                      if selected else ("not available for this source type"
                                        if not supported else ""))
            items.append(StagePlanItem(
                stage=spec.id, label=spec.display_label, description=spec.description,
                cost_hint=spec.cost_hint, method_kind=spec.method_kind, selected=selected,
                reason=reason,
                status=(StageStatus.DISABLED.value if not spec.enabled else StageStatus.NOT_RUN.value if selected
                        else NOT_SELECTED if supported else UNSUPPORTED),
                dependencies=registry.dependencies_for(spec.id, category) if supported else [],
                model_dependencies=list(spec.model_dependencies),
            ))
        return items

    def _required_by(self, stage: str, expanded: List[str], category: str) -> str:
        dependents = [s for s in expanded if stage in self.stages.dependencies_for(s, category)]
        names = ", ".join(self.stages.get(s).display_label for s in dependents if self.stages.get(s))
        return f"required by {names}" if names else "prerequisite"

    def _with_status(self, source: Dict[str, Any], item: StagePlanItem) -> StagePlanItem:
        if item.status in {UNSUPPORTED, StageStatus.DISABLED.value}:
            return item
        source_id = source.get("id") or ""
        db = getattr(self.project_manager, "db_intelligence", None)
        row = (db.get_stage_statuses(source_id).get(item.stage) if db else None) or {}
        status = row.get("status") or item.status
        detail = row.get("error_text") or ""
        if status == StageStatus.COMPLETE.value and self.scheduler and not \
                self.scheduler.is_stage_current(source_id, item.stage):
            status, detail = StageStatus.STALE.value, "inputs, settings or model changed"
        provider = ""
        if self.scheduler and hasattr(self.scheduler, "stage_identity"):
            identity = self.scheduler.stage_identity(source_id, item.stage)
            provider = identity.describe() if identity else ""
        latest = db.latest_stage_run(source_id, item.stage) if db else None
        recorded = ""
        if latest:
            identity = ProducerIdentity.from_metadata((latest.get("metadata") or {}).get("producer"))
            recorded = identity.describe() if identity else "Method not recorded for this historical run"
        return StagePlanItem(**{**asdict(item), "status": status, "detail": detail,
                                "provider": provider, "recorded_provider": recorded})

    def processing_snapshot(self, source_id, selection=None):
        return snapshot(self, source_id, selection)

    def save_selection(self, source_id, selection, expected_project=None):
        return save_selection(self, source_id, selection, expected_project)

    def process_source(self, source_id, mode="missing", stages=None, expected_project=None, cancel_check=None):
        return process_source(self, source_id, mode, stages, expected_project, cancel_check)

    def _source(self, source_id: str) -> Optional[Dict[str, Any]]:
        pm = self.project_manager
        for source in pm.list_sources() if pm else []:
            if source.get("id") == source_id:
                return source
        return None


def _selection(raw: Optional[Dict[str, Any]], policy: Any) -> ProcessingSelection:
    raw = dict(raw or {})
    if not raw.get("preset") and policy:
        raw["preset"] = policy.get_project_settings().get(
            "default_processing_mode", ProcessingMode.BALANCED.value)
    fields = ProcessingSelection.__dataclass_fields__
    return ProcessingSelection(**{key: value for key, value in raw.items() if key in fields})


def _transcribes(selection: ProcessingSelection) -> bool:
    return str(selection.transcription_policy).lower() in {"always", "auto"}


def _supported(registry: Any, stage: str, category: str) -> bool:
    spec = registry.get(stage)
    return bool(spec and spec.enabled and category in spec.source_categories)

