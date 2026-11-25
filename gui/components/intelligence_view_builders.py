"""Shared tree projections for intelligence artifacts."""
from __future__ import annotations

import json
from collections import Counter

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTreeWidgetItem

from core.models.entity_categories import type_label


def artifact_columns(row):
    metadata = row.get("metadata") or {}
    artifact_type = row.get("artifact_type") or row.get("stage") or ""
    return (
        (metadata.get("argument_role") or "argument unit") if artifact_type == "argument_unit"
        else artifact_type,
        row.get("display_text") or row.get("exact_text") or row.get("normalized_text") or "",
        row.get("source_id") or "",
        row.get("review_state") or row.get("status") or "",
    )


def plural(count, noun):
    return f"{count} {noun}{'' if count == 1 else 's'}"


def explore_columns(row, source_title):
    """Explore lists any kind; entities show their type and how often they are mentioned."""
    from gui.docks.intelligence_dock_style import kind_label
    metadata = row.get("metadata") or {}
    value = row.get("display_text") or row.get("exact_text") or row.get("normalized_text") or ""
    if row.get("artifact_type") == "entity" and metadata.get("mention_count") is not None:
        value = f"{value}  ·  {plural(metadata['mention_count'], 'mention')}"
    state = row.get("review_state") or row.get("status") or ""
    return kind_label(row), value, source_title(row.get("source_id")) if row.get("source_id") else "Whole project", state


def argument_unit_columns(row, source_title):
    confidence = row.get("confidence")
    state = row.get("review_state") or "unreviewed"
    return (
        (row.get("metadata") or {}).get("argument_role") or "Unclassified",
        row.get("exact_text") or "",
        source_title(row.get("source_id")),
        f"{confidence:.0%} · {state}" if isinstance(confidence, (float, int)) else state,
    )


def populate_citation_tree(tree, details, rows, links, source_title):
    tree.clear()
    details.clear()
    entries = {
        row["artifact_id"]: row for row in rows
        if row.get("artifact_type") == "citation_entry"
    }
    items = {}
    for entry in entries.values():
        item = QTreeWidgetItem([
            "Bibliography", entry.get("exact_text") or "",
            source_title(entry.get("source_id")),
            entry.get("review_state") or "",
        ])
        item.setData(0, Qt.ItemDataRole.UserRole, entry)
        items[entry["artifact_id"]] = item
    unmatched = None
    for citation in rows:
        if citation.get("artifact_type") != "in_text_citation":
            continue
        parent = items.get(links.get(citation.get("artifact_id")))
        if parent is None:
            if unmatched is None:
                unmatched = QTreeWidgetItem([
                    "Unmatched references", "", "", "Review assignments"
                ])
            parent = unmatched
        child = QTreeWidgetItem([
            "In-text reference",
            (citation.get("metadata") or {}).get("raw_text")
            or citation.get("exact_text") or "",
            source_title(citation.get("source_id")),
            citation_review_text(citation),
        ])
        child.setData(0, Qt.ItemDataRole.UserRole, citation)
        parent.addChild(child)
    for entry_id, citers in (links.get("__named__") or {}).items():
        for entity in citers if entry_id in items else []:
            child = QTreeWidgetItem(["Cited by name", entity["exact_text"], "",
                                     plural(int(entity.get("mention_count") or 0), "mention")])
            child.setToolTip(1, "Every mention of this name cites this entry (set on the entity).")
            items[entry_id].addChild(child)
    for item in items.values():
        tree.addTopLevelItem(item)
    if unmatched is not None:
        tree.addTopLevelItem(unmatched)
    tree.resizeColumnToContents(0)


def citation_review_text(citation):
    state = citation.get("review_state") or ""
    if state in {"corrected", "verified"}:
        return state
    metadata = citation.get("metadata") or {}
    if metadata.get("unmatched_numeric_keys"):
        return "Missing references: " + ", ".join(metadata["unmatched_numeric_keys"])
    if metadata.get("ambiguous"):
        return "Ambiguous — review assignment"
    return state


def _graph_key(node):
    return node.get("argument_unit_id") or node.get("primary_evidence_id") or node.get("artifact_id")


def graph_preview_rows(output, artifacts_by_id, needle=""):
    """``project_graph`` returns ``nodes``; older analysis outputs used ``entities``."""
    visible = set(output.get("visible_node_ids") or [])
    return [
        {
            **(artifacts_by_id.get(_graph_key(node)) or {}),
            "artifact_type": (artifacts_by_id.get(_graph_key(node)) or {}).get("artifact_type") or node.get("type"),
            "display_text": node.get("title"),
            "source_id": node.get("source_id"),
            "review_state": node.get("review_state"),
            "graph_view": node,
        }
        for node in output.get("nodes") or output.get("entities") or []
        if not visible or node.get("id") in visible
        if not needle or needle in json.dumps(
            node, ensure_ascii=False, default=str
        ).casefold()
    ]


def timeline_preview_rows(timeline, artifacts_by_id, needle=""):
    return [
        {**(artifacts_by_id.get(row.get("artifact_id")) or {}), **row}
        for row in timeline
        if not needle or needle in json.dumps(
            row, ensure_ascii=False, default=str
        ).casefold()
    ]


