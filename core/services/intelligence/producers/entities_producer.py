"""Entities stage: layered NER → patterns → resolution → relations."""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from core.models.producer_contract import ProducerContext, ProducerResult
from core.models.producer_models import ProducerIdentity, deterministic
from core.services.intelligence.entity_extraction_pipeline import extractor_identity


class EntitiesProducer:
    producer_id = "entity_pipeline"

    def __init__(self, entity_extraction_pipeline: Any) -> None:
        self._pipeline = entity_extraction_pipeline

    def identity(self, context: ProducerContext) -> ProducerIdentity:
        """The learned NER layer when it loaded, else the rule layers alone."""
        registry = getattr(self._pipeline, "registry", None)
        configure = getattr(self._pipeline, "configuration_identity", None)
        configuration = configure(context.settings or {}) if callable(configure) else ""
        for extractor in registry.all() if registry else []:
            identity = extractor_identity(extractor)
            if identity.model_id:
                return replace(identity, configuration_digest=configuration) if configuration else identity
        return replace(
            deterministic("domain_pattern", context.producer_version),
            fallback_reason="no NER model loaded; rules only", configuration_digest=configuration,
        )

    def run(self, context: ProducerContext) -> ProducerResult:
        if not self._pipeline:
            return ProducerResult()
        ids = self._pipeline.run(context.source, cancel_check=context.cancel_check, settings=context.settings)
        context.raise_if_cancelled()
        return ProducerResult(output_ids=list(ids or []), identity=self.identity(context))
