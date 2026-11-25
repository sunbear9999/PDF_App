"""Summaries stage: extractive TextRank summary per section + one per document."""
from __future__ import annotations

from typing import Any, Dict, List

from core.models.intelligence_models import ArtifactRecord, ProcessingStage, StageStatus
from core.models.producer_contract import ProducerContext, ProducerResult
from core.models.producer_models import ProducerIdentity, deterministic
from core.services.intelligence.artifact_eligibility import is_current_artifact


class SummariesProducer:
    producer_id = "extractive_summarizer"

    def __init__(self, project_manager: Any, artifact_service: Any, summarizer: Any) -> None:
        self._pm = project_manager
        self._artifacts = artifact_service
        self._summarizer = summarizer

    def identity(self, context: ProducerContext) -> ProducerIdentity:
        return deterministic(self.producer_id, context.producer_version)

    def run(self, context: ProducerContext) -> ProducerResult:
        db = getattr(self._pm, "db_intelligence", None)
        if db is None or not self._summarizer:
            return ProducerResult()
        sentences = self._sentences(db, context.source_id)
        if not sentences:
            return ProducerResult()
        identity = self.identity(context)
        source_artifact = self._artifacts.ensure_source_artifact(context.source)
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for sentence in sentences:
            key = " > ".join(sentence.get("section_path") or []) or "Document"
            groups.setdefault(key, []).append(sentence)
        written: List[str] = []
        section_summaries: List[str] = []
        for section_key, members in groups.items():
            context.raise_if_cancelled()
            summary = self._summarizer.summarize(
                [s.get("exact_text", "") for s in members], max_sentences=3
            )
            if summary.strip():
                section_summaries.append(summary)
                written.append(self._write(
                    db, context, identity, source_artifact, "section_summary", section_key,
                    summary, members[0].get("section_path") or [],
                    {"scope": "section", "section": section_key},
                ))
        document = self._summarizer.summarize(
            section_summaries or [s.get("exact_text", "") for s in sentences], max_sentences=5,
        )
        if document.strip():
            written.append(self._write(
                db, context, identity, source_artifact, "doc_summary", "doc",
                document, [], {"scope": "document"},
            ))
        return ProducerResult(output_ids=written, identity=identity)

    def _sentences(self, db, source_id: str) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for kind in ("sentence", "transcript_segment"):
            rows.extend(self._artifacts.get_all_artifacts(source_id, {
                "artifact_type": kind, "status": StageStatus.COMPLETE.value,
            }))
        return [row for row in rows if is_current_artifact(db, row)]

    def _write(self, db, context, identity, parent, ref_type, ref_id, text,
               section_path, extra) -> str:
        artifact_id = ArtifactRecord.stable_id(context.source_id, "summary", ref_type, ref_id)
        db.upsert_artifact(identity.stamp(ArtifactRecord(
            artifact_id=artifact_id, source_id=context.source_id, source_path=context.path,
            source_type=context.source.get("source_type", ""), artifact_type="summary",
            storage_ref_type=ref_type, storage_ref_id=ref_id, parent_artifact_id=parent,
            exact_text=text, normalized_text=text.lower(), section_path=list(section_path),
            metadata={"stage": ProcessingStage.SUMMARIES.value, **extra},
            extractor_name=self.producer_id, extractor_version="1.0",
            input_hash=self._artifacts.text_input_hash(text),
        )))
        return artifact_id
