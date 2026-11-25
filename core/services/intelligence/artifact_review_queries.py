"""Reusable review queue access and project-scoped progress persistence."""
from dataclasses import replace
import json

from core.db.intelligence_artifact_queries import coerce_query
from core.models.intelligence_models import SourceScope
from core.services.intelligence.decision_queries import relation_queue

PROGRESS_KEY = "intelligence_review_progress"


def review_queue(service, registry, issue_id, source_id="", action="load", artifact_id="", receipt=None, entity_types=(),
                 count=True):
    """The next item of a queue. ``count=False`` skips counting the rest (a full scan), so the
    item shows sooner; ``count_review_queue`` supplies the count afterwards."""
    spec = registry.get(issue_id) if registry else None
    if not spec:
        raise ValueError("Choose a registered review queue.")
    pm = service.project_manager
    raw = pm.get_metadata(PROGRESS_KEY, "{}") if hasattr(pm, "get_metadata") else "{}"
    progress = json.loads(raw or "{}")
    key = json.dumps([issue_id, source_id])
    state = progress.get(key) or {"skipped": [], "deferred": [], "reviewed": 0}
    _advance(state, action, artifact_id, receipt)
    if action != "load" and hasattr(pm, "set_metadata"):
        progress[key] = state
        pm.set_metadata(PROGRESS_KEY, json.dumps(progress))
    if spec.resource_kind == "assertion":
        row, count = relation_queue(service, spec, source_id, state["skipped"] + state["deferred"], list(entity_types or []))
        return {"row":row, "remaining":count, "resource_kind":"assertion", "progress":_summary(state)}
    if spec.resource_kind == "merge":
        from core.services.intelligence.merge_suggestions import merge_queue
        pair, count = merge_queue(service, state["skipped"] + state["deferred"], entity_types)
        return {"row": pair, "remaining": count, "resource_kind": "merge", "progress": _summary(state)}
    base = coerce_query(spec.query)
    scope = base.source_scope
    unresolved = scope.unresolved_refs or ((scope.source_paths or scope.display_names) and not scope.source_ids)
    if source_id and not unresolved:
        scope = SourceScope(unresolved_refs=[source_id]) if scope.source_ids and source_id not in scope.source_ids else SourceScope(source_ids=[source_id])
    query = replace(base, source_scope=scope,
                    statuses=["complete", "stale"] if spec.query.get("issue_kind") == "migration" else ["complete"],
                    review_states=["unreviewed", "corrected", "verified", "rejected"] if spec.query.get("issue_kind") == "migration" else ["unreviewed"],
                    exclude_artifact_ids=state["skipped"] + state["deferred"], limit=1,
                    entity_types=list(entity_types or []) or base.entity_types)
    page = service.query_artifacts(query)
    row = next(iter(page.get("items") or []), None)
    summary = _summary(state)
    context = editor_context(service, row) if row else {}
    context["read_only"] = spec.query.get("issue_kind") == "migration"
    remaining = service.db.count_artifact_query(query) if count else None
    return {"row": row, "progress": summary, "remaining": remaining, "editor_context": context, "resource_kind":"migration" if spec.query.get("issue_kind") == "migration" else "artifact"}


def _summary(state):
    return {"reviewed":state.get("reviewed",0), "skipped_count":len(state["skipped"]),
            "deferred_count":len(state["deferred"]), "receipt":state.get("receipt")}


def _advance(state, action, artifact_id, receipt):
    if action == "reset":
        state.update(skipped=[], deferred=[])
    elif action in {"skip", "defer"} and artifact_id:
        key = "skipped" if action == "skip" else "deferred"
        state[key] = list(dict.fromkeys([*state.get(key, []), artifact_id]))
    elif action == "saved":
        state["reviewed"] = state.get("reviewed", 0) + 1
        state["receipt"] = receipt
    elif action == "undone":
        state["reviewed"] = max(0, state.get("reviewed", 0) - 1)
        state["receipt"] = None


def editor_context(service, row, *, search="", cursor=""):
    from core.models.intelligence_models import ArtifactQuery
    if not row:
        return {}
    choices, target, next_cursor = [], None, ""
    if row.get("artifact_type") == "in_text_citation":
        page = service.query_artifacts(ArtifactQuery(
            source_scope=SourceScope(source_ids=[row.get("source_id")]), artifact_types=["citation_entry"],
            statuses=["complete"], review_states=["unreviewed", "verified", "corrected"], text=search, cursor=cursor, limit=100))
        choices = [(item["artifact_id"], (item.get("exact_text") or "")[:180]) for item in page["items"]]
        target = service.db.citation_assignments(artifact_ids=[row["artifact_id"]]).get(row["artifact_id"])
        if target and target not in {identity for identity, _ in choices}:
            assigned = service.db.get_artifact(target)
            if assigned:
                choices.insert(0, (target, (assigned.get("exact_text") or "")[:180]))
        next_cursor = page.get("next_cursor") or ""
    elif row.get("artifact_type") == "entity":
        # An entity can also be a way of citing a source (its mentions cite that entry).
        from core.services.intelligence.entity_citations import cited_entries, entry_choices
        cited = cited_entries(service.db, [row["artifact_id"]]).get(row["artifact_id"])
        target = cited["artifact_id"] if cited else None
        choices = [(item["artifact_id"], (item.get("exact_text") or "")[:180]) for item in entry_choices(service, search)]
        if cited and target not in {identity for identity, _ in choices}:
            choices.insert(0, (target, cited["exact_text"][:180]))
    context = {"citation_choices": choices, "citation_target": target, "next_cursor": next_cursor}
    if row.get("artifact_type") == "entity_mention":
        context["mention_sentence"] = mention_sentence(service.db, row)
    return context


def mention_sentence(db, mention):
    """The whole sentence a mention is in, with the mention's span inside it (None when unknown)."""
    meta, locator = mention.get("metadata") or {}, mention.get("locator") or {}
    sentence = db.get_artifact(meta.get("evidence_artifact_id")) if meta.get("evidence_artifact_id") else None
    if not sentence or not sentence.get("exact_text"):
        return None
    from core.db.mention_snippets import locate
    span = locate(mention, sentence)
    where = sentence.get("locator") or {}
    return {"artifact_id": sentence["artifact_id"], "text": sentence["exact_text"],
            "page": where.get("page", locator.get("page")),
            "start": span[0] if span else None, "end": span[1] if span else None}
