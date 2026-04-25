# gui/components/annotation_manager.py
import fitz
import uuid
from PySide6.QtWidgets import QGraphicsRectItem, QInputDialog, QWidget, QMenu, QDialog, QVBoxLayout, QTextEdit, QPushButton
from PySide6.QtGui import QColor, QBrush, QPen, QAction, QTextCursor, QDesktopServices
from PySide6.QtCore import Qt, QRectF, QObject, Signal, QThread, QUrl
import re 


class AnnotationManager(QObject):
    note_added = Signal()
    highlight_created = Signal(dict)

    def __init__(self, viewer):
        super().__init__()
        self.viewer = viewer
        
        self.is_selecting = False
        self.start_word_idx = None
        self.current_page_idx = -1
        self.page_words = [] 
        self.temp_highlights = [] 
        self.selected_words = []

    def toggle_search(self):
        if hasattr(self.viewer, 'toggle_search_bar'):
            self.viewer.toggle_search_bar()

    def _get_page_at_pos(self, scene_pos):
        # Use pixmap if present, else placeholder
        items = []
        if hasattr(self.viewer, 'page_pixmaps') and hasattr(self.viewer, 'page_placeholders'):
            for pix, placeholder in zip(self.viewer.page_pixmaps, self.viewer.page_placeholders):
                items.append(pix if pix is not None else placeholder)
        else:
            return -1, None
        for i, item in enumerate(items):
            if item is not None and item.sceneBoundingRect().contains(scene_pos):
                return i, item
        return -1, None

    def _get_word_at_pos(self, local_pos, zoom):
        if not self.page_words: return None
        
        pdf_x, pdf_y = local_pos.x() / zoom, local_pos.y() / zoom
        best_idx = None
        best_dist = float('inf')
        
        for i, w in enumerate(self.page_words):
            rect = fitz.Rect(w[:4])
            if rect.contains(fitz.Point(pdf_x, pdf_y)):
                return i
                
            cx, cy = (w[0] + w[2]) / 2, (w[1] + w[3]) / 2
            dist = (pdf_x - cx)**2 + ((pdf_y - cy) * 4)**2 
            if dist < best_dist:
                best_dist = dist
                best_idx = i
                
        return best_idx if best_dist < 5000 else None

    def clear_selection(self):
        for h in self.temp_highlights:
            try:
                if h.scene():
                    self.viewer.scene.removeItem(h)
            except RuntimeError:
                pass # The C++ object was already deleted, safe to ignore
        self.temp_highlights.clear()
        self.selected_words.clear()
        self.start_word_idx = None

    def has_selection(self):
        return len(self.selected_words) > 0

    def is_pos_in_selection(self, scene_pos):
        if not self.temp_highlights: return False
        for h in self.temp_highlights:
            try:
                if h.sceneBoundingRect().contains(scene_pos):
                    return True
            except RuntimeError:
                pass
        return False

    def _event_scene_pos(self, event):
        if hasattr(self.viewer, "_event_scene_pos"):
            return self.viewer._event_scene_pos(event)
        return self.viewer.mapToScene(event.pos())

    def start_selection(self, event):
        self.clear_selection()
        scene_pos = self._event_scene_pos(event)
        self.current_page_idx, self.active_page_item = self._get_page_at_pos(scene_pos)
        
        if self.current_page_idx != -1 and self.viewer.doc and self.active_page_item is not None:
            self.is_selecting = True
            page = self.viewer.doc.load_page(self.current_page_idx)
            
            words = page.get_text("words")
            words.sort(key=lambda w: (w[5], w[6], w[7]))
            self.page_words = words
            
            local_pos = self.active_page_item.mapFromScene(scene_pos)
            self.start_word_idx = self._get_word_at_pos(local_pos, self.viewer.base_zoom)

    def update_selection(self, event):
        if self.is_selecting and self.start_word_idx is not None and self.active_page_item is not None:
            scene_pos = self._event_scene_pos(event)
            local_pos = self.active_page_item.mapFromScene(scene_pos)
            end_word_idx = self._get_word_at_pos(local_pos, self.viewer.base_zoom)
            
            if end_word_idx is not None:
                self._draw_temp_selection(self.start_word_idx, end_word_idx)

    def _draw_temp_selection(self, start_idx, end_idx):
        # Remove old temp highlights
        for h in self.temp_highlights:
            try:
                if h.scene():
                    self.viewer.scene.removeItem(h)
            except RuntimeError:
                pass
        self.temp_highlights.clear()

        lo, hi = sorted([start_idx, end_idx])
        zoom = self.viewer.base_zoom

        for w in self.page_words[lo:hi+1]:
            qt_rect = QRectF(w[0] * zoom, w[1] * zoom, (w[2]-w[0]) * zoom, (w[3]-w[1]) * zoom)
            scene_rect = self.active_page_item.mapToScene(qt_rect).boundingRect()
            h_item = QGraphicsRectItem(scene_rect)
            # Use a more visible blue highlight for selection
            h_item.setBrush(QBrush(QColor(51, 153, 255, 120)))
            h_item.setPen(QPen(Qt.PenStyle.NoPen))
            h_item.setZValue(1000)  # Ensure highlight is above the page
            self.viewer.scene.addItem(h_item)
            self.temp_highlights.append(h_item)

    def finish_selection(self, event):
        if not self.is_selecting:
            return
        self.is_selecting = False
        
        if self.start_word_idx is not None and self.temp_highlights and self.active_page_item is not None:
            scene_pos = self._event_scene_pos(event)
            local_pos = self.active_page_item.mapFromScene(scene_pos)
            end_word_idx = self._get_word_at_pos(local_pos, self.viewer.base_zoom)
            
            if end_word_idx is not None:
                lo, hi = sorted([self.start_word_idx, end_word_idx])
                self.selected_words = self.page_words[lo:hi+1]

   # gui/components/annotation_manager.py -> AnnotationManager class
    def show_context_menu(self, global_pos):
        menu = QMenu(self.viewer)
        menu.setStyleSheet("""
            QMenu { background-color: #2b2b2b; color: white; border: 1px solid #444; font-weight: bold; } 
            QMenu::item:selected { background-color: #0078D7; }
        """)
        
        # We need the extracted text right away so we can scan it for URLs
        extracted_text = " ".join(w[4] for w in self.selected_words) if self.selected_words else ""
        
        colors = [
            ("Yellow", (1.0, 0.9, 0.0)),
            ("Green", (0.0, 0.8, 0.4)),
            ("Blue", (0.2, 0.6, 1.0)),
            ("Purple", (0.7, 0.4, 1.0)),
            ("Red", (1.0, 0.3, 0.3))
        ]
        
        hl_menu = menu.addMenu("🖍️ Highlight...")
        for name, rgb in colors:
            action = QAction(f"{name}", self.viewer)
            action.triggered.connect(lambda checked, c=rgb: self.apply_highlight(c))
            hl_menu.addAction(action)
            
        # 🔥 PHASE 2: Regex URL Extractor
        if extracted_text:
            urls = re.findall(r'(https?://[^\s]+)', extracted_text)
            if urls:
                menu.addSeparator()
                for url in urls:
                    # Strip trailing punctuation that might get caught in the regex block
                    clean_url = url.rstrip('.,;:"\'()')
                    # Shorten visually so it doesn't stretch the menu across the screen
                    display_url = clean_url[:40] + "..." if len(clean_url) > 40 else clean_url
                    url_action = menu.addAction(f"🌐 Open: {display_url}")
                    # Use a default argument in the lambda (u=clean_url) to bind the loop variable safely
                    url_action.triggered.connect(lambda checked, u=clean_url: QDesktopServices.openUrl(QUrl(u)))
            
        menu.addSeparator()
        
        ai_action = menu.addAction("🤖 Ask AI About Selection")
        ai_action.triggered.connect(self.ask_ai_about_selection)
        
        reword_action = menu.addAction("✍️ Reword this")
        reword_action.triggered.connect(self.reword_selection)

        define_action = menu.addAction("📖 Define")
        define_action.triggered.connect(self.define_selection)
        
        menu.exec(global_pos)

    def define_selection(self):
        if not self.selected_words: return
        extracted_text = " ".join(w[4] for w in self.selected_words).strip()
        
        # Clean up punctuation attached to the highlighted word
        import string
        extracted_text = extracted_text.strip(string.punctuation)
        
        # Optional: Limit the dictionary lookup to a maximum of 3-4 words 
        # so users don't accidentally try to "define" a whole paragraph
        words = extracted_text.split()
        if len(words) > 4:
            extracted_text = " ".join(words[:4])

        main_window = self.viewer.window()
        
        # 1. Force the Dictionary Dock to spawn or revive
        if hasattr(main_window, 'spawn_dictionary_dock'):
            main_window.spawn_dictionary_dock()
            
        # 2. Push the text into the dictionary's public search method
        if hasattr(main_window, 'dict_docks') and main_window.dict_docks:
            dict_dock = main_window.dict_docks[0]
            dict_dock.public_search(extracted_text)
            
        self.clear_selection()

    def apply_highlight(self, color_tuple):
        if not self.selected_words: return
        
        extracted_text = " ".join(w[4] for w in self.selected_words)
        text, ok = QInputDialog.getText(self.viewer, "Add Note", "Enter a note for this highlight (Optional):")
        
        if ok:
            try:
                page = self.viewer.doc.load_page(self.current_page_idx)
                quads = [fitz.Rect(w[:4]).quad for w in self.selected_words]
                
                annot = page.add_highlight_annot(quads)
                annot.set_colors(stroke=color_tuple)
                
                annot_info = {
                    "title": f"UserNote|{uuid.uuid4()}",
                    "content": text if text else "",
                    "subject": extracted_text
                }
                annot.set_info(info=annot_info)
                annot.update()
                main_window = self.viewer.window()
                if hasattr(main_window, 'project_manager'):
                    main_window.project_manager.mark_dirty(main_window.current_file_path)
                self.highlight_created.emit({
                    "id": annot_info["title"],
                    "subject": annot_info["subject"],
                    "content": annot_info["content"],
                    "pdf_path": self.viewer.window().current_file_path,
                    "page_num": self.current_page_idx,
                    "rect_coords": repr(list(annot.rect)),
                    "color": QColor(int(color_tuple[0] * 255), int(color_tuple[1] * 255), int(color_tuple[2] * 255)).name(),
                })
                
                self.viewer.reload_page(self.current_page_idx)
                self.note_added.emit()
            except Exception as e:
                print(f"Error saving highlight: {e}")
                
        self.clear_selection()

    def ask_ai_about_selection(self):
        if not self.selected_words: return
        extracted_text = " ".join(w[4] for w in self.selected_words)
        
        main_window = self.viewer.window()
        
        # 1. Force the AI Chat dock to spawn or revive (since it's a strict singleton)
        if hasattr(main_window, 'spawn_chat_dock'):
            main_window.spawn_chat_dock()
        
        # 2. Push the extracted text into the input field
        if hasattr(main_window, 'chat_docks') and main_window.chat_docks:
            llm_dock = main_window.chat_docks[0]
            llm_dock.chat_input.setText(f"Explain this text: \"{extracted_text}\"")
            llm_dock.chat_input.setFocus()
        
        self.clear_selection()

    def reword_selection(self):
        if not self.selected_words: return
        extracted_text = " ".join(w[4] for w in self.selected_words)
        
        main_window = self.viewer.window()
        
        # NEW: Access the Chat Dock instead of the old Tabs dictionary
        if hasattr(main_window, 'chat_docks') and main_window.chat_docks:
            llm_dock = main_window.chat_docks[0]
            llm_manager = llm_dock.llm_manager
            model = llm_dock.model_combo.currentText()
            
            # Keep a reference to the dialog so it isn't garbage collected
            self.reword_dialog = RewordDialog(extracted_text, llm_manager, model, self.viewer)
            self.reword_dialog.show()
        else:
            # Failsafe: Ensure the dock is open first
            if hasattr(main_window, 'spawn_chat_dock'):
                main_window.spawn_chat_dock()
                self.reword_selection() # Try again once spawned
                return
        
        self.clear_selection()

