"""Tests for core/help/ui_target_registry.py."""
import sys
import unittest
import weakref
from unittest.mock import MagicMock, patch


def _make_app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv)


class TestUITargetRegistry(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = _make_app()

    def setUp(self):
        from core.help.ui_target_registry import UITargetRegistry
        self.reg = UITargetRegistry()

    # ------------------------------------------------------------------
    # register_widget / resolve
    # ------------------------------------------------------------------

    def test_register_widget_and_resolve(self):
        from PySide6.QtWidgets import QPushButton
        btn = QPushButton("test")
        self.reg.register_widget("toolbar.btn", btn, topic_id="core.x")
        resolved = self.reg.resolve("toolbar.btn")
        self.assertIs(resolved, btn)

    def test_resolve_missing_returns_none(self):
        self.assertIsNone(self.reg.resolve("nonexistent.target"))

    def test_get_topic_id(self):
        from PySide6.QtWidgets import QPushButton
        btn = QPushButton()
        self.reg.register_widget("toolbar.btn", btn, topic_id="core.analysis")
        self.assertEqual(self.reg.get_topic_id("toolbar.btn"), "core.analysis")

    def test_get_topic_id_missing(self):
        self.assertIsNone(self.reg.get_topic_id("nonexistent"))

    # ------------------------------------------------------------------
    # register_resolver
    # ------------------------------------------------------------------

    def test_register_resolver(self):
        from PySide6.QtWidgets import QPushButton
        btn = QPushButton()
        self.reg.register_resolver("dock.research", lambda: btn, topic_id="core.research")
        resolved = self.reg.resolve("dock.research")
        self.assertIs(resolved, btn)

    def test_resolver_returning_none(self):
        self.reg.register_resolver("dock.absent", lambda: None)
        self.assertIsNone(self.reg.resolve("dock.absent"))

    # ------------------------------------------------------------------
    # Dead weakref pruning
    # ------------------------------------------------------------------

    def test_dead_weakref_prunes_on_resolve(self):
        from PySide6.QtWidgets import QPushButton
        btn = QPushButton()
        self.reg.register_widget("toolbar.dead", btn)
        del btn
        # After deletion, weakref should be dead → resolve returns None and prunes
        result = self.reg.resolve("toolbar.dead")
        self.assertIsNone(result)
        self.assertNotIn("toolbar.dead", self.reg.all_target_ids())

    # ------------------------------------------------------------------
    # resolve_nearest_ancestor
    # ------------------------------------------------------------------

    def test_resolve_nearest_ancestor_direct(self):
        from PySide6.QtWidgets import QPushButton
        btn = QPushButton()
        self.reg.register_widget("toolbar.btn", btn)
        found_id = self.reg.resolve_nearest_ancestor(btn)
        self.assertEqual(found_id, "toolbar.btn")

    def test_resolve_nearest_ancestor_child(self):
        from PySide6.QtWidgets import QWidget, QVBoxLayout, QPushButton
        parent = QWidget()
        layout = QVBoxLayout(parent)
        child = QPushButton("child", parent)
        layout.addWidget(child)
        # Register the parent, not the child
        self.reg.register_widget("panel.main", parent)
        found_id = self.reg.resolve_nearest_ancestor(child)
        self.assertEqual(found_id, "panel.main")

    def test_resolve_nearest_ancestor_unregistered(self):
        from PySide6.QtWidgets import QPushButton
        btn = QPushButton()
        self.assertIsNone(self.reg.resolve_nearest_ancestor(btn))

    # ------------------------------------------------------------------
    # unregister
    # ------------------------------------------------------------------

    def test_unregister(self):
        from PySide6.QtWidgets import QPushButton
        btn = QPushButton()
        self.reg.register_widget("toolbar.btn", btn, topic_id="core.x")
        self.reg.unregister("toolbar.btn")
        self.assertIsNone(self.reg.resolve("toolbar.btn"))
        self.assertIsNone(self.reg.get_topic_id("toolbar.btn"))


if __name__ == "__main__":
    unittest.main()
