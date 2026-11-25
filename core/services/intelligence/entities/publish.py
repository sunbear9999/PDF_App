"""Resolve mentions to canonical entities and publish them as artifacts and edges.

Mentions group by (entity type, normalized key). The canonical id is
deterministic in that pair, so the same entity in another document maps to the
same node; explicit user aliases and mention splits take precedence.
"""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List

from core.models.intelligence_models import ArtifactRecord, EntityMention, ProcessingStage
from core.models.producer_models import ProducerIdentity, deterministic
from core.services.intelligence.entities.retirement import retain_failed_group

RESOLUTION_IDENTITY = deterministic("entity_resolution", "3.2")
EDGE_VERSION = "2.0"


@dataclass
class PublishResult:
    written: List[str] = field(default_factory=list)
    canonical_ids: Dict[tuple, str] = field(default_factory=dict)
    rebuild: Dict[str, tuple] = field(default_factory=dict)
    cancelled: bool = False


def canonical_entity_id(entity_type: str, normalized_key: str) -> str:
    return ArtifactRecord.stable_id("", "entity", "entity_resolution", f"{entity_type}|{normalized_key}")


def mention_ref(mention: EntityMention) -> str:
    return f"{mention.sentence_id}|{mention.char_start}|{mention.char_end}|{mention.normalized_key}"


def group_mentions(mentions: List[EntityMention]) -> Dict[tuple, List[EntityMention]]:
    groups: Dict[tuple, List[EntityMention]] = {}
    for mention in mentions:
        if mention.normalized_key:
            groups.setdefault((mention.entity_type, mention.normalized_key), []).append(mention)
    return groups


class MentionPublisher:
    def __init__(self, artifact_service: Any, identities: Dict[str, ProducerIdentity], source: Dict[str, Any]) -> None:
        self.service = artifact_service
        self.db = artifact_service.db
        self.identities = identities
        self.source_id = source.get("id") or source.get("source_id") or ""
        self.path = source.get("path") or self.source_id
        self.source_type = source.get("source_type", "")
        self.stage = ProcessingStage.ENTITIES.value

    def publish(self, mentions: List[EntityMention], previous: Dict[str, Dict[str, Any]],
                cancel_check: Callable[[], bool], stage: str = ProcessingStage.ENTITIES.value) -> PublishResult:
        """``stage`` owns the mentions: each stage retires only its own."""
        self.stage = stage
        result = PublishResult()
        for (entity_type, key), group in group_mentions(mentions).items():
            if cancel_check():
                result.cancelled = True
                return result
            try:
                group_id = self.db.resolve_entity_alias(entity_type, key) or canonical_entity_id(entity_type, key)
            except ValueError:
                logging.getLogger(__name__).exception("Invalid entity alias for %s", key)
                result.written.extend(retain_failed_group(previous, entity_type, key))
                continue
            result.canonical_ids[(entity_type, key)] = group_id
            touched = {self._publish_mention(mention, group_id, result.written) for mention in group}
            for canonical_id in sorted(touched or {group_id}):
                result.rebuild[canonical_id] = (entity_type, key)
                result.written.append(canonical_id)
        return result

    def _publish_mention(self, mention: EntityMention, group_id: str, written: List[str]) -> str:
        prospective = ArtifactRecord.stable_id(self.source_id, "entity_mention", "entity_mentions", mention_ref(mention))
        # A user split is mention-specific; its destination must not leak to the group.
        canonical_id = self.db.resolve_entity_mention_parent(prospective) or group_id
        mention.metadata["canonical_artifact_id"] = canonical_id
        mention_id = self.write_mention(mention, canonical_id)
        if mention_id:
            written.append(mention_id)
            self._link(mention, mention_id, canonical_id)
        return canonical_id

    def _link(self, mention: EntityMention, mention_id: str, canonical_id: str) -> None:
        evidence_id = mention.metadata.get("evidence_artifact_id")
        evidence = [evidence_id] if evidence_id else []
        self.service.link_artifacts(mention_id, canonical_id, "mention_of", confidence=mention.confidence,
                                    created_by=mention.extractor_id, evidence_artifact_ids=evidence,
                                    producer_version=EDGE_VERSION)
        if evidence_id and self.db.get_artifact(evidence_id):
            self.service.link_artifacts(canonical_id, evidence_id, "mentioned_in", confidence=mention.confidence,
                                        created_by=mention.extractor_id, evidence_artifact_ids=[evidence_id],
                                        producer_version=EDGE_VERSION)

    def write_mention(self, mention: EntityMention, canonical_id: str) -> str:
        ref = mention_ref(mention)
        artifact_id = ArtifactRecord.stable_id(self.source_id, "entity_mention", "entity_mentions", ref)
        identity = self.identities.get(mention.extractor_id) or deterministic(mention.extractor_id or "entity_extractor")
        self.db.upsert_artifact(identity.stamp(ArtifactRecord(
            artifact_id=artifact_id, source_id=self.source_id, source_path=self.path,
            source_type=self.source_type, artifact_type="entity_mention",
            storage_ref_type="entity_mentions", storage_ref_id=ref, parent_artifact_id=canonical_id,
            exact_text=mention.surface_text, normalized_text=mention.normalized_key,
            normalized_key=mention.normalized_key,
            locator={"page": mention.page_num, "block_id": mention.block_id, "sentence_id": mention.sentence_id,
                     "char_start": mention.char_start, "char_end": mention.char_end},
            section_path=list(mention.section_path or []),
            metadata={**mention.metadata, "stage": self.stage,
                      "entity_type": mention.entity_type, "surface_text": mention.surface_text,
                      "evidence_artifact_id": mention.metadata.get("evidence_artifact_id")},
            confidence=mention.confidence, extractor_name=mention.extractor_id, extractor_version="1.0",
            input_hash=self.service.text_input_hash(mention.surface_text),
        )))
        return artifact_id


