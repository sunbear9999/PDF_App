"""One current-state projection for saved references on every presentation surface."""
from core.models.artifact_reference import ArtifactRef
from core.models.producer_models import ProducerIdentity


def resolve_reference(service, reference):
    return service.resolve_references([reference])[0]


def project_reference(db, ref, sources):
    result = {"reference": ref.to_dict(), "row": None, "state": "legacy",
              "summary": "Legacy quote — no recorded artifact reference.", "can_jump": False}
    if not ref.artifact_id:
        return result
    original = db.get_artifact(ref.artifact_id) if db else None
    if not original:
        return {**result, "state": "missing", "summary": "Artifact is missing from this project."}
    try:
        identity = db.resolve_current_artifact_id(ref.artifact_id)
    except ValueError as exc:
        return {**result, "state": "unresolved", "summary": str(exc)}
    row = db.get_artifact(identity)
    alias = db.resolve_artifact_alias(identity)
    if alias and alias.get("status") == "needs_review":
        return {**result, "row": row, "state": "unresolved",
                "summary": "Source correspondence needs review; the saved quote is retained."}
    if ref.source_id and ref.source_id != row.get("source_id"):
        return {**result, "state": "unresolved", "summary": "Reference source does not match the recorded artifact."}
    return _projection(ref, row, identity != ref.artifact_id, result, sources.get(row.get("source_id"), {}))


def _projection(ref, row, migrated, result, source):
    metadata = row.get("metadata") or {}
    producer = metadata.get("producer") or {}
    state = row.get("review_state") or "unreviewed"
    notices = [state.replace("_", " ").capitalize()]
    if migrated:
        state = "migrated"
        notices.append("Confirmed replacement; saved quote retained")
    elif ref.revision is not None and ref.revision != row.get("revision"):
        notices.append("Changed since this reference was saved")
    if ref.quote and ref.quote not in (row.get("exact_text") or ""):
        notices.append("Current text differs from the saved quote")
    if row.get("status") in {"stale", "superseded"}:
        notices.append(row["status"].capitalize())
    removed = bool((source.get("state") or {}).get("is_removed"))
    if removed:
        notices.append("Source removed")
    identity = ProducerIdentity.from_metadata(producer)
    method = identity.describe() if identity else (
        f"{row.get('extractor_name') or 'Legacy method not recorded'} · Model: {row.get('model_name') or 'not recorded'}")
    return {**result, "row": row, "state": state, "summary": "; ".join(notices),
            "presentation_kind": presentation_kind(row),
            "source_title": source.get("title") or source.get("display_name") or ref.source_title,
            "source_path": source.get("path") or row.get("source_path") or "",
            "method": method, "can_jump": row.get("status") != "superseded" and not removed}


def presentation_kind(row):
    if row.get("artifact_type") == "note":
        author = (row.get("metadata") or {}).get("authored_by")
        return "Saved AI note" if author == "model" else "User note"
    if row.get("artifact_type") in {"analysis_node", "analysis_suggestion", "summary", "analysis_result", "analysis_answer",
                                     "schema_extraction", "graph_suggestion", "workspace_node_ref"}:
        return "Computed interpretation"
    return "Source passage"


def highlight_reference(pm, path, highlight_id, quote):
    """Adapt a legacy highlight only when its actual annotation artifact exists."""
    from core.models.intelligence_models import ArtifactRecord
    db = getattr(pm, "db_intelligence", None)
    lookup = getattr(pm, "get_source_record_by_path", None)
    source = lookup(path) if lookup else None
    if not db or not source:
        return {}
    identity = ArtifactRecord.stable_id(source["id"], "annotation", "highlights", highlight_id)
    row = db.get_artifact(identity)
    if not row:
        return {}
    return ArtifactRef(identity, quote, row.get("revision"), source["id"],
                       source.get("title") or path, row.get("locator") or {}).to_dict()
