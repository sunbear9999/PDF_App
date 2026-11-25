"""Publish confident citation links and retain ambiguous candidates for review."""
from collections import defaultdict

MATCHER_VERSION = "2.1"


def citation_candidates(index, entry_ids):
    candidates = defaultdict(list)
    for match in index.citation_matches:
        if not match.is_ambiguous:
            continue
        ids = [match.bibliography_entry_id, *match.alternate_entry_ids]
        candidates[match.in_text_citation_id].extend(
            entry_ids[key] for key in ids if key in entry_ids)
    return {key: sorted(set(values)) for key, values in candidates.items()}


def publish_citation_links(service, index, citation_ids, entry_ids):
    accepted = defaultdict(set)
    notes = {e.entry_id for e in index.bibliography_entries if e.citation_style == "footnote"}
    for match in index.citation_matches:
        citation_id = citation_ids.get(match.in_text_citation_id)
        entry_id = entry_ids.get(match.bibliography_entry_id)
        if not citation_id or not entry_id or match.confidence <= 0 or match.is_ambiguous:
            continue
        accepted[citation_id].add(entry_id)
        service.link_artifacts(
            citation_id, entry_id, "refers_to_note" if match.bibliography_entry_id in notes else "cites", confidence=match.confidence,
            created_by="citation_matcher", evidence_artifact_ids=[citation_id],
            producer_version=MATCHER_VERSION,
            metadata={"ambiguous": False, "alternate_entry_ids": [],
                      "reason": match.reason, "score_breakdown": match.score_breakdown},
        )
    for entry in index.bibliography_entries:
        for target_key in entry.cited_entry_ids or [entry.normalized_keys.get("cites_entry")]:
            target = entry_ids.get(target_key)
            if target and entry.entry_id in notes:
                accepted[entry_ids[entry.entry_id]].add(target)
                service.link_artifacts(entry_ids[entry.entry_id], target, "cites", confidence=0.8,
                                       created_by="citation_matcher", producer_version=MATCHER_VERSION,
                                       evidence_artifact_ids=[entry_ids[entry.entry_id]])
    # Remove obsolete machine assertions while preserving explicit human review.
    # Unchanged assertions are untouched, making reruns idempotent.
    for citation_id in [*citation_ids.values(), *(entry_ids[n] for n in notes if n in entry_ids)]:
        for edge in service.db.list_edges(citation_id):
            if edge["edge_type"] not in {"cites", "refers_to_note"} or edge["source_artifact_id"] != citation_id:
                continue
            for assertion in edge.get("assertions", []):
                obsolete = (edge["target_artifact_id"] not in accepted[citation_id]
                            or assertion.get("producer_version") != MATCHER_VERSION)
                if (obsolete and assertion.get("created_by") == "citation_matcher"
                        and assertion.get("review_state") == "unreviewed"):
                    service.db.set_edge_assertion_review_state(assertion["assertion_id"], "superseded")
