# gui/components/pdf_viewer.py
import fitz
from PyQt6.QtWidgets import (QGraphicsView, QGraphicsScene, QGraphicsPixmapItem, QGraphicsRectItem,
                             QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QComboBox, QCheckBox)
from PyQt6.QtGui import QImage, QPixmap, QPainter, QColor, QBrush, QPen
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QRectF, QTimer

from gui.components.annotation_manager import AnnotationManager

class RenderWorker(QThread):
    page_ready = pyqtSignal(int, QImage)
    finished_rendering = pyqtSignal()

    def __init__(self, doc, zoom):
        super().__init__()
        self.doc = doc
        self.zoom = zoom
        self._is_running = True

    def run(self):
        mat = fitz.Matrix(self.zoom, self.zoom)
        for i in range(len(self.doc)):
            if not self._is_running: break
            page = self.doc.load_page(i)
            pix = page.get_pixmap(matrix=mat)
            img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888)
            self.page_ready.emit(i, img.copy())
        self.finished_rendering.emit()

    def stop(self):
        self._is_running = False


class SearchBarWidget(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
            QFrame { background-color: #2b2b2b; border: 1px solid #555; border-radius: 8px; }
            QLineEdit { background-color: #1e1e1e; border: 1px solid #444; padding: 6px; color: white; border-radius: 4px; }
            QLabel { color: #ccc; font-weight: bold; border: none; }
            QCheckBox { color: white; font-weight: bold; border: none; padding-right: 5px; }
            QPushButton { background-color: #444; color: white; border: none; padding: 6px 10px; border-radius: 4px; font-weight: bold; }
            QPushButton:hover { background-color: #555; }
            QComboBox { background-color: #1e1e1e; border: 1px solid #444; color: white; padding: 4px; border-radius: 4px; }
        """)
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 5)
        
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Find in document...")
        self.search_input.setFixedWidth(200)
        
        self.chk_match_case = QCheckBox("Match Case")
        
        self.hit_label = QLabel("0 / 0")
        
        self.btn_prev = QPushButton("▲")
        self.btn_next = QPushButton("▼")
        
        self.scope_combo = QComboBox()
        self.scope_combo.addItems(["Current PDF", "Entire Project"])
        
        self.btn_close = QPushButton("✖")
        self.btn_close.setStyleSheet("QPushButton { background-color: #662222; } QPushButton:hover { background-color: #ff4444; }")
        
        layout.addWidget(self.search_input)
        layout.addWidget(self.chk_match_case)
        layout.addWidget(self.hit_label)
        layout.addWidget(self.btn_prev)
        layout.addWidget(self.btn_next)
        layout.addWidget(self.scope_combo)
        layout.addWidget(self.btn_close)

    def update_hits(self, current, total):
        self.hit_label.setText(f"{current} / {total}")


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
        self.page_items = []
        self.worker = None
        self.annot_manager = AnnotationManager(self)
        
        self.pending_jump = None 

        # --- Search Feature System ---
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

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'search_bar') and self.search_bar.isVisible():
            self.search_bar.adjustSize()
            x_pos = self.viewport().width() - self.search_bar.width() - 20
            self.search_bar.move(x_pos, 20)

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
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait()

        self.doc = doc
        
        # FIX: Clear all visual items from arrays BEFORE wiping the scene
        self.annot_manager.clear_selection()
        self.clear_search_highlights()
        
        # Now it is safe to wipe the actual scene
        self.scene.clear()
        self.page_items.clear()
        self.pending_jump = None 
        
        self.worker = RenderWorker(self.doc, self.base_zoom)
        self.worker.page_ready.connect(self._on_page_ready)
        self.worker.start()
        return True

    def _on_page_ready(self, page_num, qimage):
        pixmap = QPixmap.fromImage(qimage)
        item = QGraphicsPixmapItem(pixmap)
        y_offset = sum(p.boundingRect().height() + 20 for p in self.page_items)
        item.setPos(0, y_offset)
        self.scene.addItem(item)
        self.page_items.append(item)
        self.scene.setSceneRect(self.scene.itemsBoundingRect())
        
        if self.search_hits and self.current_search_text:
            self._apply_search_highlights_to_page(page_num, item)

        if self.pending_jump and self.pending_jump[0] == page_num:
            p_num, a_id = self.pending_jump
            self.pending_jump = None
            QTimer.singleShot(100, lambda: self._execute_jump(p_num, a_id))
            
        if self.pending_search_jump and self.pending_search_jump['page'] == page_num:
            s_hit = self.pending_search_jump
            self.pending_search_jump = None
            QTimer.singleShot(100, lambda: self._execute_search_jump(s_hit))

    def reload_page(self, page_num):
        if not self.doc or page_num < 0 or page_num >= len(self.page_items): return
        page = self.doc.load_page(page_num)
        mat = fitz.Matrix(self.base_zoom, self.base_zoom)
        pix = page.get_pixmap(matrix=mat)
        img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888)
        self.page_items[page_num].setPixmap(QPixmap.fromImage(img.copy()))

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
        if 0 <= page_num < len(self.page_items):
            target_item = self.page_items[page_num]
            top_edge = QRectF(target_item.scenePos().x(), target_item.scenePos().y(), target_item.boundingRect().width(), 1)
            self.ensureVisible(top_edge, 50, 10)

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