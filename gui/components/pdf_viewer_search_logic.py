import fitz

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QBrush, QPen
from PyQt6.QtWidgets import QGraphicsRectItem


class PDFViewerSearchLogic:
    """Search and highlight logic extracted from PDFViewer.

    This helper owns the state transitions for `search_hits`, `current_hit_index`,
    and the creation/removal of highlight overlay items.
    """

    def __init__(self, view):
        self.view = view

    def _on_search_text_changed(self, text: str):
        self.view.search_debounce_timer.start(400)

    def _on_search_return_pressed(self):
        v = self.view
        if v.search_debounce_timer.isActive():
            v.search_debounce_timer.stop()
            v.trigger_search()
        else:
            v.next_search_hit()

    def trigger_search(self):
        v = self.view
        text = v.search_bar.search_input.text().strip()
        scope = v.search_bar.scope_combo.currentText()
        match_case = v.search_bar.chk_match_case.isChecked()

        if (
            text == v.current_search_text
            and scope == v.current_search_scope
            and match_case == v.current_match_case
        ):
            return

        v.current_search_scope = scope
        v.current_match_case = match_case
        v.execute_search(text, scope, match_case)

    def execute_search(self, text, scope, match_case):
        v = self.view
        v.search_hits = []
        v.current_hit_index = -1
        v.current_search_text = text

        if not text:
            v.clear_search_highlights()
            v.search_bar.update_hits(0, 0)
            return

        main_window = v.window()
        pdfs_to_search = []
        if (
            scope == "Entire Project"
            and main_window
            and hasattr(main_window, "pdf_controller")
        ):
            pdfs_to_search = main_window.pdf_controller.get_pdf_paths()
        else:
            if main_window and main_window.current_file_path:
                pdfs_to_search = [main_window.current_file_path]

        flags = fitz.TEXT_PRESERVE_LIGATURES | fitz.TEXT_PRESERVE_WHITESPACE
        if match_case:
            flags |= getattr(fitz, "TEXT_MATCH_CASE", 4)

        for pdf_path in pdfs_to_search:
            doc = (
                main_window.pdf_controller.get_doc(pdf_path)
                if main_window and hasattr(main_window, "pdf_controller")
                else None
            )
            if not doc:
                continue

            for page_num in range(len(doc)):
                page = doc.load_page(page_num)
                quads = page.search_for(
                    text, hit_max=999, quads=True, flags=flags
                )
                for q in quads:
                    v.search_hits.append(
                        {
                            "pdf": pdf_path,
                            "page": page_num,
                            "rect": q.rect,
                        }
                    )

        if v.search_hits:
            v.current_hit_index = 0
            v.search_bar.update_hits(1, len(v.search_hits))
            v.navigate_to_current_hit()
        else:
            v.clear_search_highlights()
            v.search_bar.update_hits(0, 0)

    def next_search_hit(self):
        v = self.view
        if not v.search_hits:
            return
        v.current_hit_index = (v.current_hit_index + 1) % len(v.search_hits)
        v.search_bar.update_hits(v.current_hit_index + 1, len(v.search_hits))
        v.navigate_to_current_hit()

    def prev_search_hit(self):
        v = self.view
        if not v.search_hits:
            return
        v.current_hit_index = (v.current_hit_index - 1) % len(v.search_hits)
        v.search_bar.update_hits(v.current_hit_index + 1, len(v.search_hits))
        v.navigate_to_current_hit()

    def navigate_to_current_hit(self):
        v = self.view
        if not v.search_hits or v.current_hit_index < 0:
            return

        hit = v.search_hits[v.current_hit_index]
        main_window = v.window()

        if hit["pdf"] != main_window.current_file_path:
            v.pending_search_jump = hit
            if main_window and hasattr(main_window, "pdf_controller"):
                main_window.pdf_controller.switch_to_pdf(hit["pdf"])
            else:
                main_window.switch_to_pdf(hit["pdf"])
            return

        v.render_search_highlights()
        page_num = hit["page"]
        if page_num < len(v.page_items):
            v._execute_search_jump(hit)
        else:
            v.pending_search_jump = hit

    def clear_search_highlights(self):
        v = self.view
        for h in v.search_highlight_items:
            try:
                if h.scene():
                    v.scene.removeItem(h)
            except RuntimeError:
                # Item might already be deleted.
                pass
        v.search_highlight_items.clear()

    def render_search_highlights(self):
        v = self.view
        self.clear_search_highlights()
        if not v.search_hits:
            return

        for page_num in range(len(v.page_items)):
            v._apply_search_highlights_to_page(page_num, v.page_items[page_num])

    def _apply_search_highlights_to_page(self, page_num, page_item):
        v = self.view
        current_pdf = v.window().current_file_path

        for i, hit in enumerate(v.search_hits):
            if hit["pdf"] == current_pdf and hit["page"] == page_num:
                r = hit["rect"]
                z = v.base_zoom
                qt_rect = QRectF(
                    r.x0 * z,
                    r.y0 * z,
                    (r.x1 - r.x0) * z,
                    (r.y1 - r.y0) * z,
                )

                h_item = QGraphicsRectItem(qt_rect, page_item)

                if i == v.current_hit_index:
                    h_item.setBrush(QBrush(QColor(255, 165, 0, 150)))
                    h_item.setPen(QPen(QColor(255, 140, 0), 2))
                    h_item.setZValue(10)
                else:
                    h_item.setBrush(QBrush(QColor(255, 255, 0, 100)))
                    h_item.setPen(QPen(Qt.PenStyle.NoPen))
                    h_item.setZValue(5)

                v.search_highlight_items.append(h_item)

    def _execute_search_jump(self, hit):
        v = self.view
        page_num = hit["page"]
        if 0 <= page_num < len(v.page_items):
            page_item = v.page_items[page_num]
            r = hit["rect"]
            z = v.base_zoom
            qt_rect = QRectF(
                r.x0 * z,
                r.y0 * z,
                (r.x1 - r.x0) * z,
                (r.y1 - r.y0) * z,
            )
            scene_rect = page_item.mapToScene(qt_rect).boundingRect()
            v.ensureVisible(scene_rect, 100, 100)

