"""One metadata filter policy for vector stores and legacy RAG adapters."""

def vector_where(plan):
    conditions = []
    scope = plan.source_scope
    if plan.allowed_source_ids or scope.source_ids:
        conditions.append({"source_id": {"$in": plan.allowed_source_ids or scope.source_ids}})
    elif scope.display_names:
        conditions.append({"doc_name": {"$in": scope.display_names}})
    elif scope.source_paths:
        conditions.append({"doc_id": {"$in": scope.source_paths}})
    if plan.artifact_types:
        conditions.append({"artifact_type": {"$in": plan.artifact_types}})
    tags = [{f"tag_{tag}": True} for tag in plan.tag_filters]
    if tags:
        conditions.append({"$or": tags} if plan.tag_logic.upper() == "OR" and len(tags) > 1 else {"$and": tags} if len(tags) > 1 else tags[0])
    for name in ("has_citation", "has_table"):
        if getattr(plan, name) is not None:
            conditions.append({name: getattr(plan, name)})
    if plan.block_types:
        conditions.append({"block_type": {"$in": plan.block_types}})
    if plan.argument_roles:
        conditions.append({"class_label": {"$in": plan.argument_roles}})
    return {"$and": conditions} if len(conditions) > 1 else conditions[0] if conditions else None


def metadata_matches(plan, metadata):
    scope = plan.source_scope
    if metadata.get("review_state", "unreviewed") not in plan.review_states:
        return False
    if metadata.get("status", "complete") not in ({"complete", "stale"} if plan.include_stale else {"complete"}):
        return False
    if scope.unresolved_refs:
        return False
    sources = plan.allowed_source_ids or scope.source_ids
    if sources and metadata.get("source_id") not in sources:
        return False
    if not sources and scope.source_paths and metadata.get("doc_id") not in scope.source_paths:
        return False
    if not sources and not scope.source_paths and scope.display_names and metadata.get("doc_name") not in scope.display_names:
        return False
    if plan.artifact_families:
        from core.models.artifact_families import artifact_family, artifact_types_for_families
        if (artifact_family(metadata.get("artifact_type")) not in plan.artifact_families
                and metadata.get("artifact_type") not in artifact_types_for_families(plan.artifact_families)):
            return False
    if plan.artifact_types and metadata.get("artifact_type") not in plan.artifact_types:
        return False
    if plan.argument_roles and (metadata.get("argument_role") or metadata.get("class_label")) not in plan.argument_roles:
        return False
    if plan.section_paths and _section(metadata.get("section_path")) not in plan.section_paths:
        return False
    if plan.block_types and metadata.get("block_type") not in plan.block_types:
        return False
    if plan.tag_logic.upper() not in {"AND", "OR"}:
        raise ValueError("tag_logic must be AND or OR")
    tags = [metadata.get(f"tag_{tag}") is True or tag in (metadata.get("tags") or []) for tag in plan.tag_filters]
    if tags and not (any(tags) if plan.tag_logic.upper() == "OR" else all(tags)):
        return False
    for name in ("has_citation", "has_table"):
        expected = getattr(plan, name)
        if expected is not None and metadata.get(name) != expected:
            return False
    return True


def _section(value):
    return [part.strip() for part in value.split(" > ")] if isinstance(value, str) else value or []


def filter_vector_payload(payload, plan):
    if not payload:
        return payload
    metadata = (payload.get("metadatas") or [[]])[0]
    keep = [index for index, item in enumerate(metadata) if metadata_matches(plan, item or {})]
    return {name: [[rows[0][index] for index in keep if index < len(rows[0])]]
            for name, rows in payload.items() if isinstance(rows, list) and rows and isinstance(rows[0], list)}
