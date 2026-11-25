"""Shared optimistic review commands, including reversible citation assignments."""
from contextlib import nullcontext
from copy import deepcopy
from core.db import intelligence_decision_journal as journal
from core.services.intelligence import knowledge_review

from core.models.artifact_fields import semantic_fields
from core.services.intelligence.artifact_edit_schema import validate_artifact_changes


def validate_semantic_changes(row, changes, fields=None):
    specs = {field.path: field for field in (fields if fields is not None else semantic_fields(row))}
    allowed = set(specs)
    for name, value in changes.items():
        if name == "metadata":
            if not isinstance(value, dict):
                raise ValueError("Metadata must contain named fields.")
            for key, item in value.items():
                if item != (row.get("metadata") or {}).get(key) and f"metadata.{key}" not in allowed:
                    raise ValueError(f"{key} is managed by the app and cannot be edited here.")
        elif name not in allowed:
            raise ValueError(f"{name} is read-only. Use a review or confirmed source-repair command.")
    for path, spec in specs.items():
        value = changes.get("metadata", {}).get(path.split(".")[-1]) if path.startswith("metadata.") else changes.get(path)
        supplied = path.split(".")[-1] in changes.get("metadata", {}) if path.startswith("metadata.") else path in changes
        if not supplied:
            continue
        if value is None and spec.kind != "confidence":
            raise ValueError(f"{spec.label} requires a typed value.")
        if value is None:
            continue
        if spec.choices and value not in spec.choices and value != (row.get("metadata") or {}).get(path.split(".")[-1]):
            raise ValueError(f"Invalid value for {spec.label}.")
        if spec.kind == "boolean" and not isinstance(value, bool):
            raise ValueError(f"{spec.label} must be Yes or No.")
        if spec.kind == "list" and (not isinstance(value, list) or not all(isinstance(item, str) for item in value)):
            raise ValueError(f"{spec.label} must contain text items.")
    merged = deepcopy(changes)
    if "metadata" in changes:
        merged["metadata"] = {**(row.get("metadata") or {}), **changes["metadata"]}
    if merged:
        validate_artifact_changes(row, merged)
    if row.get("artifact_type") in {"sentence", "transcript_segment"} and (changes.get("metadata") or {}).get("class_label") in set(specs["metadata.class_label"].choices) - {"unknown"}:
        merged["metadata"]["argument_eligible"] = True
        merged["metadata"]["eligibility_basis"] = "human_role_assignment"
    _validate_date(row, merged)
    return merged


def _validate_date(row, changes):
    if row.get("artifact_type") != "date_mention" or "metadata" not in changes:
        return
    metadata = changes["metadata"]
    precision = metadata.get("precision")
    date = str(metadata.get("normalized_date") or "")
    if precision in {"year", "month", "day"} and len(date) != {"year": 4, "month": 7, "day": 10}[precision]:
        raise ValueError("Date precision must agree with the interpreted date.")
    if metadata.get("timeline_eligible") and not date:
        raise ValueError("Interpret the date before including it in the timeline.")


def execute_command(service, artifact_id, command, *, changes=None, expected_revision=None,
                    citation_changed=False, citation_target=None, receipt=None, expected_project=None, provenance_confirmed=False, payload=None, expected_token=None, expected_schema_generation=None):
    if expected_schema_generation is not None and expected_schema_generation != service.schema_generation:
        raise ValueError('Artifact fields changed after this editor opened. Reload before saving.')
    if command in knowledge_review.COMMANDS or (command == "undo" and (receipt or {}).get("kind") == "knowledge"):
        return knowledge_review.execute(service, artifact_id, command, payload=payload, expected_token=expected_token,
                                        receipt=receipt, expected_project=expected_project)
    db = service.db
    if not db or not db._conn:
        raise ValueError("Open a project before reviewing artifacts.")
    lock = getattr(service.project_manager, "db_lock", None)
    with db._write_lock:
        with lock if lock is not None else nullcontext():
            if expected_schema_generation is not None and expected_schema_generation != service.schema_generation:
                raise ValueError('Artifact fields changed after this editor opened. Reload before saving.')
            if expected_project is not None and tuple(expected_project) != (id(service.db), id(service.db._conn)):
                raise ValueError("Project changed; the edit was cancelled.")
            with journal.transaction(db):
                if command == "undo":
                    original = journal.load(db, receipt, artifact_id, "artifact")
                    result = _execute_locked(service, artifact_id, command, {}, expected_revision,
                                             citation_changed, citation_target, original, provenance_confirmed)
                    journal.finish_undo(db, receipt)
                    _sync_linked_note(service, result)
                    return result
                result = _execute_locked(service, artifact_id, command, changes or {}, expected_revision,
                                         citation_changed, citation_target, receipt, provenance_confirmed)
                result["receipt"] = journal.save(db, artifact_id, "artifact", result["receipt"], result["row"]["revision"])
                _sync_linked_note(service, result)
                return result


