"""Cross-references stage: label references ("see Table 7.2", "note 12") and their targets."""
from __future__ import annotations

from typing import Any

from core.models.producer_contract import ProducerBlocked, ProducerContext, ProducerResult
from core.models.producer_models import ProducerIdentity, deterministic
from core.services.extraction import cross_references
from core.services.extraction.structure.reference_style import note_targets
from core.services.intelligence.cross_reference_publication import publish


class CrossReferencesProducer:
    producer_id = "cross_references"

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
        index = self._des.get_citation_index(context.source_id)
        notes = [e for e in (index.bibliography_entries if index else []) if e.citation_style == "footnote"]
        context.raise_if_cancelled()
        references = cross_references.find(blocks, notes, self._patterns, note_targets)
        ids = publish(self._artifacts, {**context.source, "id": context.source_id}, references, blocks)
        return ProducerResult(output_ids=ids, identity=self.identity(context),
                              diagnostics={"references": len(references),
                                           "resolved": sum(r.resolved for r in references)})
