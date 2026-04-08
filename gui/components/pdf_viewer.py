# gui/components/pdf_viewer.py
import fitz
import weakref
from PyQt6.QtWidgets import (QGraphicsView, QGraphicsScene, QGraphicsPixmapItem, QGraphicsRectItem,
                             QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QComboBox, QCheckBox)
from PyQt6.QtGui import QImage, QPixmap, QPainter, QColor, QBrush, QPen, QIntValidator
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QRectF, QTimer

from gui.components.annotation_manager import AnnotationManager
from gui.components.search_bar_widget import SearchBarWidget

class RenderWorker(QThread):
    page_ready = pyqtSignal(int, QImage)
    finished_rendering = pyqtSignal()

    def __init__(self, doc, zoom, page_num, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.zoom = zoom
        self.page_num = page_num
        self._is_running = True

    def run(self):
        # [PERF FIX] Render only requested page, not entire document
        if not self._is_running or self.page_num >= len(self.doc):
            return
        try:
            mat = fitz.Matrix(self.zoom, self.zoom)
            page = self.doc.load_page(self.page_num)
            pix = page.get_pixmap(matrix=mat)
            img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888)
            self.page_ready.emit(self.page_num, img.copy())
            # [PERF FIX] Explicitly free PyMuPDF memory
            del pix
            del img
        except Exception as e:
            print(f"[PERF] Render error for page {self.page_num}: {e}")
        self.finished_rendering.emit()

    def stop(self):
        self._is_running = False

