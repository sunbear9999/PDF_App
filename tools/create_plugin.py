#!/usr/bin/env python3
"""
tools/create_plugin.py

Plugin scaffolding CLI for Papyrus.

Usage::

    python tools/create_plugin.py my_plugin "My Plugin Name"
    python tools/create_plugin.py my_plugin "My Plugin Name" --with-dock --with-research-tab

Generates:
  plugins/my_plugin/__init__.py
  plugins/my_plugin/plugin.py
  tests/test_my_plugin_plugin.py   (if tests/ dir exists)
"""
import argparse
import os
import sys
import textwrap


PLUGIN_TEMPLATE = '''\
"""
plugins/{plugin_id}/plugin.py

{plugin_name} — Papyrus plugin.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.plugins.papyrus_api import PapyrusAPI
    from core.registries import BlueprintRegistry, WorkspaceAIToolRegistry, OntologyRegistry
    from core.plugins.extension_registry import PluginExtensionRegistry
    from core.plugins.base_plugins import PluginDockSpec


class Plugin:
    plugin_id = "{plugin_id}"
    name = "{plugin_name}"
    version = "0.1.0"
    dependencies = []  # list of plugin_ids this plugin requires

    def on_load(self, api: "PapyrusAPI") -> None:
        """
        Primary lifecycle hook — API is fully available.

        - Subscribe to events: api.subscribe("document_added", self._on_doc)
        - Access services:     api.project_manager, api.llm, api.config
        - Notify the user:     api.notify("Plugin loaded!", level="info")
        """
        api.notify(f"{plugin_name} loaded.", level="info", duration=2000)

    def register_blueprints(self, registry: "BlueprintRegistry") -> None:
        """Register custom AI action blueprints."""
        pass

    def register_workspace_tools(self, registry: "WorkspaceAIToolRegistry") -> None:
        """Register workspace AI context-menu tools."""
        pass

    def register_ontology_types(self, registry: "OntologyRegistry") -> None:
        """Register custom entity / relation types."""
        pass

    def get_dock_spec(self) -> "PluginDockSpec | None":
        """Return a PluginDockSpec to add a dock window, or None."""
        return None

    def register_gui_extensions(self, registry: "PluginExtensionRegistry") -> None:
        """Register toolbar buttons, research tabs, dock specs, menu items, etc."""
        pass

    def on_unload(self) -> None:
        """Release resources on app close or hot-reload."""
        pass
'''

PLUGIN_WITH_DOCK_EXTRA = '''\

    def get_dock_spec(self) -> "PluginDockSpec | None":
        from core.plugins.base_plugins import PluginDockSpec
        return PluginDockSpec(
            id="{plugin_id}_dock",
            menu_name="🔌 {plugin_name}",
            area="right",
            is_singleton=True,
            factory=self._make_dock_widget,
        )

    def _make_dock_widget(self, app_context: Any):
        from PySide6.QtWidgets import QLabel
        return QLabel("🔌 {plugin_name} dock placeholder")
'''

PLUGIN_WITH_RESEARCH_TAB_EXTRA = '''\

    def register_gui_extensions(self, registry: "PluginExtensionRegistry") -> None:
        from core.plugins.extension_registry import ResearchTabSpec
        registry.add_research_tab(ResearchTabSpec(
            tab_id="{plugin_id}_tab",
            label="🔌 {plugin_name}",
            factory=self._make_research_tab,
            target_id="{plugin_id}_output",  # AI output router target
            position=50,
        ))

    def _make_research_tab(self, app_context: Any):
        from PySide6.QtWidgets import QLabel
        return QLabel("🔌 {plugin_name} research tab placeholder")
'''

TEST_TEMPLATE = '''\
"""
tests/test_{plugin_id}_plugin.py

Unit tests for the {plugin_name} plugin.
"""
import pytest
from core.plugins.testing import MockPapyrusAPI


def make_plugin():
    from plugins.{plugin_id}.plugin import Plugin
    return Plugin()


def test_on_load_notifies():
    api = MockPapyrusAPI()
    plugin = make_plugin()
    plugin.on_load(api)
    api.assert_notified(level="info")


def test_register_blueprints_does_not_crash():
    api = MockPapyrusAPI()
    plugin = make_plugin()
    plugin.register_blueprints(api.blueprints)


def test_on_unload_does_not_crash():
    api = MockPapyrusAPI()
    plugin = make_plugin()
    plugin.on_load(api)
    plugin.on_unload()
'''

