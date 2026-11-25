"""Shared source-scoped occurrence browsing and project view preferences."""
import json
from dataclasses import replace

from core.models.intelligence_models import ArtifactQuery, SourceScope
from core.db.intelligence_artifact_queries import coerce_query

PREFERENCES_KEY = "intelligence_browser_preferences"


def occurrences(service, artifact_id, query=None):
    row = service.db.get_artifact(artifact_id)
    if not row or row.get("status") != "complete" or row.get("review_state") in {"rejected", "superseded"}:
        raise ValueError("Choose a current entity or bibliography entry.")
    query = coerce_query(query or ArtifactQuery())
    scope = query.source_scope
    if row.get("artifact_type") == "entity":
        query = replace(query, artifact_types=["entity_mention"], parent_artifact_ids=[artifact_id])
    elif row.get("artifact_type") == "citation_entry":
        source = row.get("source_id")
        if not source:
            raise ValueError("Bibliography entry has no recorded source.")
        if not scope.unresolved_refs and not ((scope.source_paths or scope.display_names) and not scope.source_ids):
            scope = SourceScope(unresolved_refs=[source]) if scope.source_ids and source not in scope.source_ids else SourceScope(source_ids=[source])
        query = replace(query, source_scope=scope, artifact_types=["in_text_citation"], bibliography_ids=[artifact_id])
    else:
        raise ValueError("Occurrence browsing supports entities and bibliography entries.")
    page = service.query_artifacts(replace(query, statuses=["complete"],
        review_states=["unreviewed", "verified", "corrected"], current_only=True))
    list_sources = getattr(service.project_manager, "list_sources", None)
    names = {source.get("id"): source.get("title") or source.get("path") or "Untitled source"
             for source in list_sources() if source.get("id")} if list_sources else {}
    from core.db.mention_snippets import attach
    attach(service.db, [item for item in page.get("items") or [] if item.get("artifact_type") == "entity_mention"])
    return {**page, "parent": row, "source_labels": names}


def get_preferences(service):
    pm = service.project_manager
    raw = pm.get_metadata(PREFERENCES_KEY, "{}") if hasattr(pm, "get_metadata") else "{}"
    try:
        state = validate_preferences(json.loads(raw or "{}"))
    except (ValueError, TypeError):
        state = {}
    return {"state": state, "project_identity": (id(service.db), id(getattr(service.db, "_conn", None)))}


def save_preferences(service, state, expected_project=None):
    if expected_project is not None and tuple(expected_project) != (id(service.db), id(getattr(service.db, "_conn", None))):
        raise ValueError("Project changed; view preferences were not saved.")
    state = validate_preferences(state)
    pm = service.project_manager
    if hasattr(pm, "set_metadata"):
        pm.set_metadata(PREFERENCES_KEY, json.dumps(state))
    return {"saved": True}


def validate_preferences(state):
    if not isinstance(state, dict):
        raise ValueError("View preferences must be named values.")
    result = {key: str(state[key])[:4096] for key in ("source_id", "view_id", "search") if isinstance(state.get(key), str)}
    result["views"] = {}
    views = state.get("views") or {}
    if not isinstance(views, dict):
        raise ValueError("Saved views must be named settings.")
    for identity, view in list(views.items())[:100]:
        if not isinstance(identity, str) or not isinstance(view, dict):
            continue
        result["views"][identity] = _view_preferences(view)
    return result


def _view_preferences(view):
    result = {}
    sizes = view.get("splitter")
    if isinstance(sizes, list) and len(sizes) == 2 and all(type(value) is int and 0 <= value <= 100000 for value in sizes):
        result["splitter"] = sizes
    expanded = view.get("expanded") or []
    result["expanded"] = [value for value in expanded if isinstance(value, str)][:1000] if isinstance(expanded, list) else []
    if type(view.get("sort_column")) is int and 0 <= view["sort_column"] <= 3:
        result["sort_column"] = view["sort_column"]
    if view.get("sort_order") in {"ascending", "descending"}:
        result["sort_order"] = view["sort_order"]
    filters = _filter_preferences(view.get("filters"))
    if filters:
        result["filters"] = filters
    return result


MAX_FILTER_VALUES = 100


def _filter_preferences(filters):
    """Sort key and ticked filter values of one tab's filter bar."""
    if not isinstance(filters, dict):
        return {}
    result = {"sort": filters["sort"][:64]} if isinstance(filters.get("sort"), str) else {}
    menus = {}
    for field, menu in list((filters.get("menus") or {}).items())[:10] if isinstance(filters.get("menus"), dict) else []:
        if isinstance(field, str) and isinstance(menu, dict):
            selected = menu.get("selected") if isinstance(menu.get("selected"), list) else []
            menus[field[:64]] = {"selected": [v[:128] for v in selected if isinstance(v, str)][:MAX_FILTER_VALUES],
                                 "hide_measures": bool(menu.get("hide_measures")),
                                 "require_all": bool(menu.get("require_all"))}
    if menus:
        result["menus"] = menus
    return result
