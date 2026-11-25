"""First-class note artifacts: authored commentary kept apart from quotes.

A highlight's quoted passage stays its ``annotation`` artifact; the note body
becomes a ``note`` artifact that names its author kind — the user, or an AI
suggestion the user saved (highlight IDs prefixed ``AINote|``) — and cites
the annotation. Notes are never presented as source text. Deleting the
highlight, or clearing its note, retires these artifacts so search and
analysis stop using them.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.models.intelligence_models import ArtifactRecord
from core.models.producer_models import MethodKind, ProducerIdentity, human

AI_NOTE_PREFIX = "AINote|"
_RETIRED = "superseded"


def note_author_kind(highlight_id: str) -> str:
    return "model" if str(highlight_id or "").startswith(AI_NOTE_PREFIX) else "user"


def note_identity(highlight_id: str) -> ProducerIdentity:
    if note_author_kind(highlight_id) == "model":
        return ProducerIdentity(
            producer_id="research_assistant_note", method_kind=MethodKind.GENERATIVE.value,
            fallback_reason="written by the assistant and saved by you; model not recorded",
        )
    return human()


def publish_note(service: Any, annotation_id: str, highlight: Dict[str, Any],
                 source_id: str) -> Optional[str]:
    """Create/update the note for a highlight; retire it when the note is empty."""
    highlight_id = str(highlight.get("id") or "")
    body = (highlight.get("note_content") or highlight.get("content") or "").strip()
    note_id = ArtifactRecord.stable_id(source_id, "note", "highlight_notes", highlight_id)
    if not body:
        service.db.mark_artifact_ids_status([note_id], _RETIRED)
        return None
    kind = note_author_kind(highlight_id)
    service.db.upsert_artifact(note_identity(highlight_id).stamp(ArtifactRecord(
        artifact_id=note_id, source_id=source_id, artifact_type="note",
        storage_ref_type="highlight_notes", storage_ref_id=highlight_id,
        parent_artifact_id=annotation_id, exact_text=body, normalized_text=body.lower(),
        metadata={
            "stage": "annotation", "authored_by": kind,
            "cited_artifact_ids": [annotation_id],
            "quote": highlight.get("text_content") or "",
        },
        created_by="user" if kind == "user" else "assistant",
        input_hash=service.text_input_hash(body),
    )))
    service.link_artifacts(note_id, annotation_id, "comments_on")
    return note_id


def retire_highlight(service: Any, highlight_id: str) -> List[str]:
    """Retire the annotation and note artifacts of a deleted highlight."""
    db = getattr(service, "db", None)
    if not db or not highlight_id:
        return []
    ids = [
        row["artifact_id"]
        for kind, ref in (("annotation", "highlights"), ("note", "highlight_notes"))
        for row in db.list_artifacts(artifact_type=kind, limit=100000)
        if row.get("storage_ref_type") == ref and row.get("storage_ref_id") == highlight_id
    ]
    for artifact_id in ids:
        db.update_artifact_metadata(artifact_id, {"deleted": True,
                                                  "deleted_reason": "highlight deleted"})
    db.mark_artifact_ids_status(ids, _RETIRED)
    return ids
