# core/render_worker.py
import fitz
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage

# A page image above this is rendered at a lower pixel ratio. Scans placed at
# 72 DPI can be "30 × 45 inch" pages; rendered at zoom 1.5 × device ratio they
# made 50–200 MB pixmaps each and froze the viewer. 12 MP ≈ 36 MB per page.
MAX_PAGE_PIXELS = 12_000_000


def page_pixel_ratio(page_rect, zoom, pixel_ratio, max_pixels=MAX_PAGE_PIXELS) -> float:
    """The device pixel ratio to render with so the image stays under the cap.

    The pixmap is displayed at (pixels / ratio), so lowering both together
    keeps the page's on-screen size and every scene coordinate unchanged.
    """
    pixel_ratio = pixel_ratio if pixel_ratio and pixel_ratio > 0 else 1.0
    pixels = page_rect.width * page_rect.height * (zoom * pixel_ratio) ** 2
    if pixels <= max_pixels:
        return pixel_ratio
    return pixel_ratio * (max_pixels / pixels) ** 0.5


def render_page_image(page, zoom, pixel_ratio, theme_colors=None, max_pixels=MAX_PAGE_PIXELS) -> QImage:
    """Render one page to a QImage (optionally duotone-tinted) under the pixel cap."""
    ratio = page_pixel_ratio(page.rect, zoom, pixel_ratio, max_pixels)
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom * ratio, zoom * ratio))
    if theme_colors:
        try:
            bg_hex = int(theme_colors.get('bg_main', '#ffffff').lstrip('#'), 16)
            text_hex = int(theme_colors.get('text_main', '#000000').lstrip('#'), 16)
            pix.tint_with(text_hex, bg_hex)
        except Exception as e:
            print(f"Failed to apply theme tint: {e}")
    img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
    img.setDevicePixelRatio(ratio)
    return img


class RenderWorker(QThread):
    page_ready = Signal(int, QImage)
    finished_rendering = Signal()

    def __init__(self, pdf_path, zoom, page_queue_or_list, pixel_ratio=1.0, theme_mode_enabled=False, theme_colors=None):
        super().__init__()
        self.pdf_path = pdf_path
        self.zoom = zoom
        self.pixel_ratio = pixel_ratio if pixel_ratio and pixel_ratio > 0 else 1.0
        self._is_running = True
        self.page_queue = page_queue_or_list
        self.theme_mode_enabled = theme_mode_enabled
        self.theme_colors = theme_colors or {}

    def run(self):
        if not self.pdf_path:
            self.finished_rendering.emit()
            return

        try:
            # 🔥 Thread opens its OWN completely isolated copy of the C-pointers
            local_doc = fitz.open(self.pdf_path)
        except Exception as e:
            print(f"Background PDF thread failed to open {self.pdf_path}: {e}")
            self.finished_rendering.emit()
            return

        try:
            for page_num in self._pages():
                if not self._is_running:
                    break
                if 0 <= page_num < len(local_doc):
                    self.page_ready.emit(page_num, self._render(local_doc.load_page(page_num)))
        finally:
            local_doc.close()  # Clean up C-pointers safely

        self.finished_rendering.emit()

    def _pages(self):
        if not hasattr(self.page_queue, 'get'):
            yield from self.page_queue
            return
        while self._is_running:
            try:
                yield self.page_queue.get(timeout=0.2)
            except Exception:
                continue

    def _render(self, page) -> QImage:
        colors = self.theme_colors if self.theme_mode_enabled else None
        return render_page_image(page, self.zoom, self.pixel_ratio, colors)

    def stop(self):
        self._is_running = False
