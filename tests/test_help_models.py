"""Tests for core/help/models.py — pure dataclasses, no Qt required."""
import unittest
from core.help.models import HelpTopic, TutorialStep, TutorialDefinition, TutorialState


class TestHelpTopic(unittest.TestCase):

    def _make(self, **kw):
        defaults = dict(id="core.x", title="T", summary="S", body="B", category="C")
        defaults.update(kw)
        return HelpTopic(**defaults)

    def test_required_fields(self):
        t = self._make()
        self.assertEqual(t.id, "core.x")
        self.assertEqual(t.title, "T")
        self.assertEqual(t.body, "B")

    def test_defaults(self):
        t = self._make()
        self.assertEqual(t.keywords, [])
        self.assertEqual(t.related_topics, [])
        self.assertEqual(t.tutorial_ids, [])
        self.assertEqual(t.feature_id, "")
        self.assertEqual(t.version, "1.0")
        self.assertEqual(t.namespace, "core")
        self.assertEqual(t.plugin_id, "")

    def test_plugin_namespace(self):
        t = self._make(id="plugin.myplugin.x", namespace="plugin", plugin_id="myplugin")
        self.assertEqual(t.namespace, "plugin")
        self.assertEqual(t.plugin_id, "myplugin")

    def test_keywords_stored(self):
        t = self._make(keywords=["foo", "bar"])
        self.assertIn("foo", t.keywords)


class TestTutorialStep(unittest.TestCase):

    def test_defaults(self):
        s = TutorialStep(id="s1", target_id="toolbar.btn", text="Click here")
        self.assertEqual(s.before_actions, [])
        self.assertEqual(s.advance_condition, "next_button")
        self.assertEqual(s.interaction_mode, "passive")
        self.assertFalse(s.optional)

    def test_with_before_actions(self):
        s = TutorialStep(
            id="s1", target_id="dock.research", text="Open dock",
            before_actions=[{"type": "open_dock", "dock_id": "research"}]
        )
        self.assertEqual(len(s.before_actions), 1)
        self.assertEqual(s.before_actions[0]["type"], "open_dock")


class TestTutorialDefinition(unittest.TestCase):

    def test_empty_steps(self):
        td = TutorialDefinition(id="core.tour", title="Tour")
        self.assertEqual(td.steps, [])
        self.assertEqual(td.estimated_minutes, 2)
        self.assertEqual(td.namespace, "core")

    def test_step_count(self):
        steps = [TutorialStep(id=f"s{i}", target_id="t", text="text") for i in range(3)]
        td = TutorialDefinition(id="core.tour", title="Tour", steps=steps)
        self.assertEqual(len(td.steps), 3)


class TestTutorialState(unittest.TestCase):

    def test_enum_values(self):
        states = list(TutorialState)
        self.assertIn(TutorialState.IDLE, states)
        self.assertIn(TutorialState.COMPLETED, states)
        self.assertIn(TutorialState.FAILED, states)
        self.assertIn(TutorialState.CANCELLED, states)


if __name__ == "__main__":
    unittest.main()
