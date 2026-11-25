"""Producers for source registration, parsing, extraction sync and indexing."""
from __future__ import annotations

from typing import Any

from core.models.intelligence_models import ModelRole, ProcessingStage
from core.models.producer_contract import (
    ProducerBlocked, ProducerContext, ProducerResult,
)
from core.models.producer_models import (
    MethodKind, ProducerIdentity, deterministic, transcription_identity,
)
from core.services.document.ocr_engine_setup import ocr_engine_status
from core.services.extraction.text_layer_quality import assess_pdf_text_layer

# Stage → the extractor that actually produces its artifacts.
_EXTRACTION_PRODUCERS = {
    ProcessingStage.PARSED_BLOCKS.value: "pdf_layout_parser",
    ProcessingStage.SECTIONS.value: "sentence_segmentation",
    ProcessingStage.DATES.value: "date_extraction",
    ProcessingStage.CITATIONS.value: "citation_extraction",
    ProcessingStage.TABLES.value: "table_extraction",
}


class SourceRegisteredProducer:
    producer_id = "source_registry"

    def __init__(self, artifact_service: Any) -> None:
        self._artifacts = artifact_service

    def identity(self, context: ProducerContext) -> ProducerIdentity:
        return deterministic(self.producer_id, context.producer_version)

    def run(self, context: ProducerContext) -> ProducerResult:
        artifact_id = self._artifacts.ensure_source_artifact(context.source)
        return ProducerResult(output_ids=[artifact_id] if artifact_id else [])


class ExtractionProducer:
    """Runs (or reuses) cached document extraction, then publishes artifacts."""

    def __init__(self, stage: str, document_extraction_service: Any, artifact_service: Any):
        self.stage = stage
        self.producer_id = _EXTRACTION_PRODUCERS.get(stage, stage)
        self._des = document_extraction_service
        self._artifacts = artifact_service

    def identity(self, context: ProducerContext) -> ProducerIdentity:
        return deterministic(self.producer_id, context.producer_version)

    def run(self, context: ProducerContext) -> ProducerResult:
        parsing = self.stage == ProcessingStage.PARSED_BLOCKS.value
        if parsing:
            _require_text_layer(context.source)
        blocks = self._extract(context.source_id)
        if parsing and self._des and not blocks:
            raise _no_text_blocked(context.source)
        context.raise_if_cancelled()
        identity = self.identity(context)
        ids = self._artifacts.sync_extraction_artifacts(
            context.source_id, stages=[self.stage], identity=identity,
        )
        return ProducerResult(output_ids=list(ids or []), identity=identity)

    def _extract(self, source_id: str):
        """Run this stage's extraction; parsing returns its blocks."""
        des = self._des
        if not des:
            return None
        if self.stage in {ProcessingStage.PARSED_BLOCKS.value, ProcessingStage.SECTIONS.value}:
            if self.stage == ProcessingStage.PARSED_BLOCKS.value:
                return getattr(des, 'refresh_blocks', des.get_blocks)(source_id)
            else:
                des.get_blocks(source_id)
            if self.stage == ProcessingStage.SECTIONS.value:
                des.get_sections(source_id)
                if hasattr(des, "get_sentences"):
                    des.get_sentences(source_id)
        elif self.stage == ProcessingStage.DATES.value:
            des.get_dates(source_id)
        elif self.stage == ProcessingStage.CITATIONS.value:
            des.get_citation_index(source_id)
        elif self.stage == ProcessingStage.TABLES.value:
            des.get_tables(source_id)
        return None


def _require_text_layer(source: dict) -> None:
    """Every intelligence run starts here: a scan without usable text blocks
    the pipeline with a reason the GUI turns into a "Run OCR" prompt."""
    path = str(source.get("path") or "")
    if (source.get("source_type") or "").lower() != "pdf" or not path:
        return
    report = assess_pdf_text_layer(path)
    if report.needs_ocr:
        raise ProducerBlocked(
            f"Needs OCR: {report.summary()}. Run OCR on this PDF, then intelligence "
            "processing resumes.", reason="needs_ocr",
            details={**report.to_metadata(), **_ocr_engine_details()},
        )


