"""Tests for core/help/tutorial_registry.py."""
import unittest
from core.help.models import TutorialDefinition, TutorialStep
from core.help.tutorial_registry import TutorialRegistry


def _step(sid: str = "s1") -> TutorialStep:
    return TutorialStep(id=sid, target_id="toolbar.btn", text="Do this")


def _tutorial(tid: str, category: str = "General", plugin_id: str = "", steps=None) -> TutorialDefinition:
    return TutorialDefinition(
        id=tid, title=f"Tutorial {tid}", category=category,
        plugin_id=plugin_id, steps=steps or [_step()]
    )


class TestTutorialRegistry(unittest.TestCase):

    def setUp(self):
        self.reg = TutorialRegistry()

    def test_register_and_get(self):
        t = _tutorial("core.tour")
        self.reg.register(t)
        self.assertIs(self.reg.get("core.tour"), t)

    def test_get_missing_returns_none(self):
        self.assertIsNone(self.reg.get("no.such"))

    def test_register_many(self):
        self.reg.register_many([_tutorial(f"core.t{i}") for i in range(4)])
        self.assertEqual(len(self.reg), 4)

    def test_all_tutorials(self):
        self.reg.register_many([_tutorial("core.a"), _tutorial("core.b")])
        ids = {t.id for t in self.reg.all_tutorials()}
        self.assertIn("core.a", ids)
        self.assertIn("core.b", ids)

    def test_get_by_category(self):
        self.reg.register(_tutorial("core.gs", category="Getting Started"))
        self.reg.register(_tutorial("core.adv", category="Advanced"))
        gs = self.reg.get_by_category("Getting Started")
        self.assertEqual(len(gs), 1)
        self.assertEqual(gs[0].id, "core.gs")

    def test_different_owner_raises(self):
        self.reg.register(_tutorial("core.x", plugin_id="p1"))
        with self.assertRaises(ValueError):
            self.reg.register(_tutorial("core.x", plugin_id="p2"))

    def test_same_owner_can_overwrite(self):
        self.reg.register(_tutorial("core.x", plugin_id="p1"))
        t2 = _tutorial("core.x", plugin_id="p1")
        t2.title = "Updated"
        self.reg.register(t2)
        self.assertEqual(self.reg.get("core.x").title, "Updated")

    def test_remove_plugin_tutorials(self):
        self.reg.register(_tutorial("plugin.p1.tour", plugin_id="p1"))
        self.reg.register(_tutorial("core.base"))
        self.reg.remove_plugin_tutorials("p1")
        self.assertIsNone(self.reg.get("plugin.p1.tour"))
        self.assertIsNotNone(self.reg.get("core.base"))


if __name__ == "__main__":
    unittest.main()
