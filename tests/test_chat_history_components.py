import json
import os
import sqlite3
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from core.db.ai_db import AIDB
from core.engine.default_blueprints import DefaultBlueprints
from core.engine.action_model import AIActionBlueprint
from core.events.event_bus import EventBus
from core.engine.ui_payloads import serialize_payloads
from gui.docks.unified_research.components.chat_streamer import ChatMessageWidget
from gui.docks.unified_research.components.history_renderer import ChatHistoryRenderer
from gui.docks.unified_research.components.note_bubble import NoteBubbleWidget
from gui.docks.unified_research.components.manifest_bubble import ManifestUpdateWidget
from gui.docks.unified_research.tabs.base_tab import BaseTab


APP = QApplication.instance() or QApplication([])


class _ThemeManager:
    def get_theme(self):
        return {
            "text_main": "#ffffff", "text_muted": "#aaaaaa", "accent": "#8b5cf6",
            "bg_main": "#202020", "bg_input": "#282828", "bg_panel": "#242424",
            "border": "#444444",
        }


def _tab():
    context = SimpleNamespace(
        theme_manager=_ThemeManager(), blueprint_manager=None, prompt_manager=None,
        project_manager=None, llm_manager=None, ui_router=None, viewer=None,
    )
    tab = BaseTab(context, target_id="chat_dock")
    tab.chat_layout = QVBoxLayout(tab)
    tab.chat_layout.addStretch()
    return tab


def test_saved_turn_replays_markdown_and_note_bubbles_in_one_widget():
    payloads = [
        {"type": "replace_stream_text", "text": "## Finding\n\nA **strong** result."},
        {"type": "citation_cards", "items": [
            {"doc_name": "paper.pdf", "quote": "Exact evidence", "note": "Supports the finding"}
        ]},
        {"type": "hide_status"},
    ]
    history = [{
        "role": "ai", "content": "raw", "ui_format": "live_stream",
        "trace_id": None, "ui_payload": serialize_payloads(payloads),
    }]
    tab = _tab()

    ChatHistoryRenderer(tab, theme=tab.theme).render(history)

    message = tab.chat_layout.itemAt(0).widget()
    assert isinstance(message, ChatMessageWidget)
    assert "Finding" in message.main_browser.toPlainText()
    assert message.bubbles_layout.count() == 1
    assert isinstance(message.bubbles_layout.itemAt(0).widget(), NoteBubbleWidget)
    assert tab._active_stream_widget is None


def test_history_reload_replaces_previous_project_turns():
    tab = _tab()
    first = [{"role": "user", "content": "old project", "ui_format": "text"}]
    second = [{"role": "user", "content": "new project", "ui_format": "text"}]

    tab.render_chat_history(first, clear_existing=True)
    tab.render_chat_history(second, clear_existing=True)

    assert tab.chat_layout.count() == 2  # one message plus trailing stretch
    message = tab.chat_layout.itemAt(0).widget()
    assert message.raw_buffer == "new project"


def test_duplicate_save_is_upgraded_with_rich_payload():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE chat_messages ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, tab_name TEXT, role TEXT, content TEXT, "
        "ui_format TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP, trace_id TEXT, ui_payload TEXT)"
    )
    db = AIDB(SimpleNamespace(_conn=conn))
    db.save_chat_message("chat_dock", "ai", "answer", "live_stream")
    rich = json.dumps([{"type": "replace_stream_text", "text": "answer"}])

    db.save_chat_message("chat_dock", "ai", "answer", "live_stream", "trace-1", rich)

    rows = conn.execute("SELECT trace_id, ui_payload FROM chat_messages").fetchall()
    assert rows == [("trace-1", rich)]


def test_note_bubble_actions_use_viewer_and_annotation_service():
    calls = []

    class _AnnotationManager:
        def trigger_similar_context(self, quote):
            calls.append(("similar", quote))

    class _Viewer:
        annot_manager = _AnnotationManager()

        def jump_to_source(self, doc_name, quote):
            calls.append(("jump", doc_name, quote))

        def jump_to_video(self, **kwargs):
            calls.append(("video", kwargs))

    class _AnnotationService:
        def add_ai_annotation(self, quote, note, doc_name):
            calls.append(("save", quote, note, doc_name))

    host = QWidget()
    host.viewer = _Viewer()
    host.app_context = SimpleNamespace(workspace_annotation_service=_AnnotationService())
    message = ChatMessageWidget("AI Agent", theme=_ThemeManager().get_theme(), parent=host)
    bubble = message.add_bubble("paper.pdf", "Exact evidence", "Initial note")

    bubble.search_requested.emit("Exact evidence")
    bubble.jump_requested.emit("paper.pdf", "Exact evidence")
    bubble.save_requested.emit("Exact evidence", "Edited note", "paper.pdf")

    assert ("similar", "Exact evidence") in calls
    assert ("jump", "paper.pdf", "Exact evidence") in calls
    assert ("save", "Exact evidence", "Edited note", "paper.pdf") in calls


