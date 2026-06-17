"""
core/plugins/testing.py

MockPapyrusAPI and supporting stubs for unit testing plugins without starting
the full Qt application or loading PapyrusCore.

Usage::

    from core.plugins.testing import MockPapyrusAPI

    def test_my_plugin():
        api = MockPapyrusAPI()
        plugin = Plugin()
        plugin.on_load(api)
        api.assert_subscribed("document_added")
        api.assert_notified(level="info")
        api.assert_service_registered("my_plugin.service")
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple


class MockPluginConfig:
    """In-memory substitute for PluginConfig."""

    def __init__(self) -> None:
        self._data: Dict[str, Any] = {}

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def get_all(self) -> Dict[str, Any]:
        return dict(self._data)

    def reset(self) -> None:
        self._data.clear()


class MockEventBus:
    """Records subscriptions and emissions; triggers slots on emit."""

    def __init__(self) -> None:
        self._subscriptions: Dict[str, List[Callable]] = {}
        self.emissions: List[Tuple[str, tuple]] = []

    def __getattr__(self, name: str):
        class _Signal:
            def connect(inner, slot: Callable) -> None:
                self._subscriptions.setdefault(name, []).append(slot)

            def emit(inner, *args) -> None:
                self.emissions.append((name, args))
                for slot in list(self._subscriptions.get(name, [])):
                    try:
                        slot(*args)
                    except Exception:
                        pass

        return _Signal()

    def get_subscriptions(self, signal_name: str) -> List[Callable]:
        return list(self._subscriptions.get(signal_name, []))


class MockProjectManager:
    """Minimal project manager stub."""

    def __init__(self) -> None:
        self.project_filepath: Optional[str] = None
        self.dirty_docs: List[str] = []

    def get_metadata(self, key: str, default: str = "") -> str:
        return default

    def get_documents(self) -> List[dict]:
        return []


class MockPluginExtensionRegistry:
    """Records all add_*() calls for assertion."""

    def __init__(self) -> None:
        self._calls: List[Tuple[str, tuple, dict]] = []

    def __getattr__(self, name: str):
        def _recorder(*args, **kwargs):
            self._calls.append((name, args, kwargs))
        return _recorder

    def get_calls(self, method_name: str) -> List[tuple]:
        return [args for n, args, _ in self._calls if n == method_name]

    def was_called(self, method_name: str) -> bool:
        return any(n == method_name for n, _, _ in self._calls)


class MockPapyrusAPI:
    """
    Full in-memory substitute for PapyrusAPI.

    Tracks every call to subscribe(), emit(), notify(), register_service(),
    and register_gui_extensions() with assertion helpers for test code.
    """

    def __init__(self) -> None:
        self._subscriptions: List[Tuple[str, Callable]] = []
        self._notifications: List[Tuple[str, str, int]] = []
        self._services: Dict[str, Any] = {}

        # Public attribute surface matching PapyrusAPI
        self.config = MockPluginConfig()
        self.event_bus = MockEventBus()
        self.project_manager = MockProjectManager()
        self.gui_extensions = MockPluginExtensionRegistry()

        # Lightweight stubs for other services
        self.llm = None
        self.blueprints = _NullRegistry()
        self.workspace_tools = _NullRegistry()
        self.ontology = _NullRegistry()
        self.workspace_node_types = _NullRegistry()
        self.workflow_node_types = _NullRegistry()

    # ----------------------------------------------------------------
    # PapyrusAPI surface
    # ----------------------------------------------------------------

    def subscribe(self, signal_name: str, slot: Callable) -> None:
        self._subscriptions.append((signal_name, slot))
        self.event_bus._subscriptions.setdefault(signal_name, []).append(slot)

    def emit(self, signal_name: str, *args) -> None:
        self.event_bus.emissions.append((signal_name, args))

    def notify(self, message: str, level: str = "info", duration: int = 3000) -> None:
        self._notifications.append((message, level, duration))

    def register_service(self, service_id: str, instance: Any) -> None:
        self._services[service_id] = instance

    def get_service(self, service_id: str) -> Optional[Any]:
        return self._services.get(service_id)

    def require_service(self, service_id: str, expected_type: Optional[type] = None) -> Any:
        service = self.get_service(service_id)
        if service is None:
            raise RuntimeError(f"Required service '{service_id}' not registered in mock.")
        if expected_type and not isinstance(service, expected_type):
            raise TypeError(
                f"Service '{service_id}' is {type(service).__name__}, "
                f"expected {expected_type.__name__}."
            )
        return service

    def on_project_open(self, callback: Callable[[str], None]) -> None:
        self.subscribe("project_loaded", lambda e, p: callback(""))

    def on_project_close(self, callback: Callable[[], None]) -> None:
        self.subscribe("project_clearing_started", lambda e, p: callback())

    def reload_plugin(self, plugin_id: str) -> bool:
        return False

    # ----------------------------------------------------------------
    # Simulation helpers
    # ----------------------------------------------------------------

    def simulate_signal(self, signal_name: str, *args) -> None:
        """Fire a subscribed slot as if the EventBus emitted signal_name."""
        for slot in list(self.event_bus._subscriptions.get(signal_name, [])):
            try:
                slot(*args)
            except Exception:
                pass

    # ----------------------------------------------------------------
    # Assertion helpers
    # ----------------------------------------------------------------

    def assert_subscribed(self, signal_name: str) -> None:
        names = [n for n, _ in self._subscriptions]
        assert signal_name in names, (
            f"Expected subscription to '{signal_name}'. "
            f"Actual: {names}"
        )

    def assert_not_subscribed(self, signal_name: str) -> None:
        names = [n for n, _ in self._subscriptions]
        assert signal_name not in names, (
            f"Expected NO subscription to '{signal_name}', but found one."
        )

    def assert_notified(
        self,
        message: Optional[str] = None,
        level: Optional[str] = None,
    ) -> None:
        assert self._notifications, "Expected at least one notification, but none were emitted."
        if message is not None:
            msgs = [m for m, _, _ in self._notifications]
            assert any(message in m for m in msgs), (
                f"Expected notification containing '{message}'. Got: {msgs}"
            )
        if level is not None:
            levels = [lv for _, lv, _ in self._notifications]
            assert level in levels, (
                f"Expected notification with level='{level}'. Got: {levels}"
            )

    def assert_not_notified(self) -> None:
        assert not self._notifications, (
            f"Expected no notifications, but got: {self._notifications}"
        )

    def assert_service_registered(self, service_id: str) -> None:
        assert service_id in self._services, (
            f"Expected service '{service_id}' to be registered. "
            f"Registered: {list(self._services.keys())}"
        )

    def assert_gui_extension_registered(self, method_name: str) -> None:
        assert self.gui_extensions.was_called(method_name), (
            f"Expected gui_extensions.{method_name}() to have been called."
        )


class _NullRegistry:
    """Silently discards all registry calls (register, add_*, etc.)."""

    def __getattr__(self, name: str):
        return lambda *a, **kw: None
