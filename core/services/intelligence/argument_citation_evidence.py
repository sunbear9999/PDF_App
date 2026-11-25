"""Recorded citation destinations carried by an argument's exact source span.

Attribution is provenance, not evidence that one proposition entails another.
Only current, unambiguous links are eligible for a relation proposal.
"""
from collections import defaultdict
from core.services.intelligence.artifact_eligibility import active_assertions, is_current_artifact


def citation_evidence(service, source_id, units, cancelled=lambda: False):
    by_block = defaultdict(list)
    for kind in ("in_text_citation", "note_reference"):
        for marker in service.get_all_artifacts(source_id, {"artifact_type": kind, "status": "complete"}):
            if cancelled():
                return {}
            metadata = marker.get("metadata") or {}
            if not is_current_artifact(service.db, marker) or metadata.get("ambiguous") or metadata.get("migration_conflict"):
                continue
            loc = marker.get("locator") or {}
            if not loc.get("block_id") or not isinstance(loc.get("char_start"), int):
                continue
            targets = []
            for edge in service.db.list_edges(marker["artifact_id"]):
                if (edge["source_artifact_id"] != marker["artifact_id"] or edge["edge_type"] not in {"cites", "refers_to_note"}
                        or not active_assertions(edge)):
                    continue
                target = service.db.get_artifact(edge["target_artifact_id"])
                if (target and target.get("source_id") == source_id and is_current_artifact(service.db, target)
                        and not (target.get("metadata") or {}).get("migration_conflict")):
                    targets.append(target["artifact_id"])
            if targets:
                by_block[loc["block_id"]].append((loc, [marker["artifact_id"], *targets]))
    output = {}
    for unit in units:
        if cancelled():
            break
        loc = unit.get("locator") or {}
        found = set()
        for span in loc.get("source_spans") or [loc]:
            start, end = span.get("char_start"), span.get("char_end")
            if not isinstance(start, int) or not isinstance(end, int):
                continue
            for marker, targets in by_block.get(span.get("block_id"), []):
                if start <= marker["char_start"] and marker.get("char_end", end+1) <= end:
                    found.update(targets)
        if found:
            output[unit["artifact_id"]] = sorted(found)
    return output
