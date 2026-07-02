"""Architecture contracts for the domain-organized application service layer."""

from __future__ import annotations

import ast
import importlib
import json
from pathlib import Path
import subprocess
import sys


COMPATIBILITY_IMPORTS = {
    "core.services.agent_loop_service": (
        "core.services.ai.agent_loop_service", "AgentLoopConfig"
    ),
    "core.services.ai_bootstrap_service": (
        "core.services.gui_bridge.ai_bootstrap_service", "AIBootstrapService"
    ),
    "core.services.citation_app_service": (
        "core.services.reference.citation_app_service", "CitationAppService"
    ),
    "core.services.dictionary_app_service": (
        "core.services.reference.dictionary_app_service", "DictionaryAppService"
    ),
    "core.services.document_app_service": (
        "core.services.document.document_app_service", "DocumentAppService"
    ),
    "core.services.document_ingestion_service": (
        "core.services.document.document_ingestion_service", "DocumentIngestionTask"
    ),
    "core.services.embedding_service": (
        "core.services.project.embedding_service", "EmbeddingService"
    ),
    "core.services.graph_analysis_service": (
        "core.services.knowledge.graph_analysis_service", "GraphAnalysisService"
    ),
    "core.services.notes_app_service": (
        "core.services.content.notes_app_service", "NotesAppService"
    ),
    "core.services.ocr_app_service": (
        "core.services.document.ocr_app_service", "OCRAppService"
    ),
    "core.services.ontology_service": (
        "core.services.knowledge.ontology_service", "OntologyService"
    ),
    "core.services.project_app_service": (
        "core.services.project.project_app_service", "ProjectAppService"
    ),
    "core.services.research_agent_service": (
        "core.services.ai.research_agent_service", "ResearchAgentService"
    ),
    "core.services.tag_app_service": (
        "core.services.content.tag_app_service", "TagAppService"
    ),
    "core.services.tts_app_service": (
        "core.services.ai.tts_app_service", "TTSAppService"
    ),
    "core.services.workflow_runner_service": (
        "core.services.ai.workflow_runner_service", "WorkflowRunnerService"
    ),
    "core.services.data_dock_service": (
        "core.services.data.data_dock_service", "DataDockService"
    ),
    "core.services.chart_data_service": (
        "core.services.data.chart_data_service", "ChartDataAdapter"
    ),
    "core.services.grid_mapping_service": (
        "core.services.data.grid_mapping_service", "GridMappingService"
    ),
    "core.services.pdf_data_selection_service": (
        "core.services.data.pdf_data_selection_service", "PdfDataSelectionService"
    ),
    "core.services.pdf_grid_extraction_service": (
        "core.services.data.pdf_grid_extraction_service", "PdfGridExtractionService"
    ),
    "core.services.media_asset_service": (
        "core.services.project.media_asset_service", "MediaAssetService"
    ),
    "core.services.pack_service": (
        "core.services.packs.pack_service", "PackService"
    ),
}


def test_compatibility_imports_preserve_class_identity():
    checks = json.dumps(list((legacy, canonical, symbol) for legacy, (canonical, symbol) in COMPATIBILITY_IMPORTS.items()))
    script = (
        "import importlib, json; "
        f"checks=json.loads({checks!r}); "
        "assert all(getattr(importlib.import_module(a), s) is getattr(importlib.import_module(b), s) "
        "for a,b,s in checks)"
    )
    subprocess.run([sys.executable, "-c", script], check=True)


def test_workspace_compatibility_facade_preserves_class_identity():
    expected = {
        "WorkspaceService": "core.services.workspace.workspace_service",
        "WorkspaceGraphService": "core.services.workspace.workspace_graph_service",
        "WorkspaceLayoutService": "core.services.workspace.workspace_layout_service",
        "WorkspaceStateService": "core.services.workspace.workspace_state_service",
        "WorkspaceAnnotationService": "core.services.gui_bridge.workspace_annotation_service",
        "WorkspaceAIService": "core.services.gui_bridge.workspace_ai_service",
    }
    checks = json.dumps(list(expected.items()))
    script = (
        "import importlib, json; legacy=importlib.import_module('core.services.workspace_services'); "
        f"checks=json.loads({checks!r}); "
        "assert all(getattr(legacy, s) is getattr(importlib.import_module(m), s) for s,m in checks)"
    )
    subprocess.run([sys.executable, "-c", script], check=True)


def test_first_party_code_uses_canonical_service_imports():
    root = Path(__file__).resolve().parents[1]
    legacy_modules = set(COMPATIBILITY_IMPORTS) | {"core.services.workspace_services"}
    violations = []
    for base in (root / "core", root / "gui", root / "web", root / "plugins"):
        for path in base.rglob("*.py"):
            if path.parent == root / "core" / "services":
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module in legacy_modules:
                    violations.append(f"{path.relative_to(root)}:{node.lineno} imports {node.module}")
    assert not violations, "Legacy service imports remain:\n" + "\n".join(violations)