def _sync_linked_note(service, result):
    from core.services.intelligence.reference_note_records import materialize
    materialize(service.project_manager, result["row"])


def _execute_locked(service, artifact_id, command, changes, expected, citation_changed, citation_target, receipt, provenance_confirmed):
    db = service.db
    row = db.get_artifact(artifact_id)
    if not row:
        raise ValueError("This artifact is no longer available.")
    if expected is not None and row["revision"] != expected:
        raise ValueError("The artifact changed since you opened it. Reload before saving.")
    if (row.get("metadata") or {}).get("migration_conflict") and command in {"save", "verify"}:
        raise ValueError("Resolve the changed source span before confirming this artifact. You can reject an obsolete observation.")
    if command == "undo":
        return _undo(service, row, receipt)
    if command == "repair":
        if (row.get("metadata") or {}).get("reference_note"):
            raise ValueError("Linked notes preserve a saved source span. Repair the original source artifact, then create a replacement linked note.")
        if not provenance_confirmed or set(changes) != {"locator"}:
            raise ValueError("Confirm a source-location repair explicitly.")
        updated = service.apply_artifact_edit(artifact_id, changes, provenance_confirmed=True)
        return {"row": updated, "receipt": {"artifact_id": artifact_id, "before": row, "after_revision": updated["revision"], "command": "repair"}, "editor_context": service.get_editor_context(updated)}
    if command not in {"save", "verify", "reject"}:
        raise ValueError("Unknown review command.")
    patch = validate_semantic_changes(row, changes, service.get_review_fields(row))
    before_target = db.citation_assignments(artifact_ids=[artifact_id]).get(artifact_id)
    if command in {"verify", "reject"}:
        patch["review_state"] = "verified" if command == "verify" else "rejected"
        if command == "reject" and (row.get("metadata") or {}).get("migration_conflict"):
            patch["metadata"] = {**(row.get("metadata") or {}), "migration_conflict": None}
    db._conn.execute("SAVEPOINT artifact_review_command")
    try:
        updated = service.apply_artifact_edit(artifact_id, patch, citation_changed=citation_changed, citation_target=citation_target)
        undo = {"artifact_id": artifact_id, "before": row, "after_revision": updated["revision"],
                "citation_changed": citation_changed, "citation_target": before_target}
        db._conn.execute("RELEASE SAVEPOINT artifact_review_command")
        return {"row": updated, "receipt": undo, "editor_context": service.get_editor_context(updated)}
    except Exception:
        db._conn.execute("ROLLBACK TO SAVEPOINT artifact_review_command")
        db._conn.execute("RELEASE SAVEPOINT artifact_review_command")
        raise


def _undo(service, row, receipt):
    if not receipt or receipt.get("artifact_id") != row["artifact_id"] or receipt.get("after_revision") != row["revision"]:
        raise ValueError("A newer edit prevents undo. Reload and inspect history.")
    before = receipt["before"]
    patch = {"confidence": before.get("confidence"), "metadata": before.get("metadata") or {},
             "review_state": before.get("review_state") or "unreviewed"}
    if "exact_text" in {field.path for field in semantic_fields(row)}:
        patch["exact_text"] = before.get("exact_text")
    db = service.db
    db._conn.execute("SAVEPOINT artifact_review_undo")
    try:
        if receipt.get("citation_changed"):
            service.assign_citation(row["artifact_id"], receipt.get("citation_target"))
        if receipt.get("command") == "repair":
            patch["locator"] = before.get("locator") or {}
        updated = service.revise_artifact_fields(row["artifact_id"], patch, provenance_confirmed=receipt.get("command") == "repair")
        db._conn.execute("RELEASE SAVEPOINT artifact_review_undo")
        return {"row": updated, "receipt": None, "editor_context": service.get_editor_context(updated)}
    except Exception:
        db._conn.execute("ROLLBACK TO SAVEPOINT artifact_review_undo")
        db._conn.execute("RELEASE SAVEPOINT artifact_review_undo")
        raise
