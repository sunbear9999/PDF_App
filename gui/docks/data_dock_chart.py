from __future__ import annotations

from typing import Any, List, Tuple

from PySide6.QtCore import Qt, QRectF, QSize
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QBrush, QPolygonF
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QWidget

from core.models.data_dock_models import ChartConfig, DataGridState
from core.utils.numeric_utils import coerce_number
from core.services.chart_data_service import ChartDataAdapter


class DataChartWidget(QWidget):
    """Qt chart renderer shared by Data Dock preview, workspace nodes, and export."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state: DataGridState | None = None
        self.config: ChartConfig | None = None
        self.palette = ["#2f80ed", "#27ae60", "#f2994a", "#eb5757", "#9b51e0"]
        self.chart_spec = None
        self.normalized = {}
        self.setMinimumHeight(150)

    def sizeHint(self) -> QSize:
        return QSize(420, 260)

    def set_chart(self, state: DataGridState | None, config: ChartConfig | None, palette: List[str] | None = None, chart_spec=None):
        self.state = state
        self.config = config
        if palette:
            self.palette = list(palette)
        elif config and (config.export_options or {}).get("palette_colors"):
            self.palette = list(config.export_options["palette_colors"])
        self.chart_spec = chart_spec
        if state and config:
            adapter = getattr(chart_spec, "data_adapter", None) if chart_spec else None
            adapt = getattr(adapter, "adapt", adapter)
            self.normalized = adapt(state, config) if adapt else ChartDataAdapter().adapt(state, config)
        else:
            self.normalized = {}
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
        factory = getattr(self.chart_spec, "renderer_factory", None) if self.chart_spec else None
        if factory:
            try:
                renderer = factory() if callable(factory) else factory
                render = getattr(renderer, "render", renderer)
                render(painter, bounds, self.normalized, self.config, list(self.palette))
            except Exception as exc:
                painter.drawText(bounds, Qt.AlignmentFlag.AlignCenter, f"Chart renderer failed: {exc}")
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

        all_values = [value for _, items in series for _, value in items if value is not None] if series else [v for _, v in points if v is not None]
        min_val = min(0.0, min(all_values, default=0.0))
        max_val = max(0.0, max(all_values, default=0.0))
        if self.config.chart_type in {"stacked_bar", "stacked_area"} and series:
            count = max((len(items) for _, items in series), default=0)
            positive = [sum(max(0.0, items[index][1] or 0.0) for _, items in series if index < len(items)) for index in range(count)]
            negative = [sum(min(0.0, items[index][1] or 0.0) for _, items in series if index < len(items)) for index in range(count)]
            min_val = min(0.0, min(negative, default=0.0))
            max_val = max(0.0, max(positive, default=0.0))
        elif self.config.chart_type == "stacked_100_bar":
            has_negative = any(value < 0 for value in all_values)
            min_val, max_val = (-1.0 if has_negative else 0.0), 1.0
        if min_val == max_val:
            max_val = min_val + 1.0
        if self.config.show_grid_lines:
            self._paint_grid(painter, area)
        painter.setPen(QPen(QColor("#8792a2"), 1))
        zero_y = self._value_y(area, 0.0, min_val, max_val)
        painter.drawLine(area.left(), zero_y, area.right(), zero_y)
        painter.drawLine(area.bottomLeft(), area.topLeft())

        bars = lambda: self._paint_grouped_bar(painter, area, series, min_val, max_val) if len(series) > 1 else self._paint_bar(painter, area, points, min_val, max_val)
        line = lambda: self._paint_multi_line(painter, area, series, min_val, max_val) if len(series) > 1 else self._paint_line(painter, area, points, min_val, max_val)
        renderers = {
            "bar": bars,
            "horizontal_bar": lambda: self._paint_horizontal_bar(painter, area, points, min_val, max_val, series),
            "stacked_bar": lambda: self._paint_stacked_bar(painter, area, series or [("", points)], min_val, max_val, False),
            "stacked_100_bar": lambda: self._paint_stacked_bar(painter, area, series or [("", points)], min_val, max_val, True),
            "line": line,
            "area": lambda: self._paint_area(painter, area, series or [("", points)], min_val, max_val, False),
            "stacked_area": lambda: self._paint_area(painter, area, series or [("", points)], min_val, max_val, True),
            "scatter": lambda: self._paint_scatter_xy(painter, area, min_val, max_val),
            "pie": lambda: self._paint_pie(painter, area, points, False),
            "donut": lambda: self._paint_pie(painter, area, points, True),
            "histogram": lambda: self._paint_histogram(painter, area, points),
            "box": lambda: self._paint_box(painter, area, series or [("", points)], min_val, max_val),
            "heatmap": lambda: self._paint_heatmap(painter, area),
        }
        renderers.get(self.config.chart_type, bars)()
        if self.config.show_legend and len(series) > 1:
            self._paint_legend(painter, bounds, series)
        self._paint_axes(painter, bounds, area, points, min_val, max_val)

    def _paint_legend(self, painter, bounds, series):
        x = bounds.right() - 130
        y = bounds.top() + 6
        for index, (name, _) in enumerate(series[:6]):
            painter.fillRect(QRectF(x, y + index * 15, 9, 9), self._color(index))
            painter.setPen(QPen(QColor("#d7dde5")))
            painter.drawText(QRectF(x + 13, y - 3 + index * 15, 112, 14), Qt.AlignmentFlag.AlignLeft, str(name)[:18])

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
                points.append((label, value))
            if any(value is not None for _, value in points):
                result.append((spec.get("name") or y_field, points))
        return result

    def _number(self, value: Any) -> float | None:
        return coerce_number(value)

    def _color(self, idx: int, label: str = "") -> QColor:
        override = (self.config.color_overrides or {}).get(label) if self.config else None
        return QColor(override or self.palette[idx % len(self.palette)])

    def _paint_grid(self, painter, area):
        painter.setPen(QPen(QColor("#394452"), 1))
        for idx in range(1, 5):
            y = area.bottom() - (idx / 4) * area.height()
            painter.drawLine(area.left(), y, area.right(), y)

    @staticmethod
    def _value_y(area, value, min_val, max_val):
        return area.bottom() - ((value - min_val) / (max_val - min_val)) * area.height()

    def _paint_bar(self, painter, area, points, min_val, max_val):
        gap = 4
        bar_w = max(4, (area.width() - gap * (len(points) - 1)) / max(1, len(points)))
        for idx, (label, value) in enumerate(points):
            x = area.left() + idx * (bar_w + gap)
            value_y = self._value_y(area, value, min_val, max_val)
            zero_y = self._value_y(area, 0.0, min_val, max_val)
            rect = QRectF(x, min(value_y, zero_y), bar_w, abs(zero_y - value_y))
            painter.setBrush(QBrush(self._color(idx, label)))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRect(rect)
            if self.config and self.config.show_data_labels:
                painter.setPen(QPen(QColor("#f3f6fb")))
                label_y = rect.top() - 18 if value >= 0 else rect.bottom() + 2
                painter.drawText(QRectF(x - 8, label_y, bar_w + 16, 16), Qt.AlignmentFlag.AlignCenter, self._format_number(value))

    def _paint_grouped_bar(self, painter, area, series, min_val, max_val):
        if not series:
            return
        labels = [label for label, _ in series[0][1]]
        group_gap = 8
        group_w = max(12, (area.width() - group_gap * max(0, len(labels) - 1)) / max(1, len(labels)))
        bar_w = max(3, (group_w - 4) / max(1, len(series)))
        for group_idx, label in enumerate(labels):
            group_x = area.left() + group_idx * (group_w + group_gap)
            for series_idx, (_, points) in enumerate(series):
                value = points[group_idx][1] if group_idx < len(points) else None
                if value is None:
                    continue
                x = group_x + series_idx * bar_w
                value_y = self._value_y(area, value, min_val, max_val)
                zero_y = self._value_y(area, 0.0, min_val, max_val)
                rect = QRectF(x, min(value_y, zero_y), bar_w, abs(zero_y - value_y))
                painter.setBrush(QBrush(self._color(series_idx, label)))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRect(rect)

    def _paint_line(self, painter, area, points, min_val, max_val):
        prev = None
        painter.setPen(QPen(self._color(0), 2))
        for idx, (label, value) in enumerate(points):
            if value is None:
                prev = None
                continue
            x = area.left() + (idx / max(1, len(points) - 1)) * area.width()
            y = self._value_y(area, value, min_val, max_val)
            if prev:
                painter.drawLine(prev[0], prev[1], x, y)
            painter.setBrush(QBrush(self._color(idx, label)))
            painter.drawEllipse(QRectF(x - 3, y - 3, 6, 6))
            if self.config and self.config.show_data_labels:
                painter.drawText(QRectF(x - 22, y - 22, 44, 16), Qt.AlignmentFlag.AlignCenter, self._format_number(value))
            prev = (x, y)

    def _paint_multi_line(self, painter, area, series, min_val, max_val):
        for idx, (_, points) in enumerate(series):
            original = list(self.palette)
            self.palette = [original[idx % len(original)]]
            self._paint_line(painter, area, points, min_val, max_val)
            self.palette = original

    def _paint_scatter(self, painter, area, points, min_val, max_val):
        painter.setPen(Qt.PenStyle.NoPen)
        for idx, (label, value) in enumerate(points):
            x = area.left() + (idx / max(1, len(points) - 1)) * area.width()
            y = self._value_y(area, value, min_val, max_val)
            painter.setBrush(QBrush(self._color(idx, label)))
            painter.drawEllipse(QRectF(x - 4, y - 4, 8, 8))

    def _paint_pie(self, painter, area, points, donut=False):
        points = [(label, value) for label, value in points if value is not None]
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
        if donut:
            hole = rect.adjusted(rect.width() * .28, rect.height() * .28, -rect.width() * .28, -rect.height() * .28)
            painter.setBrush(QBrush(QColor("#1f2933")))
            painter.drawEllipse(hole)

    def _paint_horizontal_bar(self, painter, area, points, min_val, max_val, series=None):
        row_h = area.height() / max(1, len(points))
        zero_x = area.left() + ((0 - min_val) / (max_val - min_val)) * area.width()
        source_series = series or [("", points)]
        bar_h = max(2, (row_h - 4) / max(1, len(source_series)))
        for series_index, (_, values) in enumerate(source_series):
            for idx, (label, value) in enumerate(values):
                if value is None: continue
                value_x = area.left() + ((value - min_val) / (max_val - min_val)) * area.width()
                rect = QRectF(min(zero_x, value_x), area.top() + idx * row_h + 2 + series_index * bar_h,
                              abs(value_x - zero_x), bar_h)
                painter.fillRect(rect, self._color(series_index, label))

    def _paint_stacked_bar(self, painter, area, series, min_val, max_val, normalize=False):
        labels = [label for label, _ in series[0][1]] if series else []
        width = area.width() / max(1, len(labels))
        positive_totals = [sum(max(0.0, points[i][1] or 0) for _, points in series if i < len(points)) or 1 for i in range(len(labels))]
        negative_totals = [sum(abs(min(0.0, points[i][1] or 0)) for _, points in series if i < len(points)) or 1 for i in range(len(labels))]
        for i, label in enumerate(labels):
            positive_cursor = negative_cursor = 0.0
            for series_index, (_, values) in enumerate(series):
                value = values[i][1] if i < len(values) else None
                if value is None:
                    continue
                shown = value
                if normalize:
                    shown = value / (positive_totals[i] if value >= 0 else negative_totals[i])
                start = positive_cursor if shown >= 0 else negative_cursor
                end = start + shown
                if shown >= 0: positive_cursor = end
                else: negative_cursor = end
                start_y, end_y = self._value_y(area, start, min_val, max_val), self._value_y(area, end, min_val, max_val)
                rect = QRectF(area.left() + i * width + 2, min(start_y, end_y), max(2, width - 4), abs(end_y - start_y))
                painter.fillRect(rect, self._color(series_index, label))

    def _paint_area(self, painter, area, series, min_val, max_val, stacked=False):
        cumulative = []
        for series_index, (_, points) in enumerate(series):
            values = [value for _, value in points]
            if not cumulative:
                cumulative = [0.0] * len(values)
            top = [None if value is None else (base + value if stacked else value) for base, value in zip(cumulative, values)]
            base_values = cumulative if stacked else [0.0] * len(values)
            color = self._color(series_index); color.setAlpha(105)
            painter.setBrush(color); painter.setPen(QPen(self._color(series_index), 2))
            segment = []
            for index, value in enumerate(top + [None]):
                if value is not None:
                    segment.append(index)
                    continue
                if segment:
                    polygon = [QPointF(area.left() + i / max(1, len(values) - 1) * area.width(), self._value_y(area, top[i], min_val, max_val)) for i in segment]
                    polygon += [QPointF(area.left() + i / max(1, len(values) - 1) * area.width(), self._value_y(area, base_values[i], min_val, max_val)) for i in reversed(segment)]
                    painter.drawPolygon(QPolygonF(polygon)); segment = []
            if stacked:
                cumulative = [base if value is None else base + value for base, value in zip(cumulative, values)]

    def _paint_scatter_xy(self, painter, area, min_val, max_val):
        x_values = self.normalized.get("x_values") or []
        series = self.normalized.get("series") or []
        valid_x = [value for value in x_values if value is not None]
        if not valid_x:
            return self._paint_scatter(painter, area, self._points(), min_val, max_val)
        x_min, x_max = min(valid_x), max(valid_x)
        if x_min == x_max: x_max += 1
        for series_index, item in enumerate(series):
            for x_value, y_value in zip(x_values, item["values"]):
                if x_value is None or y_value is None: continue
                x = area.left() + (x_value - x_min) / (x_max - x_min) * area.width()
                y = self._value_y(area, y_value, min_val, max_val)
                painter.setBrush(self._color(series_index)); painter.drawEllipse(QRectF(x - 4, y - 4, 8, 8))

    def _paint_histogram(self, painter, area, points):
        values = sorted(value for _, value in points if value is not None)
        if not values: return
        bins = int((self.config.options or {}).get("bins") or max(1, min(20, round(len(values) ** .5))))
        low, high = min(values), max(values)
        width = (high - low) / bins if high != low else 1
        counts = [0] * bins
        for value in values: counts[min(bins - 1, int((value - low) / width))] += 1
        maximum = max(counts) or 1; bar_w = area.width() / bins
        for idx, count in enumerate(counts):
            height = count / maximum * area.height()
            painter.fillRect(QRectF(area.left() + idx * bar_w + 1, area.bottom() - height, max(1, bar_w - 2), height), self._color(idx))

    def _paint_box(self, painter, area, series, min_val, max_val):
        for idx, (_, points) in enumerate(series):
            values = sorted(value for _, value in points if value is not None)
            if not values: continue
            def percentile(p):
                position = (len(values) - 1) * p; lower = int(position); upper = min(len(values) - 1, lower + 1)
                return values[lower] + (values[upper] - values[lower]) * (position - lower)
            q1, q2, q3 = percentile(.25), percentile(.5), percentile(.75)
            iqr = q3 - q1
            lower_candidates = [value for value in values if value >= q1 - 1.5 * iqr]
            upper_candidates = [value for value in values if value <= q3 + 1.5 * iqr]
            lower = min(lower_candidates or values); upper = max(upper_candidates or values)
            x = area.left() + (idx + .5) / len(series) * area.width(); box_w = min(50, area.width() / max(2, len(series) * 2))
            painter.setPen(QPen(self._color(idx), 2)); painter.setBrush(QBrush(self._color(idx).lighter(150)))
            painter.drawLine(x, self._value_y(area, lower, min_val, max_val), x, self._value_y(area, upper, min_val, max_val))
            rect = QRectF(x - box_w / 2, self._value_y(area, q3, min_val, max_val), box_w, self._value_y(area, q1, min_val, max_val) - self._value_y(area, q3, min_val, max_val))
            painter.drawRect(rect); painter.drawLine(rect.left(), self._value_y(area, q2, min_val, max_val), rect.right(), self._value_y(area, q2, min_val, max_val))
            for value in values:
                if value < lower or value > upper:
                    y = self._value_y(area, value, min_val, max_val)
                    painter.drawEllipse(QRectF(x - 3, y - 3, 6, 6))

    def _paint_heatmap(self, painter, area):
        series = self.normalized.get("series") or []
        categories = self.normalized.get("categories") or []
        values = [value for item in series for value in item["values"] if value is not None]
        if not values: return
        low, high = min(values), max(values); cell_w = area.width() / max(1, len(categories)); cell_h = area.height() / max(1, len(series))
        for row, item in enumerate(series):
            for column, value in enumerate(item["values"]):
                if value is None: continue
                ratio = .5 if high == low else (value - low) / (high - low)
                color = QColor.fromHsvF((.65 * (1 - ratio)), .75, .9)
                painter.fillRect(QRectF(area.left() + column * cell_w, area.top() + row * cell_h, cell_w, cell_h), color)

    def _paint_axes(self, painter, bounds, area, points, min_val, max_val):
        painter.setPen(QPen(QColor("#d7dde5")))
        if self.config and self.config.show_x_labels and self.config.chart_type not in {"pie", "donut"}:
            if self.config.chart_type == "scatter":
                x_values = [value for value in self.normalized.get("x_values", []) if value is not None]
                if x_values:
                    x_min, x_max = min(x_values), max(x_values)
                    for idx in range(5):
                        x = area.left() + idx / 4 * area.width(); value = x_min + idx / 4 * (x_max - x_min)
                        painter.drawText(QRectF(x - 38, area.bottom() + 5, 76, 16), Qt.AlignmentFlag.AlignCenter, self._format_number(value))
            else:
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
                value = min_val + (idx / 4) * (max_val - min_val)
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