def test_manifest_history_renders_actionable_change_widget():
    history = [{
        "role": "ai",
        "content": "Project brief updated",
        "ui_format": "manifest_update",
        "ui_payload": serialize_payloads([{
            "type": "manifest_updated",
            "text": "Project brief updated",
            "changes": {"Core Thesis": "A refined thesis"},
        }]),
    }]
    tab = _tab()

    ChatHistoryRenderer(tab, theme=tab.theme).render(history)

    message = tab.chat_layout.itemAt(0).widget()
    update = message.bubbles_layout.itemAt(0).widget()
    assert isinstance(update, ManifestUpdateWidget)
    assert "open manifest" in update.btn_open.text().lower()


def test_legacy_history_hides_manifest_tags_and_adds_actionable_update():
    raw = 'Visible answer <UPDATE_MANIFEST>{"Core Thesis":"Changed"}</UPDATE_MANIFEST>'
    history = [{
        "role": "ai", "content": raw, "ui_format": "live_stream",
        "ui_payload": serialize_payloads([
            {"type": "replace_stream_text", "text": raw},
            {"type": "hide_status"},
        ]),
    }]
    tab = _tab()

    ChatHistoryRenderer(tab, theme=tab.theme).render(history)

    assert tab.chat_layout.count() == 2
    message = tab.chat_layout.itemAt(0).widget()
    assert message.main_browser.toPlainText() == "Visible answer"
    assert isinstance(message.bubbles_layout.itemAt(0).widget(), ManifestUpdateWidget)


def test_chat_blueprint_uses_separate_citation_and_manifest_steps():
    blueprint = DefaultBlueprints.get_universal_chat_blueprint(None)
    answer = next(step for step in blueprint.steps if step.step_id == "chat_response")
    citation_plan = next(step for step in blueprint.steps if step.step_id == "plan_citation_coverage")
    citation_loop = next(step for step in blueprint.steps if step.step_id == "gather_claim_citations")
    citation_render = next(step for step in blueprint.steps if step.step_id == "render_citation_coverage")
    citation_worker = citation_loop.inputs["sub_blueprint"].steps[0]
    manifest_router = next(step for step in blueprint.steps if step.step_id == "manifest_update_router")
    manifest = manifest_router.if_true[0]

    assert answer.inline_citations is False
    assert answer.allow_manifest_updates is False
    assert citation_plan.output_key == "citation_claim_plan"
    assert citation_loop.step_type == "FOREACH"
    assert "{final_answer}" in citation_worker.inputs["query"]
    assert "{rag_context}" in citation_worker.inputs["query"]
    assert citation_render.ui_format == "chat_widgets"
    assert manifest.ui_format == "silent"
    assert manifest.allow_manifest_updates is True


def test_deep_chat_blueprint_plans_and_foreaches_targeted_searches():
    blueprint = DefaultBlueprints.get_universal_chat_blueprint(None)
    router = next(step for step in blueprint.steps if step.step_id == "rag_router")
    deep_steps = {step.step_id: step for step in router.if_true}

    assert deep_steps["collect_source_statistics"].step_type == "SOURCE_STATISTICS"
    planner = deep_steps["plan_adaptive_research"]
    assert "{source_statistics}" in planner.inputs["query"]
    assert "{initial_rag_context}" in planner.inputs["query"]
    loop = deep_steps["run_targeted_research"]
    assert loop.step_type == "FOREACH"
    child = loop.inputs["sub_blueprint"].steps[0]
    assert child.step_type == "RAG_SEARCH"
    assert child.inputs["queries"] == ["{item.query}"]
    assert deep_steps["combine_research_context"].output_key == "rag_context"


def test_global_output_workspace_setting_reaches_brainstorm_pipeline():
    class _Context(SimpleNamespace):
        def build_rag_context_payload(self):
            return {"_global_ai_settings": {"output_workspace": True}}

        def get_active_ai_model(self):
            return "test-model"

    context = _Context(
        theme_manager=_ThemeManager(), blueprint_manager=None, prompt_manager=None,
        project_manager=object(), llm_manager=None, ui_router=None, viewer=None,
    )
    tab = BaseTab(context, target_id="brainstorm_dock")
    captured = []
    bus = EventBus.get_instance()
    slot = lambda _intent, payload: captured.append(payload)
    bus.workflow_action_requested.connect(slot)
    try:
        tab.send_to_pipeline(AIActionBlueprint(name="test", description="", steps=[]), {"query": "plan"})
    finally:
        bus.workflow_action_requested.disconnect(slot)

    assert captured[-1].initial_state["output_workspace"] is True
    brainstorm = DefaultBlueprints.get_brainstorm_blueprint(None, "Brainstorm System - Default")
    graph_router = next(step for step in brainstorm.steps if step.step_id == "graph_router")
    assert [step.step_type for step in graph_router.if_true] == [
        "ONTOLOGY_CATALOG", "LLM_QUERY", "GRAPH_VALIDATOR", "WORKSPACE_WRITE"
    ]
