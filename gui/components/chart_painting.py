"""Shared QPainter chart primitives (Data Dock pie/donut, memory panel)."""
from __future__ import annotations

from typing import Iterable, Optional, Tuple

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor

FULL_CIRCLE = 360 * 16      # QPainter angles are in 1/16 degree
TOP = 90 * 16               # start at 12 o'clock


def paint_donut(painter, rect: QRectF, slices: Iterable[Tuple[float, QColor]],
                hole_ratio: float = 0.0, hole_color: Optional[QColor] = None,
                start: int = 0) -> None:
    """Pie (``hole_ratio=0``) or donut of ``(value, color)`` slices in ``rect``."""
    slices = [(abs(value), color) for value, color in slices if value]
    total = sum(value for value, _ in slices) or 1.0
    painter.setPen(Qt.PenStyle.NoPen)
    angle = start
    for value, color in slices:
        span = int(value / total * FULL_CIRCLE)
        painter.setBrush(QBrush(color))
        painter.drawPie(rect, angle, span)
        angle += span
    if hole_ratio > 0:
        inset = rect.width() * (1 - hole_ratio) / 2
        painter.setBrush(QBrush(hole_color or QColor("#000000")))
        painter.drawEllipse(rect.adjusted(inset, inset, -inset, -inset))


def square_in(area: QRectF) -> QRectF:
    size = min(area.width(), area.height())
    return QRectF(area.center().x() - size / 2, area.center().y() - size / 2, size, size)
