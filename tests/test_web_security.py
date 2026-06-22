import time
import threading
import os
import asyncio
import httpx
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication

from web.bridge.lease import EditLeaseManager
from web.bridge.dispatcher import QtCommandDispatcher
from web.bridge.commands import WebCommands
from web.server.app import create_app
from web.bridge.state import RevisionConflict, WebState
from web.security import AuthManager, private_interfaces


def test_pairing_is_one_use_and_session_is_bound():
    auth = AuthManager(session_ttl=60, pair_ttl=60)
    token = auth.pair_token
    session = auth.pair(token, "Phone", "192.168.1.4", "Browser A")
    assert session is not None
    assert auth.pair(token, "Replay", "192.168.1.4", "Browser A") is None
    assert auth.authenticate(session.session_id, "192.168.1.4", "Browser A") == session
    assert auth.authenticate(session.session_id, "192.168.1.5", "Browser A") is None
    assert auth.authenticate(session.session_id, "192.168.1.4", "Browser B") is None
    assert auth.validate_csrf(session, session.csrf)
    assert not auth.validate_csrf(session, "wrong")


def test_private_interfaces_never_contains_wildcard():
    addresses = {item["address"] for item in private_interfaces()}
    assert "127.0.0.1" in addresses
    assert "0.0.0.0" not in addresses


def test_exclusive_lease_and_forced_release():
    changes = []
    manager = EditLeaseManager(ttl_seconds=60, on_change=lambda active, name: changes.append((active, name)))
    assert manager.acquire("one", "Phone")[0]
    assert not manager.acquire("two", "Laptop")[0]
    assert manager.owns("one")
    assert not manager.owns("two")
    assert manager.release("one")
    assert changes == [(True, "Phone"), (False, "")]


def test_revision_events_and_conflicts():
    state = WebState()
    state.check(0)
    event = state.publish("notes", "updated", "n1", {"text": "x"})
    assert event["revision"] == 1
    assert state.wait_after(0, 0.01)[0]["resource_id"] == "n1"
    try:
        state.check(0)
    except RevisionConflict as exc:
        assert exc.actual == 1
    else:
        raise AssertionError("stale revision was accepted")


def test_dispatcher_runs_worker_request_on_qt_main_thread():
    app = QApplication.instance() or QApplication([])
    dispatcher = QtCommandDispatcher()
    seen = []
    dispatcher.register("thread.check", lambda payload: seen.append(QThread.currentThread() is app.thread()) or payload["value"])
    result = []
    worker = threading.Thread(target=lambda: result.append(dispatcher.invoke("thread.check", {"value": 7}, timeout=2)))
    worker.start()
    while worker.is_alive():
        app.processEvents()
        worker.join(0.01)
    assert result == [7]
    assert seen == [True]


def test_project_snapshot_accepts_boolean_ai_enabled_property():
    pm = SimpleNamespace(
        project_filepath="/tmp/research.pdfproj", project_name="Research",
        active_file=None, list_sources=lambda: [],
    )
    context = SimpleNamespace(
        project_manager=pm, bus=SimpleNamespace(),
        llm_manager=SimpleNamespace(ai_enabled=True),
        get_active_ai_model=lambda: "local-model",
    )
    snapshot = WebCommands(context, WebState()).cmd_project__snapshot({})
    assert snapshot["open"] is True
    assert snapshot["name"] == "Research"
    assert snapshot["ai_available"] is True


def test_project_snapshot_survives_unavailable_optional_ai_backend():
    pm = SimpleNamespace(
        project_filepath="/tmp/research.pdfproj", project_name="Research",
        active_file=None, list_sources=lambda: [],
    )
    context = SimpleNamespace(
        project_manager=pm, bus=SimpleNamespace(),
        llm_manager=SimpleNamespace(ai_enabled=lambda: (_ for _ in ()).throw(RuntimeError("offline"))),
        get_active_ai_model=lambda: (_ for _ in ()).throw(RuntimeError("not configured")),
    )
    snapshot = WebCommands(context, WebState()).cmd_project__snapshot({})
    assert snapshot["open"] is True
    assert snapshot["ai_available"] is False
    assert snapshot["active_model"] is None


def test_legacy_project_pdfs_are_visible_as_opaque_web_sources(tmp_path):
    path = tmp_path / "legacy-paper.pdf"
    path.write_bytes(b"%PDF-1.7\n")
    pm = SimpleNamespace(
        project_filepath=str(tmp_path / "legacy.pdfproj"), project_name="Legacy",
        active_file=None, pdfs=[str(path)], list_sources=lambda: [],
        get_tags_for_doc=lambda _path: [],
    )
    context = SimpleNamespace(project_manager=pm, bus=SimpleNamespace())
    sources = WebCommands(context, WebState()).cmd_sources__list({})
    assert len(sources) == 1
    assert sources[0]["filename"] == "legacy-paper.pdf"
    assert sources[0]["id"].startswith("legacy-")
    assert "path" not in sources[0]


def test_project_paths_are_replaced_with_opaque_source_ids():
    source_path = "/home/alice/private/paper.pdf"
    pm = SimpleNamespace(list_sources=lambda: [{"id": "source:opaque", "path": source_path}])
    context = SimpleNamespace(project_manager=pm, bus=SimpleNamespace())
    public = WebCommands(context, WebState()).public_data({
        "pdf_path": source_path,
        "nested": {"doc_id": source_path, "text": "safe"},
    })
    encoded = str(public)
    assert "/home/alice" not in encoded
    assert public["document_source_id"] == "source:opaque"
    assert public["nested"]["document_source_id"] == "source:opaque"


def test_authenticated_snapshot_crosses_real_qt_dispatcher():
    app = QApplication.instance() or QApplication([])
    dispatcher = QtCommandDispatcher()
    dispatcher.register("project.snapshot", lambda _payload: {"open": True, "name": "Live Project"})
    runtime = SimpleNamespace(
        host="127.0.0.1", port=8765, dispatcher=dispatcher, state=WebState(),
        auth=AuthManager(), leases=EditLeaseManager(), extensions=None,
        idempotency_get=lambda *_: None, idempotency_put=lambda *_: None,
    )
    result = []

    def request_snapshot():
        async def run():
            transport = httpx.ASGITransport(app=create_app(runtime))
            async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client:
                paired = await client.post("/api/v1/auth/pair", json={"token": runtime.auth.pair_token, "device_name": "Browser"})
                assert paired.status_code == 200
                response = await client.get("/api/v1/snapshot/project")
                result.append(response.json())
        asyncio.run(run())

    worker = threading.Thread(target=request_snapshot)
    worker.start()
    while worker.is_alive():
        app.processEvents()
        worker.join(0.01)
    assert result[0]["data"] == {"open": True, "name": "Live Project"}
