import unittest

from gui.utils.document_helpers import active_pdf_names, active_pdf_paths, prune_doc_names


class _Source:
    def __init__(self, path, removed=False):
        self.origin_id = path
        self.properties = {"path": path}
        self.state = {"is_removed": removed}


class _ProjectManager:
    def __init__(self):
        self.pdfs = ["/docs/active.pdf", "/docs/removed.pdf"]

    def list_source_entities(self):
        return [
            _Source("/docs/active.pdf"),
            _Source("/docs/removed.pdf", removed=True),
        ]


class TestDocumentHelpers(unittest.TestCase):

    def test_active_pdf_paths_excludes_soft_removed_sources(self):
        self.assertEqual(active_pdf_paths(_ProjectManager()), ["/docs/active.pdf"])

    def test_active_pdf_names_returns_basenames(self):
        self.assertEqual(active_pdf_names(_ProjectManager()), ["active.pdf"])

    def test_prune_doc_names_drops_removed_documents(self):
        self.assertEqual(
            prune_doc_names(_ProjectManager(), ["active.pdf", "removed.pdf"]),
            ["active.pdf"],
        )


if __name__ == "__main__":
    unittest.main()