def _no_text_blocked(source: dict) -> ProducerBlocked:
    is_pdf = (source.get("source_type") or "").lower() == "pdf"
    if is_pdf:
        return ProducerBlocked(
            "Needs OCR: no readable text was extracted from this PDF. Run OCR, then "
            "intelligence processing resumes.", reason="needs_ocr",
            details={"path": source.get("path") or "", "summary": "No readable text was extracted.",
                     **_ocr_engine_details()},
        )
    engine = _ocr_engine_details()
    if (source.get("source_type") or "").lower() == "image" and not engine["ocr_engine_ready"]:
        # Image parsers OCR on their own; without an engine they read nothing.
        return ProducerBlocked(
            "Needs the OCR engine: images are read with Tesseract, which is not "
            "installed. Install it from Settings → AI Setup → Pipeline Models.",
            reason="needs_ocr_engine", details={"path": source.get("path") or "", **engine},
        )
    return ProducerBlocked(
        "No readable text was extracted from this source, so there is nothing to analyse.",
        reason="no_text", details={"path": source.get("path") or ""},
    )


def _ocr_engine_details() -> dict:
    status = ocr_engine_status()
    return {"ocr_engine_ready": status["ready"], "ocr_engine_error": status["error"]}


class FullTextIndexProducer:
    producer_id = "fts_index"

    def __init__(self, project_manager: Any) -> None:
        self._pm = project_manager

    def identity(self, context: ProducerContext) -> ProducerIdentity:
        return deterministic(self.producer_id, context.producer_version)

    def run(self, context: ProducerContext) -> ProducerResult:
        db = getattr(self._pm, "db_intelligence", None)
        return ProducerResult(output_ids=list(db.reindex_source_fts(context.source_id) or []) if db else [])


class TranscriptIndexProducer:
    """Indexes an existing transcript; it never generates one."""
    producer_id = "transcript_index"

    def __init__(self, project_manager: Any, artifact_service: Any) -> None:
        self._pm = project_manager
        self._artifacts = artifact_service

    def identity(self, context: ProducerContext) -> ProducerIdentity:
        return transcription_identity(self._transcript(context.source_id))

    def run(self, context: ProducerContext) -> ProducerResult:
        if not (self._transcript(context.source_id).get("segments")):
            raise ProducerBlocked(
                "Waiting for transcription: no stored transcript exists yet. "
                "Transcribe the source, then this stage runs automatically."
            )
        ids = self._artifacts.sync_transcript_artifacts(context.source_id)
        return ProducerResult(output_ids=list(ids or []), identity=self.identity(context))

    def _transcript(self, source_id: str) -> dict:
        try:
            return self._pm.get_video_transcript(source_id) or {}
        except Exception:
            return {}


class EmbeddingProducer:
    producer_id = "artifact_embeddings"

    def __init__(self, llm_manager: Any, embedding_service: Any, model_role_service: Any):
        self._llm = llm_manager
        self._embeddings = embedding_service
        self._roles = model_role_service

    def identity(self, context: ProducerContext) -> ProducerIdentity:
        model = ""
        if self._roles:
            try:
                model = self._roles.resolve_role(ModelRole.EMBEDDING.value) or ""
            except Exception:
                model = ""
        return ProducerIdentity(
            producer_id=self.producer_id, producer_version=context.producer_version,
            method_kind=MethodKind.LEARNED.value, backend="embedding", model_id=model,
            fallback_reason="" if model else "no embedding model is configured",
        )

    def run(self, context: ProducerContext) -> ProducerResult:
        if not self._llm or not getattr(self._llm, "ai_enabled", False) or not self._embeddings:
            raise ProducerBlocked(
                "Waiting for an embedding model: local AI is disabled or no "
                "embedding service is available."
            )
        embedded = int(self._embeddings.embed_source(
            context.source_id, cancel_check=context.cancel_check) or 0)
        context.raise_if_cancelled()
        if embedded <= 0:
            raise RuntimeError(
                "Embedding did not complete all eligible canonical artifacts. Check that parsing is "
                "complete and an embedding model/vector collection is available."
            )
        return ProducerResult(
            identity=self.identity(context), diagnostics={"embedded": embedded},
        )
