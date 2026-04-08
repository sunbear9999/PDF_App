import os
import uuid


class MainWindowAIAnnotation:
    """
    Helper for creating AI-driven highlights in PDFs.

    Extracted from MainWindow to keep the GUI orchestrator smaller.
    """

    def __init__(self, main_window):
        self.main_window = main_window

    def add_ai_annotation(
        self,
        quote,
        note,
        target_doc_name=None,
        allowed_paths=None,
        forced_annot_id=None,
        emit_signal=True,
    ):
        v = self.main_window

        if not quote:
            return False

        clean_quote = quote.strip()
        words = clean_quote.split()
        if not words:
            return False

        chunks = []
        if len(words) <= 6:
            chunks = [" ".join(words)]
        else:
            for i in range(0, len(words), 4):
                chunk = " ".join(words[i : i + 6])
                if chunk.strip():
                    chunks.append(chunk)

        search_paths = allowed_paths if allowed_paths else v.pdf_controller.get_pdf_paths()
        if target_doc_name:
            filtered_paths = []
            for p in search_paths:
                if target_doc_name.lower().strip() in os.path.basename(p).lower():
                    filtered_paths.append(p)
            if filtered_paths:
                search_paths = filtered_paths

        found_any = False
        for path in search_paths:
            try:
                doc = v.pdf_controller.get_doc(path)
                if not doc:
                    continue
                for page_num in range(len(doc)):
                    page = doc.load_page(page_num)
                    rects = page.search_for(clean_quote)

                    if not rects and len(chunks) > 1:
                        rects = []
                        for chunk in chunks:
                            res = page.search_for(chunk)
                            if res:
                                rects.extend(res)

                    if rects:
                        quads = [r.quad for r in rects]
                        annot = page.add_highlight_annot(quads)
                        annot.set_colors(stroke=(0.7, 0.4, 1.0))

                        annot_id_to_use = (
                            forced_annot_id
                            if forced_annot_id
                            else f"AINote|{uuid.uuid4()}"
                        )
                        annot_info = {
                            "title": annot_id_to_use,
                            "content": note,
                            "subject": clean_quote,
                        }
                        annot.set_info(info=annot_info)
                        annot.update()

                        found_any = True
                        v.pdf_controller.mark_dirty(path)

                        if path == v.current_file_path:
                            v.viewer.reload_page(page_num)
                        break

                if found_any and forced_annot_id:
                    break

            except Exception as e:
                print(f"Error adding AI annotation to {path}: {e}")

        if found_any and emit_signal:
            v.viewer.annot_manager.note_added.emit()

        return found_any

