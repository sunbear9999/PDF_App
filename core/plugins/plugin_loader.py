from __future__ import annotations

import importlib.util
import os
import sys
from collections import deque
from typing import TYPE_CHECKING, Dict, List, Tuple

from core.plugins.base_plugins import PapyrusPlugin

if TYPE_CHECKING:
    from core.papyrus_core import PapyrusCore
    from core.plugins.papyrus_api import PapyrusAPI


class PluginLoadError(RuntimeError):
    """Raised when plugin discovery or dependency resolution fails."""
    pass


def _plugins_dir() -> str:
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, "plugins")
    return os.path.join(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")), "plugins")


def _discover_plugins(plugins_dir: str) -> Dict[str, PapyrusPlugin]:
    """Scan plugins/ and return {plugin_id: instance} for each valid plugin."""
    discovered: Dict[str, PapyrusPlugin] = {}

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

            plugin_id = instance.plugin_id
            if plugin_id in discovered:
                print(f"[PluginLoader] Duplicate plugin_id '{plugin_id}' in {entry}, skipping.")
                continue

            discovered[plugin_id] = instance

        except Exception as exc:
            print(f"[PluginLoader] Failed to load plugin '{entry}': {exc}")

    return discovered


def _topological_sort(plugins_by_id: Dict[str, PapyrusPlugin]) -> List[PapyrusPlugin]:
    """
    Sort plugins by dependency order using Kahn's algorithm.

    Plugins with no dependencies come first; each plugin is guaranteed to
    be loaded after all plugins it declares in ``dependencies``.

    Raises PluginLoadError on missing or circular dependencies.
    """
    in_degree: Dict[str, int] = {pid: 0 for pid in plugins_by_id}
    dependents: Dict[str, List[str]] = {pid: [] for pid in plugins_by_id}

    for plugin in plugins_by_id.values():
        for dep_id in getattr(plugin, "dependencies", []):
            if dep_id not in plugins_by_id:
                raise PluginLoadError(
                    f"Plugin '{plugin.plugin_id}' requires '{dep_id}', "
                    f"which is not installed or failed to load."
                )
            dependents[dep_id].append(plugin.plugin_id)
            in_degree[plugin.plugin_id] += 1

    queue = deque(pid for pid, degree in in_degree.items() if degree == 0)
    result: List[PapyrusPlugin] = []

    while queue:
        pid = queue.popleft()
        result.append(plugins_by_id[pid])
        for dependent_pid in dependents[pid]:
            in_degree[dependent_pid] -= 1
            if in_degree[dependent_pid] == 0:
                queue.append(dependent_pid)

    if len(result) != len(plugins_by_id):
        cycle_ids = [pid for pid, deg in in_degree.items() if deg > 0]
        raise PluginLoadError(
            f"Circular plugin dependency detected among: {', '.join(cycle_ids)}"
        )

    return result


def _tag_plugin_specs(registry, plugin_id: str, before_count: dict) -> None:
    """
    Tag newly-added extension specs with plugin_id so hot-reload can remove them.

    *before_count* maps list-attribute-name → count before registration.
    Any spec in those lists beyond the previous count gets plugin_id set.
    """
    list_attrs = [
        "_toolbar_buttons", "_research_tabs", "_ai_renderers", "_extra_docks",
        "_menu_items", "_main_toolbar_buttons", "_shortcuts", "_commands",
        "_workspace_context_menu_items",
    ]
    for attr in list_attrs:
        lst = getattr(registry, attr, [])
        prev = before_count.get(attr, len(lst))
        for spec in lst[prev:]:
            if hasattr(spec, "plugin_id"):
                spec.plugin_id = plugin_id


def load_plugins(core: "PapyrusCore") -> List[PapyrusPlugin]:
    """
    Discover, sort by dependency, and register all plugins.

    Each plugin receives its own PapyrusAPI instance. The (plugin, api) pairs
    are stored on ``core._loaded_plugins`` for later cleanup.
    """
    from core.plugins.papyrus_api import PapyrusAPI

    plugins_dir = _plugins_dir()
    if not os.path.isdir(plugins_dir):
        return []

    if not hasattr(core, "_loaded_plugins"):
        core._loaded_plugins: List[Tuple[PapyrusPlugin, "PapyrusAPI"]] = []
    if not hasattr(core, "_custom_services"):
        core._custom_services: Dict[str, object] = {}

    try:
        discovered = _discover_plugins(plugins_dir)
        ordered = _topological_sort(discovered)
    except PluginLoadError as exc:
        print(f"[PluginLoader] {exc}")
        return []

    loaded: List[PapyrusPlugin] = []

    for instance in ordered:
        api = PapyrusAPI(core, plugin_id=instance.plugin_id)
        try:
            # Primary lifecycle hook
            if hasattr(instance, "on_load") and callable(instance.on_load):
                instance.on_load(api)

            # Backward-compat: on_register now receives PapyrusAPI, not PapyrusCore
            if hasattr(instance, "on_register") and callable(instance.on_register):
                instance.on_register(api)

            # Snapshot counts before registration so we can tag new specs
            reg = core.plugin_extension_registry
            before = {
                attr: len(getattr(reg, attr, []))
                for attr in [
                    "_toolbar_buttons", "_research_tabs", "_ai_renderers", "_extra_docks",
                    "_menu_items", "_main_toolbar_buttons", "_shortcuts", "_commands",
                    "_workspace_context_menu_items",
                ]
            }

            core.register_plugin(instance, api)

            # Tag all newly added specs with this plugin's id
            _tag_plugin_specs(reg, instance.plugin_id, before)

            core._loaded_plugins.append((instance, api))
            loaded.append(instance)
            print(f"[PluginLoader] Loaded: {instance.name} ({instance.plugin_id}) v{instance.version}")

        except Exception as exc:
            print(f"[PluginLoader] Failed to register plugin '{instance.plugin_id}': {exc}")
            api._cleanup()

    return loaded


def reload_plugin(core: "PapyrusCore", plugin_id: str) -> bool:
    """
    Hot-reload a single plugin by ID. Dev mode only.

    Unloads the plugin, removes its extension specs, invalidates the module
    cache, re-discovers, and re-runs the full load lifecycle.

    Returns True on success, False if the plugin was not found or failed.
    """
    from core.plugins.papyrus_api import PapyrusAPI

    target = next(
        ((p, a) for p, a in getattr(core, "_loaded_plugins", []) if p.plugin_id == plugin_id),
        None,
    )
    if target is None:
        print(f"[HotReload] Plugin '{plugin_id}' not currently loaded.")
        return False

    plugin, api = target

    # 1. Unload
    try:
        if hasattr(plugin, "on_unload") and callable(plugin.on_unload):
            plugin.on_unload()
    except Exception as exc:
        print(f"[HotReload] on_unload error for '{plugin_id}': {exc}")
    api._cleanup()

    # 2. Remove extension specs contributed by this plugin
    core.plugin_extension_registry.remove_plugin_specs(plugin_id)

    # 3. Remove from loaded list
    core._loaded_plugins = [(p, a) for p, a in core._loaded_plugins if p.plugin_id != plugin_id]

    # 4. Invalidate module cache
    module_name = f"plugins.{plugin_id}.plugin"
    sys.modules.pop(module_name, None)

    # 5. Re-discover just this plugin
    plugins_dir = _plugins_dir()
    discovered = _discover_plugins(plugins_dir)
    new_instance = discovered.get(plugin_id)
    if new_instance is None:
        print(f"[HotReload] Plugin '{plugin_id}' not found after re-scan.")
        return False

    # 6. Re-run lifecycle
    new_api = PapyrusAPI(core, plugin_id=plugin_id)
    try:
        if hasattr(new_instance, "on_load") and callable(new_instance.on_load):
            new_instance.on_load(new_api)
        if hasattr(new_instance, "on_register") and callable(new_instance.on_register):
            new_instance.on_register(new_api)

        reg = core.plugin_extension_registry
        before = {
            attr: len(getattr(reg, attr, []))
            for attr in [
                "_toolbar_buttons", "_research_tabs", "_ai_renderers", "_extra_docks",
                "_menu_items", "_main_toolbar_buttons", "_shortcuts", "_commands",
                "_workspace_context_menu_items",
            ]
        }

        core.register_plugin(new_instance, new_api)
        _tag_plugin_specs(reg, plugin_id, before)

        core._loaded_plugins.append((new_instance, new_api))
        print(f"[HotReload] Reloaded: {new_instance.name} ({plugin_id}) v{new_instance.version}")
        return True

    except Exception as exc:
        print(f"[HotReload] Reload failed for '{plugin_id}': {exc}")
        new_api._cleanup()
        return False
