# core/papyrus_core.py
import json
import os
import sys
from core.events.event_bus import EventBus
from core.engine.process_manager import ProcessRegistry
from core.project_manager import ProjectManager
from core.llm_manager import LocalLLMManager
from core.prompt_manager import PromptManager
from core.dictionary_manager import DictionaryManager
from core.citation_manager import CitationManager
from core.engine.step_manager import StepManager
from core.engine.blueprint_manager import BlueprintManager

from core.engine.registries import build_default_blueprint_registry, build_default_workflow_node_type_registry
from core.services.workspace.registries import build_default_workspace_ai_tool_registry, build_default_workspace_node_type_registry
from core.ontology.registry import OntologyRegistry

from core.services.project.embedding_service import EmbeddingService
from core.services.project.project_app_service import ProjectAppService
from core.services.project.network_access_service import NetworkAccessService
from core.services.document.document_app_service import DocumentAppService
from core.services.document.source_import_service import SourceImportService
from core.services.document.document_ingestion_service import DocumentIngestionService
from core.services.document.ocr_app_service import OCRAppService
from core.registries.memory_consumer_registry import MemoryConsumerRegistry
from core.services.system.memory_manager_service import MemoryManagerService
from core.services.system.memory_consumers import register_core_consumers
from core.services.extraction.language_feature_service import set_shared_language_service
from core.services.content.notes_app_service import NotesAppService
from core.services.content.tag_app_service import TagAppService
from core.services.content.essay_app_service import EssayAppService
from core.services.reference.citation_app_service import CitationAppService
from core.services.reference.dictionary_app_service import DictionaryAppService
from core.services.knowledge.ontology_service import OntologyService
from core.services.knowledge.index_app_service import IndexAppService
from core.services.workspace.workspace_service import WorkspaceService
from core.services.workspace.workspace_graph_service import WorkspaceGraphService
from core.services.workspace.workspace_layout_service import WorkspaceLayoutService
from core.services.ai.workflow_runner_service import WorkflowRunnerService
from core.services.ai.research_agent_service import ResearchAgentService
from core.services.ai.prompt_app_service import PromptAppService
from core.services.ai.llm_settings_service import LLMSettingsService
from core.services.ai.tts_app_service import TTSAppService
from core.plugins.plugin_loader import load_plugins
from core.plugins.extension_registry import PluginExtensionRegistry
from core.plugins.pack_contributor import PackContributorRegistry
from core.services.packs.pack_service import PackService
from core.registries.data_provider_registry import DataProviderRegistry
from core.registries.llm_backend_registry import LLMBackendRegistry
from core.registries.source_format_registry import SourceFormatRegistry, SourceFormatSpec
from core.backends.ollama_backend import make_ollama_backend_spec
from core.backends.llamacpp_backend import make_llamacpp_backend_spec
from core.services.data.data_dock_service import DataDockService
from core.services.document.video_transcription_service import VideoTranscriptionService
from core.services.content.deterministic_extractor_service import DeterministicExtractorService
from core.services.document.source_evaluation_service import SourceEvaluationService
from core.services.ai.ai_setup_service import AISetupService
from core.services.project.media_asset_service import MediaAssetService
from core.services.data.pdf_data_selection_service import PdfDataSelectionService
from core.registries.ai_widget_registry import AIWidgetRegistry
from core.registries.ai_widget_definitions import register_builtin_widgets
from core.registries.pattern_detector_registry import PatternDetectorRegistry
from core.services.extraction.extraction_cache import ExtractionCache
from core.services.extraction.document_extraction_service import DocumentExtractionService
from core.services.extraction.pdf_layout_parser import PDFLayoutParser, DocumentParserRegistry
from core.services.extraction.semantic_block_service import SemanticBlockService
from core.services.extraction.section_structure_extractor import SectionStructureExtractor
from core.services.extraction.language_feature_service import LanguageFeatureService
from core.services.extraction.sentence_segmentation_service import SentenceSegmentationService
from core.services.extraction.entity_extraction_service import EntityExtractionService
from core.services.extraction.date_extraction_service import DateExtractionService
from core.services.extraction.bibliography_extraction_service import BibliographyExtractionService
from core.services.extraction.citation_extraction_service import CitationExtractionService
from core.services.extraction.citation_matcher import CitationMatcher
from core.services.extraction.table_extraction_service import TableExtractionService
from core.services.extraction.ranking_feature_service import RankingFeatureService
from core.registries.deterministic_pass_registry import DeterministicPassRegistry
from core.services.intelligence.artifact_service import ArtifactService
from core.services.intelligence.pdf_anchor_geometry import PdfAnchorGeometry
from core.services.intelligence.source_anchor_resolver import SourceAnchorResolver
from core.registries.intelligence_producer_registry import IntelligenceProducerRegistry
from core.services.intelligence.producers.builtin import register_builtin_producers
from core.services.intelligence.processing_plan_service import ProcessingPlanService
from core.services.intelligence.context_packing_service import ContextPackingService
from core.services.intelligence.hybrid_retrieval_service import HybridRetrievalService
from core.services.intelligence.intelligence_scheduler import IntelligenceScheduler
from core.services.intelligence.model_role_service import ModelRoleService
from core.services.intelligence.processing_policy_service import ProcessingPolicyService
from core.services.intelligence.source_intelligence_service import SourceIntelligenceService

