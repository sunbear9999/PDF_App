import fitz
import os

class PDFCache:
    def __init__(self, max_cache_size=5):
        self.open_docs = {}
        self.dirty_docs = set()
        self.max_cache_size = max_cache_size
        self.active_file = None

    def set_active_file(self, filepath):
        self.active_file = filepath

    def mark_dirty(self, filepath):
        if filepath:
            self.dirty_docs.add(filepath)

    def _clear_cache(self):
        try:
            for doc in self.open_docs.values():
                if not doc.is_closed: doc.close()
            self.open_docs.clear()
            self.dirty_docs.clear()
            self.active_file = None
        except Exception as e:
            print(f"Error clearing cache: {e}")

    def get_doc(self, filepath):
        try:
            if filepath in self.open_docs:
                doc = self.open_docs.pop(filepath)
                self.open_docs[filepath] = doc
                return doc

            self._evict_old_docs()
            self.open_docs[filepath] = fitz.open(filepath)
            return self.open_docs[filepath]
        except Exception as e:
            print(f"Failed to open document {filepath}: {e}")
            return None

    def _evict_old_docs(self):
        try:
            while len(self.open_docs) >= self.max_cache_size:
                evict_candidate = next((p for p in self.open_docs if p != self.active_file), None)
                if not evict_candidate: break

                doc = self.open_docs.pop(evict_candidate)
                if evict_candidate in self.dirty_docs:
                    self._save_single_doc(doc, evict_candidate)

                if not doc.is_closed: doc.close()
        except Exception as e:
            print(f"Error evicting docs: {e}")

    def _save_single_doc(self, doc, path):
        if not doc or doc.is_closed: return
        try:
            temp_path = path + ".tmp_save"
            doc.save(temp_path, garbage=3, deflate=True)

            if os.path.exists(temp_path):
                os.replace(temp_path, path)

            self.dirty_docs.discard(path)
        except Exception as e:
            print(f"Primary save failed for {path}: {e}")
            try:
                pdf_bytes = doc.write()
                with open(path, 'wb') as f: f.write(pdf_bytes)
                self.dirty_docs.discard(path)
            except Exception as fallback_e:
                print(f"Full save fallback failed for {path}: {fallback_e}")

    def save_all_docs(self):
        try:
            for path, doc in list(self.open_docs.items()):
                if path in self.dirty_docs and path != "workspace":
                    self._save_single_doc(doc, path)
            self.dirty_docs.discard("workspace")
        except Exception as e:
            print(f"Error during save_all_docs: {e}")
