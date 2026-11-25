"""Retire machine mentions without losing reviews or stale entity counts."""
from core.services.intelligence.review_migration import ReviewMigration, source_span_key, REVIEWED


def retire(service, previous, current, rebuild):
    retiring = set(previous) - set(current)
    retiring -= ReviewMigration(service).carry_forward(retiring, current, source_span_key)
    service.db.mark_artifact_ids_status(sorted(retiring), "superseded")
    parents = {row.get("parent_artifact_id") for key, row in previous.items() if key in retiring}
    for identity in sorted(parents - {None, ""}):
        row = service.db.get_artifact(identity)
        if not row:
            continue
        metadata = row.get("metadata") or {}
        if service.db.list_entity_mentions_for_canonical(identity, limit=1):
            rebuild(service.db, identity, metadata.get("entity_type"), row.get("normalized_key"))
        else:
            service.db.update_artifact_metadata(identity, {"mention_count": 0, "source_count": 0, "source_ids": []})
            if row.get("review_state") not in REVIEWED:
                service.db.mark_artifact_ids_status([identity], "superseded")


def retain_failed_group(previous, entity_type, key):
    """An invalid alias cannot erase that group's previously published mentions."""
    return [identity for identity, row in previous.items()
            if row.get("normalized_key") == key
            and (row.get("metadata") or {}).get("entity_type") == entity_type]
