from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
import shutil
import tempfile
from pathlib import Path
from typing import Any

from fastapi import Cookie, Depends, FastAPI, File, Header, HTTPException, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from web.bridge.state import RevisionConflict
from web.models import CommandRequest, PairRequest
from web.security import CSRF_HEADER, SESSION_COOKIE, Session, allowed_hosts
from .media import ranged_file

MAX_BODY = 260 * 1024 * 1024
MAX_UPLOAD = 250 * 1024 * 1024
SAFE_NAME = re.compile(r"[^A-Za-z0-9._() -]+")
LOGGER = logging.getLogger(__name__)


def _valid_upload_signature(path: str, extension: str) -> bool:
    with open(path, "rb") as handle:
        head = handle.read(32)
    if extension == ".pdf":
        return head.startswith(b"%PDF-")
    if extension in {".mp4", ".mov", ".m4v"}:
        return len(head) >= 12 and head[4:8] == b"ftyp"
    if extension in {".mkv", ".webm"}:
        return head.startswith(b"\x1a\x45\xdf\xa3")
    if extension == ".avi":
        return head.startswith(b"RIFF") and head[8:12] == b"AVI "
    return False

READ_COMMANDS = {
    "project.snapshot", "sources.list", "sources.details", "pdf.page_text", "pdf.search",
    "annotations.list", "tags.list", "notes.list", "workspaces.list", "workspaces.get",
    "essays.list", "essays.get", "citations.list", "dictionary.search", "data.list", "data.get",
    "ai.catalog", "ai.history", "ai.jobs", "ai.trace", "research.session", "plugins.list",
    "ai.audit", "project.metadata", "ontology.catalog", "analysis.saved",
}


