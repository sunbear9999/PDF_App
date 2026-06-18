import sqlite3
import unittest
from types import SimpleNamespace

from core.citation_manager import CitationManager
from core.db.document_db import DocumentDB
from core.plugins.extension_registry import PluginExtensionRegistry
from plugins.zotero.gui.sync_dialog import _auto_match
from plugins.zotero.plugin import Plugin
from plugins.zotero.zotero_formatter import ZoteroFormatter
from plugins.zotero.zotero_sync_adapter import (
    DEFAULT_ZOTERO_LOCAL_API_BASE_URL,
    PyZoteroClient,
    PyZoteroOutboundSyncAdapter,
    ZoteroReadOnlyAdapter,
    default_zotero_settings,
)


class _Manager:
    def __init__(self):
        self._conn = sqlite3.connect(":memory:")


class _Project:
    def __init__(self):
        self.pdfs = ["/tmp/project/alpha.pdf", "/tmp/project/beta.pdf"]
        self.project_name = "Project Alpha"
        self.project_filepath = "/tmp/project/Project Alpha.pdfproj"
        self.upserts = []
        self._citations = {
            "/tmp/project/alpha.pdf": {"doc_id": "/tmp/project/alpha.pdf", "doi": "10.1000/alpha"},
            "/tmp/project/beta.pdf": {"doc_id": "/tmp/project/beta.pdf", "title": "Beta Study"},
        }

    def get_citation(self, doc_id):
        return self._citations.get(doc_id, {})

    def upsert_citation(self, data):
        self.upserts.append(data)
        self._citations[data["doc_id"]] = data


class _Config:
    def __init__(self):
        self.data = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value


class _API:
    def __init__(self):
        self.project_manager = _Project()
        self.config = _Config()
        self.notifications = []

    def register_service(self, *args, **kwargs):
        pass

    def notify(self, message, level="info", duration=3000):
        self.notifications.append((message, level, duration))


class _RemoteZotero:
    def __init__(self):
        self.created_items = []
        self.attachments = []

    def collections(self):
        return [{"data": {"key": "COLL", "name": "Papers"}}]

    def create_collections(self, collections):
        return {"success": {"0": "NEWCOLL"}}

    def create_items(self, items):
        self.created_items.extend(items)
        return {"success": {"0": "ZITEM"}}

    def attachment_simple(self, files, parentid=None):
        self.attachments.append((list(files), parentid))
        return {"success": ["ATTACH"]}

    def item(self, key):
        return {"data": {"key": key}}

    def addto_collection(self, collection, item):
        return True


class _Client(PyZoteroClient):
    def __init__(self, *, package=True, local=False, library_id="", api_key=""):
        super().__init__(
            local_api_base_url=DEFAULT_ZOTERO_LOCAL_API_BASE_URL,
            library_id=library_id,
            library_type="user",
            api_key=api_key,
        )
        self.package = package
        self.local = local
        self.remote = _RemoteZotero()

    def _probe_local_api(self):
        return self.local

    def _load_zotero_factory(self):
        return object() if self.package else None

    def _remote_client(self):
        if not self.package:
            raise RuntimeError("missing package")
        return self.remote


