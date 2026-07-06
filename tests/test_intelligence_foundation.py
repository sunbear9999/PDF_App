import json
import sqlite3
from unittest.mock import MagicMock

from core.db.intelligence_db import IntelligenceDB
from core.engine.action_model import ActionStep, AIActionBlueprint
from core.engine.master_runner import MasterActionRunner
from core.engine.steps.intelligence_steps import ArtifactRetrieveStep, BuildRetrievalPlanStep, PackArtifactContextStep
from core.extraction.deterministic import ExtractedEntityGroup
from core.models.intelligence_models import ArtifactEdgeRecord, ArtifactRecord, RetrievalPlan
from core.models.extraction_models import ExtractedEntity, SourceLocation
from core.services.content.deterministic_extractor_service import DeterministicExtractorService
from core.services.ai.ai_setup_service import AISetupService
from core.services.intelligence.artifact_service import ArtifactService
from core.services.intelligence.context_packing_service import ContextPackingService
from core.services.intelligence.hybrid_retrieval_service import HybridRetrievalService
from core.services.intelligence.intelligence_scheduler import IntelligenceScheduler
from core.services.intelligence.model_role_service import ModelRoleService
from core.services.intelligence.processing_policy_service import ProcessingPolicyService


class DummyPM:
    def __init__(self):
        self._conn = sqlite3.connect(":memory:")
        self.db_intelligence = IntelligenceDB(self)
        self.db_intelligence.init_tables()
        self._metadata = {}
        self._sources = [{
            "id": "source:one",
            "path": "/tmp/source.pdf",
            "source_type": "pdf",
            "title": "source.pdf",
        }]
        self._highlights = {
            "h1": {
                "id": "h1",
                "doc_id": "/tmp/source.pdf",
                "page_num": 2,
                "rect_coords": "[1,2,3,4]",
                "text_content": "George W. Bush is mentioned here.",
                "note_content": "note",
                "color": "#ffee00",
            }
        }
        self._transcripts = {
            "source:one": {
                "model": "small",
                "segments": [{"index": 0, "start": 1.0, "end": 2.0, "text": "hello world"}],
            }
        }

    def get_metadata(self, key, default=None):
        return self._metadata.get(key, default)

    def set_metadata(self, key, value):
        self._metadata[key] = value

    def list_sources(self, source_type=None, include_removed=False):
        return list(self._sources)

    def get_source_record_by_path(self, path):
        return next((s for s in self._sources if s["path"] == path), None)

    def get_source_type(self, path):
        source = self.get_source_record_by_path(path)
        return source["source_type"] if source else "pdf"

    def get_highlight(self, highlight_id):
        return self._highlights.get(highlight_id)

    def get_highlights(self):
        return dict(self._highlights)

    def get_video_transcript(self, source_id):
        return self._transcripts.get(source_id, {})


def test_intelligence_db_idempotent_artifacts_and_edges():
    pm = DummyPM()
    artifact = ArtifactRecord(
        artifact_id=ArtifactRecord.stable_id("source:one", "block", "extraction_blocks", "b1"),
        source_id="source:one",
        artifact_type="block",
        storage_ref_type="extraction_blocks",
        storage_ref_id="b1",
        exact_text="hello",
    )

    pm.db_intelligence.upsert_artifact(artifact)
    pm.db_intelligence.upsert_artifact(artifact)
    rows = pm.db_intelligence.list_artifacts("source:one")
    assert len(rows) == 1

    edge = ArtifactEdgeRecord(
        edge_id=ArtifactEdgeRecord.stable_id(artifact.artifact_id, "artifact:source", "located_in"),
        source_artifact_id=artifact.artifact_id,
        target_artifact_id="artifact:source",
        edge_type="located_in",
    )
    pm.db_intelligence.upsert_edge(edge)
    pm.db_intelligence.upsert_edge(edge)
    assert len(pm.db_intelligence.list_edges(artifact.artifact_id)) == 1


