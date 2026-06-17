import sqlite3
import unittest

from core.citation_manager import CitationManager
from core.db.document_db import DocumentDB
from core.plugins.extension_registry import PluginExtensionRegistry
from plugins.zotero.gui.sync_dialog import _auto_match
from plugins.zotero.plugin import Plugin
from plugins.zotero.zotero_formatter import ZoteroFormatter


class _Manager:
    def __init__(self):
        self._conn = sqlite3.connect(":memory:")


class _Project:
    def __init__(self):
        self.pdfs = ["/tmp/project/alpha.pdf", "/tmp/project/beta.pdf"]
        self._citations = {
            "/tmp/project/alpha.pdf": {"doc_id": "/tmp/project/alpha.pdf", "doi": "10.1000/alpha"},
            "/tmp/project/beta.pdf": {"doc_id": "/tmp/project/beta.pdf", "title": "Beta Study"},
        }

    def get_citation(self, doc_id):
        return self._citations.get(doc_id, {})


class _API:
    def __init__(self):
        self.project_manager = _Project()

    def register_service(self, *args, **kwargs):
        pass

    def notify(self, *args, **kwargs):
        pass


class TestCitationZoteroSync(unittest.TestCase):
    def test_citation_storage_preserves_arbitrary_fields(self):
        manager = _Manager()
        db = DocumentDB(manager)

        db.upsert_citation({
            "doc_id": "/tmp/a.pdf",
            "title": "A Title",
            "authors": "Author, Ada",
            "accessDate": "2026-06-17T00:00:00Z",
            "fields": {"dateModified": "2026-06-18T00:00:00Z"},
            "source_provider": "zotero",
            "source_item_key": "ABC123",
        })

        stored = db.get_citation("/tmp/a.pdf")
        self.assertEqual(stored["title"], "A Title")
        self.assertEqual(stored["source_provider"], "zotero")
        self.assertEqual(stored["source_item_key"], "ABC123")
        self.assertEqual(stored["fields"]["accessDate"], "2026-06-17T00:00:00Z")
        self.assertEqual(stored["fields"]["dateModified"], "2026-06-18T00:00:00Z")

    def test_zotero_formatter_keeps_full_metadata(self):
        item = {
            "item_id": 7,
            "item_type": "journalArticle",
            "key": "ZKEY",
            "title": "Zotero Rich Record",
            "year": "2024",
            "DOI": "10.1000/rich",
            "fields": {"accessDate": "2026-06-17T00:00:00Z", "extra": "kept"},
            "creators": [{"type": "author", "first": "Ada", "last": "Lovelace"}],
        }

        citation = ZoteroFormatter().to_citation_dict(item)

        self.assertEqual(citation["source_provider"], "zotero")
        self.assertEqual(citation["source_item_key"], "ZKEY")
        self.assertEqual(citation["fields"]["accessDate"], "2026-06-17T00:00:00Z")
        self.assertEqual(citation["fields"]["extra"], "kept")
        self.assertEqual(citation["authors"], "Lovelace, Ada")

    def test_auto_match_is_project_scoped_and_uses_doi(self):
        project = _Project()
        items = [
            {"key": "A", "title": "Unrelated", "DOI": "10.1000/alpha"},
            {"key": "B", "title": "Beta Study", "DOI": ""},
            {"key": "C", "title": "Outside Project", "DOI": "10.1000/outside"},
        ]

        matches = _auto_match(["/tmp/project/alpha.pdf"], items, project)

        self.assertEqual(set(matches), {"/tmp/project/alpha.pdf"})
        self.assertEqual(matches["/tmp/project/alpha.pdf"]["key"], "A")
        self.assertNotIn("/tmp/project/beta.pdf", matches)

    def test_citation_manager_formats_flexible_records(self):
        manager = _Project()
        cm = CitationManager(manager)
        cm.set_style("APA")

        text = cm.format_entry({
            "title": "Flexible Metadata",
            "authors": "Lovelace, Ada",
            "year": "1843",
            "journal": "Notes",
            "volume": "1",
            "issue": "2",
            "url": "https://example.test",
        })

        self.assertIn("Lovelace, A.", text)
        self.assertIn("Flexible Metadata", text)
        self.assertIn("https://example.test", text)

    def test_zotero_plugin_registers_generic_context_actions(self):
        plugin = Plugin()
        plugin._api = _API()
        plugin._db = object()
        plugin._formatter = object()
        registry = PluginExtensionRegistry()

        plugin.register_gui_extensions(registry)
        mounts = {mount for action in registry.get_actions() for mount in action.mounts}

        self.assertIn("context_menu:document_list:item", mounts)
        self.assertIn("context_menu:citation:item", mounts)


if __name__ == "__main__":
    unittest.main()
