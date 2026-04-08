from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPen, QBrush
from PyQt6.QtWidgets import QGraphicsRectItem, QGraphicsTextItem


def get_text_color_for_bg(bg_color):
    try:
        if isinstance(bg_color, (tuple, list)):
            c = QColor(int(bg_color[0] * 255), int(bg_color[1] * 255), int(bg_color[2] * 255))
        else:
            c = QColor(bg_color)
        brightness = (c.red() * 299 + c.green() * 587 + c.blue() * 114) / 1000
        return "#000000" if brightness > 140 else "#ffffff"
    except Exception:
        return "#ffffff"


class InPlaceTextItem(QGraphicsTextItem):
    def __init__(self, node, text=""):
        super().__init__(text, node)
        self.node = node

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Return and not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            self.clearFocus()  # Triggers focusOutEvent to commit save.
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event):
        # Automatically save text whenever the user clicks away or loses focus.
        super().focusOutEvent(event)
        self.node.finish_in_place_edit()


class ResizeHandle(QGraphicsRectItem):
    def __init__(self, parent):
        super().__init__(0, 0, 16, 16, parent)
        self.setBrush(QBrush(QColor(100, 100, 100, 255)))
        self.setPen(QPen(QColor(255, 255, 255, 200), 1))
        self.setCursor(Qt.CursorShape.SizeFDiagCursor)

        # We no longer rely on Qt's ItemIsMovable flag because Qt will hijack the event
        # and drag the parent instead of resizing if the parent happens to be "selected".
        self._is_resizing = False
        self._start_pos = None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._is_resizing = True
            # Start positions in parent's local coordinates.
            self._start_pos = self.parentItem().mapFromScene(event.scenePos())
            self._start_w = self.parentItem().base_width
            self._start_h = self.parentItem().base_height
            if self.scene() and hasattr(self.scene(), "view"):
                self.scene().view.save_state_for_undo()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._is_resizing:
            current_pos = self.parentItem().mapFromScene(event.scenePos())
            delta = current_pos - self._start_pos

            new_w = max(50, self._start_w + delta.x())
            new_h = max(30, self._start_h + delta.y())
            self.parentItem().update_size(new_w, new_h)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._is_resizing and event.button() == Qt.MouseButton.LeftButton:
            self._is_resizing = False
            event.accept()
        else:
            super().mouseReleaseEvent(event)