def test_intelligence_db_stage_status_and_fts_search():
    pm = DummyPM()
    artifact = ArtifactRecord(
        artifact_id=ArtifactRecord.stable_id("source:one", "annotation", "highlights", "h2"),
        source_id="source:one",
        artifact_type="annotation",
        storage_ref_type="highlights",
        storage_ref_id="h2",
        exact_text="Artifact search finds a durable stage ledger.",
        metadata={"stage": "entities"},
    )
    pm.db_intelligence.upsert_artifact(artifact)
    pm.db_intelligence.upsert_stage_status("source:one", "entities", {
        "status": "complete",
        "model_role": "fast_worker_model",
        "model_name": "worker",
    })

    rows = pm.db_intelligence.search_artifacts("durable ledger", limit=5)
    assert rows and rows[0]["artifact_id"] == artifact.artifact_id
    statuses = pm.db_intelligence.get_stage_statuses("source:one")
    assert statuses["entities"]["status"] == "complete"
    assert statuses["entities"]["model_role"] == "fast_worker_model"


def test_artifact_service_references_highlights_and_transcripts_without_duplication():
    pm = DummyPM()
    service = ArtifactService(project_manager=pm)

    annotation_id = service.sync_annotation_artifact("h1")
    transcript_ids = service.sync_transcript_artifacts("source:one")

    annotation = pm.db_intelligence.get_artifact(annotation_id)
    assert annotation["storage_ref_type"] == "highlights"
    assert annotation["storage_ref_id"] == "h1"
    assert "George W. Bush" in annotation["exact_text"]
    assert transcript_ids
    assert pm.db_intelligence.get_artifact(transcript_ids[0])["storage_ref_type"] == "video_transcripts"


def test_annotation_artifact_includes_nearest_section_path_when_blocks_exist():
    class Block:
        block_id = "b1"
        page_num = 2
        exact_text = "George W. Bush is mentioned here."
        section_path = ["Chapter 1", "Presidency"]

    class DES:
        def get_blocks(self, _doc_id):
            return [Block()]

    pm = DummyPM()
    service = ArtifactService(project_manager=pm, document_extraction_service=DES())
    artifact_id = service.sync_annotation_artifact("h1")
    artifact = pm.db_intelligence.get_artifact(artifact_id)
    assert artifact["section_path"] == ["Chapter 1", "Presidency"]
    assert artifact["locator"]["block_id"] == "b1"


def test_model_role_service_legacy_fallback_and_embedding_staleness():
    pm = DummyPM()
    pm.db_intelligence.upsert_artifact(ArtifactRecord(
        artifact_id="artifact:chunk",
        source_id="source:one",
        artifact_type="chunk",
        storage_ref_type="chroma",
        storage_ref_id="c1",
        model_role="embedding_model",
    ))
    ai_setup = MagicMock()
    ai_setup.get_default_chat_model.return_value = "chat-model"
    ai_setup.get_default_embedding_model.return_value = "embed-a"
    svc = ModelRoleService(project_manager=pm, ai_setup_service=ai_setup)

    assert svc.resolve_role("main_answer_model") == "chat-model"
    assert svc.resolve_role("embedding_model") == "embed-a"
    svc.set_role("embedding_model", "embed-b")
    assert pm.db_intelligence.get_artifact("artifact:chunk")["status"] == "stale"


def test_model_role_service_scales_recommendations_and_context_to_low_hardware():
    pm = DummyPM()
    ai_setup = MagicMock()
    ai_setup.get_default_chat_model.return_value = "gemma4:e2b"
    ai_setup.get_default_embedding_model.return_value = "nomic-embed-text"
    ai_setup.get_hardware_info.return_value = {"ram_gb": 7.1, "vram_gb": 0, "gpu_name": "Integrated"}
    svc = ModelRoleService(project_manager=pm, ai_setup_service=ai_setup)

    assert svc.hardware_tier() == "low"
    assert svc.recommendations_for_role("main_answer_model")[0] == "gemma4:e2b"
    assert "lfm2:1.2b" in svc.recommendations_for_role("fast_worker_model")
    assert svc.max_context_tokens_for_model("gemma4:e2b", role="main_answer_model") <= 8192


