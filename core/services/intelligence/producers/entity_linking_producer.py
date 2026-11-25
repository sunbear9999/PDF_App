"""Entity linking stage: names established elsewhere in the project, and same-entity proposals."""
from __future__ import annotations

from typing import Any

from core.models.producer_contract import ProducerContext, ProducerResult
from core.models.producer_models import ProducerIdentity, deterministic
from core.services.intelligence.entities.project_linking import ProjectEntityLinker
from core.services.intelligence.entity_extraction_pipeline import EntityExtractionPipeline


class EntityLinkingProducer:
    producer_id = "project_linking"

    def __init__(self, artifact_service: Any) -> None:
        self._artifacts = artifact_service

    def identity(self, context: ProducerContext) -> ProducerIdentity:
        return deterministic(self.producer_id, context.producer_version)

    def run(self, context: ProducerContext) -> ProducerResult:
        linker = ProjectEntityLinker(self._artifacts, EntityExtractionPipeline._templates)
        ids = linker.link({**context.source, "id": context.source_id}, context.cancel_check)
        context.raise_if_cancelled()
        return ProducerResult(output_ids=ids, identity=self.identity(context))
