"""Shared eligibility policy for consuming canonical artifacts."""
from __future__ import annotations

from typing import Any, Dict, Iterable


CURRENT_REVIEWS = frozenset({"unreviewed", "verified", "corrected"})


def active_assertions(edge: Dict[str, Any]) -> list[Dict[str, Any]]:
    """The edge row is only a container; assertions determine current truth."""
    return [
        assertion for assertion in (edge.get("assertions") or [])
        if assertion.get("review_state") in CURRENT_REVIEWS
    ]


def is_current_artifact(
    db, row: Dict[str, Any], *, review_states: Iterable[str] = CURRENT_REVIEWS,
    include_stale: bool = False,
    parent_cache: Dict[str, Dict[str, Any]] | None = None,
) -> bool:
    if not row or row.get("review_state") not in set(review_states):
        return False
    if row.get("status") not in ({"complete", "stale"} if include_stale else {"complete"}):
        return False
    if row.get("artifact_type") != "argument_unit":
        return True
    parent_id = row.get("parent_artifact_id")
    parent = (parent_cache or {}).get(parent_id) if parent_id else None
    if parent is None and db and parent_id:
        parent = db.get_artifact(parent_id)
    if not parent or parent.get("review_state") not in CURRENT_REVIEWS:
        return False
    if parent.get("status") != "complete":
        return False
    if (row.get("exact_text") or "") != (parent.get("exact_text") or ""):
        return False
    label = (parent.get("metadata") or {}).get("class_label")
    role = (row.get("metadata") or {}).get("argument_role")
    if row.get("review_state") not in {"corrected", "verified"} and (
        (row.get("metadata") or {}).get("argument_eligible") is False
        or (parent.get("metadata") or {}).get("argument_eligible") is False
    ):
        return False
    return row.get("review_state") in {"corrected", "verified"} or not (
        label and role and label != role
    )