def get_resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    return os.path.join(root_dir, relative_path)

class PapyrusCore:
    """The headless engine of the application. Plugins interact with this, not the GUI."""
    def __init__(self, user_data_dir: str):
        self.user_data_dir = user_data_dir
        self.bus = EventBus.get_instance()
        self.process_registry = ProcessRegistry()
        self.network_access_service = NetworkAccessService(user_data_dir)
        
        # 1. Base Managers
        # Source format dispatch — instantiated before ProjectManager so
        # detect_source_type() can consult it from the very first call.
        # Built-in pdf/video specs are registered here; document/data/audio/
        # image parsers are registered further down once their registries
        # (document_parser_registry, data_provider_registry) exist.
        self.source_format_registry = SourceFormatRegistry()
        self.source_format_registry.register(SourceFormatSpec(
            source_type="pdf", label="PDF Document", category="document",
            extensions=[".pdf"], mime_types=["application/pdf"],
            parser_provider_id="pymupdf_layout", icon="📄", priority=10,
        ))
        self.source_format_registry.register(SourceFormatSpec(
            source_type="video", label="Video", category="video",
            extensions=[
                ".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v",
                ".wmv", ".flv", ".mpg", ".mpeg", ".3gp", ".ogv", ".ts",
            ],
            icon="🎬", priority=10,
        ))
        self.source_format_registry.register(SourceFormatSpec(
            source_type="audio", label="Audio", category="audio",
            extensions=[".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac", ".wma", ".opus"],
            icon="🎧", priority=10,
        ))

        self.project_manager = ProjectManager()
        self.project_manager.set_source_format_registry(self.source_format_registry)
        self.prompt_manager = PromptManager()
        self.step_manager = StepManager()
        
        self.llm_manager = LocalLLMManager()
        self.llm_manager.set_audit_logger(self.project_manager.log_ai_interaction_threadsafe)
        # Provider-level instructions (e.g. "JSON Mode Instruction") honour user
        # edits made in the Prompt Editor instead of shipped defaults.
        self.llm_manager.prompt_resolver = self.prompt_manager.get_prompt
        # One owner for memory: model preload/unload, reserves, caches (rules §1.3).
        self.memory_consumer_registry = MemoryConsumerRegistry()
        self.memory_manager_service = MemoryManagerService(
            user_data_dir=user_data_dir, bus=self.bus, llm_manager=self.llm_manager,
            registry=self.memory_consumer_registry,
        )
        self.memory_manager_service.start()
        # Phase 7: will be set after document_extraction_service is created

        # Load any prompt files shipped with the app under assets/prompts/
        _assets_prompts = get_resource_path(os.path.join("assets", "prompts"))
        if os.path.isdir(_assets_prompts):
            self.prompt_manager.load_assets_dir(_assets_prompts, category="Asset Prompts")

        # Widget registry — must be initialised after PromptManager so that
        # register_builtin_widgets() can seed new default prompt entries.
        self.widget_registry = AIWidgetRegistry.get_instance()
        register_builtin_widgets(self.widget_registry, self.prompt_manager)

        self.dictionary_manager = DictionaryManager(user_data_dir)
        self._ensure_default_dictionary()
        self.citation_manager = CitationManager(self.project_manager)

        # 2. Registries
        self.blueprint_registry = build_default_blueprint_registry()
        self.blueprint_manager = BlueprintManager(self.blueprint_registry)
        self.workflow_node_type_registry = build_default_workflow_node_type_registry()
        self.workspace_ai_tools_registry = build_default_workspace_ai_tool_registry()
        self.workspace_node_type_registry = build_default_workspace_node_type_registry()
        self.ontology_registry = OntologyRegistry()
        self.data_provider_registry = DataProviderRegistry()
        self.pattern_detector_registry = PatternDetectorRegistry()
        self.pattern_detector_registry.seed_builtin_packs()

        # Deterministic extraction layer — Phase 0: core skeleton
        self.extraction_cache = ExtractionCache(self.project_manager)
        self.document_extraction_service = DocumentExtractionService(
            project_manager=self.project_manager,
            bus=self.bus,
            extraction_cache=self.extraction_cache,
        )

        # Phase 1: shared document structure
        self.document_parser_registry = DocumentParserRegistry()
        _layout_parser = PDFLayoutParser()
        _semantic_block_svc = SemanticBlockService(self.pattern_detector_registry)
        _section_extractor = SectionStructureExtractor(_semantic_block_svc)
        self.document_extraction_service.set_layout_parser(_layout_parser)
        self.document_extraction_service.set_semantic_block_service(_semantic_block_svc)
        self.document_extraction_service.set_section_structure_extractor(_section_extractor)
        self.document_extraction_service.set_parser_registry(self.document_parser_registry)
        self.document_extraction_service.set_source_format_registry(self.source_format_registry)

        # New text-format parsers — each registers a DocumentParserRegistry
        # provider plus a matching SourceFormatSpec. is_available() lets a
        # format declare itself unavailable (e.g. missing LibreOffice for
        # .doc) without silently failing.
        from core.services.extraction.formats.docx_parser import DocxParser
        from core.services.extraction.formats.epub_parser import EpubParser
        from core.services.extraction.formats.html_parser import HtmlParser
        from core.services.extraction.formats.legacy_doc_parser import LegacyDocParser
        from core.services.extraction.formats.plaintext_parser import PlainTextParser
        from core.services.extraction.formats.rtf_parser import RtfParser

        _plaintext_parser = PlainTextParser()
        self.document_parser_registry.register(DocxParser.provider_id, DocxParser().parse)
        self.document_parser_registry.register(EpubParser.provider_id, EpubParser().parse)
        self.document_parser_registry.register(_plaintext_parser.provider_id, _plaintext_parser.parse)
        self.document_parser_registry.register(HtmlParser.provider_id, HtmlParser().parse)
        self.document_parser_registry.register(RtfParser.provider_id, RtfParser().parse)
        self.document_parser_registry.register(LegacyDocParser.provider_id, LegacyDocParser().parse)

        self.source_format_registry.register(SourceFormatSpec(
            source_type="docx", label="Word Document", category="document",
            extensions=[".docx"],
            mime_types=["application/vnd.openxmlformats-officedocument.wordprocessingml.document"],
            parser_provider_id=DocxParser.provider_id, is_available=DocxParser.is_available,
            icon="📝",
        ))
        self.source_format_registry.register(SourceFormatSpec(
            source_type="epub", label="EPUB Book", category="document",
            extensions=[".epub"], mime_types=["application/epub+zip"],
            parser_provider_id=EpubParser.provider_id, is_available=EpubParser.is_available,
            icon="📚",
        ))
        self.source_format_registry.register(SourceFormatSpec(
            source_type="txt", label="Text File", category="document",
            extensions=[".txt"], mime_types=["text/plain"],
            parser_provider_id=_plaintext_parser.provider_id, icon="📄",
        ))
        self.source_format_registry.register(SourceFormatSpec(
            source_type="md", label="Markdown", category="document",
            extensions=[".md", ".markdown"], mime_types=["text/markdown"],
            parser_provider_id=_plaintext_parser.provider_id, icon="📄",
        ))
        self.source_format_registry.register(SourceFormatSpec(
            source_type="html", label="HTML Document", category="document",
            extensions=[".html", ".htm"], mime_types=["text/html"],
            parser_provider_id=HtmlParser.provider_id, is_available=HtmlParser.is_available,
            icon="🌐",
        ))
        self.source_format_registry.register(SourceFormatSpec(
            source_type="rtf", label="Rich Text Document", category="document",
            extensions=[".rtf"], mime_types=["application/rtf"],
            parser_provider_id=RtfParser.provider_id, is_available=RtfParser.is_available,
            icon="📄",
        ))
        self.source_format_registry.register(SourceFormatSpec(
            source_type="doc", label="Legacy Word Document (.doc)", category="document",
            extensions=[".doc"], mime_types=["application/msword"],
            parser_provider_id=LegacyDocParser.provider_id, is_available=LegacyDocParser.is_available,
            unavailable_reason=LegacyDocParser.unavailable_reason, icon="📄",
        ))

        # CSV/TSV/XLSX — data formats. Loaded via DataProviderRegistry's
        # DataSourceLoaderSpec (distinct from the PDF-region-specific
        # GridExtractorSpec) rather than DocumentParserRegistry, since these
        # produce a DataGridState, not a List[DocumentBlock]. category="data"
        # keeps them out of DocumentIngestionService's entity/citation
        # pipeline (see DocumentIngestionService._accepts_ingestion).
        from core.registries.data_provider_registry import DataSourceLoaderSpec
        from core.services.data.csv_loader_service import CsvLoaderService
        from core.services.data.xlsx_loader_service import XlsxLoaderService

        _csv_loader = CsvLoaderService()
        _xlsx_loader = XlsxLoaderService()
        self.data_provider_registry.register_data_source_loader(DataSourceLoaderSpec(
            loader_id=_csv_loader.loader_id, label="CSV/TSV", extensions=[".csv", ".tsv"],
            callback=_csv_loader.load, is_available=CsvLoaderService.is_available,
        ))
        self.data_provider_registry.register_data_source_loader(DataSourceLoaderSpec(
            loader_id=_xlsx_loader.loader_id, label="Excel Workbook", extensions=[".xlsx"],
            callback=_xlsx_loader.load, is_available=XlsxLoaderService.is_available,
        ))
        self.source_format_registry.register(SourceFormatSpec(
            source_type="csv", label="CSV File", category="data",
            extensions=[".csv"], mime_types=["text/csv"],
            data_loader_id=_csv_loader.loader_id, icon="📊",
        ))
        self.source_format_registry.register(SourceFormatSpec(
            source_type="tsv", label="TSV File", category="data",
            extensions=[".tsv"], mime_types=["text/tab-separated-values"],
            data_loader_id=_csv_loader.loader_id, icon="📊",
        ))
        self.source_format_registry.register(SourceFormatSpec(
            source_type="xlsx", label="Excel Workbook", category="data",
            extensions=[".xlsx"],
            mime_types=["application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"],
            data_loader_id=_xlsx_loader.loader_id, is_available=XlsxLoaderService.is_available,
            icon="📊",
        ))

        # Images — OCR-backed (Tesseract), word-level bboxes so highlights
        # work the same way PDF word-rects do. category="image" is treated
        # like "document" by DocumentIngestionService (OCR'd text is just
        # text) — see DocumentIngestionService._accepts_ingestion.
        from core.services.extraction.formats.image_ocr_parser import ImageOcrParser

        _image_ocr_parser = ImageOcrParser()
        self.document_parser_registry.register(_image_ocr_parser.provider_id, _image_ocr_parser.parse)
        self.source_format_registry.register(SourceFormatSpec(
            source_type="image", label="Image", category="image",
            extensions=[".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp"],
            mime_types=["image/png", "image/jpeg", "image/tiff", "image/bmp", "image/webp"],
            parser_provider_id=_image_ocr_parser.provider_id, is_available=ImageOcrParser.is_available,
            unavailable_reason="Image OCR requires pytesseract and the Tesseract binary installed",
            icon="🖼️",
        ))

        # Phase 2: language + entity + date services
        self.language_feature_service = LanguageFeatureService()
        self.entity_extraction_service = EntityExtractionService(
            language_feature_service=self.language_feature_service,
            pattern_registry=self.pattern_detector_registry,
        )
        self.date_extraction_service = DateExtractionService(
            pattern_registry=self.pattern_detector_registry,
        )
        self.document_extraction_service.set_entity_extraction_service(
            self.entity_extraction_service
        )
        self.document_extraction_service.set_date_extraction_service(
            self.date_extraction_service
        )
        self.sentence_segmentation_service = SentenceSegmentationService(
            language_feature_service=self.language_feature_service,
        )
        self.document_extraction_service.set_sentence_segmentation_service(
            self.sentence_segmentation_service
        )

        # Phase 3: citation services
        _bib_svc = BibliographyExtractionService(self.pattern_detector_registry)
        _citation_matcher = CitationMatcher()
        self.citation_extraction_service = CitationExtractionService(
            bibliography_service=_bib_svc,
            citation_matcher=_citation_matcher,
            pattern_registry=self.pattern_detector_registry,
        )
        self.document_extraction_service.set_citation_extraction_service(
            self.citation_extraction_service
        )

        # Plugin-contributed analysis templates (in-memory, not written to project file).
        # Plugins call api.contribute_analysis_template(template_dict) in on_load().
        # ProjectManager.get_analysis_templates() merges this list into its output.
        self._plugin_analysis_templates: list = []
        self.project_manager._plugin_analysis_templates = self._plugin_analysis_templates

        # LLM backend registry — plugins can extend this via api.llm_backends.register()
        self.llm_backend_registry = LLMBackendRegistry()
        self.llm_backend_registry.register(make_ollama_backend_spec())
        self.llm_backend_registry.register(make_llamacpp_backend_spec(user_data_dir))

        # 3. Headless App Services
        self.embedding_service = EmbeddingService(self.llm_manager, self.project_manager)
        self.project_app_service = ProjectAppService(self.project_manager, self.llm_manager)
        self.document_app_service = DocumentAppService(self.project_manager)
        self.source_import_service = SourceImportService(
            self.project_manager,
            self.document_extraction_service,
            self.bus,
        )
        self.tts_app_service = TTSAppService()
        self.ocr_app_service = OCRAppService(
            self.project_manager, memory_manager=self.memory_manager_service,
            process_registry=self.process_registry)
        self.dictionary_app_service = DictionaryAppService(self.dictionary_manager)
        self.citation_app_service = CitationAppService(self.project_manager, self.citation_manager)
        self.notes_app_service = NotesAppService(self.project_manager)
        self.tag_app_service = TagAppService(self.project_manager)
        self.prompt_app_service = PromptAppService(
            self.prompt_manager,
            blueprint_manager=self.blueprint_manager,
            blueprint_registry=self.blueprint_registry,
            step_manager=self.step_manager,
        )
        self.llm_settings_service = LLMSettingsService(
            project_manager=self.project_manager,
            blueprint_registry=self.blueprint_registry,
            blueprint_manager=self.blueprint_manager,
            prompt_manager=self.prompt_manager,
            node_type_registry=self.workflow_node_type_registry,
        )
        self.workspace_service = WorkspaceService(self.project_manager, self.bus)
        self.workspace_graph_service = WorkspaceGraphService(self.bus)
        self.workspace_layout_service = WorkspaceLayoutService(self.project_manager, self.llm_manager, self.bus)
        self.ontology_service = OntologyService(self.project_manager, self.bus, self.ontology_registry)
        self.data_dock_service = DataDockService(self.project_manager, self.data_provider_registry, self.bus)
        from core.services.data.structured_query_service import StructuredQueryService
        self.structured_query_service = StructuredQueryService(self.data_dock_service)
        from core.services.data.pdf_grid_extraction_service import PdfGridExtractionService
        self.pdf_grid_extraction_service = PdfGridExtractionService(self.data_provider_registry)

        # Phase 4: canonical table extraction layer
        self.table_extraction_service = TableExtractionService(
            project_manager=self.project_manager,
            extraction_cache=self.extraction_cache,
            data_dock_service=self.data_dock_service,
        )
        self.document_extraction_service.set_table_extraction_service(
            self.table_extraction_service
        )

        # Phase 5: block ranking / feature computation
        self.ranking_feature_service = RankingFeatureService()
        self.deterministic_pass_registry = DeterministicPassRegistry()
        DeterministicPassRegistry.seed_builtin_passes(
            self.deterministic_pass_registry, self.ranking_feature_service
        )

        # Intelligence layer foundation: lightweight artifact references,
        # source/stage policy, retrieval hooks, and model-role resolution.
        self.model_role_service = ModelRoleService(
            project_manager=self.project_manager,
            llm_manager=self.llm_manager,
            ai_setup_service=None,
            bus=self.bus,
        )
        self.llm_manager.set_model_role_service(self.model_role_service)
        from core.services.intelligence.model_dependency_service import ModelDependencyService
        self.model_dependency_service = ModelDependencyService(
            models_root=os.path.join(user_data_dir, "models")
        )
        from core.registries.intelligence_stage_registry import (
            IntelligenceStageRegistry,
            builtin_stage_specs,
        )
        self.intelligence_stage_registry = IntelligenceStageRegistry()
        for _stage_spec in builtin_stage_specs():
            self.intelligence_stage_registry.register(_stage_spec)
        self.processing_policy_service = ProcessingPolicyService(
            project_manager=self.project_manager,
            source_format_registry=self.source_format_registry,
            stage_registry=self.intelligence_stage_registry,
        )
        self.artifact_service = ArtifactService(
            project_manager=self.project_manager,
            document_extraction_service=self.document_extraction_service,
        )
        # Shared "jump to source" resolution: artifact -> typed anchor with an
        # explicit precision, used by every navigation surface and plugins.
        self.source_anchor_resolver = SourceAnchorResolver(
            project_manager=self.project_manager,
            artifact_service=self.artifact_service,
            document_extraction_service=self.document_extraction_service,
            pdf_geometry=PdfAnchorGeometry(),
        )
        from core.services.intelligence.reference_note_service import ReferenceNoteService
        self.reference_note_service = ReferenceNoteService(self.project_manager, self.artifact_service, self.source_anchor_resolver)
        from core.services.intelligence.cross_encoder_reranker import CrossEncoderReranker
        self.cross_encoder_reranker = CrossEncoderReranker(
            model_dir=self.model_dependency_service.model_dir("cross_encoder")
        )
        self.model_dependency_service.register_reload_callback(
            "cross_encoder", self.cross_encoder_reranker.reload
        )
        self.hybrid_retrieval_service = HybridRetrievalService(
            project_manager=self.project_manager,
            llm_manager=self.llm_manager,
            artifact_service=self.artifact_service,
            cross_encoder=self.cross_encoder_reranker,
            language_feature_service=self.language_feature_service,
            structured_query_service=self.structured_query_service,
        )
        self.context_packing_service = ContextPackingService()

        # Layered, pluggable entity extraction (replaces case-heuristic logic).
        from core.services.intelligence.entity_extractor import (
            EntityExtractorRegistry, GeneralNERExtractor, DomainPatternExtractor,
        )
        from core.services.intelligence.entity_extraction_pipeline import EntityExtractionPipeline
        self.entity_extractor_registry = EntityExtractorRegistry()
        self.entity_extractor_registry.register(
            GeneralNERExtractor(language_feature_service=self.language_feature_service)
        )
        self.entity_extractor_registry.register(DomainPatternExtractor())
        from core.services.intelligence.document_links import DocumentLinkService
        self.document_link_service = DocumentLinkService(
            self.project_manager, self.artifact_service, self.document_extraction_service, self.memory_manager_service)
        for edge_type in ("refers_to_note", "links_to"):
            self.artifact_service.register_edge_type(edge_type, {"structural": True})
        from core.services.intelligence.entities.project_lexicon import LexiconDigest
        self.artifact_service.register_edge_type("same_as_candidate", {"structural": False})
        self.artifact_service.register_edge_type("refers_to", {"structural": True})
        self.intelligence_stage_registry.register_project_input("entity_lexicon", LexiconDigest())
        from core.services.intelligence.entities.evidence_setup import compose_entity_evidence
        compose_entity_evidence(self)   # optional AI Setup resources: gazetteer, GLiNER, language model
        self.entity_extraction_pipeline = EntityExtractionPipeline(
            pattern_registry=self.pattern_detector_registry,
            project_manager=self.project_manager,
            artifact_service=self.artifact_service,
            extractor_registry=self.entity_extractor_registry,
            document_extraction_service=self.document_extraction_service,
            evidence_registry=self.entity_evidence_registry,
            language_model=self.language_id_runtime,
        )

        # Deterministic CLAIMS + SUMMARIES stage engines (no LLM at ingestion).
        from core.services.intelligence.sentence_classifier_service import SentenceClassifierService
        from core.services.intelligence.extractive_summarizer import ExtractiveSummarizer
        from core.services.intelligence.nli_relation_scorer import NliRelationScorer
        from core.services.intelligence.zero_shot_role_enhancer import (
            DEFAULT_HYPOTHESES, ZeroShotRoleEnhancer,
        )
        _enhancer_dir = self.model_dependency_service.model_dir("sentence_role_enhancer")
        self._role_enhancer_scorer = NliRelationScorer(_enhancer_dir)
        self.sentence_classifier_service = SentenceClassifierService(
            model_dir=self.model_dependency_service.model_dir(
                "sentence_classifier"
            ),
            enhancer=ZeroShotRoleEnhancer(self._role_enhancer_scorer, _enhancer_dir),
        )
        self.model_dependency_service.register_reload_callback(
            "sentence_classifier", self.sentence_classifier_service.reload
        )
        self.model_dependency_service.register_reload_callback(
            "sentence_role_enhancer", self._role_enhancer_scorer.reload
        )
        self.model_dependency_service.register_extra_file_provider(
            "sentence_role_hypotheses",
            lambda: json.dumps(DEFAULT_HYPOTHESES, indent=2, ensure_ascii=False),
        )
        self.nli_relation_scorer = NliRelationScorer(
            self.model_dependency_service.model_dir("nli_relation")
        )
        self.model_dependency_service.register_reload_callback(
            "nli_relation", self.nli_relation_scorer.reload
        )
        self.extractive_summarizer = ExtractiveSummarizer()

        # Fine-grained artifact embeddings (fuses semantic search with artifacts).
        from core.services.intelligence.artifact_embedding_service import ArtifactEmbeddingService
        self.artifact_embedding_service = ArtifactEmbeddingService(
            project_manager=self.project_manager,
            llm_manager=self.llm_manager,
            model_role_service=self.model_role_service,
        )
        from core.services.intelligence.deterministic_graph_analysis_service import (
            DeterministicGraphAnalysisService,
        )
        self.deterministic_graph_analysis_service = DeterministicGraphAnalysisService(
            project_manager=self.project_manager,
            artifact_service=self.artifact_service,
            stage_registry=self.intelligence_stage_registry,
            ontology_registry=self.ontology_registry,
        )
        from core.registries.intelligence_view_registry import (
            IntelligenceViewRegistry, builtin_intelligence_views,
        )
        self.intelligence_review_registry = self.artifact_service.review_registry
        self.intelligence_view_registry = IntelligenceViewRegistry()
        for _view_spec in builtin_intelligence_views():
            self.intelligence_view_registry.register(_view_spec)

        # Analysis modes: pluggable registry of blueprint-backed modes. Modes are
        # discovered at runtime and run via WorkflowRunnerService (never direct
        # LLM calls). Plugins add modes via api.intelligence.register_analysis_mode.
        from core.registries.analysis_mode_registry import AnalysisModeRegistry, builtin_analysis_modes
        from core.services.intelligence.analysis_contract_compiler import AnalysisContractCompiler
        self.analysis_mode_registry = AnalysisModeRegistry(
            compiler=AnalysisContractCompiler(self.ontology_registry, self.workspace_node_type_registry),
            stage_registry=self.intelligence_stage_registry)
        for _spec in builtin_analysis_modes():
            self.analysis_mode_registry.register(_spec)

        self.media_asset_service = MediaAssetService(self.project_manager)
        self.pdf_data_selection_service = PdfDataSelectionService(
            self.data_dock_service, self.data_provider_registry, self.pdf_grid_extraction_service,
        )

        self.essay_app_service = EssayAppService(self.project_manager)
        self.index_app_service = IndexAppService(self.llm_manager, self.project_manager)
        self.deterministic_extractor_service = DeterministicExtractorService(self.project_manager)
        self.document_ingestion_service = DocumentIngestionService(
            self.llm_manager, source_format_registry=self.source_format_registry
        )

        # Phase 1 adapters: route PDF parsing through shared DocumentExtractionService
        self.deterministic_extractor_service.set_document_extraction_service(
            self.document_extraction_service
        )
        self.document_ingestion_service.set_document_extraction_service(
            self.document_extraction_service
        )

        # Phase 7: enrich RAG indexing with shared block metadata
        self.llm_manager.set_document_extraction_service(self.document_extraction_service)

        # Phase 8: citation dock diagnostics — citation index access from UI
        self.citation_app_service.set_document_extraction_service(
            self.document_extraction_service
        )
        _metrics_db = get_resource_path(os.path.join("assets", "metrics.db"))
        self.source_evaluation_service = SourceEvaluationService(
            self.project_manager, _metrics_db, self.citation_manager
        )
        self.video_transcription_service = VideoTranscriptionService(
            self.project_manager, self.llm_manager, memory_manager=self.memory_manager_service)
        self.video_transcription_service.set_processing_policy_service(self.processing_policy_service)

        # Executable stage producers (built-in + plugin), looked up by the
        # StageSpec handler_id; binding verifies every enabled stage can run.
        self.intelligence_producer_registry = IntelligenceProducerRegistry()
        register_builtin_producers(
            self.intelligence_producer_registry,
            project_manager=self.project_manager,
            artifact_service=self.artifact_service,
            document_extraction_service=self.document_extraction_service,
            date_extraction_service=self.date_extraction_service,
            entity_extraction_pipeline=self.entity_extraction_pipeline,
            sentence_classifier=self.sentence_classifier_service,
            extractive_summarizer=self.extractive_summarizer,
            artifact_embedding_service=self.artifact_embedding_service,
            llm_manager=self.llm_manager,
            model_role_service=self.model_role_service,
            nli_relation_scorer=self.nli_relation_scorer,
            source_format_registry=self.source_format_registry,
            data_provider_registry=self.data_provider_registry,
            pattern_registry=self.pattern_detector_registry,
        )
        self.intelligence_stage_registry.bind_producers(self.intelligence_producer_registry)
        self.intelligence_scheduler = IntelligenceScheduler(
            producer_registry=self.intelligence_producer_registry,
            process_registry=self.process_registry,
            project_manager=self.project_manager,
            artifact_service=self.artifact_service,
            policy_service=self.processing_policy_service,
            model_role_service=self.model_role_service,
            document_extraction_service=self.document_extraction_service,
            llm_manager=self.llm_manager,
            bus=self.bus,
            entity_extraction_pipeline=self.entity_extraction_pipeline,
            sentence_classifier=self.sentence_classifier_service,
            extractive_summarizer=self.extractive_summarizer,
            artifact_embedding_service=self.artifact_embedding_service,
            stage_registry=self.intelligence_stage_registry,
            date_extraction_service=self.date_extraction_service,
            nli_relation_scorer=self.nli_relation_scorer,
            memory_manager=self.memory_manager_service,
        )
        self.processing_plan_service = ProcessingPlanService(
            policy_service=self.processing_policy_service,
            stage_registry=self.intelligence_stage_registry,
            scheduler=self.intelligence_scheduler,
            project_manager=self.project_manager,
            model_dependency_service=self.model_dependency_service,
            model_role_service=self.model_role_service,
        )
        self.source_intelligence_service = SourceIntelligenceService(
            project_manager=self.project_manager,
            artifact_service=self.artifact_service,
            scheduler=self.intelligence_scheduler,
            policy_service=self.processing_policy_service,
            hybrid_retrieval_service=self.hybrid_retrieval_service,
            bus=self.bus,
            processing_plan_service=self.processing_plan_service,
            llm_manager=self.llm_manager,
        )
        # Analysis readiness depends on the fully constructed source-processing facade.
        from core.services.intelligence.analysis_mode_service import AnalysisModeService
        self.analysis_mode_service = AnalysisModeService(
            mode_registry=self.analysis_mode_registry,
            blueprint_registry=self.blueprint_registry,
            project_manager=self.project_manager,
            bus=self.bus,
            artifact_service=self.artifact_service,
            prompt_manager=self.prompt_manager,
            source_intelligence_service=self.source_intelligence_service,
        )

        self.document_ingestion_service.set_intelligence_scheduler(
            self.intelligence_scheduler
        )
        self.index_app_service.set_source_intelligence_service(
            self.source_intelligence_service
        )

        # 4. Workflow Services (ui_router and model_provider injected later by GUI layer)
        self.workflow_runner_service = WorkflowRunnerService(
            llm_manager=self.llm_manager,
            prompt_manager=self.prompt_manager,
            step_manager=self.step_manager,
            process_registry=self.process_registry,
            project_manager=self.project_manager,
            ontology_registry=self.ontology_registry,
            blueprint_manager=self.blueprint_manager,
            node_type_registry=self.workflow_node_type_registry,
            data_provider_registry=self.data_provider_registry,
            llm_settings_service=self.llm_settings_service,
            document_extraction_service=self.document_extraction_service,
            ranking_feature_service=self.ranking_feature_service,
            pattern_detector_registry=self.pattern_detector_registry,
            deterministic_pass_registry=self.deterministic_pass_registry,
            artifact_service=self.artifact_service,
            hybrid_retrieval_service=self.hybrid_retrieval_service,
            context_packing_service=self.context_packing_service,
            model_role_service=self.model_role_service,
            source_intelligence_service=self.source_intelligence_service,
            event_bus=self.bus,
        )
        self.research_agent_service = ResearchAgentService(
            self.project_manager,
            self.prompt_manager,
            self.blueprint_registry,
            workflow_executor=self.workflow_runner_service.prepare_runner,
            runner_starter=self.workflow_runner_service.start_runner,
        )

        # 4.5 Help & Tutorial subsystem
        from core.services.help_service import HelpService
        self.help_service = HelpService(self.bus)

        # Source viewer display settings service — manages global viewer preferences
        # (font size, text/bg colors, reading mode, pagination) (Phase 8).
        from core.services.gui_bridge.source_viewer_settings_service import SourceViewerSettingsService
        self.source_viewer_settings_service = SourceViewerSettingsService(app_config=None)

        # AI setup service — coordinates backend detection and provider switching.
        # startup_check() is called here to detect installed backends synchronously;
        # provider switching and LLM availability are set before plugins load.
        self.ai_setup_service = AISetupService(
            self.llm_backend_registry,
            self.llm_manager,
            user_data_dir,
            self.bus,
        )
        self.ai_setup_service.startup_check()
        self.model_role_service.ai_setup_service = self.ai_setup_service
        self.ai_setup_service.set_intelligence_services(
            model_role_service=self.model_role_service,
            processing_policy_service=self.processing_policy_service,
        )

        # Model readiness needs the configured AI setup service, not a placeholder.
        from core.services.intelligence.model_readiness_service import ModelReadinessService
        self.model_readiness_service = ModelReadinessService(self.project_manager, self.model_dependency_service,
            self.sentence_classifier_service, self.processing_policy_service, backends=self.llm_backend_registry,
            setup=self.ai_setup_service, roles=self.model_role_service, user_data_dir=self.user_data_dir)

        # 5. Plugin extension registry (GUI extension points for plugins)
        self.plugin_extension_registry = PluginExtensionRegistry()
        self._register_builtin_dock_actions()
        # plugin_dock_specs consumed by MainWindow
        self.plugin_dock_specs = []
        # Storage for plugin instances + their API objects (populated by loader)
        self._loaded_plugins = []
        self._custom_services = {}

        # 6. Pack import/export system
        self.pack_contributor_registry = PackContributorRegistry()
        self.pack_service = PackService(
            blueprint_manager=self.blueprint_manager,
            prompt_manager=self.prompt_manager,
            step_manager=self.step_manager,
            contributor_registry=self.pack_contributor_registry,
            project_manager=self.project_manager,
        )

        # Discover and register external plugins
        # Everything that holds memory is visible to (and releasable by) one owner.
        set_shared_language_service(self.language_feature_service)
        register_core_consumers(self.memory_manager_service, self)
        self.process_registry.admission = self.memory_manager_service.admit_job
        self.process_registry.express_limit = self.memory_manager_service.express_limit
        # Bound QObject slot → queued onto the registry's thread.
        self.bus.memory_state_changed.connect(self.process_registry.on_memory_state)
        # The in-memory extraction LRU belongs to the project being closed.
        self.bus.project_clearing_started.connect(lambda *_: self.extraction_cache.clear_memory())
        load_plugins(self)

    def _register_builtin_dock_actions(self) -> None:
        """Register core built-in DockActionSpecs (not from plugins)."""
        try:
            from core.engine.dock_mounts import SOURCE_VIEWER_TEXT_SELECTION
            from core.plugins.extension_registry import DockActionSpec, DockActionContext
            from core.engine.blueprints.extract_claims import get_extract_claims_blueprint

            def _extract_claims_state(ctx: DockActionContext) -> dict:
                return {
                    "selected_text": ctx.selection or "",
                    "source_path": ctx.source_path or "",
                }

            self.plugin_extension_registry.add_dock_action(DockActionSpec(
                action_id="core.extract_claims",
                label="Extract Claims",
                tooltip="Use AI to extract structured claims from the selected text and save them as workspace nodes.",
                mounts=[SOURCE_VIEWER_TEXT_SELECTION],
                blueprint_factory=lambda ctx: get_extract_claims_blueprint(),
                initial_state_builder=_extract_claims_state,
                position=30,
            ))
        except Exception as exc:
            print(f"[PapyrusCore] Failed to register builtin dock actions: {exc}")

    def register_plugin(self, plugin, api=None) -> None:
        """Register a plugin's contributions (blueprints, tools, ontology, dock, GUI)."""
        plugin.register_blueprints(self.blueprint_registry)
        plugin.register_workspace_tools(self.workspace_ai_tools_registry)
        plugin.register_ontology_types(self.ontology_registry)
        dock_spec = plugin.get_dock_spec()
        if dock_spec is not None:
            dock_spec.plugin_id = plugin.plugin_id
            self.plugin_dock_specs.append(dock_spec)
        if hasattr(plugin, "register_gui_extensions"):
            plugin.register_gui_extensions(self.plugin_extension_registry)

    def _unload_plugins(self) -> None:
        """Call on_unload on all plugins and clean up API subscriptions."""
        for plugin, api in reversed(self._loaded_plugins):
            try:
                if hasattr(plugin, "on_unload") and callable(plugin.on_unload):
                    plugin.on_unload()
            except Exception as exc:
                print(f"[PluginLoader] Error unloading '{plugin.plugin_id}': {exc}")
            finally:
                api._cleanup()
        self._loaded_plugins.clear()

    def _ensure_default_dictionary(self):
        """Silently provisions the default dictionary on first launch."""
        if not self.dictionary_manager.get_available_dictionaries():
            default_dict_path = get_resource_path(os.path.join("assets", "default_english.json"))
            if os.path.exists(default_dict_path):
                print("[System] First launch detected. Building default dictionary...")
                self.dictionary_manager.import_json(default_dict_path, "Default English")