NAV_STATUS_RESOLVING = "Locating source…"
_NAV_STATUS = {
    "exact": "Exact location highlighted.",
    "repaired": "Location repaired: the text was found inside its recorded block.",
    "coarse": "Approximate location: only the block or page is known.",
    "missing_source": "Source unavailable.",
    "unresolved": "No source location recorded.",
}


def navigation_status_text(target: dict) -> str:
    """User-facing summary of a resolved navigation target's precision."""
    label = _NAV_STATUS.get(target.get("precision") or "", "")
    reason = target.get("reason") or ""
    if target.get("precision") in {"missing_source", "unresolved", "coarse"} and reason:
        return f"{label} {reason}"
    return label


def overview_text(snapshot: dict) -> str:
    """Plain-text overview: artifact counts, stage outcomes and model state."""
    artifacts = snapshot.get("artifacts") or []
    runs = snapshot.get("runs") or []
    counts = snapshot.get("counts") or {}
    by_type = counts.get("by_type") or Counter(row.get("artifact_type") or "unknown" for row in artifacts)
    by_status = counts.get("by_status") or Counter(row.get("status") or "unknown" for row in artifacts)
    run_status = snapshot.get("run_counts") or Counter(row.get("status") or "unknown" for row in runs)
    roles = snapshot.get("sentence_roles") or {}
    model_lines = [
        f"- {spec.get('label') or spec.get('id')}: "
        f"{'not installed (optional)' if spec.get('status') == 'missing' else spec.get('status') or 'unknown'}"
        for spec in snapshot.get("models") or []
    ]
    return (
        f"Artifacts: {counts.get('total', len(artifacts))}\n"
        f"Artifact states: {dict(by_status)}\n"
        f"Artifact types: {dict(by_type)}\n"
        f"Stage runs: {dict(run_status)}\n\n"
        f"Argument roles are labelled by: {roles.get('active') or 'Built-in heuristic (no model)'}\n\n"
        "Optional local models:\n" + ("\n".join(model_lines) or "- No manifests registered")
    )


def populate_entity_tree(tree, raw, rows, source_title):
    tree.clear()
    raw.clear()
    mentions_by_parent = {}
    for row in rows:
        if row.get("artifact_type") == "entity_mention":
            mentions_by_parent.setdefault(row.get("parent_artifact_id"), []).append(row)
    tree.setUpdatesEnabled(False)
    try:
        for row in rows:
            if row.get("artifact_type") == "entity":
                tree.addTopLevelItem(_entity_item(
                    row, mentions_by_parent.get(row.get("artifact_id"), []),
                    source_title,
                ))
    finally:
        tree.setUpdatesEnabled(True)
    tree.setHeaderLabels(["Type", "Name", "Mentions", "Review"])
    tree.resizeColumnToContents(0)


def _entity_item(row, loose_mentions, source_title, subgroup=False):
    """An entity row: its subgroups first (each expandable to its own mentions), then its mentions."""
    metadata = row.get("metadata") or {}
    sources = metadata.get("source_count") or 0
    groups = row.get("subgroups") or []
    counts = plural(metadata.get("mention_count") or 0, "mention") + (f" · {plural(sources, 'source')}" if sources else "")
    if groups:
        counts += f" · {plural(len(groups), 'subgroup')} ({sum(int(g.get('mention_count') or 0) for g in groups)} more)"
    kind = type_label(metadata.get("entity_type")) + (" + citation" if metadata.get("cited_entry") else "")
    item = QTreeWidgetItem([("subgroup · " if subgroup else "") + kind,
                            metadata.get("canonical_label") or row.get("exact_text") or "", counts, row.get("review_state") or ""])
    item.setToolTip(1, _entity_tip(row))
    item.setData(0, Qt.ItemDataRole.UserRole, row)
    for group in groups:
        item.addChild(_entity_item(group, [], source_title, subgroup=True))
    for mention in row.get("mentions", loose_mentions):
        child = QTreeWidgetItem(["mention", mention.get("context_snippet") or mention.get("exact_text") or "",
                                 source_title(mention.get("source_id")), mention.get("review_state") or ""])
        child.setData(0, Qt.ItemDataRole.UserRole, mention)
        item.addChild(child)
    if row.get("mentions_truncated"):
        item.addChild(QTreeWidgetItem(["More mentions", "Showing the most recent ones; use Browse all occurrences for every one.", "", ""]))
    return item


def _entity_tip(row):
    meta = row.get("metadata") or {}
    aliases = [a for a in meta.get("aliases") or [] if a != row.get("exact_text")][:6]
    lines = [f"{row.get('exact_text') or ''} — {type_label(meta.get('entity_type'))}"]
    if meta.get("parent_entity"):
        lines.append("Part of: " + meta["parent_entity"]["exact_text"])
    if aliases:
        lines.append("Also written: " + ", ".join(aliases))
    lines.append("Expand to see mentions; select two or more entities and Merge to combine spellings.")
    return "\n".join(lines)