def rebuild_canonical(db: Any, canonical_id: str, entity_type: str, key: str) -> None:
    """Recompute a canonical entity's display surface, aliases and counts from all its mentions."""
    rows = db.list_entity_mentions_for_canonical(canonical_id)
    surfaces = [str(row.get("exact_text") or "").strip() for row in rows if str(row.get("exact_text") or "").strip()]
    if not rows or not surfaces:
        return
    counts: Dict[str, int] = {}
    for surface in surfaces:
        counts[surface] = counts.get(surface, 0) + 1
    inferred = max(counts, key=lambda surface: (counts[surface], len(surface), surface.casefold()))
    existing = db.get_artifact(canonical_id) or {}
    key = existing.get("normalized_key") or key
    corrected = existing.get("review_state") == "corrected"
    # A key's entity is named by its full name ("TI" -> "Testaments of Ixil").
    named = next(((row.get("metadata") or {}).get("abbreviation_of") for row in rows
                  if (row.get("metadata") or {}).get("abbreviation_of")), None)
    display = (existing.get("exact_text") if corrected else (named or inferred)) or inferred
    source_ids = {row.get("source_id") for row in rows if row.get("source_id")}
    db.upsert_artifact(RESOLUTION_IDENTITY.stamp(ArtifactRecord(
        artifact_id=canonical_id, source_id=None, artifact_type="entity",
        storage_ref_type="entity_resolution", storage_ref_id=f"{entity_type}|{key}",
        exact_text=display, normalized_text=key, normalized_key=key,
        metadata={
            "stage": ProcessingStage.ENTITIES.value, "entity_type": entity_type,
            "aliases": sorted({s for s in surfaces if s != display}, key=lambda s: (s.casefold(), s)),
            "first_seen_doc_id": (existing.get("metadata") or {}).get("first_seen_doc_id") or rows[0].get("source_id"),
            "mention_count": len(rows), "source_count": len(source_ids), "source_ids": sorted(source_ids),
            # Where the mentions sit (body, note, caption, ...), so views can count body text only.
            "region_counts": dict(sorted(Counter(
                str((row.get("metadata") or {}).get("region") or "body") for row in rows).items())),
        },
        confidence=max((float(row.get("confidence") or 0.0) for row in rows), default=0.7),
        extractor_name="entity_pipeline", extractor_version="2.0",
    )))
