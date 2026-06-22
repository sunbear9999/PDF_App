from __future__ import annotations

import threading
import time
from collections import OrderedDict

import uvicorn


class ServerRuntime:
    def __init__(self, *, host, port, dispatcher, state, auth, leases, extensions=None, tls=None):
        self.host = host
        self.port = port
        self.dispatcher = dispatcher
        self.state = state
        self.auth = auth
        self.leases = leases
        self.extensions = extensions
        # Reserved for administrator-provided certificates; the settings UI
        # intentionally exposes trusted-LAN HTTP only in v1.
        self.tls = dict(tls or {})
        self._server = None
        self._thread = None
        self._idempotency: OrderedDict[tuple[str, str], tuple[float, dict]] = OrderedDict()
        self._idempotency_lock = threading.RLock()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and self._server and not self._server.should_exit)

    def start(self):
        if self.running:
            return
        from .app import create_app
        config = uvicorn.Config(
            create_app(self), host=self.host, port=self.port, log_level="warning",
            access_log=False, server_header=False,
            ssl_certfile=self.tls.get("certfile"), ssl_keyfile=self.tls.get("keyfile"),
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, name="PapyrusWebServer", daemon=True)
        self._thread.start()
        deadline = time.time() + 5
        while time.time() < deadline and not self._server.started and self._thread.is_alive():
            time.sleep(0.02)
        if not self._server.started:
            raise RuntimeError(f"Could not start web server on {self.host}:{self.port}")

    def stop(self):
        self.leases.release(force=True)
        self.auth.revoke_all()
        if self._server:
            self._server.should_exit = True
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._server = None
        self._thread = None

    def idempotency_get(self, session_id: str, command_id: str):
        with self._idempotency_lock:
            item = self._idempotency.get((session_id, command_id))
            if item and time.time() - item[0] < 3600:
                return item[1]
            return None

    def idempotency_put(self, session_id: str, command_id: str, value: dict):
        with self._idempotency_lock:
            self._idempotency[(session_id, command_id)] = (time.time(), value)
            while len(self._idempotency) > 1000:
                self._idempotency.popitem(last=False)
