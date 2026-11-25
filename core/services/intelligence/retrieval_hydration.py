"""Canonical hydration and evidence-backed relation expansion."""
from dataclasses import asdict
from typing import Any, Dict, List
from core.models.intelligence_models import RetrievalPlan, RetrievalResult
from core.db.intelligence_artifact_queries import plan_query
from core.services.intelligence.artifact_eligibility import CURRENT_REVIEWS, active_assertions, is_current_artifact

_DETERMINISTIC_EDGE_TYPES = {
    "located_in", "part_of", "mentioned_in", "annotates",
    "cites", "references", "same_as", "derived_from",
}


def _result_metadata(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        **(row.get("metadata") or {}),
        "parent_artifact_id": row.get("parent_artifact_id"),
        "canonical_evidence_id": (
            (row.get("metadata") or {}).get("canonical_source_artifact_id")
            or row.get("parent_artifact_id")
            or row.get("artifact_id")
            if row.get("artifact_type") == "argument_unit"
            else row.get("artifact_id")
        ),
        "review_state": row.get("review_state") or "unreviewed",
        "revision": row.get("revision") or 1,
        "stale": row.get("status") == "stale",
        "status": row.get("status") or "",
        "model_name": row.get("model_name"),
        "source_type": row.get("source_type"),
    }


def _row_to_result(row: Dict[str, Any], score: float, signal: str, reason: str, source_title) -> RetrievalResult:
    artifact_type = row.get("artifact_type") or ""
    return RetrievalResult(
        artifact_id=row["artifact_id"],
        artifact_type=artifact_type,
        source_id=row.get("source_id"),
        source_title=source_title(row.get("source_id"), row.get("source_path")),
        locator=row.get("locator") or {},
        section_path=row.get("section_path") or [],
        exact_text=row.get("exact_text") or row.get("normalized_text") or "",
        score=score,
        score_breakdown={signal: score},
        retrieval_reason=reason,
        confidence=row.get("confidence"),
        metadata=_result_metadata(row),
    )


def _canonical_results(
    db, source_ids, row_to_result, plan: RetrievalPlan, results: List[RetrievalResult]
) -> List[RetrievalResult]:
    """Apply one DB-backed eligibility gate to every signal and cache hit."""
    if not db:
        return results
    query = plan_query(plan, source_ids)
    rows = db.filter_artifacts([item.artifact_id for item in results], query)
    by_id = {row["artifact_id"]: row for row in rows}
    parents = db.list_artifacts_by_ids(list({
        row["parent_artifact_id"] for row in rows
        if row.get("artifact_type") == "argument_unit" and row.get("parent_artifact_id")
    }))
    parent_cache = {row["artifact_id"]: row for row in parents}
    canonical = []
    for item in results:
        row = by_id.get(item.artifact_id)
        if not is_current_artifact(
            db, row, review_states=set(plan.review_states) & CURRENT_REVIEWS,
            include_stale=plan.include_stale, parent_cache=parent_cache,
        ):
            continue
        current = row_to_result(
            row, item.score, next(iter(item.score_breakdown or {"structured": 0})),
            item.retrieval_reason,
        )
        canonical.append(RetrievalResult(**{
            **asdict(current), "score_breakdown": item.score_breakdown,
            "metadata": _canonical_metadata(db, source_ids, plan, item, current.metadata),
        }))
    return canonical


def _canonical_metadata(db, source_ids, plan, item, metadata):
    """Retain relation explanations only when their current edge validates."""
    path = (item.metadata or {}).get("relation_path") or {}
    edge_id, origin_id = path.get("edge_id"), path.get("from_artifact_id")
    if not edge_id or not origin_id:
        return metadata
    if not db.filter_artifacts([origin_id], plan_query(plan, source_ids)):
        return metadata
    for edge in db.list_edges(item.artifact_id):
        endpoints = {edge.get("source_artifact_id"), edge.get("target_artifact_id")}
        if edge.get("edge_id") != edge_id or {item.artifact_id, origin_id} != endpoints:
            continue
        evidence = sorted({identity for assertion in active_assertions(edge)
                           if edge.get("edge_type") in _DETERMINISTIC_EDGE_TYPES or assertion.get("review_state") == "verified"
                           for identity in ([assertion["evidence_artifact_id"]] if assertion.get("evidence_artifact_id") else assertion.get("evidence_artifact_ids") or [])})
        if evidence:
            return {**metadata, "relation_path": {
                "from_artifact_id": origin_id, "edge_id": edge_id,
                "relation_type": edge.get("edge_type"), "relation_confidence": edge.get("confidence"),
                "evidence_artifact_ids": evidence,
            }}
    return metadata


def _graph_paths(db, plan, results):
    existing = {item.artifact_id for item in results}
    max_neighbors = max(plan.max_results, min(50, plan.rerank_top_k * plan.graph_expansion))
    neighbor_ids: List[str] = []
    paths: Dict[str, Dict[str, Any]] = {}
    for origin in results[: max(plan.max_results, 10)]:
        for edge in db.list_edges(origin.artifact_id):
            assertions = active_assertions(edge)
            evidence_ids = [
                assertion.get("evidence_artifact_id") for assertion in assertions
                if assertion.get("evidence_artifact_id")
                and (
                    edge.get("edge_type") in _DETERMINISTIC_EDGE_TYPES
                    or assertion.get("review_state") == "verified"
                )
            ]
            if not evidence_ids:
                continue
            neighbor_id = (
                edge.get("target_artifact_id")
                if edge.get("source_artifact_id") == origin.artifact_id
                else edge.get("source_artifact_id")
            )
            if not neighbor_id or neighbor_id in existing or neighbor_id in paths:
                continue
            paths[neighbor_id] = {
                "from_artifact_id": origin.artifact_id,
                "edge_id": edge.get("edge_id"),
                "relation_type": edge.get("edge_type"),
                "relation_confidence": edge.get("confidence"),
                "evidence_artifact_ids": evidence_ids,
            }
            neighbor_ids.append(neighbor_id)
            if len(neighbor_ids) >= max_neighbors:
                break
        if len(neighbor_ids) >= max_neighbors:
            break
    return paths



def _expand_graph_neighbors(
    db, source_ids, row_to_result, plan: RetrievalPlan, results: List[RetrievalResult]
) -> List[RetrievalResult]:
    """Add bounded, evidence-backed one-hop neighbors of the strongest hits."""
    if not db or not results:
        return results
    allowed = set(source_ids)
    existing = {item.artifact_id for item in results}
    expanded: List[RetrievalResult] = list(results)
    paths = _graph_paths(db, plan, results)
    neighbor_ids = list(paths)
    for row in db.list_artifacts_by_ids(neighbor_ids):
        if allowed and row.get("source_id") not in allowed:
            continue
        if not plan.include_stale and row.get("status") == "stale":
            continue
        if row.get("review_state") not in set(plan.review_states):
            continue
        path = paths.get(row.get("artifact_id"), {})
        confidence = float(path.get("relation_confidence") or 0.5)
        item = row_to_result(
            row, 0.012 * confidence, "graph",
            f"Evidence-backed {path.get('relation_type') or 'graph'} neighbor",
        )
        expanded.append(RetrievalResult(
            **{**asdict(item), "metadata": {**item.metadata, "relation_path": path}}
        ))
        existing.add(item.artifact_id)
    return expanded
