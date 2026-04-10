# gui/components/pdf_viewer.py
import fitz
from PyQt6.QtWidgets import (QGraphicsView, QGraphicsScene, QGraphicsPixmapItem, QGraphicsRectItem,
                             QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QComboBox, QCheckBox)
from PyQt6.QtGui import QImage, QPixmap, QPainter, QColor, QBrush, QPen
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QRectF, QTimer
from PyQt6.QtCore import QPointF

from gui.components.annotation_manager import AnnotationManager
from gui.components.search_bar_widget import SearchBarWidget

class PageHUD(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
            background-color: rgba(30, 30, 30, 200);
            border-radius: 8px;
            color: white;
            padding: 4px;
        """)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 8, 2)
        self.label_page = QLabel("Page:", self)
        self.line_edit = QLineEdit(self)
        self.line_edit.setFixedWidth(40)
        self.line_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.line_edit.setStyleSheet("""
            background: #222;
            color: white;
            border: 1px solid #444;
            border-radius: 4px;
            padding: 2px 4px;
        """)
        self.label_total = QLabel("/ 1", self)
        self.btn_jump = QPushButton("Jump", self)
        self.btn_jump.setStyleSheet("""
            background: #444;
            color: white;
            border-radius: 4px;
            padding: 2px 8px;
        """)
        layout.addWidget(self.label_page)
        layout.addWidget(self.line_edit)
        layout.addWidget(self.label_total)
        layout.addWidget(self.btn_jump)
        self.setLayout(layout)
        self._block_update = False

    def update_hud(self, current_page, total_pages):
        self._block_update = True
        self.line_edit.setText(str(current_page))
        self.label_total.setText(f"/ {total_pages}")
        self._block_update = False



from queue import Queue
from core.render_worker import RenderWorker

class PDFViewer(QGraphicsView):
    def viewportEvent(self, event):
            # Keep HUD in bottom left of viewport on scroll/resize
            result = super().viewportEvent(event)
            if hasattr(self, 'page_hud') and self.page_hud.isVisible():
                self.page_hud.move(20, self.viewport().height() - self.page_hud.height() - 20)
            return result
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
        self.page_placeholders = []  # QGraphicsRectItem for each page
        self.page_pixmaps = [None] * 0  # QGraphicsPixmapItem for each page, or None
        self.worker = None
        self.annot_manager = AnnotationManager(self)
        
        self.pending_jump = None 

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

        # Page HUD
        self.page_hud = PageHUD(self)
        self.page_hud.setParent(self.viewport())
        self.page_hud.setVisible(False)
        # Remove invalid z-index property (not supported in Qt stylesheets)
        self.page_hud.btn_jump.clicked.connect(self._hud_jump_requested)
        self.page_hud.line_edit.returnPressed.connect(self._hud_jump_requested)

    def update_theme(self, theme):
        self.search_bar.update_theme(theme)
        self.setBackgroundBrush(QBrush(QColor(theme['canvas'])))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'search_bar') and self.search_bar.isVisible():
            self.search_bar.adjustSize()
            x_pos = self.viewport().width() - self.search_bar.width() - 20
            self.search_bar.move(x_pos, 20)
        # Position HUD in bottom left
        if hasattr(self, 'page_hud') and self.page_hud.isVisible():
            self.page_hud.move(20, self.viewport().height() - self.page_hud.height() - 20)

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
        if scope == "Entire Project":
            pdfs_to_search = main_window.project_manager.pdfs
        else:
            if main_window.current_file_path:
                pdfs_to_search = [main_window.current_file_path]
                
        flags = fitz.TEXT_PRESERVE_LIGATURES | fitz.TEXT_PRESERVE_WHITESPACE
        if match_case:
            flags |= getattr(fitz, "TEXT_MATCH_CASE", 4)
                
        for pdf_path in pdfs_to_search:
            doc = main_window.project_manager.get_doc(pdf_path)
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
            main_window.switch_to_pdf(hit['pdf'])
        else:
            self.render_search_highlights()
            page_num = hit['page']
            if page_num < len(self.page_pixmaps):
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
        for page_num in range(len(self.page_pixmaps)):
            self._apply_search_highlights_to_page(page_num, self.page_pixmaps[page_num])

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
        if 0 <= page_num < len(self.page_pixmaps):
            page_item = self.page_pixmaps[page_num]
            r = hit['rect']
            z = self.base_zoom
            qt_rect = QRectF(r.x0 * z, r.y0 * z, (r.x1 - r.x0) * z, (r.y1 - r.y0) * z)
            scene_rect = page_item.mapToScene(qt_rect).boundingRect()
            self.ensureVisible(scene_rect, 100, 100)

    def load_document(self, doc):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait()

        self.doc = doc
        self.annot_manager.clear_selection()
        self.clear_search_highlights()
        self.scene.clear()
        self.page_placeholders.clear()
        self.page_pixmaps.clear()
        self.pending_jump = None

        self.page_rects = []
        self.page_placeholders = []
        self.page_pixmaps = []
        y_offset = 0
        for i in range(len(self.doc)):
            page = self.doc.load_page(i)
            rect = page.rect
            width = rect.width * self.base_zoom
            height = rect.height * self.base_zoom
            page_rect = QRectF(0, y_offset, width, height)
            self.page_rects.append(page_rect)

            placeholder = QGraphicsRectItem(page_rect)
            placeholder.setBrush(QBrush(QColor(240, 240, 240)))
            placeholder.setPen(QPen(Qt.PenStyle.NoPen))
            placeholder.setZValue(0)
            self.scene.addItem(placeholder)
            self.page_placeholders.append(placeholder)

            # Pre-allocate empty pixmap items
            pixmap_item = QGraphicsPixmapItem()
            pixmap_item.setPos(page_rect.topLeft())
            pixmap_item.setZValue(1)
            pixmap_item.setVisible(False)
            self.scene.addItem(pixmap_item)
            self.page_pixmaps.append(pixmap_item)

            y_offset += height + 20
        self.scene.setSceneRect(self.scene.itemsBoundingRect())

        self.rendered_pages = set()
        self.pages_in_flight = set()
        self.render_queue = Queue()
        self.worker = RenderWorker(self.doc, self.base_zoom, self.render_queue)
        self.worker.page_ready.connect(self._on_page_ready)
        self.worker.start()

        self.verticalScrollBar().valueChanged.connect(self._on_scroll)
        QTimer.singleShot(0, self._on_scroll)
        # Failsafe: force call to _on_scroll to ensure render_queue is filled
        self._on_scroll()

        # Show and update HUD
        self.page_hud.setVisible(True)
        self.page_hud.update_hud(1, len(self.doc))
        self.resizeEvent(None)
        return True

    def _on_page_ready(self, page_num, qimage):
        print(f"[DEBUG] _on_page_ready called for page {page_num}, image size: {qimage.width()}x{qimage.height()}")
        # Replace placeholder with pixmap item (not as child)
        if not (0 <= page_num < len(self.page_pixmaps)):
            return

        pixmap = QPixmap.fromImage(qimage)
        if pixmap.isNull():
            try:
                import fitz
                page = self.doc.load_page(page_num)
                pix = page.get_pixmap(matrix=fitz.Matrix(self.base_zoom, self.base_zoom))
                png_bytes = pix.tobytes("png")
                pixmap = QPixmap()
                pixmap.loadFromData(png_bytes, "PNG")
            except Exception:
                pass
        self.page_pixmaps[page_num].setPixmap(pixmap)
        self.page_pixmaps[page_num].setVisible(True)
        self.page_pixmaps[page_num].setZValue(1)
        self.page_placeholders[page_num].setVisible(False)
        self.rendered_pages.add(page_num)
        self.pages_in_flight.discard(page_num)

        # Draw highlight annotations as overlays
        if self.doc:
            try:
                page = self.doc.load_page(page_num)
                for annot in page.annots():
                    if annot.type[0] == 8:  # Highlight
                        for quad in annot.vertices:
                            # Handle both list of points and flat float lists
                            if hasattr(quad[0], 'x') and hasattr(quad[0], 'y'):
                                xs = [p.x for p in quad]
                                ys = [p.y for p in quad]
                            elif isinstance(quad[0], (float, int)) and len(quad) == 8:
                                xs = [quad[i] for i in range(0, 8, 2)]
                                ys = [quad[i] for i in range(1, 8, 2)]
                            else:
                                print(f"[DEBUG] Unexpected quad format: {quad}")
                                continue
                            x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
                            z = self.base_zoom
                            qt_rect = QRectF(x0 * z, y0 * z, (x1 - x0) * z, (y1 - y0) * z)
                            h_item = QGraphicsRectItem(qt_rect, self.page_pixmaps[page_num])
                            h_item.setBrush(QBrush(QColor(255, 255, 0, 80)))
                            h_item.setPen(QPen(Qt.PenStyle.NoPen))
                            h_item.setZValue(20)
            except Exception as e:
                print(f"[DEBUG] Failed to draw highlight overlays: {e}")
        # Replace placeholder with pixmap item (not as child)
        if not (0 <= page_num < len(self.page_pixmaps)):
            return

        pixmap = QPixmap.fromImage(qimage)
        if pixmap.isNull():
            try:
                import fitz
                page = self.doc.load_page(page_num)
                pix = page.get_pixmap(matrix=fitz.Matrix(self.base_zoom, self.base_zoom))
                png_bytes = pix.tobytes("png")
                pixmap = QPixmap()
                pixmap.loadFromData(png_bytes, "PNG")
            except Exception:
                pass
        self.page_pixmaps[page_num].setPixmap(pixmap)
        self.page_pixmaps[page_num].setVisible(True)
        self.page_pixmaps[page_num].setZValue(1)
        self.page_placeholders[page_num].setVisible(False)
        self.rendered_pages.add(page_num)
        self.pages_in_flight.discard(page_num)

        if self.search_hits and self.current_search_text:
            self._apply_search_highlights_to_page(page_num, self.page_pixmaps[page_num])

        if self.pending_jump and self.pending_jump[0] == page_num:
            p_num, a_id = self.pending_jump
            self.pending_jump = None
            QTimer.singleShot(100, lambda: self._execute_jump(p_num, a_id))

        if self.pending_search_jump and self.pending_search_jump['page'] == page_num:
            s_hit = self.pending_search_jump
            self.pending_search_jump = None
            QTimer.singleShot(100, lambda: self._execute_search_jump(s_hit))
    def _on_scroll(self, *args):
        # Determine which pages are visible in the viewport
        viewport_rect = self.viewport().rect()
        scene_rect = self.mapToScene(viewport_rect).boundingRect()
        visible_indices = []
        for i, page_rect in enumerate(self.page_rects):
            if page_rect.intersects(scene_rect):
                visible_indices.append(i)

        # Expand to ±2 page buffer
        buffer_indices = set()
        for idx in visible_indices:
            for offset in range(-2, 3):
                buf_idx = idx + offset
                if 0 <= buf_idx < len(self.page_rects):
                    buffer_indices.add(buf_idx)

        # Render buffered pages if not already rendered or in flight
        for idx in buffer_indices:
            pixmap_item = self.page_pixmaps[idx]
            # If pixmap is empty (not just None), request render
            if (pixmap_item.pixmap().isNull() or not pixmap_item.isVisible()) and idx not in self.pages_in_flight:
                self.render_queue.put(idx)
                self.pages_in_flight.add(idx)

        # VRAM free: use min/max buffer boundaries
        if buffer_indices:
            min_buf = min(buffer_indices)
            max_buf = max(buffer_indices)
            for i in list(self.rendered_pages):
                if i < min_buf - 2 or i > max_buf + 2:
                    # Release VRAM instantly without touching the scene tree
                    self.page_pixmaps[i].setPixmap(QPixmap())
                    self.page_pixmaps[i].setVisible(False)
                    self.page_placeholders[i].setVisible(True)
                    self.page_placeholders[i].setBrush(QBrush(QColor(240, 240, 240)))
                    self.page_placeholders[i].setZValue(0)
                    self.rendered_pages.discard(i)

        # HUD: update if page changed
        if visible_indices:
            current_page = visible_indices[0]
            if not hasattr(self, '_last_hud_page') or self._last_hud_page != current_page:
                self.page_hud.update_hud(current_page + 1, len(self.doc))
                self._last_hud_page = current_page
    def _hud_jump_requested(self):
        try:
            text = self.page_hud.line_edit.text()
            page = int(text) - 1
            if page < 0:
                page = 0
            if page >= len(self.doc):
                page = len(self.doc) - 1
            self.jump_to_page(page)
            # Force scroll bar to update and HUD to refresh
            QTimer.singleShot(0, self._on_scroll)
        except Exception:
            pass


    def reload_page(self, page_num):
        if not self.doc or page_num < 0 or page_num >= len(self.page_pixmaps): return
        page = self.doc.load_page(page_num)
        mat = fitz.Matrix(self.base_zoom, self.base_zoom)
        pix = page.get_pixmap(matrix=mat)
        img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888)
        self.page_pixmaps[page_num].setPixmap(QPixmap.fromImage(img.copy()))

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
        if 0 <= page_num < len(self.page_placeholders):
            target_item = self.page_pixmaps[page_num] if self.page_pixmaps[page_num] is not None else self.page_placeholders[page_num]
            # Scroll so the top of the page is visible
            page_rect = target_item.sceneBoundingRect()
            top_rect = QRectF(page_rect.left(), page_rect.top(), page_rect.width(), 10)  # 10px tall at top
            self.ensureVisible(top_rect, 50, 10)
            QTimer.singleShot(0, self._on_scroll)
            # Scroll event will reposition HUD and trigger buffer update

    def jump_to_annotation(self, page_num, annot_id):
        if page_num >= len(self.page_placeholders):
            self.pending_jump = (page_num, annot_id)
        else:
            self._execute_jump(page_num, annot_id)

    def _execute_jump(self, page_num, annot_id):
        if 0 <= page_num < len(self.page_placeholders) and self.doc:
            target_item = self.page_pixmaps[page_num] if self.page_pixmaps[page_num] is not None else self.page_placeholders[page_num]
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
        if not self.page_placeholders: return
        self.resetTransform()
        view_width = self.viewport().width()
        item = self.page_pixmaps[0] if self.page_pixmaps[0] is not None else self.page_placeholders[0]
        doc_width = item.boundingRect().width()
        if doc_width > 0:
            target_scale = (view_width - 40) / doc_width
            self.scale(target_scale, target_scale)