def create_app(runtime) -> FastAPI:
    app = FastAPI(title="Papyrus Local Web Companion", version="1.0.0", docs_url=None, redoc_url=None, openapi_url=None)
    static_dir = Path(__file__).resolve().parents[1] / "frontend" / "dist"
    hosts = allowed_hosts(runtime.host, runtime.port)

    @app.middleware("http")
    async def security_middleware(request: Request, call_next):
        host = request.headers.get("host", "").lower()
        if host not in hosts:
            return JSONResponse({"error": {"code": "invalid_host", "message": "Host is not permitted"}}, 400)
        length = request.headers.get("content-length")
        if length:
            try:
                if int(length) > MAX_BODY:
                    return JSONResponse({"error": {"code": "body_too_large", "message": "Request is too large"}}, 413)
            except ValueError:
                return JSONResponse({"error": {"code": "invalid_length", "message": "Invalid Content-Length"}}, 400)
        origin = request.headers.get("origin")
        expected = f"http://{host}"
        if origin and origin.rstrip("/") != expected:
            return JSONResponse({"error": {"code": "invalid_origin", "message": "Cross-origin requests are disabled"}}, 403)
        response = await call_next(request)
        response.headers.update({
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; object-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
            "Cache-Control": (
                "no-store" if request.url.path.startswith("/api/")
                else "public, max-age=31536000, immutable" if request.url.path.startswith("/assets/")
                else "no-cache"
            ),
        })
        return response

    async def remote(request: Request) -> str:
        return request.client.host if request.client else ""

    async def current_session(request: Request, papyrus_web_session: str | None = Cookie(default=None, alias=SESSION_COOKIE)) -> Session:
        session = runtime.auth.authenticate(papyrus_web_session, request.client.host if request.client else "", request.headers.get("user-agent", ""))
        if not session:
            raise HTTPException(401, detail={"code": "not_paired", "message": "Pair this browser from Papyrus Settings"})
        request.state.session_token = papyrus_web_session
        return session

    async def mutation_session(request: Request, session: Session = Depends(current_session), x_papyrus_csrf: str | None = Header(default=None)) -> Session:
        if not runtime.auth.validate_csrf(session, x_papyrus_csrf):
            raise HTTPException(403, detail={"code": "csrf_failed", "message": "Invalid request token"})
        if not runtime.leases.owns(session.session_id):
            raise HTTPException(423, detail={"code": "edit_lease_required", "message": "This browser does not own the edit lease"})
        return session

    @app.exception_handler(RevisionConflict)
    async def revision_conflict(_request, exc: RevisionConflict):
        return JSONResponse({"error": {"code": "revision_conflict", "message": str(exc), "details": {"expected": exc.expected, "actual": exc.actual}}}, 409)

    @app.exception_handler(KeyError)
    async def key_error(_request, exc: KeyError):
        return JSONResponse({"error": {"code": "not_found", "message": str(exc).strip("'")}}, 404)

    @app.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception):
        LOGGER.exception("Papyrus web request failed: %s", request.url.path, exc_info=exc)
        return JSONResponse({"error": {
            "code": "internal_error",
            "message": "Papyrus could not complete this request. Check the desktop diagnostics and retry.",
        }}, 500)

    @app.get("/api/v1/health")
    async def health():
        return {
            "status": "ok",
            "service": "papyrus-web-companion",
            "version": "1.0.0",
            "pairing_required": True,
        }

    @app.post("/api/v1/auth/pair")
    async def pair(body: PairRequest, request: Request, response: Response):
        session = runtime.auth.pair(body.token, body.device_name, request.client.host if request.client else "", request.headers.get("user-agent", ""))
        if not session:
            raise HTTPException(401, detail={"code": "pairing_failed", "message": "Pairing code is invalid, expired, used, or rate-limited"})
        response.set_cookie(SESSION_COOKIE, session.session_id, httponly=True, samesite="strict", secure=False, max_age=runtime.auth.session_ttl, path="/")
        runtime.state.publish("auth", "paired", None, {"device_name": session.device_name}, mutate=False)
        return {"csrf": session.csrf, "session": {"device_name": session.device_name, "expires_at": session.expires_at}}

    @app.post("/api/v1/auth/logout")
    async def logout(request: Request, response: Response, session: Session = Depends(current_session), x_papyrus_csrf: str | None = Header(default=None)):
        if not runtime.auth.validate_csrf(session, x_papyrus_csrf):
            raise HTTPException(403, "Invalid request token")
        runtime.leases.release(session.session_id)
        runtime.auth.revoke(session.session_id)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return {"ok": True}

    @app.get("/api/v1/session")
    async def session_info(session: Session = Depends(current_session)):
        return runtime.state.envelope({"device_name": session.device_name, "csrf": session.csrf, "lease": runtime.leases.status()})

    @app.post("/api/v1/lease/acquire")
    async def acquire_lease(session: Session = Depends(current_session), x_papyrus_csrf: str | None = Header(default=None)):
        if not runtime.auth.validate_csrf(session, x_papyrus_csrf):
            raise HTTPException(403, "Invalid request token")
        granted, status = runtime.leases.acquire(session.session_id, session.device_name)
        if not granted:
            raise HTTPException(423, detail={"code": "lease_held", "message": "Another browser is editing", "details": status})
        runtime.state.publish("lease", "acquired", None, status, mutate=False)
        return runtime.state.envelope(status)

    @app.post("/api/v1/lease/heartbeat")
    async def heartbeat(session: Session = Depends(current_session), x_papyrus_csrf: str | None = Header(default=None)):
        if not runtime.auth.validate_csrf(session, x_papyrus_csrf) or not runtime.leases.heartbeat(session.session_id):
            raise HTTPException(423, "Edit lease is not active")
        return runtime.state.envelope(runtime.leases.status())

    @app.post("/api/v1/lease/release")
    async def release(session: Session = Depends(current_session), x_papyrus_csrf: str | None = Header(default=None)):
        if not runtime.auth.validate_csrf(session, x_papyrus_csrf):
            raise HTTPException(403, "Invalid request token")
        runtime.dispatcher.invoke("project.save", {})
        runtime.leases.release(session.session_id)
        return runtime.state.envelope(runtime.leases.status())

    @app.get("/api/v1/snapshot/{domain}")
    async def snapshot(domain: str, request: Request, session: Session = Depends(current_session), source_id: str | None = None, workspace_id: int = 1, query: str = "", trace_id: str = ""):
        mapping = {
            "project": ("project.snapshot", {}), "sources": ("sources.list", {}),
            "annotations": ("annotations.list", {"source_id": source_id}), "notes": ("notes.list", {"source_id": source_id}),
            "tags": ("tags.list", {}), "workspaces": ("workspaces.list", {}),
            "workspace": ("workspaces.get", {"workspace_id": workspace_id}), "essays": ("essays.list", {}),
            "citations": ("citations.list", {}), "data": ("data.list", {}), "ai": ("ai.catalog", {}),
            "history": ("ai.history", {"target_id": query or "chat_dock"}), "jobs": ("ai.jobs", {}),
            "trace": ("ai.trace", {"trace_id": trace_id}), "research": ("research.session", {}), "plugins": ("plugins.list", {}),
        }
        if domain not in mapping or (domain in {"annotations", "notes"} and domain == "annotations" and not source_id):
            raise HTTPException(400, "Unknown snapshot or missing identifier")
        name, payload = mapping[domain]
        return runtime.state.envelope(runtime.dispatcher.invoke(name, payload))

    @app.get("/api/v1/source/{source_id}")
    async def source_details(source_id: str, session: Session = Depends(current_session)):
        return runtime.state.envelope(runtime.dispatcher.invoke("sources.details", {"source_id": source_id}))

    @app.get("/api/v1/source/{source_id}/media")
    async def source_media(source_id: str, request: Request, session: Session = Depends(current_session)):
        media = runtime.dispatcher.invoke("sources.resolve_media", {"source_id": source_id})
        return ranged_file(request, media["path"], media["mime_type"], media["filename"])

    @app.get("/api/v1/source/{source_id}/page/{page_num}/text")
    async def page_text(source_id: str, page_num: int, session: Session = Depends(current_session)):
        return runtime.state.envelope(runtime.dispatcher.invoke("pdf.page_text", {"source_id": source_id, "page_num": page_num}))

    @app.get("/api/v1/audio/{filename}")
    async def generated_audio(filename: str, request: Request, session: Session = Depends(current_session)):
        media = runtime.dispatcher.invoke("tools.resolve_audio", {"filename": filename})
        return ranged_file(request, media["path"], media["mime_type"], media["filename"])

    @app.get("/api/v1/source/{source_id}/search")
    async def pdf_search(source_id: str, q: str, session: Session = Depends(current_session)):
        return runtime.state.envelope(runtime.dispatcher.invoke("pdf.search", {"source_id": source_id, "query": q}))

    @app.post("/api/v1/command/{domain}/{operation}")
    async def command(domain: str, operation: str, body: CommandRequest, request: Request, session: Session = Depends(current_session), x_papyrus_csrf: str | None = Header(default=None)):
        name = f"{domain}.{operation}"
        is_read = name in READ_COMMANDS
        cached = runtime.idempotency_get(session.session_id, body.command_id)
        if cached is not None:
            return cached
        if not is_read:
            if not runtime.auth.validate_csrf(session, x_papyrus_csrf):
                raise HTTPException(403, detail={"code": "csrf_failed", "message": "Invalid request token"})
            if not runtime.leases.owns(session.session_id):
                raise HTTPException(423, detail={"code": "edit_lease_required", "message": "Acquire the edit lease first"})
            runtime.state.check(body.expected_revision)
        result = runtime.dispatcher.invoke(name, body.payload)
        if not is_read:
            runtime.state.publish(domain, operation, body.payload.get("id") or body.payload.get("source_id"), {"command_id": body.command_id}, mutate=True)
        envelope = runtime.state.envelope(result)
        runtime.idempotency_put(session.session_id, body.command_id, envelope)
        return envelope

    @app.post("/api/v1/uploads")
    async def upload(request: Request, file: UploadFile = File(...), session: Session = Depends(mutation_session)):
        original = Path(file.filename or "upload").name
        safe = SAFE_NAME.sub("_", original).strip(". ") or "upload"
        ext = Path(safe).suffix.lower()
        if ext not in {".pdf", ".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}:
            raise HTTPException(415, "Only PDF and supported video uploads are accepted")
        assets = Path(runtime.dispatcher.invoke("project.assets_dir", {})["path"])
        assets.mkdir(parents=True, exist_ok=True)
        destination = assets / safe
        if destination.exists():
            destination = assets / f"{destination.stem}-{secrets.token_hex(4)}{destination.suffix}"
        fd, temp_name = tempfile.mkstemp(prefix=".upload-", dir=assets)
        total = 0
        try:
            with os.fdopen(fd, "wb") as output:
                while chunk := await file.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_UPLOAD:
                        raise HTTPException(413, "Upload exceeds 250 MiB")
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            if not _valid_upload_signature(temp_name, ext):
                raise HTTPException(415, "File contents do not match the selected PDF/video type")
            os.replace(temp_name, destination)
            result = runtime.dispatcher.invoke("sources.add_uploaded", {"path": str(destination)})
            runtime.state.publish("sources", "uploaded", result.get("id"), {"filename": destination.name}, mutate=True)
            return runtime.state.envelope(result)
        except Exception:
            if os.path.exists(temp_name):
                os.remove(temp_name)
            if destination.exists() and destination.stat().st_size == total:
                try: destination.unlink()
                except OSError: pass
            raise
        finally:
            await file.close()

    @app.websocket("/api/v1/events")
    async def events(websocket: WebSocket):
        host = websocket.headers.get("host", "").lower()
        origin = websocket.headers.get("origin")
        if host not in hosts or (origin and origin.rstrip("/") != f"http://{host}"):
            await websocket.close(code=1008)
            return
        raw_cookie = websocket.cookies.get(SESSION_COOKIE)
        client_ip = websocket.client.host if websocket.client else ""
        session = runtime.auth.authenticate(raw_cookie, client_ip, websocket.headers.get("user-agent", ""))
        if not session:
            await websocket.close(code=4401)
            return
        await websocket.accept()
        sequence = int(websocket.query_params.get("after", "0") or 0)
        try:
            while True:
                items = await asyncio.to_thread(runtime.state.wait_after, sequence, 20.0)
                if items:
                    for item in items:
                        await websocket.send_json(item)
                        sequence = max(sequence, item["sequence"])
                else:
                    await websocket.send_json({"type": "heartbeat", "sequence": sequence})
        except WebSocketDisconnect:
            pass

    @app.get("/plugins/{plugin_id}/{filename}")
    async def plugin_module(plugin_id: str, filename: str, session: Session = Depends(current_session)):
        registry = getattr(runtime, "extensions", None)
        extension = registry.get(plugin_id) if registry else None
        if not extension or filename != extension.entrypoint.name:
            raise HTTPException(404, "Plugin web module not found")
        return FileResponse(extension.entrypoint, media_type="text/javascript")

    if static_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

        @app.get("/{path:path}")
        async def spa(path: str):
            candidate = (static_dir / path).resolve()
            if path and candidate.is_file() and static_dir.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(static_dir / "index.html")
    return app
