from __future__ import annotations

from typing import Any, List, Tuple

from PySide6.QtCore import Qt, QRectF, QSize
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QBrush
from PySide6.QtWidgets import QWidget

from core.models.data_dock_models import ChartConfig, DataGridState


class DataChartWidget(QWidget):
    """Qt chart renderer shared by Data Dock preview, workspace nodes, and export."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state: DataGridState | None = None
        self.config: ChartConfig | None = None
        self.palette = ["#2f80ed", "#27ae60", "#f2994a", "#eb5757", "#9b51e0"]
        self.setMinimumHeight(150)

    def sizeHint(self) -> QSize:
        return QSize(420, 260)

    def set_chart(self, state: DataGridState | None, config: ChartConfig | None, palette: List[str] | None = None):
        self.state = state
        self.config = config
        if palette:
            self.palette = list(palette)
        self.update()

    def render_image(self, size: QSize | None = None) -> QImage:
        target_size = size or self.size()
        if target_size.width() <= 0 or target_size.height() <= 0:
            target_size = self.sizeHint()
        image = QImage(target_size, QImage.Format.Format_ARGB32)
        image.fill(QColor("#1f2933"))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._paint(painter, QRectF(0, 0, image.width(), image.height()))
        painter.end()
        return image

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._paint(painter, QRectF(self.rect()))

    def _paint(self, painter: QPainter, bounds: QRectF):
        painter.fillRect(bounds, QColor("#1f2933"))
        painter.setPen(QPen(QColor("#d7dde5")))
        if not self.state or not self.config:
            painter.drawText(bounds, Qt.AlignmentFlag.AlignCenter, "Chart preview")
            return
        series = self._series_points()
        points = series[0][1] if series else self._points()
        if not points:
            painter.drawText(bounds, Qt.AlignmentFlag.AlignCenter, "No numeric values to chart")
            return

        title_h = 22 if (self.config.title or self.config.name) else 0
        axis_h = 42 if self.config.x_title else 20
        axis_w = 92 if self.config.y_title and self.config.show_y_labels else 72 if self.config.y_title else 52 if self.config.show_y_labels else 30
        area = bounds.adjusted(axis_w, 12 + title_h, -18, -axis_h)

        if self.config.title or self.config.name:
            painter.setPen(QPen(QColor("#f3f6fb")))
            painter.drawText(QRectF(bounds.left() + 8, bounds.top() + 4, bounds.width() - 16, 20),
                             Qt.AlignmentFlag.AlignCenter, self.config.title or self.config.name)

        all_values = [abs(value) for _, items in series for _, value in items] if series else [abs(v) for _, v in points]
        max_val = max(all_values) if all_values else 1.0
        max_val = max_val or 1.0
        if self.config.show_grid_lines:
            self._paint_grid(painter, area, max_val)
        painter.setPen(QPen(QColor("#8792a2"), 1))
        painter.drawLine(area.bottomLeft(), area.bottomRight())
        painter.drawLine(area.bottomLeft(), area.topLeft())

        chart_type = self.config.chart_type
        if chart_type == "line":
            self._paint_multi_line(painter, area, series, max_val) if len(series) > 1 else self._paint_line(painter, area, points, max_val)
        elif chart_type == "scatter":
            self._paint_scatter(painter, area, points, max_val)
        elif chart_type == "pie":
            self._paint_pie(painter, area, points)
        else:
            self._paint_grouped_bar(painter, area, series, max_val) if len(series) > 1 else self._paint_bar(painter, area, points, max_val)
        self._paint_axes(painter, bounds, area, points, max_val)

    def _points(self) -> List[Tuple[str, float]]:
        state = self.state
        config = self.config
        if not state or not config:
            return []
        headers = list(state.headers)
        x_idx = headers.index(config.x_field) if config.x_field in headers else 0
        y_idx = headers.index(config.y_field) if config.y_field in headers else min(1, len(headers) - 1)
        selected_cells = {tuple(cell) for cell in (config.source_selection or {}).get("cells", []) if len(cell) == 2}
        selected_rows = sorted({row for row, _ in selected_cells})
        row_indexes = selected_rows if selected_rows else list(range(len(state.rows)))
        points: List[Tuple[str, float]] = []
        for row_num in row_indexes[:80]:
            if row_num < 0 or row_num >= len(state.rows):
                continue
            row = state.rows[row_num]
            if config.x_field == "__row_header__":
                label = state.row_headers[row_num] if row_num < len(state.row_headers) else str(row_num + 1)
            else:
                label = str(row[x_idx]) if x_idx < len(row) else ""
            value_text = row[y_idx] if y_idx < len(row) else ""
            value = self._number(value_text)
            if value is not None:
                points.append((label, value))
        return points

    def _series_points(self) -> List[Tuple[str, List[Tuple[str, float]]]]:
        state = self.state
        config = self.config
        if not state or not config or not config.series:
            return []
        headers = list(state.headers)
        x_idx = headers.index(config.x_field) if config.x_field in headers else 0
        selected_cells = {tuple(cell) for cell in (config.source_selection or {}).get("cells", []) if len(cell) == 2}
        selected_rows = sorted({row for row, _ in selected_cells})
        row_indexes = selected_rows if selected_rows else list(range(len(state.rows)))
        result: List[Tuple[str, List[Tuple[str, float]]]] = []
        for spec in config.series:
            y_field = spec.get("y_field") or spec.get("field") or spec.get("name")
            if y_field not in headers:
                continue
            y_idx = headers.index(y_field)
            points: List[Tuple[str, float]] = []
            for row_num in row_indexes[:80]:
                if row_num < 0 or row_num >= len(state.rows):
                    continue
                row = state.rows[row_num]
                label = state.row_headers[row_num] if config.x_field == "__row_header__" and row_num < len(state.row_headers) else str(row[x_idx]) if x_idx < len(row) else ""
                value = self._number(row[y_idx] if y_idx < len(row) else "")
                if value is not None:
                    points.append((label, value))
            if points:
                result.append((spec.get("name") or y_field, points))
        return result

    def _number(self, value: Any) -> float | None:
        text = str(value if value is not None else "").strip()
        if not text:
            return None
        negative = text.startswith("(") and text.endswith(")")
        if negative:
            text = text[1:-1]
        text = text.replace(",", "").replace("$", "").replace("%", "").strip()
        try:
            number = float(text)
        except Exception:
            return None
        return -abs(number) if negative else number

    def _color(self, idx: int, label: str = "") -> QColor:
        override = (self.config.color_overrides or {}).get(label) if self.config else None
        return QColor(override or self.palette[idx % len(self.palette)])

    def _paint_grid(self, painter, area, max_val):
        painter.setPen(QPen(QColor("#394452"), 1))
        for idx in range(1, 5):
            y = area.bottom() - (idx / 4) * area.height()
            painter.drawLine(area.left(), y, area.right(), y)

    def _paint_bar(self, painter, area, points, max_val):
        gap = 4
        bar_w = max(4, (area.width() - gap * (len(points) - 1)) / max(1, len(points)))
        for idx, (label, value) in enumerate(points):
            h = (abs(value) / max_val) * area.height()
            x = area.left() + idx * (bar_w + gap)
            rect = QRectF(x, area.bottom() - h, bar_w, h)
            painter.setBrush(QBrush(self._color(idx, label)))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRect(rect)
            if self.config and self.config.show_data_labels:
                painter.setPen(QPen(QColor("#f3f6fb")))
                painter.drawText(QRectF(x - 8, rect.top() - 18, bar_w + 16, 16), Qt.AlignmentFlag.AlignCenter, self._format_number(value))

    def _paint_grouped_bar(self, painter, area, series, max_val):
        if not series:
            return
        labels = [label for label, _ in series[0][1]]
        group_gap = 8
        group_w = max(12, (area.width() - group_gap * max(0, len(labels) - 1)) / max(1, len(labels)))
        bar_w = max(3, (group_w - 4) / max(1, len(series)))
        for group_idx, label in enumerate(labels):
            group_x = area.left() + group_idx * (group_w + group_gap)
            for series_idx, (_, points) in enumerate(series):
                value = points[group_idx][1] if group_idx < len(points) else 0
                h = (abs(value) / max_val) * area.height()
                x = group_x + series_idx * bar_w
                rect = QRectF(x, area.bottom() - h, bar_w, h)
                painter.setBrush(QBrush(self._color(series_idx, label)))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRect(rect)

    def _paint_line(self, painter, area, points, max_val):
        prev = None
        painter.setPen(QPen(self._color(0), 2))
        for idx, (label, value) in enumerate(points):
            x = area.left() + (idx / max(1, len(points) - 1)) * area.width()
            y = area.bottom() - (abs(value) / max_val) * area.height()
            if prev:
                painter.drawLine(prev[0], prev[1], x, y)
            painter.setBrush(QBrush(self._color(idx, label)))
            painter.drawEllipse(QRectF(x - 3, y - 3, 6, 6))
            if self.config and self.config.show_data_labels:
                painter.drawText(QRectF(x - 22, y - 22, 44, 16), Qt.AlignmentFlag.AlignCenter, self._format_number(value))
            prev = (x, y)

    def _paint_multi_line(self, painter, area, series, max_val):
        for idx, (_, points) in enumerate(series):
            original = list(self.palette)
            self.palette = [original[idx % len(original)]]
            self._paint_line(painter, area, points, max_val)
            self.palette = original

    def _paint_scatter(self, painter, area, points, max_val):
        painter.setPen(Qt.PenStyle.NoPen)
        for idx, (label, value) in enumerate(points):
            x = area.left() + (idx / max(1, len(points) - 1)) * area.width()
            y = area.bottom() - (abs(value) / max_val) * area.height()
            painter.setBrush(QBrush(self._color(idx, label)))
            painter.drawEllipse(QRectF(x - 4, y - 4, 8, 8))

    def _paint_pie(self, painter, area, points):
        total = sum(abs(v) for _, v in points) or 1.0
        size = min(area.width(), area.height())
        rect = QRectF(area.center().x() - size / 2, area.center().y() - size / 2, size, size)
        start = 0
        for idx, (label, value) in enumerate(points[:20]):
            span = int((abs(value) / total) * 360 * 16)
            painter.setBrush(QBrush(self._color(idx, label)))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawPie(rect, start, span)
            start += span

    def _paint_axes(self, painter, bounds, area, points, max_val):
        painter.setPen(QPen(QColor("#d7dde5")))
        if self.config and self.config.show_x_labels and self.config.chart_type != "pie":
            step = max(1, len(points) // 8)
            for idx, (label, _) in enumerate(points):
                if idx % step:
                    continue
                x = area.left() + (idx / max(1, len(points) - 1)) * area.width()
                if self.config.show_tick_marks:
                    painter.drawLine(x, area.bottom(), x, area.bottom() + 4)
                painter.drawText(QRectF(x - 38, area.bottom() + 5, 76, 16), Qt.AlignmentFlag.AlignCenter, label[:12])
        if self.config and self.config.show_y_labels:
            for idx in range(0, 5):
                value = (idx / 4) * max_val
                y = area.bottom() - (idx / 4) * area.height()
                if self.config.show_tick_marks:
                    painter.drawLine(area.left() - 4, y, area.left(), y)
                label_left = bounds.left() + (24 if self.config.y_title else 2)
                painter.drawText(QRectF(label_left, y - 8, area.left() - label_left - 8, 16),
                                 Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self._format_number(value))
        if self.config and self.config.x_title:
            painter.drawText(QRectF(area.left(), bounds.bottom() - 18, area.width(), 16),
                             Qt.AlignmentFlag.AlignCenter, self.config.x_title)
        if self.config and self.config.y_title:
            painter.save()
            painter.translate(bounds.left() + 10, area.center().y())
            painter.rotate(-90)
            painter.drawText(QRectF(-area.height() / 2, -8, area.height(), 16), Qt.AlignmentFlag.AlignCenter, self.config.y_title)
            painter.restore()

    def _format_number(self, value: Any) -> str:
        try:
            number = float(value)
        except Exception:
            return str(value)
        return f"{number:,.4f}".rstrip("0").rstrip(".")
