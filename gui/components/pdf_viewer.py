# gui/components/pdf_viewer.py
import fitz
import webbrowser
from PySide6.QtWidgets import (QGraphicsView, QGraphicsScene, QGraphicsPixmapItem, QGraphicsRectItem,
                             QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QComboBox, QCheckBox, QApplication)
from PySide6.QtGui import QImage, QPixmap, QPainter, QColor, QBrush, QPen, QShortcut, QKeySequence
from PySide6.QtCore import Qt, QThread, Signal, QRectF, QTimer
from PySide6.QtCore import QPointF, QPoint
from PySide6.QtCore import QEvent

from gui.components.annotation_manager import AnnotationManager
from gui.components.search_bar_widget import SearchBarWidget
from core.events.event_bus import EventBus
from core.events.domains.document_events import AnnotationIntent, AnnotationPayload, DocumentEvent, DocumentEventPayload, DocumentIntent, DocumentPayload
from core.events.domains.project_events import ProjectEvent, ProjectEventPayload

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
    annotation_clicked = Signal(str,int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.bus = EventBus.get_instance()
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)

        self.doc = None
        self.base_zoom = 1.5
        self.dark_mode_enabled = False
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
        self.current_links = []
        self.page_links = {}

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

        # Viewer toolbar
        self.viewer_toolbar = QFrame(self.viewport())
        self.viewer_toolbar.setStyleSheet("""
            background-color: rgba(30, 30, 30, 200);
            border-radius: 8px;
            color: white;
            padding: 4px;
        """)
        toolbar_layout = QHBoxLayout(self.viewer_toolbar)
        toolbar_layout.setContentsMargins(8, 4, 8, 4)
        self.dark_mode_btn = QPushButton("Dark Mode", self.viewer_toolbar)
        self.dark_mode_btn.setCheckable(True)
        self.dark_mode_btn.setStyleSheet("""
            background: #444;
            color: white;
            border-radius: 4px;
            padding: 2px 8px;
        """)
        self.dark_mode_btn.clicked.connect(self.toggle_dark_mode)
        toolbar_layout.addWidget(self.dark_mode_btn)


        self.viewer_toolbar.setLayout(toolbar_layout)
        self.viewer_toolbar.setVisible(True)
        self._zoom_in_sc = QShortcut(QKeySequence("Ctrl+="), self)
        self._zoom_in_sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._zoom_in_sc.activated.connect(self.zoom_in)
        self._zoom_in_sc.activatedAmbiguously.connect(self.zoom_in)
        self._zoom_out_sc = QShortcut(QKeySequence("Ctrl+-"), self)
        self._zoom_out_sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._zoom_out_sc.activated.connect(self.zoom_out)
        self._zoom_out_sc.activatedAmbiguously.connect(self.zoom_out)

        self.copy_shortcut = QShortcut(QKeySequence("Ctrl+C"), self)
        self.copy_shortcut.activated.connect(self.copy_to_clipboard)

        self.bus.document_opened.connect(self._on_document_opened_event)
        self.bus.project_clearing_started.connect(self._on_project_clearing_started)
        self.bus.document_action_requested.connect(self._handle_doc_action)

    def _handle_doc_action(self, intent: DocumentIntent, payload: DocumentPayload):
        if intent == DocumentIntent.RELOAD_PAGE:
            if payload.page_num is not None:
                self.reload_page(payload.page_num)

    def _on_document_opened_event(self, event: DocumentEvent, payload: DocumentEventPayload):
        if event == DocumentEvent.DOCUMENT_OPENED:
            self._on_document_opened(payload.path, payload.doc, payload.needs_ocr)

    def _on_document_opened(self, path: str, doc: object, needs_ocr: bool):
        """Catches the document from the background service and renders it."""
        # --- FIX: Give the Viewer the string path so the RenderWorker thread can open it! ---
        self.pdf_path = path
        main_window = self.window()
        if main_window and hasattr(main_window, "current_file_path"):
            main_window.current_file_path = path

        self.load_document(doc)

        if needs_ocr:
            self.bus.document_action_requested.emit(
                DocumentIntent.SHOW_OCR_BANNER,
                DocumentPayload(path=path)
            )

    def _on_project_clearing_started(self, event: ProjectEvent, payload: ProjectEventPayload):
        if event == ProjectEvent.CLEARING_STARTED:
            self._clear_viewer()

    def _clear_viewer(self):
        """Wipes the viewer cleanly when a new project loads."""
        if hasattr(self, 'scene') and self.scene:
            self.scene.clear()
        self.doc = None

    def _doc_valid(self):
        """Return True only when self.doc exists and is not closed."""
        return self.doc is not None and not self.doc.is_closed

    def update_theme(self, theme):
        self.search_bar.update_theme(theme)
        self.setBackgroundBrush(QBrush(QColor(theme['canvas'])))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'viewer_toolbar') and self.viewer_toolbar.isVisible():
            self.viewer_toolbar.adjustSize()
            self.viewer_toolbar.move(20, 20)
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
            if not doc:
                continue
            for page_num in range(len(doc)):
                page = doc.load_page(page_num)
                # PyMuPDF's search_for uses 'max' not 'hit_max' in recent versions
                try:
                    quads = page.search_for(text, max=999, quads=True, flags=flags)
                except TypeError:
                    # fallback for older versions without 'max' argument
                    quads = page.search_for(text, quads=True, flags=flags)
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
        self.current_links = []
        self.page_links = {}

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
        # Use the viewer's locally stored path!
        self.worker = RenderWorker(getattr(self, 'pdf_path', None), self.base_zoom, self.render_queue, pixel_ratio=self._get_dpi_scale())
        self.worker.page_ready.connect(self._on_page_ready)
        self.worker.start()

        self.verticalScrollBar().valueChanged.connect(self._on_scroll)
        QTimer.singleShot(0, self._on_scroll)
        # Failsafe: force call to _on_scroll to ensure render_queue is filled
        self._on_scroll()

        # Reset HUD page tracking to avoid out-of-range errors
        self._last_hud_page = 0

        # Show and update HUD
        self.page_hud.setVisible(True)
        self.page_hud.update_hud(1, len(self.doc))
        self.resizeEvent(None)
        return True
    def swap_document_handle(self, new_doc):
        """Safely updates the document handle and restarts the background thread."""
        # 1. Stop the active render thread safely
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait()

        self.doc = new_doc

        # 2. Clear out the old render queue to prevent stale requests
        while not self.render_queue.empty():
            try:
                self.render_queue.get_nowait()
            except:
                break

        # 3. Restart the background worker with the fresh document handle
        self.worker = RenderWorker(getattr(self, 'pdf_path', None), self.base_zoom, self.render_queue, pixel_ratio=self._get_dpi_scale())
        self.worker.page_ready.connect(self._on_page_ready)
        self.worker.start()

        # 4. Force re-render of currently visible pages (so the deleted highlight disappears)
        self.rendered_pages.clear()
        self._on_scroll()
    def rotate_view(self):
        """Rotates the native view by 90 degrees cleanly."""
        self.rotate(90)
    def _on_page_ready(self, page_num, qimage):
        """Callback for when the background RenderWorker finishes a page."""
        if not self._doc_valid() or not (0 <= page_num < len(self.page_pixmaps)):
            return

        try:
            from PySide6.QtGui import QPixmap
            from PySide6.QtCore import QTimer
            if getattr(self, 'dark_mode_enabled', False):
                qimage.invertPixels(QImage.InvertMode.InvertRgb)
            pixmap = QPixmap.fromImage(qimage)
            self.page_pixmaps[page_num].setPixmap(pixmap)
            self.page_pixmaps[page_num].setVisible(True)
            self.page_pixmaps[page_num].setZValue(1)
            self.page_placeholders[page_num].setVisible(False)

            if hasattr(self, 'rendered_pages'):
                self.rendered_pages.add(page_num)
            if hasattr(self, 'pages_in_flight'):
                self.pages_in_flight.discard(page_num)

            # 🔥 Cleanly render highlights using the new memory manager
            self._draw_standard_highlights(page_num, self.page_pixmaps[page_num])

            # Check if there is a pending jump waiting for this page to finish rendering
            if getattr(self, 'pending_jump', None) and self.pending_jump[0] == page_num:
                p_num, a_id = self.pending_jump
                self.pending_jump = None
                QTimer.singleShot(100, lambda: self._execute_jump(p_num, a_id))

            # Check if there is a pending search jump waiting for this page to finish rendering
            if getattr(self, 'pending_search_jump', None) and self.pending_search_jump['page'] == page_num:
                s_hit = self.pending_search_jump
                self.pending_search_jump = None
                QTimer.singleShot(100, lambda: self._execute_search_jump(s_hit))

        except Exception as e:
            print(f"Error handling ready page {page_num}: {e}")

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
        if not self.page_rects:
            return
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
            if idx >= len(self.page_pixmaps):
                continue
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
                    if i < len(self.page_pixmaps):
                        self.page_pixmaps[i].setPixmap(QPixmap())
                        self.page_pixmaps[i].setVisible(False)
                        self.page_placeholders[i].setVisible(True)
                        self.page_placeholders[i].setBrush(QBrush(QColor(240, 240, 240)))
                        self.page_placeholders[i].setZValue(0)
                        self.rendered_pages.discard(i)

        # HUD: update if page changed
        if visible_indices:
            current_page = visible_indices[0]
            # Clamp current_page to valid range
            if not self._doc_valid():
                return
            if current_page >= len(self.doc):
                current_page = len(self.doc) - 1
            if current_page < 0:
                current_page = 0
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
        if not self._doc_valid() or page_num < 0 or page_num >= len(self.page_pixmaps): return
        page = self.doc.load_page(page_num)
        dpi_scale = self._get_dpi_scale()
        mat = fitz.Matrix(self.base_zoom * dpi_scale, self.base_zoom * dpi_scale)
        pix = page.get_pixmap(matrix=mat)
        img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
        if self.dark_mode_enabled:
            img.invertPixels(QImage.InvertMode.InvertRgb)
        img.setDevicePixelRatio(dpi_scale)
        self.page_pixmaps[page_num].setPixmap(QPixmap.fromImage(img))

    def toggle_dark_mode(self):
        self.dark_mode_enabled = not self.dark_mode_enabled
        if hasattr(self, 'dark_mode_btn'):
            self.dark_mode_btn.setChecked(self.dark_mode_enabled)

        if not self._doc_valid() or not self.page_pixmaps:
            return

        # Apply to all currently rendered pages; off-screen pages rendered later
        # will follow self.dark_mode_enabled in _on_page_ready.
        for pixmap_item in self.page_pixmaps:
            if pixmap_item is None:
                continue
            pixmap = pixmap_item.pixmap()
            if pixmap.isNull():
                continue
            img = pixmap.toImage()
            img.invertPixels(QImage.InvertMode.InvertRgb)
            pixmap_item.setPixmap(QPixmap.fromImage(img))

    def mousePressEvent(self, event):
        is_shift = event.modifiers() == Qt.KeyboardModifier.ShiftModifier
        is_right = event.button() == Qt.MouseButton.RightButton
        is_left = event.button() == Qt.MouseButton.LeftButton

        # Handle PDF links before selection/annotation behavior.
        if is_left and self._doc_valid():
            scene_pos = self._event_scene_pos(event)
            link = self._get_link_at_scene_pos(scene_pos)
            if link:
                if self._open_link_target(link):
                    return

        if is_right and self.annot_manager.has_selection():
            scene_pos = self._event_scene_pos(event)
            if self.annot_manager.is_pos_in_selection(scene_pos):
                self.annot_manager.show_context_menu(event.globalPosition().toPoint())
                return

        if is_right or (is_left and is_shift):
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            self.viewport().setCursor(Qt.CursorShape.IBeamCursor)
            self.annot_manager.start_selection(event)
            return

        if is_left:
            scene_pos = self._event_scene_pos(event)

            if self.annot_manager.has_selection() and not self.annot_manager.is_pos_in_selection(scene_pos):
                self.annot_manager.clear_selection()

            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            page_idx, page_item = self.annot_manager._get_page_at_pos(scene_pos)

            if page_idx != -1 and self._doc_valid():
                local_pos = page_item.mapFromScene(scene_pos)
                pdf_x, pdf_y = local_pos.x() / self.base_zoom, local_pos.y() / self.base_zoom
                point = fitz.Point(pdf_x, pdf_y)
                try:
                    page = self.doc.load_page(page_idx)
                    for annot in page.annots():
                        # 🔥 Expand the hitbox slightly to make thin lines easier to click
                        hitbox = annot.rect + (-2, -2, 2, 2)

                        if hitbox.contains(point) and annot.info:
                                title = annot.info.get("title", "")
                                if title.startswith("UserNote") or title.startswith("AINote"):
                                    self.bus.annotation_action_requested.emit(
                                        AnnotationIntent.EDIT_POPUP,
                                        AnnotationPayload(
                                            target_annot=annot,
                                            annot_id=title,
                                            page_num=page_idx,
                                            pdf_path=getattr(self, 'pdf_path', None),
                                        ),
                                    )
                                    return
                except Exception as e:
                    print(f"Ignoring PyMuPDF annot error: {e}")

            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.annot_manager.is_selecting:
            self.annot_manager.update_selection(event)
        else:
            super().mouseMoveEvent(event)
            if self._doc_valid():
                scene_pos = self._event_scene_pos(event)
                link = self._get_link_at_scene_pos(scene_pos)
                if link:
                    self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
                    link_tip = self._get_link_tooltip(link)
                    self.viewport().setToolTip(link_tip if link_tip else "Link")
                else:
                    self.viewport().unsetCursor()
                    self.viewport().setToolTip("")

    def mouseReleaseEvent(self, event):
        if self.annot_manager.is_selecting:
            self.annot_manager.finish_selection(event)
            self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        super().mouseReleaseEvent(event)
    def jump_to_source(self, doc_name, quote):
        """
        Receives jump requests from Universal Note Bubbles.
        Leverages the existing search infrastructure to cross-document jump,
        highlight, and perfectly frame the AI's quote.
        """
        if not quote: return

        # 1. Clean the string (LLMs love wrapping citations in quotes)
        clean_quote = quote.strip('"\'').strip()

        # 2. Open the search bar so the user has visual UI feedback
        if not self.search_bar.isVisible():
            self.toggle_search_bar()

        # 3. Inject the quote and force a global search to handle doc switching
        self.search_bar.search_input.setText(clean_quote)
        self.search_bar.scope_combo.setCurrentText("Entire Project")
        self.trigger_search()

        # 4. Anti-Hallucination Fallback: If PyMuPDF chokes on a weird line-break
        # or hyphen, search for the first 6 words to guarantee we still find the context.
        if not self.search_hits:
            words = clean_quote.split()
            if len(words) > 6:
                partial_quote = " ".join(words[:6])
                self.search_bar.search_input.setText(partial_quote)
                self.trigger_search()
    def jump_to_page(self, page_num):
        if 0 <= page_num < len(self.page_rects):
            self._scroll_to_scene_y(self.page_rects[page_num].top())
            QTimer.singleShot(0, self._on_scroll)
            # Scroll event will reposition HUD and trigger buffer update

    def jump_to_annotation(self, page_num, annot_id):
        if page_num >= len(self.page_placeholders):
            self.pending_jump = (page_num, annot_id)
        else:
            self._execute_jump(page_num, annot_id)
    def _render_page_sync(self, page_num):
        """Synchronously renders a page immediately on the main thread to prevent ghost-jumping."""
        if not self._doc_valid() or not (0 <= page_num < len(self.page_pixmaps)):
            return

        try:
            import fitz
            from PySide6.QtGui import QImage, QPixmap

            page = self.doc.load_page(page_num)
            dpi_scale = getattr(self, '_get_dpi_scale', lambda: 1.0)()
            mat = fitz.Matrix(self.base_zoom * dpi_scale, self.base_zoom * dpi_scale)
            pix = page.get_pixmap(matrix=mat)
            img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()

            if getattr(self, 'dark_mode_enabled', False):
                img.invertPixels(QImage.InvertMode.InvertRgb)

            img.setDevicePixelRatio(dpi_scale)

            pixmap = QPixmap.fromImage(img)
            self.page_pixmaps[page_num].setPixmap(pixmap)
            self.page_pixmaps[page_num].setVisible(True)
            self.page_pixmaps[page_num].setZValue(1)
            self.page_placeholders[page_num].setVisible(False)

            if hasattr(self, 'rendered_pages'):
                self.rendered_pages.add(page_num)
            if hasattr(self, 'pages_in_flight'):
                self.pages_in_flight.discard(page_num)

            # 🔥 Cleanly render highlights using the new memory manager
            self._draw_standard_highlights(page_num, self.page_pixmaps[page_num])

        except Exception as e:
            print(f"Sync render failed for page {page_num}: {e}")
    def _draw_standard_highlights(self, page_num, page_item):
        """Safely renders permanent highlights while cleaning up old ones to prevent memory leaks."""
        if not self._doc_valid(): return

        if not hasattr(self, 'pdf_highlight_items'):
            self.pdf_highlight_items = {}

        # Clean up old standard highlights for this page to prevent infinite stacking on scroll
        if page_num in self.pdf_highlight_items:
            for old_h in self.pdf_highlight_items[page_num]:
                if old_h.scene():
                    self.scene.removeItem(old_h)
            self.pdf_highlight_items[page_num].clear()
        else:
            self.pdf_highlight_items[page_num] = []

        try:
            page = self.doc.load_page(page_num)
            self.current_links = page.get_links()
            self.page_links[page_num] = self.current_links
            for annot in page.annots():
                if annot.type[0] == 8:  # Highlight
                    for quad in annot.vertices:
                        if hasattr(quad[0], 'x') and hasattr(quad[0], 'y'):
                            xs = [p.x for p in quad]
                            ys = [p.y for p in quad]
                        elif isinstance(quad[0], (float, int)) and len(quad) == 8:
                            xs = [quad[i] for i in range(0, 8, 2)]
                            ys = [quad[i] for i in range(1, 8, 2)]
                        else:
                            continue
                        x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
                        z = self.base_zoom
                        qt_rect = QRectF(x0 * z, y0 * z, (x1 - x0) * z, (y1 - y0) * z)
                        h_item = QGraphicsRectItem(qt_rect, page_item)
                        h_item.setBrush(QBrush(QColor(255, 255, 0, 80)))
                        h_item.setPen(QPen(Qt.PenStyle.NoPen))
                        h_item.setZValue(20)
                        self.pdf_highlight_items[page_num].append(h_item)
        except Exception as e:
            print(f"Failed to draw standard highlights: {e}")
    def _execute_jump(self, page_num, annot_id):
        if 0 <= page_num < len(self.page_placeholders) and self.doc:
            if page_num not in getattr(self, 'rendered_pages', set()):
                self._render_page_sync(page_num)

            from PySide6.QtWidgets import QApplication
            QApplication.processEvents()

            target_item = self.page_pixmaps[page_num] if self.page_pixmaps[page_num] is not None else self.page_placeholders[page_num]
            page = self.doc.load_page(page_num)
            for annot in page.annots():
                if annot.info.get("title") == annot_id:
                    r = annot.rect
                    z = self.base_zoom
                    qt_rect = QRectF(r.x0 * z, r.y0 * z, (r.x1 - r.x0) * z, (r.y1 - r.y0) * z)
                    scene_rect = target_item.mapToScene(qt_rect).boundingRect()

                    # 🔥 FIX: Pan both X and Y axes to perfectly frame the highlight
                    self.ensureVisible(scene_rect, 50, 150)

                    if hasattr(self, 'page_hud'):
                        self.page_hud.update_hud(page_num + 1, len(self.doc))
                    return
            self.jump_to_page(page_num)

    def _execute_search_jump(self, hit):
        page_num = hit['page']
        if 0 <= page_num < len(self.page_pixmaps):
            if page_num not in getattr(self, 'rendered_pages', set()):
                self._render_page_sync(page_num)

            from PySide6.QtWidgets import QApplication
            QApplication.processEvents()

            page_item = self.page_pixmaps[page_num]
            r = hit['rect']
            z = self.base_zoom
            qt_rect = QRectF(r.x0 * z, r.y0 * z, (r.x1 - r.x0) * z, (r.y1 - r.y0) * z)
            scene_rect = page_item.mapToScene(qt_rect).boundingRect()

            # 🔥 FIX: Pan both X and Y axes to perfectly frame the search hit
            self.ensureVisible(scene_rect, 50, 150)

            if hasattr(self, 'page_hud') and self.doc:
                self.page_hud.update_hud(page_num + 1, len(self.doc))

    def _scroll_to_scene_y(self, scene_y):
        scale_y = self.transform().m22()
        if not scale_y:
            scale_y = 1.0
        target_value = int(scene_y * scale_y)
        vbar = self.verticalScrollBar()
        target_value = max(vbar.minimum(), min(target_value, vbar.maximum()))
        vbar.setValue(target_value)

    def zoom_in(self): self.scale(1.1, 1.1)
    def zoom_out(self): self.scale(1 / 1.1, 1 / 1.1)
    def zoom_reset(self):
        if not self.page_placeholders: return
        self.resetTransform()
        view_width = self.viewport().width()
        item = self.page_pixmaps[0] if self.page_pixmaps[0] is not None else self.page_placeholders[0]
        doc_width = item.boundingRect().width()
        if doc_width > 0:
            target_scale = (view_width - 40) / doc_width
            self.scale(target_scale, target_scale)
    # --- NEW METHOD ---
    def sharpen_focus(self):
        """
        Recalculates the core rendering resolution to match the current visual scale.
        This completely eliminates blurriness when zoomed very far out or in!
        """
        if not self.doc: return

        # 1. Calculate how much Qt is currently scaling the image visually
        current_scale = self.transform().m11()

        # If we are already at 100% native scale, there's no blur to fix
        if abs(current_scale - 1.0) < 0.01:
            return

        # 2. Save current scroll position so we don't lose our place
        vbar = self.verticalScrollBar()
        scroll_ratio = vbar.value() / vbar.maximum() if vbar.maximum() > 0 else 0

        # 3. Permanently bake the visual scale into the PDF engine's rendering resolution
        self.base_zoom = self.base_zoom * current_scale

        # 4. Reset Qt's visual scale back to 1.0 (no stretching/squishing)
        self.resetTransform()

        # 5. Force the engine to re-render the document natively at the new resolution
        self.load_document(self.doc)

        # 6. Restore our scroll position (using a tiny delay to let the UI update first)
        QTimer.singleShot(50, lambda: vbar.setValue(int(scroll_ratio * vbar.maximum())))
    def _get_dpi_scale(self):
        try:
            w = self.window()
            return w.devicePixelRatioF() if w else self.devicePixelRatioF()
        except Exception:
            return 1.0

    def _event_scene_pos(self, event):
        # IMPORTANT: Do NOT multiply by devicePixelRatioF here.
        # Qt mouse events are in logical coordinates and mapToScene expects logical viewport coords.
        p = event.position() if hasattr(event, "position") else event.pos()
        return self.mapToScene(p.toPoint() if hasattr(p, "toPoint") else p)

    def _get_link_at_scene_pos(self, scene_pos):
        if not self._doc_valid():
            return None

        page_idx, page_item = self.annot_manager._get_page_at_pos(scene_pos)
        if page_idx == -1 or page_item is None:
            return None

        local_pos = page_item.mapFromScene(scene_pos)
        pdf_x, pdf_y = local_pos.x() / self.base_zoom, local_pos.y() / self.base_zoom
        point = fitz.Point(pdf_x, pdf_y)

        links = self.page_links.get(page_idx)
        if links is None:
            # --- FIX: Safely wrap link extraction so corrupted PDFs don't crash the click! ---
            try:
                page = self.doc.load_page(page_idx)
                links = page.get_links()
            except Exception as e:
                print(f"Ignoring PyMuPDF link error: {e}")
                links = []
            self.page_links[page_idx] = links

        self.current_links = links

        for link in self.current_links:
            link_rect = link.get("from")
            if link_rect and link_rect.contains(point):
                return link
        return None

    def _open_link_target(self, link):
        kind = link.get("kind")

        if kind == fitz.LINK_URI and link.get("uri"):
            webbrowser.open(link["uri"])
            return True

        internal_kinds = {
            getattr(fitz, "LINK_GOTO", -1),
            getattr(fitz, "LINK_GOTOR", -2),
            getattr(fitz, "LINK_NAMED", -3),
        }
        if kind in internal_kinds:
            page = link.get("page")
            if isinstance(page, int) and page >= 0:
                self.jump_to_page(page)
                return True
            resolved_page = self._resolve_internal_link_page(link)
            if isinstance(resolved_page, int) and resolved_page >= 0:
                self.jump_to_page(resolved_page)
                return True

        return False

    def _resolve_internal_link_page(self, link):
        if not self._doc_valid() or not hasattr(self.doc, "resolve_link"):
            return None

        candidates = [link.get("to"), link.get("name"), link.get("uri")]
        for candidate in candidates:
            if not candidate:
                continue
            try:
                resolved = self.doc.resolve_link(candidate)
            except Exception:
                continue

            if isinstance(resolved, int):
                return resolved
            if isinstance(resolved, (tuple, list)) and resolved:
                for value in resolved:
                    if isinstance(value, int):
                        return value
            if isinstance(resolved, dict):
                for key in ("page", "pno", "number"):
                    value = resolved.get(key)
                    if isinstance(value, int):
                        return value

        return None

    def _get_link_tooltip(self, link):
        kind = link.get("kind")
        uri = link.get("uri")

        if kind == fitz.LINK_URI and uri:
            return f"Open: {uri}"

        page = link.get("page")
        if isinstance(page, int) and page >= 0:
            return f"Go to page {page + 1}"

        return "Follow link"

    def copy_to_clipboard(self):
        if not hasattr(self, 'annot_manager') or not self.annot_manager.selected_words:
            return

        selected_text = " ".join(w[4] for w in self.annot_manager.selected_words if len(w) > 4).strip()
        if selected_text:
            QApplication.clipboard().setText(selected_text)
