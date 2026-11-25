"""The plugin's "Legal analysis" stage: opinion parts, reasoning steps, dispositions and treatments.

Each finding is an artifact on the sentence it comes from (same page, block and offsets),
typed ``locallaws.<kind>`` so the Case analysis tab can filter by kind; reruns replace the
plugin's earlier findings for the source and keep reviewed ones.
"""
from __future__ import annotations

from typing import Any, List

from core.models.intelligence_models import ArtifactRecord
from core.models.producer_contract import ProducerContext, ProducerResult
from core.models.producer_models import deterministic

from .analysis import LABELS, analyze, display_text

STAGE_ID = "locallaws_legal_analysis"
PRODUCER_ID = "locallaws.legal_analysis"
ARTIFACT_TYPES = [f"locallaws.{kind}" for kind in LABELS]


class LegalAnalysisProducer:
    producer_id = PRODUCER_ID

    def __init__(self, intelligence_api: Any) -> None:
        self._api = intelligence_api

    def identity(self, context: ProducerContext):
        return deterministic(PRODUCER_ID, context.producer_version)

    def run(self, context: ProducerContext) -> ProducerResult:
        rows = self._api.get_all_artifacts(context.source_id, {"artifact_type": "sentence", "status": "complete"})
        rows.sort(key=_reading_order)
        context.raise_if_cancelled()
        findings = analyze(rows)
        records = [_record(context, finding) for finding in findings]
        if records:
            self._api.upsert_artifacts(records)
        written = [record.artifact_id for record in records]
        previous = {row["artifact_id"] for kind in ARTIFACT_TYPES
                    for row in self._api.get_all_artifacts(context.source_id, {"artifact_type": kind})
                    if row.get("review_state") not in {"verified", "corrected"}}
        self._api.retire_artifacts(sorted(previous - set(written)))
        counts = {}
        for finding in findings:
            counts[finding.kind] = counts.get(finding.kind, 0) + 1
        return ProducerResult(output_ids=written, identity=self.identity(context),
                              diagnostics={"findings": counts, "legal_document": bool(findings)})


def _reading_order(row) -> tuple:
    from core.services.extraction.pdf_layout_parser import parse_block_id
    locator = row.get("locator") or {}
    block = parse_block_id(str(locator.get("block_id") or "")) or (0, 0)
    return (locator.get("page") or 0, block[1], locator.get("char_start") or 0)


def _record(context: ProducerContext, finding) -> ArtifactRecord:
    row = finding.row
    kind = f"locallaws.{finding.kind}"
    reference = f"{row.get('storage_ref_id') or row['artifact_id']}:{finding.kind}:{finding.metadata.get('authority_key', '')}"
    return deterministic(PRODUCER_ID, context.producer_version).stamp(ArtifactRecord(
        artifact_id=ArtifactRecord.stable_id(context.source_id, kind, PRODUCER_ID, reference),
        source_id=context.source_id, source_path=context.source.get("path"), source_type=context.source.get("source_type"),
        artifact_type=kind, storage_ref_type=PRODUCER_ID, storage_ref_id=reference,
        parent_artifact_id=row["artifact_id"], exact_text=row.get("exact_text") or "",
        section_path=list(row.get("section_path") or []), locator=dict(row.get("locator") or {}),
        metadata={"stage": STAGE_ID, "display_kind": LABELS[finding.kind], "display_text": display_text(finding),
                  "cue": finding.cue, **finding.metadata},
        confidence=0.8, created_by="plugin:locallaws",
    ))
