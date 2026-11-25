"""Shared plan readiness, guarded selection and deterministic processing commands."""
from dataclasses import asdict
from core.models.intelligence_models import ProcessingSelection, ProcessingMode


def project_token(service):
    db = getattr(service.project_manager, "db_intelligence", None)
    return (id(db), id(getattr(db, "_conn", None)))


def snapshot(service, source_id, selection=None):
    plan = service.plan_source(source_id, selection)
    if not plan:
        raise ValueError("Select a current source to inspect processing.")
    source = service._source(source_id)
    models = service.models.list_dependencies() if service.models else []
    catalog = {entry["id"]: entry for entry in models}
    rows = [readiness(service, plan, item, catalog) for item in plan.items]
    saved = service.policy.selection_for_source(source)
    return {**plan.to_dict(), "items": rows, "models": models, "selection": asdict(saved),
            "project_identity": list(project_token(service)),
            "background_enabled": service.policy.get_project_settings().get("background_processing_enabled", True)}


def readiness(service, plan, item, catalog):
    row = asdict(item)
    state, detail = "ready", "Prerequisites are current; this stage can run locally."
    spec = service.stages.get(item.stage)
    if item.status in {"unsupported", "disabled"} or spec.method_kind == "generative" or spec.resource_class == "generative":
        state, detail = "unavailable", "Not available through local source processing."
    else:
        pending = [dependency for dependency in item.dependencies if not service.scheduler or not service.scheduler.is_stage_current(plan.source_id, dependency)]
        if pending:
            state, detail = "prerequisites", "Will first prepare: " + ", ".join(service.stages.get(key).display_label for key in pending if service.stages.get(key))
        missing = [catalog[key] for key in item.model_dependencies if key in catalog and catalog[key]["status"] != "ready"]
        if any(not entry.get("optional", True) for entry in missing):
            state, detail = "blocked_model", "Required model unavailable: " + ", ".join(entry["label"] for entry in missing if not entry.get("optional", True))
        elif missing:
            detail += " Optional model unavailable; the recorded would-run method explains the fallback."
        if spec.model_role and service.roles:
            role = service.roles.describe_role(spec.model_role)
            if not role.get("available") or not role.get("capability_ok") or not getattr(getattr(service.roles, "llm_manager", None), "ai_enabled", False):
                state, detail = "blocked_model", "Configure and enable a compatible model for " + spec.model_role.replace("_", " ") + " in AI Setup."
        elif spec.model_role:
            state, detail = "blocked_model", "Model-role service is unavailable."
        if plan.category in {"audio", "video"} and item.stage != "source_registered":
            if not service.scheduler or not service.scheduler.has_source_transcript(plan.source_id):
                state, detail = "blocked_input", "Create a transcript in the source viewer first. Index transcript uses an existing transcript; it does not transcribe."
    row.update(readiness=state, readiness_detail=detail)
    return row


def save_selection(service, source_id, selection, expected=None):
    check_project(service, expected)
    source = service._source(source_id)
    if not source:
        raise ValueError("Source is unavailable.")
    if not isinstance(selection, dict):
        raise ValueError("Choose a valid source processing plan.")
    selection = dict(selection)
    ProcessingMode(selection.get("preset", "balanced"))
    overrides = selection.get("stage_overrides") or {}
    if not isinstance(overrides, dict) or any(type(value) is not bool for value in overrides.values()):
        raise ValueError("Stage choices must be Yes or No.")
    category = service.policy.category_for_type(source.get("source_type") or "")
    if any(not (spec := service.stages.get(key)) or not spec.enabled or category not in spec.source_categories for key in overrides):
        raise ValueError("Stage choices must be supported registered stages.")
    if any(enabled and (service.stages.get(key).method_kind == "generative" or service.stages.get(key).resource_class == "generative") for key, enabled in overrides.items()):
        raise ValueError("Generative stages require a separate registered workflow.")
    if selection.get("transcription_policy", "ask") not in {"ask", "always", "never", "auto"}:
        raise ValueError("Invalid transcription policy.")
    service.policy.set_source_selection(source_id, selection)
    return snapshot(service, source_id)


def check_project(service, expected):
    if expected is not None and tuple(map(int, expected)) != project_token(service):
        raise ValueError("Project changed; processing was cancelled.")


def process_source(service, source_id, mode, stages, expected, cancelled):
    if mode not in {"missing", "stale", "selected", "force"}:
        raise ValueError("Choose a registered local processing action.")
    cancelled = cancelled or (lambda: False)
    check_project(service, expected)
    expected = project_token(service)
    plan = service.plan_source(source_id)
    if not plan or not service.scheduler:
        raise ValueError("Source processing is unavailable.")
    requested = list(stages or []) if mode in {"selected", "force"} else [item.stage for item in plan.items if item.selected and (mode != "stale" or item.status == "stale")]
    ordered = service.stages.expand_dependencies(requested, plan.category)
    for key in ordered:
        spec = service.stages.get(key)
        if not spec or not spec.enabled or plan.category not in spec.source_categories or spec.method_kind == "generative" or spec.resource_class == "generative":
            raise ValueError("Only supported local stages can run in this plan.")
    results = []
    for key in ordered:
        if cancelled() or expected != project_token(service):
            return {"status": "cancelled", "results": results}
        if key not in requested or mode != "force":
            if service.scheduler.is_stage_current(source_id, key):
                results.append({"stage": key, "status": "reused"})
                continue
        if key in requested:
            service.scheduler.mark_downstream_stale(source_id, key)
        result = service.scheduler.run_stage_inline(source_id, key,
            cancel_check=lambda: cancelled() or expected != project_token(service))
        if expected != project_token(service):
            return {"status": "cancelled", "results": results}
        results.append(result)
        if result.get("status") == "cancelled":
            return {"status": "cancelled", "results": results}
    return {"status": "partial" if any(row.get("status") in {"blocked", "failed"} for row in results) else "complete", "results": results}
