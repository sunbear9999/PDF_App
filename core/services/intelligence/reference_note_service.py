"""Guarded universal linked-note commands, shared by GUI, workflows and plugins."""
from contextlib import nullcontext
import hashlib
import json
import math
import os
import uuid

from core.db.intelligence_decision_journal import transaction
from core.events.domains.intelligence_events import IntelligenceEvent, LinkedNotePayload
from core.models.artifact_reference import ArtifactRef
from core.models.intelligence_models import ArtifactRecord
from core.models.producer_models import human
from core.services.intelligence import reference_note_records as records
from core.services.intelligence.reference_note_lineage import resolve_lineage
from core.services.intelligence.artifact_references import presentation_kind
from core.services.intelligence.anchor_text_locator import find_occurrences
from core.services.intelligence import selection_note_source


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class ReferenceNoteService:
    def __init__(self, project_manager, artifact_service, anchor_resolver):
        self.project_manager, self.artifacts, self.anchors = project_manager, artifact_service, anchor_resolver

    @property
    def db(self):
        return self.artifacts.db

    @property
    def project_identity(self):
        return (id(self.db), id(getattr(self.db, "_conn", None)))

    def prepare(self, reference):
        """Resolve and fingerprint the actual source/span for a reviewable save."""
        if self.db is None or self.db._conn is None:
            raise ValueError("Open a project before saving a linked note.")
        with self.db._write_lock, getattr(self.project_manager, "db_lock", nullcontext()):
            ref = ArtifactRef.from_value(reference)
            projected = self.artifacts.resolve_reference(ref)
            row = projected.get("row") or {}
            if row and presentation_kind(row) != "Source passage":
                raise ValueError("Choose an original source passage. Commentary and computed interpretations are not source quotations.")
            if not projected.get("can_jump") or row.get("review_state") in {"rejected", "superseded"}:
                raise ValueError(projected["summary"] + " Resolve the reference before saving.")
            if row.get("status") != "complete" or (row.get("metadata") or {}).get("migration_conflict"):
                raise ValueError("Reprocess or resolve the stale artifact before saving a linked note.")
            if not ref.quote.strip():
                raise ValueError("A linked note needs the original quoted passage.")
            if projected["state"] == "migrated":
                original = self.db.get_artifact(ref.artifact_id)
                if len(find_occurrences((original or {}).get("exact_text") or "", ref.quote)) != 1:
                    raise ValueError("The saved quotation does not belong uniquely to its original artifact. Inspect the reference before saving.")
            anchor = self.anchors.resolve_artifact_quote(row["artifact_id"], ref.quote,
                allow_replacement=projected["state"] == "migrated").to_dict()
            if not anchor.get("navigable"):
                raise ValueError(anchor.get("reason") or "The recorded source location could not be resolved.")
            validate_anchor(anchor)
            if not os.path.isfile(anchor["source_path"]):
                raise ValueError("The recorded source file is missing. Restore it before saving a linked note.")
            file_hash = self.artifacts.source_input_hash(anchor["source_path"])
            state = {"reference": ref.to_dict(), "row": row, "anchor": anchor, "file_hash": file_hash}
            return {"reference": ref.to_dict(), "current_artifact_id": row["artifact_id"],
                    "anchor": anchor, "summary": projected["summary"], "token": digest(state),
                    "project_identity": self.project_identity, "source_hash": file_hash}

    def prepare_selection(self, selection):
        if self.db is None or self.db._conn is None:
            raise ValueError("Open a project before saving a selection.")
        with self.db._write_lock, getattr(self.project_manager, "db_lock", nullcontext()):
            result = selection_note_source.prepare(self, selection)
            validate_anchor(result["anchor"])
            return {**result, "token": digest(result), "project_identity": self.project_identity}

    def selection_source_snapshot(self, source_path, block_ids=None):
        """Worker-facing cache-only input for text/image viewers and plugins."""
        from core.services.intelligence.block_selection_notes import source_snapshot
        if self.db is None or self.db._conn is None:
            raise ValueError('Open a project before reading source selections.')
        with self.db._write_lock, getattr(self.project_manager, 'db_lock', nullcontext()):
            return source_snapshot(self, source_path, block_ids)

    def query_notes(self, **kwargs):
        from core.services.content.note_queries import query
        return query(self, **kwargs)

    def event_snapshot(self, artifact_id):
        from core.services.intelligence.event_authoring import snapshot
        return snapshot(self, artifact_id)

    def save_event(self, reference, values, **guards):
        from core.services.intelligence.event_authoring import save
        return save(self, reference, values, **guards)

    def update_event(self, artifact_id, values, **guards):
        from core.services.intelligence.event_authoring import update
        return update(self, artifact_id, values, **guards)

    def query_chat_history(self, tab_name, cursor='', limit=12):
        return self.project_manager.query_chat_history(tab_name, cursor, limit)

    def query_workspace_inbox(self, **query):
        from core.services.intelligence.workspace_inbox import query as read
        return read(self, **query)

    def prepare_workspace_import(self, items, **guards):
        from core.services.intelligence.workspace_inbox import prepare_import
        return prepare_import(self, items, **guards)

    def save(self, reference, body, *, expected_token, expected_project, request_id, origin=None, selection=None):
        if not isinstance(body, str):
            raise ValueError("Note content must be text.")
        request_id = str(uuid.UUID(request_id))
        db, pm = self.db, self.project_manager
        if db is None:
            raise ValueError("Open a project before saving.")
        with db._write_lock, getattr(pm, "db_lock", nullcontext()):
            if tuple(expected_project) != self.project_identity:
                raise ValueError("Project changed; reload before saving.")
            prepared = self.prepare_selection(selection) if selection is not None else self.prepare(reference)
            if prepared["token"] != expected_token:
                raise ValueError("The artifact or source changed. Reload the preview before saving.")
            legacy = (prepared.get('selection') or {}).get('legacy_snapshot') or {}
            identity = records.ids(prepared["anchor"]["source_id"], legacy.get('request_id') or request_id)
            request = {"reference": prepared["reference"], "body": body, "origin": origin}
            if selection is not None:
                request["selection"] = prepared["selection"]
            request_hash = digest(request)
            prior = db.get_artifact(identity["annotation_id"])
            if prior:
                if (prior.get("metadata") or {}).get("save_request_hash") != request_hash or prior["status"] != "complete":
                    raise ValueError("This save request was already used. Start a new note.")
                return {**identity, "reused": True}
            if pm.get_highlight(identity["highlight_id"]):
                raise ValueError("This save request belongs to another note. Start a new note.")
            producer, lineage = resolve_lineage(pm, origin, body)
            from core.services.intelligence.legacy_note_adoption import note_lineage
            producer, lineage = note_lineage(prepared, body, producer, lineage)
            with transaction(db):
                self._publish(prepared, identity, body, producer, lineage, request_hash)
        self._notify(identity["annotation_id"], prepared["anchor"]["source_id"], expected_project)
        return {**identity, "reused": False}

    def _publish(self, prepared, identity, body, producer, lineage, request_hash):
        ref, anchor = prepared["reference"], prepared["anchor"]
        data = records.highlight_data(identity["highlight_id"], anchor, ref["quote"], body,
            prepared.get("color", "#b366ff"), prepared["source_hash"])
        self.project_manager.upsert_highlight(data, commit=False)
        metadata = {"reference_note": True, "stage": "annotation", "artifact_ref": ref,
                    "cited_artifact_id": prepared["current_artifact_id"], "saved_anchor": anchor,
                    "saved_source_hash": prepared["source_hash"], "save_request_hash": request_hash}
        if prepared.get("selection_provenance"):
            metadata["selection_provenance"] = prepared["selection_provenance"]
        common = dict(source_id=anchor["source_id"], source_path=anchor["source_path"], source_type=anchor["source_type"],
                      locator={"page": data["page_num"], "source_spans": anchor["spans"]})
        if anchor["source_type"] in {"video", "audio"}:
            span = anchor["spans"][0]
            common["locator"] = {"start": span.get("time_start"), "end": span.get("time_end")}
        self.db.upsert_artifact(human().stamp(ArtifactRecord(
            artifact_id=identity["annotation_id"], artifact_type="annotation", storage_ref_type="highlights",
            storage_ref_id=identity["highlight_id"], exact_text=ref["quote"], metadata=metadata, **common)))
        self.db.upsert_artifact(producer.stamp(ArtifactRecord(
            artifact_id=identity["note_id"], artifact_type="note", storage_ref_type="highlight_notes",
            storage_ref_id=identity["highlight_id"], parent_artifact_id=identity["annotation_id"], exact_text=body,
            model_role=(lineage.get("assistant_lineage") or {}).get("model_role") if lineage["authored_by"] == "model" else None,
            metadata={**metadata, **lineage, "cited_artifact_ids": list(filter(None,
                [prepared["current_artifact_id"], identity["annotation_id"]]))}, **common)))
        for source, target, kind in ((identity["annotation_id"], prepared["current_artifact_id"], "annotates"),
                                     (identity["note_id"], identity["annotation_id"], "comments_on")):
            self.artifacts.link_artifacts(source, target, kind, created_by="user",
                evidence_artifact_ids=[prepared["current_artifact_id"] or identity["annotation_id"]])
        if not prepared["current_artifact_id"]:
            source = self.project_manager.get_source_record_by_path(anchor["source_path"])
            source_id = self.artifacts.ensure_source_artifact(source)
            self.artifacts.link_artifacts(identity["annotation_id"], source_id, "located_in", created_by="user")

    def note_snapshot(self, highlight_id):
        if self.db is None or self.db._conn is None:
            raise ValueError("Open a project before inspecting a linked note.")
        with self.db._write_lock, getattr(self.project_manager, "db_lock", nullcontext()):
            highlight = self.project_manager.get_highlight(highlight_id)
            anchor = records.anchor_of(highlight or {})
            if not isinstance(anchor, dict) or not anchor.get("source_id"):
                raise ValueError("Choose a project-owned linked note.")
            identity = records.ids(anchor["source_id"], highlight_id[len(records.PREFIX):])
            annotation, note = (self.db.get_artifact(identity[key]) for key in ("annotation_id", "note_id"))
            if not annotation or not note or annotation["status"] != "complete":
                raise ValueError("This linked note is no longer current.")
            return {**identity, "revision": annotation["revision"], "note_revision": note["revision"],
                    "project_identity": self.project_identity, "color": highlight.get("color"),
                    "content": note.get("exact_text") or "", "artifact_ref": ArtifactRef(
                        annotation["artifact_id"], annotation["exact_text"], annotation["revision"],
                        annotation["source_id"], anchor["source_path"], annotation.get("locator") or {}).to_dict()}

    def modify(self, annotation_id, command, *, expected_revision, expected_note_revision, expected_project, color=None):
        db, pm = self.db, self.project_manager
        if db is None:
            raise ValueError("Open a project first.")
        with db._write_lock, getattr(pm, "db_lock", nullcontext()):
            row = db.get_artifact(annotation_id)
            if tuple(expected_project) != self.project_identity or not row or row["revision"] != expected_revision:
                raise ValueError("Project or note changed. Refresh before modifying it.")
            if not (row.get("metadata") or {}).get("reference_note") or row["artifact_type"] != "annotation" or row["status"] != "complete":
                raise ValueError("Choose a current linked note.")
            note_id = records.ids(row["source_id"], row["storage_ref_id"][len(records.PREFIX):])["note_id"]
            note = db.get_artifact(note_id)
            if not note or note["revision"] != expected_note_revision:
                raise ValueError("The note content changed. Refresh before modifying it.")
            with transaction(db):
                if command == "delete":
                    pm.db_annotations.delete_highlight_record(row["storage_ref_id"], commit=False)
                    for identity in (annotation_id, note_id):
                        db.save_artifact_revision(identity, field_changes={"status": "superseded"}, created_by="user")
                elif command == "color":
                    if not isinstance(color, str) or len(color) != 7 or color[0] != "#":
                        raise ValueError("Choose a valid highlight color.")
                    int(color[1:], 16)
                    pm.db_annotations.update_highlight_color(row["storage_ref_id"], color, commit=False)
                    db.save_artifact_revision(annotation_id, field_changes={"metadata": {**row["metadata"], "color": color}}, created_by="user")
                else:
                    raise ValueError("Unknown linked-note command.")
        self._notify(annotation_id, row["source_id"], expected_project)
        return {"annotation_id": annotation_id}

    def overlays(self, source_path, page_indices):
        """Bound drawing to requested pages; all storage reads stay off Qt."""
        pages = set(page_indices)
        output = []
        current_hash = self.artifacts.source_input_hash(source_path)
        for row in self.project_manager.iter_highlights(source_path, linked_only=True, page_indices=page_indices):
            anchor = records.anchor_of(row)
            saved_hash = json.loads(row["rect_coords"]).get("source_hash") if anchor else None
            if anchor and saved_hash == current_hash:
                for span in anchor.get("spans") or []:
                    if span.get("page_index") in pages:
                        output.append({"page": span["page_index"], "rects": span.get("rects") or [], "color": row.get("color")})
        return output

    def overlay_images(self, source_path, page_indices):
        from core.services.intelligence.note_overlay_raster import rasterize
        return rasterize(self, source_path, page_indices)

    def _notify(self, identity, source_id, expected_project):
        if tuple(expected_project) != self.project_identity:
            return
        signal = getattr(getattr(self.project_manager, "bus", None), "intelligence_state_changed", None)
        if signal is not None:
            signal.emit(IntelligenceEvent.LINKED_NOTE_CHANGED, LinkedNotePayload(identity, source_id))


def validate_anchor(anchor):
    if anchor.get("precision") not in {"exact", "repaired"} or not anchor.get("spans"):
        raise ValueError("An exact recorded span is required. Inspect or repair this source location first.")
    for span in anchor["spans"]:
        page = span.get("page_index")
        if anchor["source_type"] == "pdf" and (not span.get("rects") or type(page) is not int or page < 0):
            raise ValueError("This PDF passage has no verified highlight geometry.")
        for rect in span.get("rects") or []:
            if len(rect) != 4 or not all(type(v) in (int, float) and math.isfinite(v) for v in rect) or rect[0] >= rect[2] or rect[1] >= rect[3]:
                raise ValueError("Invalid highlight geometry.")
        if anchor["source_type"] in {"video", "audio"}:
            start, end = span.get("time_start"), span.get("time_end")
            if type(start) not in (int, float) or not math.isfinite(start) or start < 0 or (end is not None and (type(end) not in (int, float) or not math.isfinite(end) or end < start)):
                raise ValueError("Invalid recorded media time range.")
