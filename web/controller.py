from __future__ import annotations

import io
from typing import ClassVar

import qrcode
from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtWidgets import QApplication, QMainWindow

from .bridge.commands import WebCommands
from .bridge.desktop_guard import DesktopEditGuard
from .bridge.dispatcher import QtCommandDispatcher
from .bridge.events import EventGateway
from .bridge.lease import EditLeaseManager
from .bridge.state import WebState
from .plugins.registry import WebExtensionRegistry
from .security import AuthManager, private_interfaces
from .server.runtime import ServerRuntime


class WebCompanionController(QObject):
    """Application-lifetime owner of the optional local web server."""
    _instance: ClassVar["WebCompanionController | None"] = None
    status_changed = Signal()

    @classmethod
    def for_context(cls, app_context, main_window: QMainWindow | None = None):
        if cls._instance is None:
            cls._instance = cls(app_context, main_window)
        return cls._instance

    def __init__(self, app_context, main_window: QMainWindow | None = None):
        super().__init__(QApplication.instance())
        self.ctx = app_context
        self.state = WebState()
        self.auth = AuthManager()
        self.dispatcher = QtCommandDispatcher(self)
        self.commands = WebCommands(app_context, self.state)
        self.commands.install(self.dispatcher)
        self.events = EventGateway(app_context.bus, self.state, self, sanitizer=self.commands.public_data)
        research = getattr(app_context, "research_agent_service", None)
        if research:
            research.session_updated.connect(lambda *args: self.events.relay_external("research", "session", *args, mutates=True))
            research.status_changed.connect(lambda *args: self.events.relay_external("research", "status", *args))
            research.checkpoint_requested.connect(lambda *args: self.events.relay_external("research", "checkpoint", *args))
            research.error.connect(lambda *args: self.events.relay_external("research", "error", *args))
        self.extensions = WebExtensionRegistry()
        self.dispatcher.register("plugins.list", lambda _payload: self.extensions.manifests())
        self.main_window = main_window or self._find_main_window()
        self.guard = DesktopEditGuard(self.main_window, self.take_back_control) if self.main_window else None
        self.dispatcher.register("desktop.lock", self._set_desktop_lock)
        self.leases = EditLeaseManager(on_change=self._lease_changed)
        self.runtime: ServerRuntime | None = None
        self.host = "127.0.0.1"
        self.port = 8765
        app = QApplication.instance()
        if app:
            app.aboutToQuit.connect(self.stop)

    def _find_main_window(self):
        viewer = getattr(self.ctx, "viewer", None)
        if viewer:
            window = viewer.window()
            if isinstance(window, QMainWindow):
                return window
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, QMainWindow):
                return widget
        return None

    @property
    def running(self) -> bool:
        return bool(self.runtime and self.runtime.running)

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    @property
    def pairing_url(self) -> str:
        return f"{self.url}#/pair?token={self.auth.pair_token}"

    def qr_png(self) -> bytes:
        image = qrcode.make(self.pairing_url)
        output = io.BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()

    @Slot(str, int)
    def start(self, host: str = "127.0.0.1", port: int = 8765) -> None:
        if self.running:
            return
        allowed = {item["address"] for item in private_interfaces()}
        if host not in allowed:
            raise ValueError("Select a concrete loopback or private IPv4 interface")
        if not 1024 <= int(port) <= 65535:
            raise ValueError("Port must be between 1024 and 65535")
        self.host, self.port = host, int(port)
        self.auth.rotate_pairing()
        self.runtime = ServerRuntime(host=self.host, port=self.port, dispatcher=self.dispatcher, state=self.state, auth=self.auth, leases=self.leases, extensions=self.extensions)
        try:
            self.runtime.start()
        except Exception:
            self.runtime = None
            raise
        self.status_changed.emit()

    @Slot()
    def stop(self) -> None:
        if self.runtime:
            try:
                if getattr(self.ctx.project_manager, "project_filepath", None):
                    self.dispatcher.invoke("project.save", {}, timeout=20)
            except Exception:
                pass
            self.runtime.stop()
            self.runtime = None
        self.leases.release(force=True)
        if self.guard:
            self.guard.set_locked(False)
        self.status_changed.emit()

    @Slot()
    def rotate_pairing(self) -> None:
        self.auth.rotate_pairing()
        self.status_changed.emit()

    @Slot()
    def take_back_control(self) -> None:
        try:
            if getattr(self.ctx.project_manager, "project_filepath", None):
                self.dispatcher.invoke("project.save", {}, timeout=20)
        finally:
            self.leases.release(force=True)
            self.state.publish("lease", "revoked_by_desktop", None, {}, mutate=False)
            self.status_changed.emit()

    def revoke_session(self, session_prefix: str) -> None:
        self.auth.revoke_prefix(session_prefix)
        self.status_changed.emit()

    def _lease_changed(self, locked: bool, device_name: str) -> None:
        self.dispatcher.invoke("desktop.lock", {"locked": locked, "device_name": device_name})
        self.status_changed.emit()

    def _set_desktop_lock(self, payload):
        if self.guard:
            self.guard.set_locked(bool(payload.get("locked")), str(payload.get("device_name") or ""))
        return {"locked": bool(payload.get("locked"))}

    def diagnostics(self) -> dict:
        return {
            "running": self.running,
            "url": self.url if self.running else None,
            "interface": self.host,
            "port": self.port,
            "pair_code": self.auth.pair_code if self.running else None,
            "pair_expires": self.auth.pair_expires if self.running else None,
            "sessions": self.auth.list_sessions(),
            "lease": self.leases.status(),
            "revision": self.state.revision,
        }
