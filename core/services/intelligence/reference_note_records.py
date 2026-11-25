"""Project-owned linked highlights; no mutation of the source document."""
import json
from core.models.intelligence_models import ArtifactRecord

PREFIX = "LinkedNote|"


def ids(source_id, request_id):
    highlight = PREFIX + request_id
    return {"highlight_id": highlight,
            "annotation_id": ArtifactRecord.stable_id(source_id, "annotation", "highlights", highlight),
            "note_id": ArtifactRecord.stable_id(source_id, "note", "highlight_notes", highlight)}


def anchor_of(highlight):
    if not str(highlight.get("id") or "").startswith(PREFIX):
        return None
    try:
        data = json.loads(highlight.get("rect_coords") or "{}")
        return data.get("reference_anchor") if isinstance(data, dict) else None
    except (TypeError, ValueError):
        return None


def materialize(pm, row):
    """The highlight body is a projection of the canonical editable note."""
    if row.get("artifact_type") != "note" or not (row.get("metadata") or {}).get("reference_note"):
        return
    pm.update_highlight_note(row["storage_ref_id"], row.get("exact_text") or "", commit=False)


def highlight_data(identity, anchor, quote, body, color, source_hash):
    span = anchor["spans"][0]
    return {"id": identity, "doc_id": anchor["source_path"],
            "page_num": span.get("time_start") if anchor["source_type"] in {"video", "audio"} else span.get("page_index"),
            "rect_coords": json.dumps({"reference_anchor": anchor, "source_hash": source_hash}, ensure_ascii=False),
            "text_content": quote, "note_content": body, "color": color}
