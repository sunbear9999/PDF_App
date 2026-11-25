"""Dates stage: document date extraction, or dates inside transcript segments."""
from __future__ import annotations

from dataclasses import replace
from typing import Any, List

from core.models.extraction_models import DocumentBlock
from core.models.intelligence_models import ProcessingStage, StageStatus
from core.models.producer_contract import (
    ProducerBlocked, ProducerContext, ProducerResult,
)
from core.models.producer_models import ProducerIdentity, deterministic
from core.services.intelligence.date_entities import publish_date_entities
from core.services.intelligence.producers.extraction_producers import ExtractionProducer

_MEDIA = {"audio", "video"}


class DatesProducer:
    producer_id = "date_extraction"

    def __init__(self, document_extraction_service: Any, artifact_service: Any,
                 date_extraction_service: Any) -> None:
        self._documents = ExtractionProducer(
            ProcessingStage.DATES.value, document_extraction_service, artifact_service,
        )
        self._artifacts = artifact_service
        self._dates = date_extraction_service

    def identity(self, context: ProducerContext) -> ProducerIdentity:
        return deterministic(self.producer_id, context.producer_version)

    def run(self, context: ProducerContext) -> ProducerResult:
        result = self._documents.run(context) if context.category not in _MEDIA else self._run_media(context)
        context.raise_if_cancelled()
        linked = publish_date_entities(self._artifacts, {**context.source, "id": context.source_id},
                                       self.identity(context), context.cancel_check)
        return replace(result, output_ids=list(result.output_ids or []) + linked)

    def _run_media(self, context: ProducerContext) -> ProducerResult:
        if not self._dates:
            raise ProducerBlocked("The date extractor is unavailable.")
        segments = self._artifacts.get_all_artifacts(context.source_id, {
            "artifact_type": "transcript_segment",
            "status": StageStatus.COMPLETE.value,
        })
        identity = self.identity(context)
        by_id = {segment.get("artifact_id"): segment for segment in segments}
        written: List[str] = []
        for item in self._dates.extract_dates(_segment_blocks(context.source_id, segments),
                                              context.source_id):
            context.raise_if_cancelled()
            artifact_id = self._write(context, by_id, item, identity)
            if artifact_id:
                written.append(artifact_id)
        return ProducerResult(output_ids=written, identity=identity)

    def _write(self, context, segments_by_id, item, identity) -> str:
        location = item.location
        evidence_id = getattr(location, "block_id", "") if location else ""
        segment = segments_by_id.get(evidence_id) or {}
        artifact_id = self._artifacts.create_artifact(
            source_id=context.source_id, source_path=context.source.get("path"),
            source_type=context.source.get("source_type", ""),
            artifact_type="date_mention", storage_ref_type="transcript_dates",
            storage_ref_id=(
                f"{evidence_id}|{getattr(location, 'char_start', None)}|"
                f"{getattr(location, 'char_end', None)}|{item.normalized_date}"
            ),
            parent_artifact_id=evidence_id or None,
            exact_text=item.context_text or item.exact_text,
            normalized_text=item.normalized_date,
            locator={
                **(segment.get("locator") or {}),
                "char_start": getattr(location, "char_start", None),
                "char_end": getattr(location, "char_end", None),
            },
            metadata={
                "stage": ProcessingStage.DATES.value, "exact_text": item.exact_text,
                "normalized_date": item.normalized_date, "certainty": item.certainty,
                **(getattr(item, "metadata", None) or {}),
            },
            confidence=getattr(item.provenance, "confidence", None),
            created_by="date_extraction_service", extractor_name=self.producer_id,
            identity=identity,
        )
        if artifact_id and evidence_id:
            self._artifacts.link_artifacts(
                artifact_id, evidence_id, "mentioned_in",
                confidence=getattr(item.provenance, "confidence", None),
                created_by="date_extraction_service",
                evidence_artifact_ids=[evidence_id], producer_version="1.0",
            )
        return artifact_id or ""


def _segment_blocks(source_id: str, segments) -> List[DocumentBlock]:
    return [
        DocumentBlock(
            block_id=str(segment.get("artifact_id")), doc_id=source_id, page_num=None,
            block_type="paragraph", exact_text=segment.get("exact_text") or "",
            normalized_text=segment.get("normalized_text") or "",
            section_path=list(segment.get("section_path") or []),
        )
        for segment in segments
    ]
