"""Presentation-neutral semantic field definitions for artifact review."""
from dataclasses import dataclass
from typing import Any
from core.models.entity_categories import ENTITY_TYPES


@dataclass(frozen=True)
class ArtifactField:
    path: str
    label: str
    kind: str = "text"
    choices: tuple = ()
    default: Any = ""
    description: str = ""
    help_topic: str = ""
    choice_labels: tuple = ()   # (value, readable label) pairs shown instead of raw codes


ROLE_LABELS = ("claim", "evidence", "reasoning", "methodology", "counterclaim", "limitation", "unknown")
ROLE_CHOICES = ("unknown", *(role for role in ROLE_LABELS if role != "unknown"))
DATE_PRECISIONS = ("year", "month", "day", "range", "unresolved")
CERTAINTIES = ("confirmed", "likely", "ambiguous", "unknown")
ENTITY_CHOICES = tuple(sorted(ENTITY_TYPES))


def semantic_fields(row):
    """Machine identity, source quotes and lifecycle fields are not semantic edits."""
    kind = row.get("artifact_type")
    fields = [ArtifactField("confidence", "Confidence", "confidence", default=None),
              ArtifactField("metadata.tags", "Tags", "list", default=[])]
    if kind in {"sentence", "transcript_segment", "argument_unit"}:
        role = "argument_role" if kind == "argument_unit" else "class_label"
        fields.append(ArtifactField(f"metadata.{role}", "Argument role", "choice", ROLE_CHOICES, "unknown"))
    if kind in {"entity", "entity_mention"}:
        from core.models.entity_categories import type_label
        labels = tuple(sorted(((c, f"{type_label(c)} ({c})") for c in ENTITY_CHOICES), key=lambda pair: pair[1]))
        fields.append(ArtifactField("metadata.entity_type", "Entity category", "choice", tuple(c for c, _ in labels),
                                    "UNKNOWN", choice_labels=labels))
    if kind == "entity":
        fields.append(ArtifactField("metadata.canonical_label", "Display name"))
    if kind == "date_mention":
        fields.extend([ArtifactField("metadata.normalized_date", "Interpreted date"),
                       ArtifactField("metadata.precision", "Date precision", "choice", DATE_PRECISIONS, "unresolved"),
                       ArtifactField("metadata.certainty", "Date certainty", "choice", CERTAINTIES, "unknown"),
                       ArtifactField("metadata.timeline_eligible", "Include in timeline", "boolean", default=False),
                       ArtifactField("metadata.event_summary", "Event summary", "multiline"),
                       ArtifactField("metadata.event_date_end", "Interval end date"),
                       ArtifactField("metadata.event_decision", "Event decision", "choice", ('candidate', 'accepted', 'dismissed'), 'candidate'),
                       ArtifactField("metadata.interpretations", "Possible interpretations", "list", default=[])])
    if kind == "note" or (row.get("metadata") or {}).get("artifact_kind") == "note":
        fields.append(ArtifactField("exact_text", "Note content", "multiline"))
    return fields


def field_value(row, spec):
    parts = spec.path.split(".")
    value = row.get(parts[0])
    if len(parts) > 1:
        value = (value or {}).get(parts[1], spec.default)
    if spec.path == "metadata.normalized_date" and not value:
        value = row.get("normalized_text") or ""
    return spec.default if value is None and spec.kind != "confidence" else value
