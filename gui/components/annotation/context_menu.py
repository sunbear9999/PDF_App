import uuid

import fitz
from PyQt6.QtWidgets import QMenu, QInputDialog
from PyQt6.QtGui import QAction


class AnnotationContextMenu:
    def __init__(self, manager):
        self.manager = manager

    @property
    def m(self):
        return self.manager

    def show_context_menu(self, global_pos):
        m = self.m
        menu = QMenu(m.viewer)
        menu.setStyleSheet(
            """
            QMenu { background-color: #2b2b2b; color: white; border: 1px solid #444; font-weight: bold; } 
            QMenu::item:selected { background-color: #0078D7; }
        """
        )

        colors = [
            ("Yellow", (1.0, 0.9, 0.0)),
            ("Green", (0.0, 0.8, 0.4)),
            ("Blue", (0.2, 0.6, 1.0)),
            ("Purple", (0.7, 0.4, 1.0)),
            ("Red", (1.0, 0.3, 0.3)),
        ]

        hl_menu = menu.addMenu("🖍️ Highlight...")
        for name, rgb in colors:
            action = QAction(f"{name}", m.viewer)
            action.triggered.connect(lambda checked, c=rgb: self.apply_highlight(c))
            hl_menu.addAction(action)

        menu.addSeparator()

        ai_action = menu.addAction("🤖 Ask AI About Selection")
        ai_action.triggered.connect(m.ask_ai_about_selection)

        reword_action = menu.addAction("✍️ Reword this")
        reword_action.triggered.connect(m.reword_selection)

        menu.exec(global_pos)

    def apply_highlight(self, color_tuple):
        m = self.m
        if not m.selected_words:
            return

        extracted_text = " ".join(w[4] for w in m.selected_words)
        text, ok = QInputDialog.getText(
            m.viewer,
            "Add Note",
            "Enter a note for this highlight (Optional):",
        )

        if ok:
            try:
                page = m.viewer.doc.load_page(m.current_page_idx)
                quads = [fitz.Rect(w[:4]).quad for w in m.selected_words]

                annot = page.add_highlight_annot(quads)
                annot.set_colors(stroke=color_tuple)

                annot_info = {
                    "title": f"UserNote|{uuid.uuid4()}",
                    "content": text if text else "",
                    "subject": extracted_text,
                }
                annot.set_info(info=annot_info)
                annot.update()

                m.viewer.reload_page(m.current_page_idx)
                m.note_added.emit()
            except Exception as e:
                print(f"Error saving highlight: {e}")

        m.clear_selection()