def test_model_role_service_reads_global_role_defaults_when_project_metadata_empty():
    pm = DummyPM()
    ai_setup = MagicMock()
    ai_setup.get_model_roles.return_value = {
        "main_answer_model": "gemma4:e2b",
        "fast_worker_model": "lfm2:1.2b",
    }
    ai_setup.get_default_chat_model.return_value = "wrong-model"
    svc = ModelRoleService(project_manager=pm, ai_setup_service=ai_setup)

    assert svc.resolve_role("main_answer_model") == "gemma4:e2b"
    assert svc.resolve_role("fast_worker_model") == "lfm2:1.2b"


def test_ai_setup_model_roles_include_legacy_defaults_and_save_backwards_compatibly():
    svc = AISetupService.__new__(AISetupService)
    svc._settings = {
        "default_chat_model": "legacy-chat",
        "default_embedding_model": "legacy-embed",
        "model_roles": {"fast_worker_model": "tiny-worker"},
    }
    saved = {}
    svc._save_settings = lambda: saved.update(svc._settings)

    roles = svc.get_model_roles()

    assert roles["main_answer_model"] == "legacy-chat"
    assert roles["embedding_model"] == "legacy-embed"
    assert roles["fast_worker_model"] == "tiny-worker"

    svc.save_model_roles({
        "main_answer_model": "gemma4:e2b",
        "embedding_model": "nomic-embed-text",
    })

    assert saved["default_chat_model"] == "gemma4:e2b"
    assert saved["default_embedding_model"] == "nomic-embed-text"


def test_unset_helper_role_prefers_installed_tiny_recommendation_before_main_model():
    pm = DummyPM()
    ai_setup = MagicMock()
    ai_setup.get_model_roles.return_value = {"main_answer_model": "gemma4:e2b"}
    ai_setup.get_default_chat_model.return_value = "gemma4:e2b"
    ai_setup.get_hardware_info.return_value = {"ram_gb": 7.1, "vram_gb": 0}
    llm = MagicMock()
    llm.get_available_models.return_value = ["gemma4:e2b", "lfm2:1.2b"]
    svc = ModelRoleService(project_manager=pm, ai_setup_service=ai_setup, llm_manager=llm)

    assert svc.resolve_role("fast_worker_model") == "lfm2:1.2b"


def test_processing_policy_respects_modes_and_audio_transcription():
    pm = DummyPM()
    svc = ProcessingPolicyService(project_manager=pm)
    pdf_source = pm.list_sources()[0]
    audio_source = {"id": "source:audio", "path": "/tmp/a.mp3", "source_type": "audio"}

    stages = svc.stages_for_source(pdf_source)
    assert "parsed_blocks" in stages
    assert "entities" in stages

    svc.set_project_settings({"auto_transcribe": "never"})
    assert "transcript" not in svc.stages_for_source(audio_source)
    svc.set_project_settings({"auto_transcribe": "always"})
    assert "transcript" in svc.stages_for_source(audio_source)

    svc.set_source_processing_mode("source:one", "manual")
    assert svc.stages_for_source(pdf_source) == []


def test_scheduler_enqueues_process_registry_metadata():
    pm = DummyPM()
    captured = {}

    class Registry:
        def enqueue_runner(self, runner, job_name, job_type="Agent", is_express=False, metadata=None):
            job = MagicMock()
            job.id = "job1"
            job.source_id = metadata["source_id"]
            job.stage = metadata["stage"]
            runner.job = job
            captured.update(metadata)
            return job

    scheduler = IntelligenceScheduler(
        process_registry=Registry(),
        project_manager=pm,
        artifact_service=ArtifactService(project_manager=pm),
        policy_service=ProcessingPolicyService(project_manager=pm),
    )
    scheduler.enqueue_stage("source:one", "parsed_blocks")
    assert captured["source_id"] == "source:one"
    assert captured["stage"] == "parsed_blocks"
    assert captured["job_type"] if "job_type" in captured else True


