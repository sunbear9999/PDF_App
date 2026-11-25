"""Composition of the optional Entities resources installed through AI Setup.

Creates the lazy runtimes (GLiNER, GlotLID, GeoNames gazetteer, sentence
encoder), registers them for hot reload after install/remove, and fills the
evidence-source registry the Entities pipeline consults. Nothing loads until a
project switches a resource on and the stage runs.
"""
from __future__ import annotations

from types import SimpleNamespace

from core.services.extraction.glotlid_runtime import GlotLidRuntime
from core.services.intelligence.entities.evidence_sources import EntityEvidenceRegistry
from core.services.intelligence.entities.gazetteer_source import GazetteerEvidenceSource
from core.services.intelligence.entities.gliner_source import GlinerEvidenceSource, default_labels_json
from core.services.intelligence.gazetteer import Gazetteer
from core.services.intelligence.gliner_runtime import GlinerRuntime
from core.services.intelligence.sentence_encoder import SentenceEncoder

# Project settings that switch each optional resource on.
RESOURCE_SETTINGS = {"gliner": "use_gliner", "gazetteer": "use_gazetteer", "language_model": "use_language_model"}


def build_entity_evidence(models, pattern_registry) -> SimpleNamespace:
    """Runtimes and the evidence registry for a ModelDependencyService's model folder."""
    built = SimpleNamespace(
        gliner=GlinerRuntime(models.model_dir("gliner_multi")),
        language_id=GlotLidRuntime(models.model_dir("language_id")),
        gazetteer=Gazetteer(models.model_dir("geonames_gazetteer")),
        encoder=SentenceEncoder(models.model_dir("sentence_encoder")),
        registry=EntityEvidenceRegistry(),
    )
    built.registry.register(GazetteerEvidenceSource(
        built.gazetteer, lambda: models.model_digest("geonames_gazetteer"), pattern_registry))
    built.registry.register(GlinerEvidenceSource(built.gliner, lambda: models.model_digest("gliner_multi")))
    return built


def compose_entity_evidence(core) -> None:
    models = core.model_dependency_service
    built = build_entity_evidence(models, core.pattern_detector_registry)
    core.gliner_runtime, core.language_id_runtime = built.gliner, built.language_id
    core.gazetteer, core.sentence_encoder = built.gazetteer, built.encoder
    core.entity_evidence_registry = built.registry
    for dep_id, runtime in (("gliner_multi", built.gliner), ("language_id", built.language_id),
                            ("geonames_gazetteer", built.gazetteer), ("sentence_encoder", built.encoder)):
        models.register_reload_callback(dep_id, runtime.reload)
    models.register_extra_file_provider("gliner_entity_labels", default_labels_json)
