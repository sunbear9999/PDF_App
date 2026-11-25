"""Register the core services that hold significant memory with the manager.

Release order follows ``priority`` (lower first): caches that rebuild cheaply
go first, then local models that reload on demand. Models used within the
last few seconds report ``busy`` and are never released mid-use.
"""
from __future__ import annotations

from core.models.memory_models import ConsumerKind, MemoryConsumer

CACHE_PRIORITY = 10
NEVER_RELEASE = -1      # shown in the panel, never released (automatically or manually)
LOCAL_MODEL_PRIORITY = 40


def register_core_consumers(memory, core) -> None:
    register = memory.register_consumer
    cache = getattr(core, "extraction_cache", None)
    if cache is not None:
        register(MemoryConsumer(
            id="extraction_cache", label="Extraction cache (blocks, sentences)",
            kind=ConsumerKind.CACHE.value, usage=cache.memory_estimate,
            release=cache.clear_memory, priority=CACHE_PRIORITY))
    pm = getattr(core, "project_manager", None)
    if pm is not None:
        # Report-only: the viewer renders from these fitz handles, and closing
        # one it still draws from is a native MuPDF crash. ProjectManager's own
        # LRU (5 documents) already bounds them.
        register(MemoryConsumer(
            id="pdf_documents", label="Open PDF documents",
            kind=ConsumerKind.APP.value, usage=pm.open_docs_bytes,
            release=lambda _level: 0, priority=NEVER_RELEASE))
    language = getattr(core, "language_feature_service", None)
    if language is not None:
        register(MemoryConsumer(
            id="spacy", label="spaCy language model", kind=ConsumerKind.MODEL.value,
            usage=language.memory_bytes, release=language.release, busy=language.busy,
            idle_seconds=language.idle_seconds, priority=LOCAL_MODEL_PRIORITY))
    for consumer_id, label, service in (
        ("onnx:reranker", "Cross-encoder reranker", getattr(core, "cross_encoder_reranker", None)),
        ("onnx:role_enhancer", "Sentence-role enhancer (NLI)", getattr(core, "_role_enhancer_scorer", None)),
        ("onnx:nli_relations", "NLI relation scorer", getattr(core, "nli_relation_scorer", None)),
        ("onnx:sentence_classifier", "Custom sentence-role model",
         getattr(core, "sentence_classifier_service", None)),
        ("onnx:gliner", "Multilingual entity recogniser (GLiNER)", getattr(core, "gliner_runtime", None)),
        ("onnx:sentence_encoder", "Sentence encoder", getattr(core, "sentence_encoder", None)),
    ):
        runtime = getattr(service, "runtime", None)
        if runtime is not None:
            register(MemoryConsumer(
                id=consumer_id, label=label, kind=ConsumerKind.MODEL.value,
                usage=runtime.memory_bytes, release=runtime.release, busy=runtime.busy,
                idle_seconds=runtime.idle_seconds, priority=LOCAL_MODEL_PRIORITY))
    for consumer_id, label, runtime in (
        ("onnx:language_id", "Language identifier (GlotLID)", getattr(core, "language_id_runtime", None)),
        ("gazetteer", "Place-name gazetteer", getattr(core, "gazetteer", None)),
    ):
        if runtime is not None:
            register(MemoryConsumer(
                id=consumer_id, label=label, kind=ConsumerKind.MODEL.value,
                usage=runtime.memory_bytes, release=runtime.release, busy=runtime.busy,
                idle_seconds=runtime.idle_seconds, priority=LOCAL_MODEL_PRIORITY))
