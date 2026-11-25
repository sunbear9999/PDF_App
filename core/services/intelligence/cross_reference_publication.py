"""Publish cross-references as navigable artifacts with ``refers_to`` edges.

Every reference is published (resolved or not, with the reason), so abstentions
stay countable and reviewable; only a resolved one gets an edge. Targets are the
document's own artifacts: the heading's ``section`` artifact, the note's
``document_note`` artifact, or a ``caption`` artifact published here for the
caption block. Reviewed references survive reruns (ReviewMigration).
"""
from __future__ import annotations

from typing import Any, Dict, List

from core.models.intelligence_models import ArtifactRecord
from core.models.producer_models import deterministic
from core.services.intelligence.review_migration import ReviewMigration, source_span_key

EDGE_TYPE = "refers_to"
VERSION = "1.0"
IDENTITY = deterministic("cross_references", VERSION)
STAGE = "cross_references"


def publish(service: Any, source: Dict[str, Any], references, blocks) -> List[str]:
    source_id = source.get("id") or source.get("source_id") or ""
    by_block = {block.block_id: block for block in blocks}
    written: List[str] = []
    for reference in references:
        target = _target(service, source, reference, by_block, written)
        record = _record(source, reference.block, "cross_reference", f"{reference.block.block_id}:{reference.start}",
                         reference.start, reference.end)
        record.metadata.update(kind=reference.kind, label=reference.label, unresolved=not target,
                               resolution_reason=reference.reason or "", stage=STAGE)
        service.db.upsert_artifact(IDENTITY.stamp(record))
        written.append(record.artifact_id)
        if target:
            service.link_artifacts(record.artifact_id, target, EDGE_TYPE, confidence=1.0, created_by="cross_references",
                                   producer_version=VERSION,
                                   metadata={"reason": f"{reference.kind.title()} {reference.label} is defined here"})
        _retire_links(service, record.artifact_id, target)
    _retire(service, source_id, written)
    return written


def _target(service, source, reference, by_block, written) -> str:
    source_id = source.get("id") or source.get("source_id") or ""
    if reference.target_note_id:
        return ArtifactRecord.stable_id(source_id, "document_note", "extraction_citation_index", reference.target_note_id)
    block = by_block.get(reference.target_block_id)
    if block is None:
        return ""
    if block.block_type == "heading" or block.structure.get("heading_level"):
        identity = ArtifactRecord.stable_id(source_id, "section", "document_structure", block.block_id)
        if service.db.get_artifact(identity):
            return identity
    caption = _record(source, block, "caption", block.block_id, 0, len(block.exact_text))
    caption.metadata.update(kind=reference.kind, label=reference.label, stage=STAGE)
    service.db.upsert_artifact(IDENTITY.stamp(caption))
    if caption.artifact_id not in written:
        written.append(caption.artifact_id)
    return caption.artifact_id


def _retire_links(service, identity: str, target: str) -> None:
    for edge in service.db.list_edges(identity):
        if edge["source_artifact_id"] != identity or edge["edge_type"] != EDGE_TYPE or edge["target_artifact_id"] == target:
            continue
        for assertion in edge.get("assertions", []):
            if assertion.get("created_by") == "cross_references" and assertion.get("review_state") == "unreviewed":
                service.db.set_edge_assertion_review_state(assertion["assertion_id"], "superseded")


def _retire(service, source_id: str, written: List[str]) -> None:
    previous = {row["artifact_id"] for kind in ("cross_reference", "caption")
                for row in service.get_all_artifacts(source_id, {"artifact_type": kind})}
    retiring = previous - set(written)
    retiring -= ReviewMigration(service).carry_forward(retiring, written, source_span_key)
    service.db.mark_artifact_ids_status(sorted(retiring), "superseded")


def _record(source, block, kind, reference, start, end) -> ArtifactRecord:
    source_id = source.get("id") or source.get("source_id") or ""
    return ArtifactRecord(
        artifact_id=ArtifactRecord.stable_id(source_id, kind, "cross_references", reference),
        source_id=source_id, source_path=source.get("path"), source_type=source.get("source_type"),
        artifact_type=kind, storage_ref_type="cross_references", storage_ref_id=reference,
        exact_text=block.exact_text[start:end], section_path=list(block.section_path or []),
        locator={"page": block.page_num, "block_id": block.block_id, "char_start": start, "char_end": end},
        metadata={"stage": STAGE},
    )