INIT_TEMPLATE = '# plugins/{plugin_id}/__init__.py\n'


def main():
    parser = argparse.ArgumentParser(
        description="Scaffold a new Papyrus plugin.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              python tools/create_plugin.py my_plugin "My Plugin"
              python tools/create_plugin.py my_plugin "My Plugin" --with-dock --with-research-tab
        """),
    )
    parser.add_argument("plugin_id", help="Snake_case plugin identifier (e.g. my_plugin)")
    parser.add_argument("plugin_name", help="Human-readable name (e.g. 'My Plugin')")
    parser.add_argument("--with-dock", action="store_true", help="Add a dock window scaffold")
    parser.add_argument("--with-research-tab", action="store_true", help="Add a Research Assistant tab scaffold")
    parser.add_argument(
        "--root",
        default=None,
        help="Project root directory (defaults to the parent of the tools/ directory)",
    )
    args = parser.parse_args()

    plugin_id: str = args.plugin_id.lower().replace("-", "_")
    plugin_name: str = args.plugin_name

    root = args.root or os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    plugin_dir = os.path.join(root, "plugins", plugin_id)
    tests_dir = os.path.join(root, "tests")

    if os.path.exists(plugin_dir):
        print(f"[create_plugin] ERROR: plugins/{plugin_id}/ already exists.")
        sys.exit(1)

    os.makedirs(plugin_dir, exist_ok=True)

    # __init__.py
    _write(os.path.join(plugin_dir, "__init__.py"), INIT_TEMPLATE.format(plugin_id=plugin_id))

    # plugin.py — assemble based on flags
    plugin_body = PLUGIN_TEMPLATE.format(plugin_id=plugin_id, plugin_name=plugin_name)

    if args.with_dock:
        # Replace the get_dock_spec stub with the full version
        dock_extra = PLUGIN_WITH_DOCK_EXTRA.format(plugin_id=plugin_id, plugin_name=plugin_name)
        plugin_body = plugin_body.replace(
            '    def get_dock_spec(self) -> "PluginDockSpec | None":\n        """Return a PluginDockSpec to add a dock window, or None."""\n        return None\n',
            dock_extra.lstrip("\n"),
        )

    if args.with_research_tab:
        research_extra = PLUGIN_WITH_RESEARCH_TAB_EXTRA.format(plugin_id=plugin_id, plugin_name=plugin_name)
        plugin_body = plugin_body.replace(
            '    def register_gui_extensions(self, registry: "PluginExtensionRegistry") -> None:\n        """Register toolbar buttons, research tabs, dock specs, menu items, etc."""\n        pass\n',
            research_extra.lstrip("\n"),
        )

    _write(os.path.join(plugin_dir, "plugin.py"), plugin_body)

    # Test file
    if os.path.isdir(tests_dir):
        test_path = os.path.join(tests_dir, f"test_{plugin_id}_plugin.py")
        if not os.path.exists(test_path):
            _write(test_path, TEST_TEMPLATE.format(plugin_id=plugin_id, plugin_name=plugin_name))
            print(f"  Created: tests/test_{plugin_id}_plugin.py")
        else:
            print(f"  Skipped: tests/test_{plugin_id}_plugin.py (already exists)")

    print(f"\n✅  Plugin scaffolded at: plugins/{plugin_id}/")
    print(f"   - plugins/{plugin_id}/__init__.py")
    print(f"   - plugins/{plugin_id}/plugin.py")
    print(f"\nNext steps:")
    print(f"  1. Edit plugins/{plugin_id}/plugin.py")
    print(f"  2. Restart the app — the plugin is auto-discovered at startup")
    print(f"  3. Run: python -m pytest tests/test_{plugin_id}_plugin.py")


def _write(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  Created: {os.path.relpath(path)}")


if __name__ == "__main__":
    main()
