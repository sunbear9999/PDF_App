"""Tests for core/help/help_registry.py — no Qt required."""
import unittest
from core.help.models import HelpTopic
from core.help.help_registry import HelpRegistry


def _topic(tid: str, category: str = "Cat", plugin_id: str = "", **kw) -> HelpTopic:
    return HelpTopic(
        id=tid, title=f"Title {tid}", summary=f"Summary {tid}",
        body=f"Body about {tid}", category=category, plugin_id=plugin_id, **kw
    )


class TestHelpRegistry(unittest.TestCase):

    def setUp(self):
        self.reg = HelpRegistry()

    def test_register_and_get(self):
        t = _topic("core.x")
        self.reg.register(t)
        self.assertIs(self.reg.get("core.x"), t)

    def test_get_missing_returns_none(self):
        self.assertIsNone(self.reg.get("no.such.topic"))

    def test_register_many(self):
        topics = [_topic(f"core.t{i}") for i in range(5)]
        self.reg.register_many(topics)
        self.assertEqual(len(self.reg), 5)

    def test_all_topics(self):
        self.reg.register_many([_topic("core.a"), _topic("core.b")])
        ids = {t.id for t in self.reg.all_topics()}
        self.assertEqual(ids, {"core.a", "core.b"})

    def test_get_by_category(self):
        self.reg.register(_topic("core.x", category="PDF"))
        self.reg.register(_topic("core.y", category="Workspace"))
        pdf_topics = self.reg.get_by_category("PDF")
        self.assertEqual(len(pdf_topics), 1)
        self.assertEqual(pdf_topics[0].id, "core.x")

    def test_get_categories(self):
        self.reg.register(_topic("core.a", category="PDF"))
        self.reg.register(_topic("core.b", category="Workspace"))
        cats = set(self.reg.get_categories())
        self.assertIn("PDF", cats)
        self.assertIn("Workspace", cats)

    def test_search_by_title(self):
        self.reg.register(_topic("core.zoom", category="PDF"))
        self.reg.register(_topic("core.highlight"))
        results = self.reg.search("zoom")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].id, "core.zoom")

    def test_search_by_keyword(self):
        t = _topic("core.zoom", keywords=["magnify", "scale"])
        self.reg.register(t)
        results = self.reg.search("magnify")
        self.assertEqual(len(results), 1)

    def test_search_by_summary(self):
        from core.help.models import HelpTopic
        t = HelpTopic(id="core.x", title="Title core.x", summary="This is about zooming in",
                      body="Body about core.x", category="Cat")
        self.reg.register(t)
        results = self.reg.search("zooming")
        self.assertTrue(any(r.id == "core.x" for r in results))

    def test_search_empty_returns_all(self):
        self.reg.register_many([_topic(f"core.t{i}") for i in range(5)])
        results = self.reg.search("")
        self.assertEqual(len(results), 5)

    def test_search_no_match_returns_empty(self):
        self.reg.register(_topic("core.x"))
        results = self.reg.search("completelymadeupword12345")
        self.assertEqual(results, [])

    def test_search_title_ranks_higher_than_body(self):
        title_match = HelpTopic(id="core.title", title="zoom topic", summary="S", body="Nothing", category="C")
        body_match = HelpTopic(id="core.body", title="Other", summary="S", body="zoom mentioned here", category="C")
        self.reg.register(title_match)
        self.reg.register(body_match)
        results = self.reg.search("zoom")
        self.assertEqual(results[0].id, "core.title")

    def test_remove_plugin_topics(self):
        self.reg.register(_topic("plugin.p1.x", plugin_id="p1"))
        self.reg.register(_topic("core.y"))
        self.reg.remove_plugin_topics("p1")
        self.assertIsNone(self.reg.get("plugin.p1.x"))
        self.assertIsNotNone(self.reg.get("core.y"))

    def test_remove_plugin_topics_only_affects_owner(self):
        self.reg.register(_topic("plugin.p1.a", plugin_id="p1"))
        self.reg.register(_topic("plugin.p2.b", plugin_id="p2"))
        self.reg.remove_plugin_topics("p1")
        self.assertIsNone(self.reg.get("plugin.p1.a"))
        self.assertIsNotNone(self.reg.get("plugin.p2.b"))

    def test_same_owner_can_overwrite(self):
        t1 = _topic("core.x", plugin_id="p1")
        t2 = _topic("core.x", plugin_id="p1")
        t2.title = "Updated title"
        self.reg.register(t1)
        self.reg.register(t2)  # Should not raise
        self.assertEqual(self.reg.get("core.x").title, "Updated title")

    def test_different_owner_raises(self):
        self.reg.register(_topic("core.x", plugin_id="p1"))
        with self.assertRaises(ValueError):
            self.reg.register(_topic("core.x", plugin_id="p2"))

    def test_lookup_by_feature(self):
        t = _topic("core.x", feature_id="toolbar.add_pdf")
        self.reg.register(t)
        found = self.reg.lookup_by_feature("toolbar.add_pdf")
        self.assertIs(found, t)

    def test_lookup_by_feature_missing(self):
        self.assertIsNone(self.reg.lookup_by_feature("nonexistent.feature"))


if __name__ == "__main__":
    unittest.main()
