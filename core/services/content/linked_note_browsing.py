"""Project notes supplement legacy physical PDF annotations in one notes list."""
from core.models.artifact_reference import ArtifactRef
from core.services.intelligence.reference_note_records import anchor_of, ids, PREFIX


NOTE_BATCH_SIZE = 64


def linked_notes(pm, paths, tag_name, *, cancelled=lambda: False):
    db = getattr(pm, "db_intelligence", None)
    if db is None:
        return []
    output = []
    for path in paths:
        after_id = None
        doc_tags = pm.get_tags_for_doc(path)
        while not cancelled():
            rows = pm.query_highlights(path, linked_only=True, after_id=after_id, limit=NOTE_BATCH_SIZE)
            if not rows:
                break
            references = [(row, anchor_of(row)) for row in rows]
            references = [(row, anchor, ids(anchor["source_id"], row["id"][len(PREFIX):]))
                          for row, anchor in references if anchor]
            identities = [identity[key] for _, _, identity in references for key in ("annotation_id", "note_id")]
            records = db.get_artifacts_by_ids(identities)
            for highlight, anchor, identity in references:
                annotation, note = (records.get(identity[key]) for key in ("annotation_id", "note_id"))
                if not annotation or annotation["status"] != "complete":
                    continue
                item = _note(pm, highlight, anchor, identity, annotation, note, tag_name, doc_tags)
                if item:
                    output.append(item)
            after_id = rows[-1]['id']
    return output


def _note(pm, highlight, anchor, identity, annotation, note, tag_name, doc_tags):
    tags = pm.get_tags_for_node(highlight["id"])
    if tag_name and tag_name not in {tag["name"] for tag in tags + doc_tags}:
        return None
    return {"pdf_path": highlight["doc_id"], "page_num": highlight.get("page_num") or 0,
        "annot_id": highlight["id"], "subject": highlight.get("text_content") or "",
        "content": (note or {}).get("exact_text") or "", "color": highlight.get("color") or "#b366ff",
        "source_type": anchor["source_type"], "tags": tags,
        "is_ai": ((note or {}).get("metadata") or {}).get("authored_by") == "model",
        "project_note": True, **identity, "revision": annotation["revision"],
        "note_revision": (note or {}).get("revision"),
        "project_identity": (id(pm.db_intelligence), id(pm.db_intelligence._conn)),
        "artifact_ref": ArtifactRef(annotation["artifact_id"], annotation["exact_text"], annotation["revision"],
            anchor["source_id"], highlight["doc_id"], annotation.get("locator") or {}).to_dict()}
