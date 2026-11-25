"""Themed donut of system memory: loaded models, Papyrus, workers, other apps, free."""
from __future__ import annotations

from typing import List, Tuple

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QWidget

from core.models.memory_models import MemorySnapshot
from gui.components.chart_painting import TOP, paint_donut, square_in

HOLE_RATIO = 0.62
MIN_SIZE = 150
# Model slices cycle through these; the fixed slices use theme colours.
MODEL_COLORS = ("#b366ff", "#ff9f43", "#ff6b81", "#48dbfb", "#feca57")

Slice = Tuple[str, int, QColor]


def memory_slices(snapshot: MemorySnapshot, theme: dict) -> List[Slice]:
    """Slices that add up to total RAM. Models served by the LLM server are
    their own slices; everything Papyrus holds in-process is one slice."""
    models = [item for item in snapshot.consumers
              if item.id.startswith("llm:") and item.bytes > 0]
    model_bytes = sum(item.bytes for item in models)
    other = max(0, snapshot.used - snapshot.app_rss - snapshot.children_rss - model_bytes)
    slices = [(item.label, item.bytes, QColor(MODEL_COLORS[index % len(MODEL_COLORS)]))
              for index, item in enumerate(models)]
    slices += [
        ("Papyrus app", snapshot.app_rss, QColor(theme.get("accent", "#4a9eff"))),
        ("Background workers", snapshot.children_rss, QColor(theme.get("warning", "#e0a800"))),
        ("Other apps", other, QColor(theme.get("text_muted", "#888888"))),
        ("Free", snapshot.available, QColor(theme.get("border", "#444444"))),
    ]
    return [item for item in slices if item[1] > 0]


class MemoryDonutWidget(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._slices: List[Slice] = []
        self._center = ""
        self._hole = QColor("#1e1e1e")
        self._text = QColor("#ffffff")
        self.setMinimumSize(MIN_SIZE, MIN_SIZE)
        self.setAccessibleName("Memory usage chart")

    def sizeHint(self) -> QSize:
        return QSize(MIN_SIZE + 30, MIN_SIZE + 30)

    def set_theme(self, theme: dict) -> None:
        self._hole = QColor(theme.get("bg_panel", "#2b2b2b"))
        self._text = QColor(theme.get("text_main", "#ffffff"))
        self.update()

    def set_data(self, slices: List[Slice], center_text: str) -> None:
        self._slices, self._center = list(slices), center_text
        self.setToolTip("\n".join(f"{label}: {size / 1024 ** 3:.2f} GB" for label, size, _ in slices))
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = square_in(QRectF(self.rect()).adjusted(4, 4, -4, -4))
        paint_donut(painter, rect, [(size, color) for _, size, color in self._slices],
                    hole_ratio=HOLE_RATIO, hole_color=self._hole, start=TOP)
        painter.setPen(self._text)
        font = QFont(painter.font())
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._center)
        painter.end()
