from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from typing import TYPE_CHECKING, List

from core.plugins.base_plugins import PapyrusPlugin

if TYPE_CHECKING:
    from core.papyrus_core import PapyrusCore


def _plugins_dir() -> str:
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, "plugins")
    return os.path.join(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")), "plugins")


def load_plugins(core: "PapyrusCore") -> List[PapyrusPlugin]:
    """Scan the plugins/ directory and register every discovered PapyrusPlugin."""
    plugins_dir = _plugins_dir()
    if not os.path.isdir(plugins_dir):
        return []

    loaded: List[PapyrusPlugin] = []

    for entry in sorted(os.listdir(plugins_dir)):
        plugin_path = os.path.join(plugins_dir, entry, "plugin.py")
        if not os.path.isfile(plugin_path):
            continue

        module_name = f"plugins.{entry}.plugin"
        try:
            spec = importlib.util.spec_from_file_location(module_name, plugin_path)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

            plugin_cls = getattr(module, "Plugin", None)
            if plugin_cls is None:
                print(f"[PluginLoader] {entry}: no 'Plugin' class found, skipping.")
                continue

            instance = plugin_cls()
            if not isinstance(instance, PapyrusPlugin):
                print(f"[PluginLoader] {entry}: 'Plugin' does not implement PapyrusPlugin, skipping.")
                continue

            core.register_plugin(instance)
            loaded.append(instance)
            print(f"[PluginLoader] Loaded plugin: {instance.name} ({instance.plugin_id}) v{instance.version}")

        except Exception as exc:
            print(f"[PluginLoader] Failed to load plugin '{entry}': {exc}")

    return loaded
