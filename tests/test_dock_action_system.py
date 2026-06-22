"""
Tests for the DockActionSpec / DockActionContext registration and lookup system.
"""
import unittest
from unittest.mock import MagicMock
from core.plugins.extension_registry import PluginExtensionRegistry, DockActionSpec, DockActionContext


def _make_registry():
    reg = PluginExtensionRegistry()
    return reg


class TestDockActionRegistration(unittest.TestCase):
    def setUp(self):
        self.reg = _make_registry()

    def test_add_and_get_dock_action_by_exact_mount(self):
        spec = DockActionSpec(
            action_id="test_action",
            label="Test",
            mounts=["notes_dock:context_menu:annotation"],
        )
        self.reg.add_dock_action(spec)
        results = self.reg.get_dock_actions("notes_dock:context_menu:annotation")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].action_id, "test_action")

    def test_get_dock_actions_wrong_mount_returns_empty(self):
        spec = DockActionSpec(
            action_id="test_action",
            label="Test",
            mounts=["data_dock:toolbar"],
        )
        self.reg.add_dock_action(spec)
        results = self.reg.get_dock_actions("notes_dock:toolbar")
        self.assertEqual(results, [])

    def test_get_dock_actions_for_dock_matches_prefix(self):
        spec = DockActionSpec(
            action_id="cell_action",
            label="Cell",
            mounts=["data_dock:context_menu:cell", "data_dock:toolbar"],
        )
        self.reg.add_dock_action(spec)
        results = self.reg.get_dock_actions_for_dock("data_dock")
        self.assertEqual(len(results), 1)

    def test_plugin_id_assigned_on_registration(self):
        spec = DockActionSpec(action_id="act", label="A", mounts=["x:y"])
        spec.plugin_id = "my_plugin"
        self.reg.add_dock_action(spec)
        results = self.reg.get_dock_actions("x:y")
        self.assertEqual(results[0].plugin_id, "my_plugin")

    def test_multiple_specs_at_same_mount_sorted_by_position(self):
        low = DockActionSpec(action_id="low", label="Low", mounts=["m:n"], position=10)
        high = DockActionSpec(action_id="high", label="High", mounts=["m:n"], position=90)
        self.reg.add_dock_action(high)
        self.reg.add_dock_action(low)
        results = self.reg.get_dock_actions("m:n")
        self.assertEqual(results[0].action_id, "low")
        self.assertEqual(results[1].action_id, "high")

    def test_remove_by_plugin_removes_dock_actions(self):
        spec = DockActionSpec(action_id="p_action", label="P", mounts=["z:w"])
        spec.plugin_id = "removable_plugin"
        self.reg.add_dock_action(spec)
        self.assertEqual(len(self.reg.get_dock_actions("z:w")), 1)
        self.reg.remove_plugin_specs("removable_plugin")
        self.assertEqual(len(self.reg.get_dock_actions("z:w")), 0)


class TestDockActionContext(unittest.TestCase):
    def test_defaults(self):
        ctx = DockActionContext(dock_id="notes_dock", zone="annotation")
        self.assertIsNone(ctx.selection)
        self.assertIsNone(ctx.source_path)
        self.assertEqual(ctx.extra, {})

    def test_extra_auto_initialized(self):
        ctx = DockActionContext(dock_id="d", zone="z")
        self.assertIsInstance(ctx.extra, dict)
        ctx.extra["key"] = "value"
        ctx2 = DockActionContext(dock_id="d", zone="z")
        self.assertEqual(ctx2.extra, {})  # independent


if __name__ == "__main__":
    unittest.main()
