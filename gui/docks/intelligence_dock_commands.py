"""Commands on the rows selected in an Intelligence tab: review, merge, split, jump, rerun."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from core.events.domains.intelligence_events import IntelligenceIntent, ReviewDecisionPayload
from gui.components.dialogs.artifact_occurrence_dialog import ArtifactOccurrenceDialog

MAX_GROUP_MERGE = 50


def _mentions(row) -> int:
    meta = row.get("metadata") or {}
    try:
        return int(meta.get("project_mention_count", meta.get("mention_count")) or 0)
    except (TypeError, ValueError):
        return 0


class IntelligenceDockCommands:
    """Mixed into ``IntelligenceDockWidget``; needs ``_rows``, ``_commands``, ``nav_status``, ``tabs``."""

    def _current_view(self):
        widget = self.tabs.currentWidget()
        return widget.property("intelligence_view_id") if widget else None

    def _selected_rows(self, view_id):
        pair = self._rows.get(view_id)
        if not pair:
            return []
        return [item.data(0, Qt.ItemDataRole.UserRole) for item in pair[0].selectedItems()
                if isinstance(item.data(0, Qt.ItemDataRole.UserRole), dict)]

    def _set_review(self, view_id, state):
        rows = self._selected_rows(view_id)
        if rows and self.artifacts:
            self._commands.start("review_artifacts", rows=rows, command="verify" if state == "verified" else "reject")

    def _show_history(self, view_id):
        rows = self._selected_rows(view_id)
        pair = self._rows.get(view_id)
        if len(rows) == 1 and pair and self.artifacts:
            pair[1].runner.start("inspect_artifact", artifact_id=rows[0]["artifact_id"], history=True)

    def _entity_decision(self, command):
        rows = self._selected_rows(self._current_view() or "entities")
        if command == "entity_split":
            mentions = [row for row in rows if row.get("artifact_type") == "entity_mention"]
            parents = {row.get("parent_artifact_id") for row in mentions}
            if not mentions or len(parents) != 1 or None in parents:
                self.nav_status.setText("Select mentions belonging to one entity to split.")
                return
            identity, payload = next(iter(parents)), {"mention_ids": [row["artifact_id"] for row in mentions]}
        else:
            entities = [row for row in rows if row.get("artifact_type") == "entity"]
            if command == "entity_merge" and len(entities) > 2:
                self._merge_group(entities)
                return
            if len(entities) != 2:
                self.nav_status.setText("Select two or more entities (rows with a type, not their mentions) to merge, "
                                        "or exactly two to keep separate.")
                return
            keep, other = sorted(entities, key=_mentions, reverse=True)
            identity, payload = other["artifact_id"], {"target_id": keep["artifact_id"]}
        self.ctx.bus.intelligence_action_requested.emit(IntelligenceIntent.REVIEW_DECISION,
                                                        ReviewDecisionPayload(identity, command, payload))

    def _merge_group(self, entities):
        if len(entities) > MAX_GROUP_MERGE:
            self.nav_status.setText(f"Merge at most {MAX_GROUP_MERGE} entities at once.")
            return
        keep = max(entities, key=_mentions)
        others = [row for row in entities if row is not keep]
        names = "\n".join(f"• {row.get('exact_text')} ({_mentions(row)} mentions)" for row in others)
        answer = QMessageBox.question(
            self, "Merge entities",
            f"Merge these into “{keep.get('exact_text')}” ({_mentions(keep)} mentions)?\n\n{names}\n\n"
            "Their mentions and spellings move to it. This is one decision: Undo merge reverses all of it.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.nav_status.setText(f"Merging {len(others)} entities into “{keep.get('exact_text')}”…")
        self._commands.start("merge_entity_group", source_artifact_ids=[row["artifact_id"] for row in others],
                             target_artifact_id=keep["artifact_id"])

    def _merge_finished(self, result):
        result = result or {}
        self._last_merge = (result.get("target_artifact_id"), result.get("receipt")) if result.get("receipt") else None
        self.nav_status.setText(f"Merged {result.get('merged', 0)} entities ({result.get('moved_mentions', 0)} mentions moved). "
                                "Use Undo merge to reverse it." if self._last_merge else "Nothing was merged.")
        self.undo_merge_button.setVisible(bool(self._last_merge))

    def _undo_merge(self):
        last, self._last_merge = getattr(self, "_last_merge", None), None
        self.undo_merge_button.hide()
        if last:
            self.nav_status.setText("Undoing the merge…")
            self._commands.start("undo_entity_merges", target_artifact_id=last[0], receipt=last[1])

    def _review_connections(self):
        from gui.components.dialogs.connection_review_dialog import open_connection_review
        if open_connection_review(self.ctx, self, source_id=self.scope.currentData() or "") is None:
            self.nav_status.setText("The connection review needs the app's DialogManager.")

    def _subgroup_entities(self):
        """Put the selected entities under one of them (the umbrella); each stays its own entity."""
        from PySide6.QtWidgets import QInputDialog
        entities = [row for row in self._selected_rows(self._current_view() or "entities") if row.get("artifact_type") == "entity"]
        if len(entities) < 2:
            self.nav_status.setText("Select the broader entity and at least one entity to put under it.")
            return
        entities.sort(key=_mentions, reverse=True)
        names = [f"{row.get('exact_text')} ({_mentions(row)} mentions)" for row in entities]
        choice, ok = QInputDialog.getItem(self, "Group under", "Which entity is the broader group?", names, 0, False)
        if not ok:
            return
        umbrella = entities[names.index(choice)]
        self.nav_status.setText(f"Putting {len(entities) - 1} entities under “{umbrella.get('exact_text')}”…")
        self._commands.start("set_entity_parents", artifact_ids=[r["artifact_id"] for r in entities if r is not umbrella],
                             parent_id=umbrella["artifact_id"])

    def _merge_entities(self):
        self._entity_decision("entity_merge")

    def _never_merge_entities(self):
        self._entity_decision("entity_never_merge")

    def _split_entity_mentions(self):
        self._entity_decision("entity_split")

    def _browse_occurrences(self, view_id):
        rows = self._selected_rows(view_id)
        manager = getattr(self.ctx, "dialog_manager", None)
        if len(rows) != 1 or rows[0].get("artifact_type") not in {"entity", "citation_entry"} or manager is None:
            self.nav_status.setText("Select one entity or bibliography entry to browse all its occurrences.")
            return
        dialog = manager.show(ArtifactOccurrenceDialog, singleton=False,
                              factory=lambda: ArtifactOccurrenceDialog(self.ctx, self))
        if dialog:
            dialog.inspector.saved.connect(self.refresh)
            dialog.configure(rows[0]["artifact_id"], self.scope.currentData())

    def _jump_to_source(self, view_id):
        rows = self._selected_rows(view_id)
        if len(rows) == 1:
            self._navigator.navigate(rows[0].get("artifact_id"))

    def _jump_from_current_tab(self):
        view_id = self._current_view()
        if view_id and view_id in self._rows:
            self._jump_to_source(view_id)

    def _rerun_selected_stage(self):
        rows = self._selected_rows("runs")
        service = getattr(self.ctx, "source_intelligence_service", None)
        if len(rows) == 1 and service:
            service.run_stage(rows[0].get("source_id"), rows[0].get("stage"), force=True)
            self.refresh()

    def _cancel_selected_source(self):
        rows = self._selected_rows("runs")
        service = getattr(self.ctx, "source_intelligence_service", None)
        for source_id in {row.get("source_id") for row in rows if row.get("source_id")}:
            if service:
                service.cancel_source_jobs(source_id)
        if service:
            self.refresh()
