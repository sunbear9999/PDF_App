"""Carry human review decisions forward when re-extraction retires artifacts.

Before a rebuild supersedes an old artifact that a person verified, corrected
or rejected, find the same occurrence among the new artifacts (same quoted
text, same context, same block when known). A unique match receives the
review state, the human-changed fields and any manual citation assignment,
and an alias records old → new. No match or several candidates: the old
artifact stays visible, flagged ``migration_conflict`` for review — a
person's work is never silently reset.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Optional, Set

from core.services.intelligence.anchor_text_locator import text_key

REVIEWED = {"verified", "corrected", "rejected"}
_SYSTEM_METADATA = {"producer", "stage", "migration_conflict"}
_ASSIGNMENT = "assigned_bibliography_artifact_id"


class ReviewMigration:
    def __init__(self, artifact_service: Any) -> None:
        self._artifacts = artifact_service
        self._db = artifact_service.db

    def carry_forward(self, retiring_ids: Iterable[str], current_ids: Iterable[str],
                      key: Callable[[Dict[str, Any]], tuple]) -> Set[str]:
        """Migrate reviewed retiring artifacts; return IDs kept for review."""
        retiring = sorted(
            (row for row in self._db.list_artifacts_by_ids(sorted(retiring_ids))
             if row.get("review_state") in REVIEWED),
            # Entries first, so citation assignments can follow their alias.
            key=lambda row: row.get("artifact_type") != "citation_entry",
        )
        if not retiring:
            return set()
        current = self._db.list_artifacts_by_ids(sorted(current_ids))
        kept: Set[str] = set()
        for old in retiring:
            matches = _matches(old, current, key)
            if len(matches) == 1:
                try:
                    self._transfer(old, matches[0])
                except ValueError as exc:
                    kept.add(old["artifact_id"])
                    self._flag(old,matches,reason=str(exc))
            else:
                kept.add(old["artifact_id"])
                self._flag(old, matches)
        return kept

    def _transfer(self, old: Dict[str, Any], new: Dict[str, Any]) -> None:
        old_meta = old.get("metadata") or {}
        if _ASSIGNMENT in old_meta and new.get("artifact_type") == "in_text_citation":
            self._reassign(old_meta.get(_ASSIGNMENT) or None, new["artifact_id"])
            # Reassignment revised the artifact; build on that, not the stale row.
            new = self._db.get_artifact(new["artifact_id"]) or new
        new_meta = new.get("metadata") or {}
        changes: Dict[str, Any] = {}
        editable = {field.path for field in self._artifacts.get_review_fields(old)}
        human = {k: v for k, v in old_meta.items()
                 if f"metadata.{k}" in editable and k != _ASSIGNMENT and new_meta.get(k) != v}
        if human:
            changes["metadata"] = {**new_meta, **human}
        # A reviewed source quote never replaces the new occurrence's verbatim text.
        if old.get("artifact_type") in {"note", "entity"} and "exact_text" in editable:
            changes["exact_text"] = old.get("exact_text")
        if old.get("review_state") == "corrected" and old.get("confidence") is not None:
            changes["confidence"] = old["confidence"]
        changes["review_state"] = old.get("review_state")
        self._artifacts.revise_artifact_fields(new["artifact_id"], changes, created_by="migration")
        self._db.record_artifact_alias(old["artifact_id"], new["artifact_id"], "migrated",
                                       "same occurrence after re-extraction")

    def assignment_target(self, old_entry_id, source_id):
        if not old_entry_id:
            return None
        identity = self._db.resolve_current_artifact_id(old_entry_id)
        entry = self._db.get_artifact(identity)
        if not entry or entry.get("artifact_type") != "citation_entry" or entry.get("source_id") != source_id or entry.get("status") != "complete" or entry.get("review_state") in {"rejected","superseded"} or (entry.get("metadata") or {}).get("migration_conflict"):
            raise ValueError("Resolve the reviewed bibliography entry before migrating this citation.")
        return identity

    def _reassign(self, old_entry_id: Optional[str], citation_id: str) -> None:
        citation = self._db.get_artifact(citation_id) or {}
        entry_id = self.assignment_target(old_entry_id,citation.get("source_id"))
        self._artifacts.assign_citation(citation_id, entry_id, created_by="migration")

    def _flag(self, old: Dict[str, Any], matches: List[Dict[str, Any]], reason=None) -> None:
        reason = reason or ("no matching occurrence after re-extraction" if not matches
                  else f"{len(matches)} possible matching occurrences")
        self._db.update_artifact_metadata(old["artifact_id"], {"migration_conflict": {
            "reason": reason,
            "candidate_artifact_ids": [row["artifact_id"] for row in matches][:10],
        }})
        self._db.record_artifact_alias(old["artifact_id"], None, "needs_review", reason)


def _matches(old, current, key) -> List[Dict[str, Any]]:
    wanted = key(old)
    compatible = {old.get("artifact_type")}
    if old.get("artifact_type") == "citation_entry" and (old.get("metadata") or {}).get("citation_style") == "footnote":
        compatible.add("document_note")
    if old.get("artifact_type") == "in_text_citation":
        compatible.add("note_reference")
    same_type = [row for row in current if row.get("artifact_type") in compatible
                 and row.get("source_id") == old.get("source_id")]
    exact = [row for row in same_type if key(row) == wanted]
    if len(exact) <= 1:
        return exact
    # Several identical quotes: the recorded block disambiguates when known.
    block = (old.get("locator") or {}).get("block_id")
    narrowed = [row for row in exact if block and (row.get("locator") or {}).get("block_id") == block]
    return narrowed or exact


def citation_key(row: Dict[str, Any]) -> tuple:
    metadata = row.get("metadata") or {}
    if row.get("artifact_type") in {"citation_entry", "document_note"}:
        return (text_key(row.get("exact_text")),)
    return (text_key(metadata.get("raw_text")), text_key(row.get("exact_text")))


def date_key(row: Dict[str, Any]) -> tuple:
    return (text_key((row.get("metadata") or {}).get("exact_text")),
            text_key(row.get("exact_text")))


def source_span_key(row: Dict[str, Any]) -> tuple:
    """Migrate resegmented prose only when quote and exact occurrence agree."""
    locator = row.get("locator") or {}
    return (locator.get("block_id"), locator.get("page"),
            locator.get("char_start"), locator.get("char_end"),
            text_key(row.get("exact_text")))
