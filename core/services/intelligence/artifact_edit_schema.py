"""Typed, shared edit contract for intelligence artifacts."""
from __future__ import annotations

import calendar
import re
from typing import Any, Dict, Iterable

from core.models.intelligence_models import ArtifactReviewState
from core.models.artifact_types import BUILTIN_TYPES, artifact_type_choices
from core.models.artifact_fields import ROLE_LABELS
from core.models.entity_categories import ENTITY_TYPES


PROVENANCE_FIELDS = frozenset({
    "source_id", "source_path", "source_type", "storage_ref_type",
    "storage_ref_id", "parent_artifact_id", "locator", "section_path",
    "created_by", "extractor_name", "extractor_version", "model_role",
    "model_name", "input_hash", "supersedes_artifact_id",
})
ORIGIN_FIELDS = frozenset({"created_by", "extractor_name", "extractor_version", "model_role", "model_name", "input_hash", "supersedes_artifact_id"})
EDITABLE_FIELDS = (PROVENANCE_FIELDS - ORIGIN_FIELDS) | frozenset({
    "artifact_type", "exact_text", "normalized_key", "metadata",
    "confidence", "review_state", "status",
})
SYSTEM_FIELDS = frozenset({
    "artifact_id", "normalized_text", "payload_hash", "revision",
    "created_at", "updated_at",
})
ENUM_FIELDS = {
    "artifact_type": BUILTIN_TYPES,
    "review_state": frozenset(item.value for item in ArtifactReviewState),
    "status": frozenset({"complete", "stale", "archived", "superseded"}),
}
METADATA_ENUMS = {
    "class_label": frozenset(ROLE_LABELS),
    "argument_role": frozenset(ROLE_LABELS),
    "certainty": frozenset({"confirmed", "likely", "ambiguous", "unknown"}),
    "event_decision": frozenset({"candidate", "accepted", "dismissed"}),
    "citation_style": frozenset({
        "unknown", "apa", "mla", "chicago", "harvard", "vancouver",
        "ieee", "legal", "numeric", "author_year",
    }),
}





def validate_artifact_changes(
    current: Dict[str, Any], changes: Dict[str, Any], *,
    provenance_confirmed: bool = False,
    extra_types: Iterable[str] = (),
) -> Dict[str, Any]:
    """Reject unknown fields, invalid values and silent provenance changes."""
    if not isinstance(changes, dict) or not changes:
        raise ValueError("Choose at least one artifact field to change.")
    unknown = set(changes) - EDITABLE_FIELDS
    if unknown:
        raise ValueError(f"Artifact fields cannot be edited: {', '.join(sorted(unknown))}")
    if set(changes) & PROVENANCE_FIELDS and not provenance_confirmed:
        raise ValueError("Confirm the provenance correction explicitly first.")
    result = dict(changes)
    for key in ("artifact_type", "review_state", "status"):
        if key in result:
            allowed = (
                set(artifact_type_choices(extra_types)) | {current.get("artifact_type")}
                if key == "artifact_type" else ENUM_FIELDS[key]
            )
            if result[key] not in allowed:
                raise ValueError(f"Invalid {key}: {result[key]}")
    if "confidence" in result:
        value = result["confidence"]
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, (int, float))
            or not 0 <= value <= 1
        ):
            raise ValueError("Confidence must be between 0 and 1.")
    for key in ("exact_text", "normalized_key", *PROVENANCE_FIELDS - {"locator", "section_path"}):
        if key in result and result[key] is not None and not isinstance(result[key], str):
            raise ValueError(f"{key} must be text.")
    if "locator" in result:
        result["locator"] = _typed_mapping(current.get("locator") or {}, result["locator"], "locator")
        for key in ("page", "page_num", "char_start", "char_end", "start", "end"):
            value = result["locator"].get(key)
            if value == (current.get("locator") or {}).get(key) or value is None:
                continue
            lower = 1 if key in {"page", "page_num"} else 0
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < lower:
                raise ValueError(f"locator.{key} must be a number at least {lower}.")
        for start, end in (("char_start", "char_end"), ("start", "end")):
            left, right = result["locator"].get(start), result["locator"].get(end)
            if left is not None and right is not None and right < left:
                raise ValueError(f"locator.{end} must not precede {start}.")
    if "metadata" in result:
        if isinstance(result["metadata"], dict) and "producer" in result["metadata"] and result["metadata"]["producer"] != (current.get("metadata") or {}).get("producer"):
            raise ValueError("Producer identity cannot be edited.")
        result["metadata"] = _typed_mapping(current.get("metadata") or {}, result["metadata"], "metadata")
        for key, allowed in METADATA_ENUMS.items():
            if (key in result["metadata"] and result["metadata"][key] not in allowed
                    and result["metadata"][key] != (current.get("metadata") or {}).get(key)):
                raise ValueError(f"Invalid {key}: {result['metadata'][key]}")
        date = result["metadata"].get("normalized_date")
        from core.models.event_projection import calendar_interval
        end = calendar_interval(result['metadata'].get('event_date_end') or '')
        start = calendar_interval(date or '') if end else None
        if end and (not start or end[1] < start[0]):
            raise ValueError('An event interval needs a valid start date before its end.')
        summary = result['metadata'].get('event_summary')
        if summary is not None and (not isinstance(summary, str) or len(summary) > 4000):
            raise ValueError('Event summaries must be text of at most 4000 characters.')
        if date and date != (current.get("metadata") or {}).get("normalized_date"):
            match = re.fullmatch(r"(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?", str(date))
            if not match:
                raise ValueError("Normalized date must be YYYY, YYYY-MM or YYYY-MM-DD.")
            year, month, day = (int(part) if part else None for part in match.groups())
            if not 1 <= year <= 9999 or (month is not None and not 1 <= month <= 12):
                raise ValueError("Normalized date has an invalid year or month.")
            if day is not None and (month is None or not 1 <= day <= calendar.monthrange(year, month)[1]):
                raise ValueError("Normalized date has an invalid day.")
    if "section_path" in result and (
        not isinstance(result["section_path"], list)
        or not all(isinstance(value, str) for value in result["section_path"])
    ):
        raise ValueError("Section path must be a list of text labels.")
    if "source_id" in result and result["source_id"] is None:
        raise ValueError("Source ID cannot be empty.")
    return result


def _typed_mapping(current: dict, updated: Any, label: str) -> dict:
    if not isinstance(updated, dict):
        raise ValueError(f"{label} must be a set of named fields.")
    for key, value in updated.items():
        if not isinstance(key, str) or not key:
            raise ValueError(f"{label} field names must be text.")
        _validate_nested_type(current.get(key), value, f"{label}.{key}")
    return updated


def _validate_nested_type(old: Any, value: Any, path: str) -> None:
    if old is None or value is None:
        return
    if isinstance(old, bool):
        valid = isinstance(value, bool)
    elif isinstance(old, int):
        valid = isinstance(value, int) and not isinstance(value, bool)
    elif isinstance(old, float):
        valid = isinstance(value, (int, float)) and not isinstance(value, bool)
    else:
        valid = isinstance(value, type(old))
    if not valid:
        raise ValueError(f"{path} must remain {type(old).__name__}.")
    if isinstance(old, dict):
        for key, child in value.items():
            _validate_nested_type(old.get(key), child, f"{path}.{key}")
    elif isinstance(old, list) and old:
        exemplar = next((item for item in old if item is not None), None)
        for index, child in enumerate(value):
            _validate_nested_type(exemplar, child, f"{path}[{index}]")
