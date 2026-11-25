"""Register the built-in producers under the handler IDs their StageSpecs use."""
from __future__ import annotations

from typing import Any

from core.models.intelligence_models import ProcessingStage
from core.services.intelligence.producers.claims_producer import ClaimsProducer
from core.services.intelligence.producers.dates_producer import DatesProducer
from core.services.intelligence.producers.entities_producer import EntitiesProducer
from core.services.intelligence.producers.entity_linking_producer import EntityLinkingProducer
from core.services.intelligence.producers.cross_refs_producer import CrossReferencesProducer
from core.services.intelligence.producers.abbreviations_producer import AbbreviationsProducer
from core.services.intelligence.producers.extraction_producers import (
    EmbeddingProducer, ExtractionProducer, FullTextIndexProducer,
    SourceRegisteredProducer, TranscriptIndexProducer,
)
from core.services.intelligence.producers.graph_seeds_producer import GraphSeedsProducer
from core.services.intelligence.producers.summaries_producer import SummariesProducer
from core.services.intelligence.producers.tables_producer import TablesProducer


def register_builtin_producers(registry: Any, *, project_manager: Any = None,
                               artifact_service: Any = None,
                               document_extraction_service: Any = None,
                               date_extraction_service: Any = None,
                               entity_extraction_pipeline: Any = None,
                               sentence_classifier: Any = None,
                               extractive_summarizer: Any = None,
                               artifact_embedding_service: Any = None,
                               llm_manager: Any = None,
                               model_role_service: Any = None,
                               nli_relation_scorer: Any = None,
                               source_format_registry: Any = None,
                               data_provider_registry: Any = None,
                               pattern_registry: Any = None) -> None:
    des = document_extraction_service
    producers = {
        "source_registered": SourceRegisteredProducer(artifact_service),
        "transcript": TranscriptIndexProducer(project_manager, artifact_service),
        "full_text_index": FullTextIndexProducer(project_manager),
        "entities": EntitiesProducer(entity_extraction_pipeline),
        "entity_linking": EntityLinkingProducer(artifact_service),
        "cross_references": CrossReferencesProducer(des, artifact_service, pattern_registry),
        "abbreviations": AbbreviationsProducer(des, artifact_service, pattern_registry),
        "dates": DatesProducer(des, artifact_service, date_extraction_service),
        "claims": ClaimsProducer(project_manager, artifact_service, sentence_classifier),
        "summaries": SummariesProducer(project_manager, artifact_service, extractive_summarizer),
        "graph_seeds": GraphSeedsProducer(project_manager, artifact_service, nli_relation_scorer),
        "embeddings": EmbeddingProducer(
            llm_manager, artifact_embedding_service, model_role_service
        ),
    }
    producers["tables"] = TablesProducer(
        des, artifact_service, source_format_registry, data_provider_registry,
    )
    for stage in (ProcessingStage.PARSED_BLOCKS, ProcessingStage.SECTIONS,
                  ProcessingStage.CITATIONS):
        producers[stage.value] = ExtractionProducer(stage.value, des, artifact_service)
    for handler_id, producer in producers.items():
        registry.register(handler_id, producer)