def test_hybrid_retrieval_and_context_packing_prioritize_artifacts():
    pm = DummyPM()
    artifact_service = ArtifactService(project_manager=pm)
    artifact_service.sync_annotation_artifact("h1")
    retrieval = HybridRetrievalService(project_manager=pm, artifact_service=artifact_service)

    results = retrieval.retrieve(RetrievalPlan(query="Bush", max_results=5))
    assert results
    assert results[0].artifact_type == "annotation"
    bundle = ContextPackingService(max_chars=2000).pack("Bush", results)
    assert "ARTIFACT:" in bundle.text
    assert "George W. Bush" in bundle.text


def test_runner_resolves_explicit_model_roles_and_traces_role():
    role_svc = MagicMock()
    role_svc.resolve_role.side_effect = lambda role: {
        "fast_worker_model": "worker-model",
        "main_answer_model": "main-model",
        "vision_model": "vision-model",
    }.get(role, "")
    runner = MasterActionRunner(
        AIActionBlueprint("bp", "desc", steps=[]),
        {"selected_model": "main-model"},
        model_role_service=role_svc,
    )

    step = ActionStep("s1", "LLM_QUERY", model_role="fast_worker_model")
    model, role = runner._resolve_step_model(step, "{selected_model}")
    assert model == "worker-model"
    assert role == "fast_worker_model"

    schema_step = ActionStep("s2", "LLM_SCHEMA_QUERY")
    model, role = runner._resolve_step_model(schema_step, "{selected_model}")
    assert model == "worker-model"
    assert role == "fast_worker_model"


def test_intelligence_workflow_steps_plan_retrieve_and_pack():
    pm = DummyPM()
    artifact_service = ArtifactService(project_manager=pm)
    artifact_service.sync_annotation_artifact("h1")
    retrieval = HybridRetrievalService(project_manager=pm, artifact_service=artifact_service)
    packer = ContextPackingService(max_chars=2000)
    ctx = MagicMock()
    ctx.hybrid_retrieval_service = retrieval
    ctx.context_packing_service = packer

    plan_json = BuildRetrievalPlanStep().execute(ctx, {"query": "Bush", "max_results": 3}).raw_value
    results_json = ArtifactRetrieveStep().execute(ctx, {"plan": plan_json}).raw_value
    packed = PackArtifactContextStep().execute(ctx, {"query": "Bush", "results": results_json}).raw_value

    assert "main_answer_model" not in plan_json
    assert "George W. Bush" in packed


def test_discovery_reuses_cached_shared_entities_without_worker():
    class Block:
        block_id = "b1"
        exact_text = "George W. Bush appears in this paragraph."

    class DES:
        def get_cached_entities(self, source_path):
            return [ExtractedEntity(
                entity_id="e1",
                doc_id=source_path,
                entity_type="entity.person_org",
                canonical_text="George W. Bush",
                normalized_text="george w bush",
                mentions=[SourceLocation(doc_id=source_path, page_num=2, block_id="b1")],
            )]

        def get_blocks(self, source_path):
            return [Block()]

    svc = DeterministicExtractorService(MagicMock())
    svc.set_document_extraction_service(DES())
    groups = svc._groups_from_shared_extraction("/tmp/source.pdf", "entity.person_org")
    assert groups
    assert isinstance(groups[0], ExtractedEntityGroup)
    assert groups[0].extra_properties["shared_extraction_cache"] is True
    assert groups[0].mentions[0].context.startswith("George W. Bush")