class PDFViewer(QGraphicsView):
    annotation_clicked = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        
        self.doc = None
        self.base_zoom = 1.5
        # [PERF FIX] Track page items and workers separately for lazy loading
        self.page_items = {}  # page_num -> QGraphicsPixmapItem or QGraphicsRectItem
        self.page_placeholders = {}  # page_num -> (width, height) dimensions
        self.active_workers = {}  # page_num -> RenderWorker
        self.annot_manager = AnnotationManager(self)
        
        self.pending_jump = None
        self.pending_page_jump = None
        self.current_page = 0

        self.search_bar = SearchBarWidget(self)
        self.search_bar.hide()
        self.search_hits = []
        self.current_hit_index = -1
        self.search_highlight_items = []
        self.pending_search_jump = None
        
        self.current_search_text = ""
        self.current_search_scope = ""
        self.current_match_case = False

        self.search_debounce_timer = QTimer(self)
        self.search_debounce_timer.setSingleShot(True)
        self.search_debounce_timer.timeout.connect(self.trigger_search)
        
        self.search_bar.search_input.textChanged.connect(self._on_search_text_changed)
        self.search_bar.search_input.returnPressed.connect(self._on_search_return_pressed)
        self.search_bar.btn_next.clicked.connect(self.next_search_hit)
        self.search_bar.btn_prev.clicked.connect(self.prev_search_hit)
        self.search_bar.btn_close.clicked.connect(self.toggle_search_bar)
        self.search_bar.scope_combo.currentIndexChanged.connect(self.trigger_search)
        self.search_bar.chk_match_case.stateChanged.connect(self.trigger_search)

        self.scroll_debounce_timer = QTimer(self)
        self.scroll_debounce_timer.setSingleShot(True)
        self.scroll_debounce_timer.timeout.connect(self._on_scroll)

        self.verticalScrollBar().valueChanged.connect(self._on_fast_scroll)
        self.last_visible_pages = set()

        self.page_indicator_frame = QFrame(self.viewport())
        self.page_indicator_frame.setObjectName("PageIndicator")
        self.page_indicator_frame.setStyleSheet(
            "QFrame#PageIndicator { background-color: rgba(30, 30, 30, 0.85); border-radius: 12px; }"
            "QLabel { color: white; font-size: 12px; }"
            "QLineEdit { color: #111111; background: rgba(255,255,255,0.98); border: 1px solid rgba(0,0,0,0.2); border-radius: 6px; padding: 4px 6px; }"
            "QLineEdit:hover { background: rgba(255,255,255,1); }"
            "QPushButton { background: rgba(255,255,255,0.96); color: #1e1e1e; border: 1px solid rgba(0,0,0,0.12); border-radius: 8px; padding: 4px 10px; }"
        )
        self.page_indicator_frame.hide()

        page_indicator_layout = QHBoxLayout(self.page_indicator_frame)
        page_indicator_layout.setContentsMargins(10, 6, 10, 6)
        page_indicator_layout.setSpacing(8)

        self.page_indicator_label = QLabel("Page 0 / 0")
        self.page_indicator_label.setStyleSheet("font-weight: bold;")
        page_indicator_layout.addWidget(self.page_indicator_label)

        self.page_indicator_input = QLineEdit(self.page_indicator_frame)
        self.page_indicator_input.setFixedWidth(50)
        self.page_indicator_input.setPlaceholderText("Jump")
        self.page_indicator_input.setValidator(QIntValidator(1, 9999, self.page_indicator_input))
        self.page_indicator_input.setStyleSheet(
            "color: #111111; background: rgba(255,255,255,0.95); border: 1px solid rgba(0,0,0,0.18); border-radius: 6px; padding: 4px 6px;"
        )
        self.page_indicator_input.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.page_indicator_input.returnPressed.connect(self._on_page_indicator_go)
        page_indicator_layout.addWidget(self.page_indicator_input)

        self.page_indicator_go = QPushButton("Go", self.page_indicator_frame)
        self.page_indicator_go.setFixedHeight(26)
        self.page_indicator_go.clicked.connect(self._on_page_indicator_go)
        page_indicator_layout.addWidget(self.page_indicator_go)

    def update_theme(self, theme):
        self.search_bar.update_theme(theme)
        self.setBackgroundBrush(QBrush(QColor(theme['canvas'])))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'search_bar') and self.search_bar.isVisible():
            self.search_bar.adjustSize()
            x_pos = self.viewport().width() - self.search_bar.width() - 20
            self.search_bar.move(x_pos, 20)
        if hasattr(self, 'page_indicator_frame') and self.page_indicator_frame.isVisible():
            self._position_page_indicator()

    def toggle_search_bar(self):
        if self.search_bar.isVisible():
            self.search_bar.hide()
            self.clear_search_highlights()
            self.search_hits.clear()
            self.current_search_text = ""
        else:
            self.search_bar.show()
            self.search_bar.adjustSize()
            x_pos = self.viewport().width() - self.search_bar.width() - 20
            self.search_bar.move(x_pos, 20)
            
            self.search_bar.search_input.setFocus()
            self.search_bar.search_input.selectAll()

    def _on_search_text_changed(self, text):
        self.search_debounce_timer.start(400)

    def _on_search_return_pressed(self):
        if self.search_debounce_timer.isActive():
            self.search_debounce_timer.stop()
            self.trigger_search()
        else:
            self.next_search_hit()

    def trigger_search(self):
        text = self.search_bar.search_input.text().strip()
        scope = self.search_bar.scope_combo.currentText()
        match_case = self.search_bar.chk_match_case.isChecked()
        
        if (text == self.current_search_text and 
            scope == self.current_search_scope and 
            match_case == self.current_match_case):
            return 
            
        self.current_search_scope = scope
        self.current_match_case = match_case
        self.execute_search(text, scope, match_case)

    def execute_search(self, text, scope, match_case):
        self.search_hits = []
        self.current_hit_index = -1
        self.current_search_text = text
        
        if not text:
            self.clear_search_highlights()
            self.search_bar.update_hits(0, 0)
            return

        main_window = self.window()
        pdfs_to_search = []
        if scope == "Entire Project" and main_window and hasattr(main_window, 'pdf_controller'):
            pdfs_to_search = main_window.pdf_controller.get_pdf_paths()
        else:
            if main_window and main_window.current_file_path:
                pdfs_to_search = [main_window.current_file_path]
                
        flags = fitz.TEXT_PRESERVE_LIGATURES | fitz.TEXT_PRESERVE_WHITESPACE
        if match_case:
            flags |= getattr(fitz, "TEXT_MATCH_CASE", 4)
                
        for pdf_path in pdfs_to_search:
            doc = main_window.pdf_controller.get_doc(pdf_path) if main_window and hasattr(main_window, "pdf_controller") else None
            if not doc: continue
            for page_num in range(len(doc)):
                page = doc.load_page(page_num)
                quads = page.search_for(text, hit_max=999, quads=True, flags=flags)
                for q in quads:
                    self.search_hits.append({
                        'pdf': pdf_path,
                        'page': page_num,
                        'rect': q.rect
                    })
                    
        if self.search_hits:
            self.current_hit_index = 0
            self.search_bar.update_hits(1, len(self.search_hits))
            self.navigate_to_current_hit()
        else:
            self.clear_search_highlights()
            self.search_bar.update_hits(0, 0)

    def next_search_hit(self):
        if not self.search_hits: return
        self.current_hit_index = (self.current_hit_index + 1) % len(self.search_hits)
        self.search_bar.update_hits(self.current_hit_index + 1, len(self.search_hits))
        self.navigate_to_current_hit()

    def prev_search_hit(self):
        if not self.search_hits: return
        self.current_hit_index = (self.current_hit_index - 1) % len(self.search_hits)
        self.search_bar.update_hits(self.current_hit_index + 1, len(self.search_hits))
        self.navigate_to_current_hit()

    def navigate_to_current_hit(self):
        if not self.search_hits or self.current_hit_index < 0: return
        
        hit = self.search_hits[self.current_hit_index]
        main_window = self.window()
        
        if hit['pdf'] != main_window.current_file_path:
            self.pending_search_jump = hit
            if main_window and hasattr(main_window, "pdf_controller"):
                main_window.pdf_controller.switch_to_pdf(hit['pdf'])
            else:
                main_window.switch_to_pdf(hit['pdf'])
        else:
            self.render_search_highlights()
            page_num = hit['page']
            
            if page_num in self.page_items:
                self._execute_search_jump(hit)
            else:
                self.pending_search_jump = hit
                # Trigger lazy load of this page
                self._request_page_render(page_num)

    def clear_search_highlights(self):
        for h in self.search_highlight_items:
            try:
                if h.scene():
                    self.scene.removeItem(h)
            except RuntimeError:
                pass
        # [PERF FIX] Clear list reference immediately after removing items
        self.search_highlight_items.clear()

    def render_search_highlights(self):
        self.clear_search_highlights()
        if not self.search_hits: return
        for page_num in self.page_items.keys():
            self._apply_search_highlights_to_page(page_num, self.page_items[page_num])

    def _apply_search_highlights_to_page(self, page_num, page_item):
        current_pdf = self.window().current_file_path
        
        for i, hit in enumerate(self.search_hits):
            if hit['pdf'] == current_pdf and hit['page'] == page_num:
                r = hit['rect']
                z = self.base_zoom
                qt_rect = QRectF(r.x0 * z, r.y0 * z, (r.x1 - r.x0) * z, (r.y1 - r.y0) * z)
                
                h_item = QGraphicsRectItem(qt_rect, page_item)
                
                if i == self.current_hit_index:
                    h_item.setBrush(QBrush(QColor(255, 165, 0, 150))) 
                    h_item.setPen(QPen(QColor(255, 140, 0), 2))
                    h_item.setZValue(10)
                else:
                    h_item.setBrush(QBrush(QColor(255, 255, 0, 100))) 
                    h_item.setPen(QPen(Qt.PenStyle.NoPen))
                    h_item.setZValue(5)
                    
                self.search_highlight_items.append(h_item)

    def _execute_search_jump(self, hit):
        page_num = hit['page']
        if page_num in self.page_items:
            page_item = self.page_items[page_num]
            r = hit['rect']
            z = self.base_zoom
            qt_rect = QRectF(r.x0 * z, r.y0 * z, (r.x1 - r.x0) * z, (r.y1 - r.y0) * z)
            scene_rect = page_item.mapToScene(qt_rect).boundingRect()
            
            self.ensureVisible(scene_rect, 100, 100)



    def _cleanup_workers(self):
        for page_num, worker in list(self.active_workers.items()):
            if worker.isRunning():
                worker.stop()
                worker.wait()  # CRITICAL: Block until C++ thread exits to prevent core dump
                worker.deleteLater()
            del self.active_workers[page_num]

    def _on_fast_scroll(self):
        if not self.doc:
            return

        visible_pages = self._get_visible_pages()
        if visible_pages:
            sorted_pages = sorted(visible_pages, key=lambda p: self.page_items[p].sceneBoundingRect().top())
            self.current_page = sorted_pages[0]
            self._update_page_indicator()

        self.scroll_debounce_timer.start(10)

    def _on_scroll(self):
        # [PERF FIX] Determine visible pages and trigger lazy loading
        if not self.doc:
            return
        
        visible_pages = self._get_visible_pages()
        
        # If no pages are visible (shouldn't happen, but safety check), load first page
        if not visible_pages and len(self.page_items) > 0:
            visible_pages.add(0)

        if visible_pages:
            sorted_pages = sorted(visible_pages, key=lambda p: self.page_items[p].sceneBoundingRect().top())
            self.current_page = sorted_pages[0]
        else:
            self.current_page = 0

        self._update_page_indicator()
        
        # Load visible pages and buffer (+/- 2 pages)
        pages_to_load = set()
        for page_num in visible_pages:
            pages_to_load.add(page_num)
            if page_num > 0:
                pages_to_load.add(page_num - 1)
            if page_num < len(self.doc) - 1:
                pages_to_load.add(page_num + 1)
            if page_num > 1:
                pages_to_load.add(page_num - 2)
            if page_num < len(self.doc) - 2:
                pages_to_load.add(page_num + 2)
        
        # Unload pages farther than 3 pages away from viewport
        min_page = min(visible_pages) if visible_pages else 0
        max_page = max(visible_pages) if visible_pages else 0
        for page_num in list(self.page_items.keys()):
            if page_num < min_page - 3 or page_num > max_page + 3:
                item = self.page_items[page_num]
                if isinstance(item, QGraphicsPixmapItem):
                    item.setPixmap(QPixmap())
                    if page_num in self.page_placeholders:
                        width, height = self.page_placeholders[page_num]
                        y_pos = item.y()
                        self.scene.removeItem(item)
                        placeholder = QGraphicsRectItem(0, 0, width, height)
                        placeholder.setBrush(QBrush(QColor(240, 240, 240)))
                        placeholder.setPen(QPen(QColor(200, 200, 200)))
                        placeholder.setPos(0, y_pos)
                        self.scene.addItem(placeholder)
                        self.page_items[page_num] = placeholder

        # Request renders for visible pages
        for page_num in pages_to_load:
            if page_num not in self.active_workers:
                self._request_page_render(page_num)

    def _get_visible_pages(self):
        # Determine which pages are in the current viewport
        visible_pages = set()
        
        # If viewport is invalid, return empty (will be handled by _on_scroll)
        if self.viewport().height() <= 0 or self.viewport().width() <= 0:
            return visible_pages
        
        viewport_rect = self.mapToScene(self.viewport().geometry()).boundingRect()
        
        for page_num, item in self.page_items.items():
            if item.sceneBoundingRect().intersects(viewport_rect):
                visible_pages.add(page_num)
        
        return visible_pages

    def _position_page_indicator(self):
        if not hasattr(self, 'page_indicator_frame'):
            return
        margin = 18
        width = self.page_indicator_frame.width()
        height = self.page_indicator_frame.height()
        viewport_height = self.viewport().height()
        # Position at bottom-left to avoid overlap with top-right argument map button
        x = margin
        y = viewport_height - height - margin
        self.page_indicator_frame.move(x, y)
        self.page_indicator_frame.raise_()

    def _update_page_indicator(self):
        if not self.doc:
            self.page_indicator_frame.hide()
            return

        total_pages = len(self.doc)
        current = max(1, min(self.current_page + 1, total_pages))
        self.page_indicator_label.setText(f"Page {current} / {total_pages}")
        self.page_indicator_frame.adjustSize()
        self._position_page_indicator()
        self.page_indicator_frame.show()

    def _on_page_indicator_go(self):
        if not self.doc:
            return
        text = self.page_indicator_input.text().strip()
        if not text.isdigit():
            return
        page_num = int(text) - 1
        if page_num < 0 or page_num >= len(self.doc):
            return
        self.jump_to_page(page_num)
        self.page_indicator_input.clear()

    def _request_page_render(self, page_num):
        # [PERF FIX] Only render if not already rendering and page is placeholder
        if page_num in self.active_workers:
            return
        
        if page_num not in self.page_items:
            return
        
        item = self.page_items[page_num]
        if isinstance(item, QGraphicsPixmapItem):
            return  # Already rendered
        
        # Start worker for this page
        worker = RenderWorker(self.doc, self.base_zoom, page_num, parent=self)
        worker.page_ready.connect(self._on_page_ready)
        worker.finished.connect(lambda pn=page_num: self._on_worker_finished(pn))
        self.active_workers[page_num] = worker
        worker.start()

    def _on_page_ready(self, page_num, qimage):
        if page_num not in self.page_items or not self.doc:
            return
        
        pixmap = QPixmap.fromImage(qimage)
        old_item = self.page_items[page_num]
        y_pos = old_item.y()
        
        # Remove old placeholder
        self.scene.removeItem(old_item)
        
        # Add rendered pixmap
        new_item = QGraphicsPixmapItem(pixmap)
        new_item.setPos(0, y_pos)
        self.scene.addItem(new_item)
        self.page_items[page_num] = new_item
        
        # Update scene rect to account for rendered page
        self.scene.setSceneRect(self.scene.itemsBoundingRect())
        
        if self.search_hits and self.current_search_text:
            self._apply_search_highlights_to_page(page_num, new_item)

        if self.pending_jump and self.pending_jump[0] == page_num:
            p_num, a_id = self.pending_jump
            self.pending_jump = None
            QTimer.singleShot(100, lambda: self._execute_jump(p_num, a_id))
            
        if self.pending_page_jump == page_num:
            self.pending_page_jump = None
            QTimer.singleShot(100, lambda: self.jump_to_page(page_num))

        if self.pending_search_jump and self.pending_search_jump['page'] == page_num:
            s_hit = self.pending_search_jump
            self.pending_search_jump = None
            QTimer.singleShot(100, lambda: self._execute_search_jump(s_hit))

    def _on_worker_finished(self, page_num):
        # Clean up worker reference
        if page_num in self.active_workers:
            del self.active_workers[page_num]

    def reload_page(self, page_num):
        if not self.doc or page_num < 0 or page_num >= len(self.page_items): 
            return
        
        # 1. Stop any active rendering for this page
        if page_num in self.active_workers:
            worker = self.active_workers[page_num]
            worker.stop()
            worker.wait()  # CRITICAL: Prevent QThread core dumps
            worker.deleteLater()
            del self.active_workers[page_num]
        
        # 2. Swap whatever is currently on screen back to a placeholder
        if page_num in self.page_items:
            old_item = self.page_items[page_num]
            self.scene.removeItem(old_item)
            
            if page_num in self.page_placeholders:
                width, height = self.page_placeholders[page_num]
                y_pos = old_item.y() if hasattr(old_item, 'y') else 0
                
                placeholder = QGraphicsRectItem(0, 0, width, height)
                placeholder.setBrush(QBrush(QColor(240, 240, 240)))
                placeholder.setPen(QPen(QColor(200, 200, 200)))
                placeholder.setPos(0, y_pos)
                
                self.scene.addItem(placeholder)
                self.page_items[page_num] = placeholder
        
        # 3. Request a fresh render
        self._request_page_render(page_num)

    def mousePressEvent(self, event):
            self.search_bar.search_input.selectAll()

    def _on_search_text_changed(self, text):
        self.search_debounce_timer.start(400)

    def _on_search_return_pressed(self):
        if self.search_debounce_timer.isActive():
            self.search_debounce_timer.stop()
            self.trigger_search()
        else:
            self.next_search_hit()

    def trigger_search(self):
        text = self.search_bar.search_input.text().strip()
        scope = self.search_bar.scope_combo.currentText()
        match_case = self.search_bar.chk_match_case.isChecked()
        
        if (text == self.current_search_text and 
            scope == self.current_search_scope and 
            match_case == self.current_match_case):
            return 
            
        self.current_search_scope = scope
        self.current_match_case = match_case
        self.execute_search(text, scope, match_case)

    def execute_search(self, text, scope, match_case):
        self.search_hits = []
        self.current_hit_index = -1
        self.current_search_text = text
        
        if not text:
            self.clear_search_highlights()
            self.search_bar.update_hits(0, 0)
            return

        main_window = self.window()
        pdfs_to_search = []
        if scope == "Entire Project" and main_window and hasattr(main_window, 'pdf_controller'):
            pdfs_to_search = main_window.pdf_controller.get_pdf_paths()
        else:
            if main_window and main_window.current_file_path:
                pdfs_to_search = [main_window.current_file_path]
                
        flags = fitz.TEXT_PRESERVE_LIGATURES | fitz.TEXT_PRESERVE_WHITESPACE
        if match_case:
            flags |= getattr(fitz, "TEXT_MATCH_CASE", 4)
                
        for pdf_path in pdfs_to_search:
            doc = main_window.pdf_controller.get_doc(pdf_path) if main_window and hasattr(main_window, "pdf_controller") else None
            if not doc: continue
            for page_num in range(len(doc)):
                page = doc.load_page(page_num)
                quads = page.search_for(text, hit_max=999, quads=True, flags=flags)
                for q in quads:
                    self.search_hits.append({
                        'pdf': pdf_path,
                        'page': page_num,
                        'rect': q.rect
                    })
                    
        if self.search_hits:
            self.current_hit_index = 0
            self.search_bar.update_hits(1, len(self.search_hits))
            self.navigate_to_current_hit()
        else:
            self.clear_search_highlights()
            self.search_bar.update_hits(0, 0)

    def next_search_hit(self):
        if not self.search_hits: return
        self.current_hit_index = (self.current_hit_index + 1) % len(self.search_hits)
        self.search_bar.update_hits(self.current_hit_index + 1, len(self.search_hits))
        self.navigate_to_current_hit()

    def prev_search_hit(self):
        if not self.search_hits: return
        self.current_hit_index = (self.current_hit_index - 1) % len(self.search_hits)
        self.search_bar.update_hits(self.current_hit_index + 1, len(self.search_hits))
        self.navigate_to_current_hit()

    def navigate_to_current_hit(self):
        if not self.search_hits or self.current_hit_index < 0: return
        
        hit = self.search_hits[self.current_hit_index]
        main_window = self.window()
        
        if hit['pdf'] != main_window.current_file_path:
            self.pending_search_jump = hit
            if main_window and hasattr(main_window, "pdf_controller"):
                main_window.pdf_controller.switch_to_pdf(hit['pdf'])
            else:
                main_window.switch_to_pdf(hit['pdf'])
        else:
            self.render_search_highlights()
            page_num = hit['page']
            
            if page_num < len(self.page_items):
                self._execute_search_jump(hit)
            else:
                self.pending_search_jump = hit

    def clear_search_highlights(self):
        for h in self.search_highlight_items:
            try:
                if h.scene():
                    self.scene.removeItem(h)
            except RuntimeError:
                pass
        self.search_highlight_items.clear()

    def render_search_highlights(self):
        self.clear_search_highlights()
        if not self.search_hits: return
        for page_num in range(len(self.page_items)):
            self._apply_search_highlights_to_page(page_num, self.page_items[page_num])

    def _apply_search_highlights_to_page(self, page_num, page_item):
        current_pdf = self.window().current_file_path
        
        for i, hit in enumerate(self.search_hits):
            if hit['pdf'] == current_pdf and hit['page'] == page_num:
                r = hit['rect']
                z = self.base_zoom
                qt_rect = QRectF(r.x0 * z, r.y0 * z, (r.x1 - r.x0) * z, (r.y1 - r.y0) * z)
                
                h_item = QGraphicsRectItem(qt_rect, page_item)
                
                if i == self.current_hit_index:
                    h_item.setBrush(QBrush(QColor(255, 165, 0, 150))) 
                    h_item.setPen(QPen(QColor(255, 140, 0), 2))
                    h_item.setZValue(10)
                else:
                    h_item.setBrush(QBrush(QColor(255, 255, 0, 100))) 
                    h_item.setPen(QPen(Qt.PenStyle.NoPen))
                    h_item.setZValue(5)
                    
                self.search_highlight_items.append(h_item)

    def _execute_search_jump(self, hit):
        page_num = hit['page']
        if 0 <= page_num < len(self.page_items):
            page_item = self.page_items[page_num]
            r = hit['rect']
            z = self.base_zoom
            qt_rect = QRectF(r.x0 * z, r.y0 * z, (r.x1 - r.x0) * z, (r.y1 - r.y0) * z)
            scene_rect = page_item.mapToScene(qt_rect).boundingRect()
            
            self.ensureVisible(scene_rect, 100, 100)

    def load_document(self, doc):
        self._cleanup_workers()
        self.doc = doc
        self.annot_manager.clear_selection()
        self.clear_search_highlights()
        self.scene.clear()
        self.page_items.clear()
        self.page_placeholders.clear()
        self.pending_jump = None 
        
        if not doc or len(doc) == 0:
            return True
        
        y_offset = 0
        for page_num in range(len(doc)):
            # ACCURATE SIZING: Use actual page rect to prevent overlapping
            rect = doc[page_num].rect
            width = rect.width * self.base_zoom
            height = rect.height * self.base_zoom
            
            self.page_placeholders[page_num] = (width, height)
            
            placeholder = QGraphicsRectItem(0, 0, width, height)
            placeholder.setBrush(QBrush(QColor(240, 240, 240)))
            placeholder.setPen(QPen(QColor(200, 200, 200)))
            placeholder.setPos(0, y_offset)
            
            self.scene.addItem(placeholder)
            self.page_items[page_num] = placeholder
            
            y_offset += height + 20 # 20px buffer between pages
        
        self.scene.setSceneRect(self.scene.itemsBoundingRect())
        self.current_page = 0
        self._update_page_indicator()
        
        max_initial = min(5, len(doc))
        for page_num in range(max_initial):
            self._request_page_render(page_num)
        
        QTimer.singleShot(50, self._on_scroll)
        return True
    def _on_worker_finished(self, page_num):
        # [PERF FIX] Clean up worker reference after completion
        if page_num in self.active_workers:
            worker = self.active_workers[page_num]
            if worker.isRunning():
                worker.stop()
            del self.active_workers[page_num]

    def reload_page(self, page_num):
        if not self.doc or page_num < 0 or page_num >= len(self.page_items): 
            return
        
        # 1. Stop any active rendering for this page
        if page_num in self.active_workers:
            worker = self.active_workers[page_num]
            worker.stop()
            worker.wait()  # CRITICAL: Prevent QThread core dumps
            worker.deleteLater()
            del self.active_workers[page_num]
        
        # 2. Swap whatever is currently on screen back to a placeholder
        if page_num in self.page_items:
            old_item = self.page_items[page_num]
            self.scene.removeItem(old_item)
            
            if page_num in self.page_placeholders:
                width, height = self.page_placeholders[page_num]
                y_pos = old_item.y() if hasattr(old_item, 'y') else 0
                
                placeholder = QGraphicsRectItem(0, 0, width, height)
                placeholder.setBrush(QBrush(QColor(240, 240, 240)))
                placeholder.setPen(QPen(QColor(200, 200, 200)))
                placeholder.setPos(0, y_pos)
                
                self.scene.addItem(placeholder)
                self.page_items[page_num] = placeholder
        
        # 3. Request a fresh render
        self._request_page_render(page_num)

    def mousePressEvent(self, event):
        is_shift = event.modifiers() == Qt.KeyboardModifier.ShiftModifier
        is_right = event.button() == Qt.MouseButton.RightButton
        is_left = event.button() == Qt.MouseButton.LeftButton
        
        if is_right and self.annot_manager.has_selection():
            scene_pos = self.mapToScene(event.pos())
            if self.annot_manager.is_pos_in_selection(scene_pos):
                self.annot_manager.show_context_menu(event.globalPosition().toPoint())
                return

        if is_right or (is_left and is_shift):
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            self.viewport().setCursor(Qt.CursorShape.IBeamCursor)
            self.annot_manager.start_selection(event)
            return
        
        if is_left:
            scene_pos = self.mapToScene(event.pos())
            
            if self.annot_manager.has_selection() and not self.annot_manager.is_pos_in_selection(scene_pos):
                self.annot_manager.clear_selection()
                
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            page_idx, page_item = self.annot_manager._get_page_at_pos(scene_pos)
            
            if page_idx != -1 and self.doc:
                local_pos = page_item.mapFromScene(scene_pos)
                pdf_x, pdf_y = local_pos.x() / self.base_zoom, local_pos.y() / self.base_zoom
                point = fitz.Point(pdf_x, pdf_y)
                page = self.doc.load_page(page_idx)
                for annot in page.annots():
                    if annot.rect.contains(point) and (annot.info.get("title", "").startswith("UserNote") or annot.info.get("title", "").startswith("AINote")):
                        self.annotation_clicked.emit(annot.info.get("title"))
                        return 
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.annot_manager.is_selecting:
            self.annot_manager.update_selection(event)
        else: 
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.annot_manager.is_selecting:
            self.annot_manager.finish_selection(event)
            self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        super().mouseReleaseEvent(event)
        
    def jump_to_page(self, page_num):
        if not self.doc or page_num < 0 or page_num >= len(self.page_items):
            return

        if page_num in self.page_items and isinstance(self.page_items[page_num], QGraphicsPixmapItem):
            target_item = self.page_items[page_num]
            top_edge = QRectF(target_item.scenePos().x(), target_item.scenePos().y(), target_item.boundingRect().width(), 1)
            self.ensureVisible(top_edge, 50, 10)
            self.current_page = page_num
            self._update_page_indicator()
            return

        # If page is not yet rendered, queue a jump and request render
        self.pending_page_jump = page_num
        self._request_page_render(page_num)

    def jump_to_annotation(self, page_num, annot_id):
        if page_num >= len(self.page_items):
            self.pending_jump = (page_num, annot_id)
        else:
            self._execute_jump(page_num, annot_id)

    def _execute_jump(self, page_num, annot_id):
        if 0 <= page_num < len(self.page_items) and self.doc:
            target_item = self.page_items[page_num]
            page = self.doc.load_page(page_num)
            for annot in page.annots():
                if annot.info.get("title") == annot_id:
                    r = annot.rect
                    z = self.base_zoom
                    qt_rect = QRectF(r.x0 * z, r.y0 * z, (r.x1 - r.x0) * z, (r.y1 - r.y0) * z)
                    scene_rect = target_item.mapToScene(qt_rect).boundingRect()
                    
                    self.ensureVisible(scene_rect, 300, 300)
                    self.centerOn(scene_rect.center())
                    return
            
            self.jump_to_page(page_num)

    def zoom_in(self): self.scale(1.2, 1.2)
    def zoom_out(self): self.scale(1 / 1.2, 1 / 1.2)
    def zoom_reset(self):
        if not self.page_items: return
        self.resetTransform()
        view_width = self.viewport().width()
        doc_width = self.page_items[0].boundingRect().width()
        if doc_width > 0:
            target_scale = (view_width - 40) / doc_width
            self.scale(target_scale, target_scale)