class _WritableAdapter:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def can_write(self):
        return True

    def sync_pdfs(self, pdf_paths, citations, *, collection_name=""):
        self.calls.append((list(pdf_paths), dict(citations), collection_name))
        if self.fail:
            raise RuntimeError("remote failed")
        from core.services.reference.citation_sync import CitationSyncResult
        return CitationSyncResult(True, "Synced 1 PDF to Zotero.", ["ZITEM"])


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

    def test_zotero_settings_defaults(self):
        defaults = default_zotero_settings()
        self.assertFalse(defaults["auto_add_pdfs_to_zotero"])
        self.assertEqual(defaults["pyzotero_local_api_base_url"], DEFAULT_ZOTERO_LOCAL_API_BASE_URL)
        self.assertEqual(defaults["pyzotero_library_type"], "user")
        self.assertEqual(defaults["target_collection_mode"], "project_named")

    def test_pyzotero_probe_states(self):
        unavailable = _Client(package=False)
        self.assertFalse(unavailable.probe().package_available)

        local_read_only = _Client(package=True, local=True)
        local_result = local_read_only.probe()
        self.assertTrue(local_result.local_api_available)
        self.assertFalse(local_result.write_capable)

        writable = _Client(package=True, local=True, library_id="123", api_key="KEY")
        write_result = writable.probe()
        self.assertTrue(write_result.package_available)
        self.assertTrue(write_result.write_capable)

        configured = _Client(package=True, local=False, library_id="123", api_key="KEY")
        self.assertTrue(configured.probe().write_capable)

    def test_pyzotero_outbound_adapter_adds_pdf(self):
        import os
        import tempfile

        handle, path = tempfile.mkstemp(suffix=".pdf")
        os.close(handle)
        try:
            client = _Client(package=True, local=True, library_id="123", api_key="KEY")
            adapter = PyZoteroOutboundSyncAdapter(client)
            result = adapter.sync_pdfs([path], {path: {"title": "Alpha", "authors": "Lovelace, Ada"}}, collection_name="Papers")

            self.assertTrue(result.ok)
            self.assertEqual(result.synced_ids, ["ZITEM"])
            self.assertEqual(client.remote.created_items[0]["title"], "Alpha")
            self.assertEqual(client.remote.created_items[0]["collections"], ["COLL"])
            self.assertEqual(client.remote.attachments[0][1], "ZITEM")
        finally:
            os.unlink(path)

    def test_auto_add_decision_flow(self):
        plugin = Plugin()
        api = _API()
        plugin._api = api

        plugin._build_outbound_adapter = lambda: ZoteroReadOnlyAdapter("missing")
        plugin._on_document_added(None, SimpleNamespace(path="/tmp/project/alpha.pdf"))
        self.assertEqual(api.notifications, [])

        api.config.set("auto_add_pdfs_to_zotero", True)
        plugin._on_document_added(None, SimpleNamespace(path="/tmp/project/alpha.pdf"))
        self.assertEqual(api.notifications[-1][1], "warning")

        writable = _WritableAdapter()
        plugin._build_outbound_adapter = lambda: writable
        plugin._on_document_added(None, SimpleNamespace(path="/tmp/project/alpha.pdf"))
        self.assertEqual(writable.calls[0][0], ["/tmp/project/alpha.pdf"])
        self.assertEqual(writable.calls[0][2], "Project Alpha")
        self.assertEqual(api.project_manager.upserts[-1]["source_item_key"], "ZITEM")

    def test_auto_add_errors_do_not_break_project_ingestion(self):
        plugin = Plugin()
        api = _API()
        api.config.set("auto_add_pdfs_to_zotero", True)
        plugin._api = api
        plugin._build_outbound_adapter = lambda: _WritableAdapter(fail=True)

        plugin._on_document_added(None, SimpleNamespace(path="/tmp/project/alpha.pdf"))

        self.assertEqual(api.notifications[-1][1], "warning")
        self.assertIn("stayed in the project", api.notifications[-1][0])

    def test_target_collection_resolution(self):
        plugin = Plugin()
        api = _API()
        plugin._api = api

        self.assertEqual(plugin._target_collection_name(api.project_manager), "Project Alpha")
        api.config.set("target_collection_mode", "existing_collection")
        api.config.set("target_collection_name", "Papers")
        self.assertEqual(plugin._target_collection_name(api.project_manager), "Papers")

    def test_settings_dialog_pyzotero_guidance(self):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from plugins.zotero.gui.settings_dialog import ZoteroSettingsDialog

        app = QApplication.instance() or QApplication([])
        api = _API()
        db = SimpleNamespace(is_available=lambda: False, get_collections=lambda: [])
        dialog = ZoteroSettingsDialog(api, db)

        self.assertIn("allow other applications", dialog.instructions_label.text().lower())
        self.assertIn("api key", dialog.instructions_label.text().lower())
        dialog.close()
        self.assertIsNotNone(app)


if __name__ == "__main__":
    unittest.main()
