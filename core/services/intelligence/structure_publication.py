"""Publish the shared structure as source-backed navigable artifacts."""
from core.models.intelligence_models import ArtifactRecord
from core.models.producer_models import deterministic
from core.services.intelligence.review_migration import ReviewMigration, source_span_key

IDENTITY = deterministic("document_structure", "2.1")


def publish(service, source, blocks):
    source_id = source["id"]
    written = []
    heading_ids = {}
    for block in blocks:
        if block.block_type != "heading" and not block.structure.get("heading_level"):
            continue
        record = _record(source, block, "section", block.block_id, 0, len(block.exact_text))
        record.metadata.update(heading_level=block.structure.get("heading_level") or block.heading_level, structure=block.structure)
        service.db.upsert_artifact(IDENTITY.stamp(record))
        heading_ids[block.block_id] = record.artifact_id
        written.append(record.artifact_id)
    for block in blocks:
        for entry in block.structure.get("toc_entries", []):
            record = _record(source, block, "toc_entry", f"{block.block_id}:{entry['char_start']}",
                             entry["char_start"], entry["char_end"])
            target = heading_ids.get(entry.get("target_block_id"))
            record.metadata.update(title=entry["title"], page_label=entry["page_label"], unresolved=not target,
                                   resolution_reason=entry.get("reason"))
            service.db.upsert_artifact(IDENTITY.stamp(record))
            written.append(record.artifact_id)
            if target:
                service.link_artifacts(record.artifact_id, target, "links_to",
                                       confidence=entry.get("confidence", 1.0),
                                       created_by="document_structure", producer_version="2.1")
            _retire_links(service, record.artifact_id, target)
    previous = {row["artifact_id"] for kind in ("section", "toc_entry")
                for row in service.get_all_artifacts(source_id, {"artifact_type": kind})}
    retiring = previous - set(written)
    retiring -= ReviewMigration(service).carry_forward(retiring, written, source_span_key)
    service.db.mark_artifact_ids_status(sorted(retiring), "superseded")
    return written


def _retire_links(service, identity, target):
    for edge in service.db.list_edges(identity):
        if edge["source_artifact_id"] != identity or edge["edge_type"] != "links_to" or edge["target_artifact_id"] == target:
            continue
        for assertion in edge.get("assertions", []):
            if assertion.get("created_by") == "document_structure" and assertion.get("review_state") == "unreviewed":
                service.db.set_edge_assertion_review_state(assertion["assertion_id"], "superseded")


def _record(source, block, kind, reference, start, end):
    return ArtifactRecord(
        artifact_id=ArtifactRecord.stable_id(source["id"], kind, "document_structure", reference),
        source_id=source["id"], source_path=source.get("path"), source_type=source.get("source_type"),
        artifact_type=kind, storage_ref_type="document_structure", storage_ref_id=reference,
        exact_text=block.exact_text[start:end], section_path=block.section_path,
        locator={"page": block.page_num, "block_id": block.block_id, "char_start": start, "char_end": end},
        metadata={"stage": "sections"},
    )
