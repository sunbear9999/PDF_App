"""Semantic payload hashing and producer-identity refresh for artifacts.

The payload hash identifies an artifact's *content* — text, location,
classification, review-relevant metadata. Producer identity (which algorithm
or model made it) is deliberately excluded, so re-running with a different
model that yields identical content is not a semantic change: it never
creates a spurious "new machine result" beside a reviewed artifact, and
identity is refreshed in place on unreviewed rows instead.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional

from core.models.producer_models import PRODUCER_METADATA_KEY

_HASHED_KEYS = (
    "source_id", "source_path", "source_type", "artifact_type",
    "storage_ref_type", "storage_ref_id", "parent_artifact_id",
    "exact_text", "normalized_text", "normalized_key", "locator_json",
    "section_path_json", "metadata_json", "confidence", "created_by",
    "input_hash", "status", "supersedes_artifact_id",
)
IDENTITY_COLUMNS = ("model_role", "model_name", "extractor_name", "extractor_version")
_REVIEWED = {"corrected", "verified", "rejected"}


def artifact_payload_hash(values: Dict[str, Any]) -> str:
    payload = {key: values.get(key) for key in _HASHED_KEYS}
    payload["metadata_json"] = _without_identity(values.get("metadata_json"))
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()


def hash_values_from_row(row: Dict[str, Any]) -> Dict[str, Any]:
    values = dict(row or {})
    values["locator_json"] = json.dumps(values.get("locator") or {}, ensure_ascii=False)
    values["section_path_json"] = json.dumps(values.get("section_path") or [], ensure_ascii=False)
    values["metadata_json"] = json.dumps(values.get("metadata") or {}, ensure_ascii=False)
    return values


def _without_identity(metadata_json: Any) -> Any:
    if not isinstance(metadata_json, str) or PRODUCER_METADATA_KEY not in metadata_json:
        return metadata_json
    try:
        metadata = json.loads(metadata_json)
    except (TypeError, ValueError):
        return metadata_json
    if isinstance(metadata, dict):
        metadata.pop(PRODUCER_METADATA_KEY, None)
        return json.dumps(metadata, ensure_ascii=False)
    return metadata_json


def stored_semantic_hash(conn: Any, artifact_id: str, row_reader) -> Optional[str]:
    """Hash of the stored row under the current rule (re-hashes legacy rows)."""
    row = row_reader(artifact_id)
    return artifact_payload_hash(hash_values_from_row(row)) if row else None


def refresh_identity(conn: Any, values: Dict[str, Any], review_state: str) -> None:
    """Record the latest producer identity without a semantic rewrite.

    Reviewed rows keep the identity of whatever produced the value the person
    reviewed; only their hash is normalized to the current rule.
    """
    if review_state in _REVIEWED:
        conn.execute(
            "UPDATE intelligence_artifacts SET payload_hash=:payload_hash "
            "WHERE artifact_id=:artifact_id AND payload_hash IS NOT :payload_hash",
            values,
        )
        return
    conn.execute(
        "UPDATE intelligence_artifacts SET payload_hash=:payload_hash, "
        "model_role=:model_role, model_name=:model_name, "
        "extractor_name=:extractor_name, extractor_version=:extractor_version, "
        "metadata_json=:metadata_json WHERE artifact_id=:artifact_id AND ("
        "payload_hash IS NOT :payload_hash OR model_name IS NOT :model_name OR "
        "model_role IS NOT :model_role OR extractor_name IS NOT :extractor_name OR "
        "extractor_version IS NOT :extractor_version OR metadata_json IS NOT :metadata_json)",
        values,
    )
