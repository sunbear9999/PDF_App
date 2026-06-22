from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class WebExtension:
    plugin_id: str
    label: str
    entrypoint: Path
    navigation_mount: str = "tools"
    capabilities: tuple[str, ...] = field(default_factory=tuple)
    routes_factory: Callable | None = None


class WebExtensionRegistry:
    """Opt-in surface for plugins that ship a browser implementation."""
    def __init__(self):
        self._extensions: dict[str, WebExtension] = {}

    def register(self, extension: WebExtension) -> None:
        path = extension.entrypoint.resolve()
        if not path.is_file():
            raise ValueError(f"Web extension entrypoint does not exist: {path}")
        self._extensions[extension.plugin_id] = extension

    def unregister(self, plugin_id: str) -> None:
        self._extensions.pop(plugin_id, None)

    def manifests(self) -> list[dict]:
        return [
            {
                "plugin_id": ext.plugin_id,
                "label": ext.label,
                "navigation_mount": ext.navigation_mount,
                "capabilities": list(ext.capabilities),
                "module_url": f"/plugins/{ext.plugin_id}/{ext.entrypoint.name}",
            }
            for ext in self._extensions.values()
        ]

    def get(self, plugin_id: str) -> WebExtension | None:
        return self._extensions.get(plugin_id)