class RewordWorker(QThread):
    token_received = Signal(str)
    finished = Signal()

    def __init__(self, llm_manager, model, text, parent=None):
        super().__init__(parent)
        self.llm_manager = llm_manager
        self.model = model
        self.text = text

    def run(self):
        system_prompt = (
            "You are an expert editor. Rewrite the following text to make it easier "
            "to understand and follow, while keeping all crucial information intact. "
            "Respond ONLY with the reworded text. Do not include introductory phrases."
        )
        try:
            def handle_chunk(chunk):
                self.token_received.emit(chunk)

            self.llm_manager.query(
                question=f"\"{self.text}\"",
                selected_model=self.model,
                allowed_docs=[],
                callback=handle_chunk,
                rag_enabled=False,
                use_agents=False,
                custom_system_prompt=system_prompt
            )
        except Exception as e:
            self.token_received.emit(f"\n[Error: {str(e)}]")
        finally:
            self.finished.emit()

class RewordDialog(QDialog):
    def __init__(self, original_text, llm_manager, model, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AI Reword")
        self.resize(450, 300)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)

        layout = QVBoxLayout(self)

        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setStyleSheet("background-color: #1e1e1e; color: #ddd; font-size: 14px; padding: 10px; border: 1px solid #444;")
        layout.addWidget(self.text_edit)

        self.close_btn = QPushButton("Close")
        self.close_btn.setStyleSheet("background-color: #444; padding: 5px;")
        self.close_btn.clicked.connect(self.accept)
        layout.addWidget(self.close_btn)

        # Start the worker thread
        self.worker = RewordWorker(llm_manager, model, original_text, self)
        self.worker.token_received.connect(self.append_text)
        self.worker.start()

    def append_text(self, token):
        cursor = self.text_edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(token)
        self.text_edit.setTextCursor(cursor)