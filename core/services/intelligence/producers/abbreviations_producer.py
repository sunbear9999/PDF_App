"""Abbreviations stage: the document's own key ("AGN  Archivo General de la Nación")."""
from __future__ import annotations

from typing import Any, Dict, List

from core.models.intelligence_models import ArtifactRecord
from core.models.producer_contract import ProducerBlocked, ProducerContext, ProducerResult
from core.models.producer_models import ProducerIdentity, deterministic
from core.services.extraction import abbreviation_keys
from core.services.intelligence.review_migration import ReviewMigration, source_span_key

STAGE = "abbreviations"


class AbbreviationsProducer:
    producer_id = "abbreviation_keys"

    def __init__(self, document_extraction_service: Any, artifact_service: Any, pattern_registry: Any = None) -> None:
        self._des = document_extraction_service
        self._artifacts = artifact_service
        self._patterns = pattern_registry

    def identity(self, context: ProducerContext) -> ProducerIdentity:
        return deterministic(self.producer_id, context.producer_version)

    def run(self, context: ProducerContext) -> ProducerResult:
        if not self._des:
            raise ProducerBlocked("Document extraction is unavailable.")
        blocks = self._des.get_blocks(context.source_id) or []
        context.raise_if_cancelled()
        path = str(context.source.get("path") or "")
        document = _open_pdf(path)
        try:
            entries = abbreviation_keys.find(blocks, self._patterns, _page_lines(document) if document else None)
        finally:
            if document is not None:
                document.close()
        identity = self.identity(context)
        ids = publish(self._artifacts, {**context.source, "id": context.source_id}, entries, identity)
        return ProducerResult(output_ids=ids, identity=identity, diagnostics={"abbreviations": len(entries)})


def _open_pdf(path: str):
    if not path.lower().endswith(".pdf"):
        return None
    import fitz
    try:
        return fitz.open(path)
    except (RuntimeError, ValueError, OSError):
        return None


def _page_lines(document):
    """(x0, y0, y1, text) per text line of a page, for keys set out in columns."""
    def lines(page_number):
        if page_number is None or not 0 <= page_number < len(document):
            return []
        out = []
        for block in document[page_number].get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                text = "".join(span["text"] for span in line["spans"]).strip()
                if text:
                    out.append((line["bbox"][0], line["bbox"][1], line["bbox"][3], text))
        return out
    return lines


def publish(service: Any, source: Dict[str, Any], entries, identity) -> List[str]:
    source_id = source.get("id") or ""
    written = []
    for entry in entries:
        record = ArtifactRecord(
            artifact_id=ArtifactRecord.stable_id(source_id, "abbreviation", STAGE, entry.key),
            source_id=source_id, source_path=source.get("path"), source_type=source.get("source_type"),
            artifact_type="abbreviation", storage_ref_type=STAGE, storage_ref_id=entry.key,
            exact_text=entry.key, normalized_text=entry.name,
            locator={"page": entry.page, "block_id": entry.block_id, "char_start": entry.char_start,
                     "char_end": entry.char_end},
            metadata={"stage": STAGE, "expansion": entry.expansion, "name": entry.name,
                      "parent_key": entry.parent_key, "layout": entry.layout},
        )
        service.db.upsert_artifact(identity.stamp(record))
        written.append(record.artifact_id)
    previous = {row["artifact_id"] for row in service.get_all_artifacts(source_id, {"artifact_type": "abbreviation"})}
    retiring = previous - set(written)
    retiring -= ReviewMigration(service).carry_forward(retiring, written, source_span_key)
    service.db.mark_artifact_ids_status(sorted(retiring), "superseded")
    return written
