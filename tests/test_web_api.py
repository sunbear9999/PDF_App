from pathlib import Path

import httpx
import pytest

from web.bridge.lease import EditLeaseManager
from web.bridge.state import WebState
from web.security import AuthManager, SESSION_COOKIE
from web.server.app import _valid_upload_signature, create_app


class Dispatcher:
    def __init__(self, media=None):
        self.calls = []
        self.media = media

    def invoke(self, name, payload, timeout=60):
        self.calls.append((name, payload))
        if name == "project.snapshot":
            return {"open": True, "name": "Test"}
        if name == "sources.resolve_media":
            return {"path": str(self.media), "mime_type": "application/pdf", "filename": "test.pdf"}
        return {"ok": True, **payload}


class Runtime:
    def __init__(self, media=None):
        self.host, self.port = "127.0.0.1", 8765
        self.auth, self.leases, self.state = AuthManager(), EditLeaseManager(), WebState()
        self.dispatcher = Dispatcher(media)
        self.cache = {}

    def idempotency_get(self, sid, cid):
        return self.cache.get((sid, cid))

    def idempotency_put(self, sid, cid, value):
        self.cache[(sid, cid)] = value


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def paired_client(runtime):
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(runtime)), base_url="http://127.0.0.1:8765")
    response = await client.post("/api/v1/auth/pair", json={"token": runtime.auth.pair_token, "device_name": "Test browser"})
    assert response.status_code == 200
    return client, response.json()["csrf"]


@pytest.mark.anyio
async def test_pair_snapshot_csrf_and_edit_lease():
    runtime = Runtime()
    client, csrf = await paired_client(runtime)
    assert (await client.get("/api/v1/snapshot/project")).json()["data"]["name"] == "Test"
    body = {"command_id": "command-123", "expected_revision": 0, "payload": {"name": "Essay"}}
    assert (await client.post("/api/v1/command/essays/save", json=body)).status_code == 403
    assert (await client.post("/api/v1/lease/acquire", headers={"x-papyrus-csrf": csrf})).status_code == 200
    assert (await client.post("/api/v1/command/essays/save", headers={"x-papyrus-csrf": csrf}, json=body)).status_code == 200
    # Same command id is idempotent even though the project revision advanced.
    assert (await client.post("/api/v1/command/essays/save", headers={"x-papyrus-csrf": csrf}, json=body)).status_code == 200
    assert [name for name, _ in runtime.dispatcher.calls].count("essays.save") == 1
    stale = {"command_id": "command-456", "expected_revision": 0, "payload": {"name": "Stale"}}
    assert (await client.post("/api/v1/command/essays/save", headers={"x-papyrus-csrf": csrf}, json=stale)).status_code == 409
    await client.aclose()


@pytest.mark.anyio
async def test_host_and_origin_are_rejected():
    runtime = Runtime()
    app = create_app(runtime)
    bad_host = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://attacker.invalid")
    assert (await bad_host.get("/")).status_code == 400
    await bad_host.aclose()
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765")
    response = await client.post("/api/v1/auth/pair", headers={"origin": "http://attacker.invalid"}, json={"token": runtime.auth.pair_token, "device_name": "x"})
    assert response.status_code == 403
    await client.aclose()


@pytest.mark.anyio
async def test_health_is_available_before_pairing():
    runtime = Runtime()
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(runtime)), base_url="http://127.0.0.1:8765")
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["pairing_required"] is True
    await client.aclose()


@pytest.mark.anyio
async def test_media_range(tmp_path):
    path = tmp_path / "test.pdf"
    path.write_bytes(b"0123456789")
    runtime = Runtime(path)
    client, _ = await paired_client(runtime)
    response = await client.get("/api/v1/source/source-id/media", headers={"range": "bytes=2-5"})
    assert response.status_code == 206
    assert response.content == b"2345"
    assert response.headers["content-range"] == "bytes 2-5/10"
    await client.aclose()


def test_upload_signature_validation(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.7\n")
    disguised = tmp_path / "bad.pdf"
    disguised.write_bytes(b"<html>not a pdf")
    assert _valid_upload_signature(str(pdf), ".pdf")
    assert not _valid_upload_signature(str(disguised), ".pdf")


@pytest.mark.anyio
async def test_compiled_frontend_is_served_locally():
    runtime = Runtime()
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(runtime)), base_url="http://127.0.0.1:8765")
    response = await client.get("/")
    assert response.status_code == 200
    assert "Papyrus" in response.text
    assert "https://" not in response.text
    assert response.headers["cache-control"] == "no-cache"
    await client.aclose()
