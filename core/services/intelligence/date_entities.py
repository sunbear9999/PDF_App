"""Dates as entities: every dated occurrence links to one project-wide date.

The Dates stage publishes ``date_mention`` observations with a context role. A
date *of the world* (an event or measurement period, not a citation,
copyright or publication year) also becomes a DATE ``entity_mention`` on its
exact phrase, grouped by normalized value and precision: every "1765" in the
project is one entity, and the 1840s stay distinct from the year 1840. Entity
browsing, counts, networks and review then treat dates like any other entity.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List

from core.models.intelligence_models import EntityMention, ProcessingStage
from core.models.producer_models import ProducerIdentity
from core.services.intelligence.entities.publish import MentionPublisher, canonical_entity_id, rebuild_canonical
from core.services.intelligence.entities.retirement import retire

DATE_TYPE = "DATE"
ENTITY_ROLES = frozenset({"event", "measurement_period"})
LINKED_PRECISIONS = frozenset({"day", "month", "year", "decade"})
EXTRACTOR_ID = "date_extraction"


def date_key(metadata: Dict[str, Any]) -> str:
    """Group key: the normalized value, with decades marked ("1840s")."""
    value = str(metadata.get("normalized_date") or "")
    return value + "s" if metadata.get("precision") == "decade" else value


def is_entity_date(row: Dict[str, Any]) -> bool:
    meta = row.get("metadata") or {}
    eligible = meta.get("timeline_eligible") is True or meta.get("context_role") in ENTITY_ROLES
    return eligible and meta.get("precision") in LINKED_PRECISIONS and bool(date_key(meta))


def date_mention_entity(row: Dict[str, Any]) -> EntityMention:
    meta, locator = row.get("metadata") or {}, row.get("locator") or {}
    phrase = str(meta.get("exact_text") or row.get("normalized_text") or "")
    return EntityMention(
        surface_text=phrase, entity_type=DATE_TYPE, normalized_key=date_key(meta), confidence=row.get("confidence") or 0.7,
        doc_id=row.get("source_id") or "", block_id=str(locator.get("block_id") or ""), page_num=locator.get("page"),
        sentence_id=f"date:{row['artifact_id']}", char_start=locator.get("char_start"), char_end=locator.get("char_end"),
        section_path=list(row.get("section_path") or []), extractor_id=EXTRACTOR_ID,
        metadata={"date_artifact_id": row["artifact_id"], "precision": meta.get("precision"),
                  "context_role": meta.get("context_role"), "coordinate_basis": "block",
                  "evidence_artifact_id": row.get("parent_artifact_id")},
    )


def publish_date_entities(artifact_service: Any, source: Dict[str, Any], identity: ProducerIdentity,
                          cancel_check: Callable[[], bool] = lambda: False) -> List[str]:
    """Publish DATE entity mentions for one source and retire its stale ones."""
    db = getattr(artifact_service, "db", None)
    source_id = source.get("id") or source.get("source_id") or ""
    if db is None or not source_id:
        return []
    dates = artifact_service.get_all_artifacts(source_id, {"artifact_type": "date_mention", "status": "complete"})
    mentions = [date_mention_entity(row) for row in dates if is_entity_date(row)]
    previous = {row["artifact_id"]: row for row in artifact_service.get_all_artifacts(
        source_id, {"artifact_type": "entity_mention", "status": "complete"}) if is_date_stage(row)}
    publisher = MentionPublisher(artifact_service, {EXTRACTOR_ID: identity}, source)
    result = publisher.publish(mentions, previous, cancel_check, stage=ProcessingStage.DATES.value)
    if result.cancelled:
        return result.written
    retire(artifact_service, previous, result.written, rebuild_canonical)
    for canonical_id, (entity_type, key) in result.rebuild.items():
        rebuild_canonical(db, canonical_id, entity_type, key)
    return [identity_ for identity_ in result.written if identity_]


def is_date_stage(row: Dict[str, Any]) -> bool:
    return (row.get("metadata") or {}).get("stage") == ProcessingStage.DATES.value


def date_entity_id(normalized_value: str) -> str:
    return canonical_entity_id(DATE_TYPE, normalized_value)
