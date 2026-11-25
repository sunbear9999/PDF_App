"""Bounded, zoomable calendar intervals; the adjacent table is the accessible equivalent."""
from datetime import date
from PySide6.QtCore import Qt, Signal, QRectF
from PySide6.QtGui import QPainter, QPen, QBrush
from PySide6.QtWidgets import QWidget, QScrollArea

ROW_HEIGHT = 28
AXIS_HEIGHT = 34
BASE_WIDTH = 640
CHART_MIN_HEIGHT = 120


class EventIntervalChart(QScrollArea):
    selected = Signal(int)

    def __init__(self, app_context, parent=None):
        super().__init__(parent)
        self.canvas = IntervalCanvas(self)
        self.setWidget(self.canvas)
        self.setMinimumHeight(CHART_MIN_HEIGHT)   # grows with its resizable splitter pane
        self.setAccessibleName('Calendar interval chart; the event table below has the same records')
        self.canvas.chosen.connect(self.selected)

    def set_events(self, rows, zoom=1):
        self.canvas.rows = rows
        self.canvas.resize(max(BASE_WIDTH, self.viewport().width()) * zoom, max(90, AXIS_HEIGHT + ROW_HEIGHT * len(rows)))
        self.canvas.update()


class IntervalCanvas(QWidget):
    chosen = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []
        self.setToolTip('Bars show the full possible calendar interval, not an invented exact day. Zoom changes scale; scroll to inspect the page. Click a bar to inspect its event.')

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(event.rect(), self.palette().base())
        dates = [(date.fromisoformat(row['date_start']).toordinal(), date.fromisoformat(row['date_end']).toordinal())
                 for row in self.rows if row.get('date_start') and row.get('date_end')]
        if not dates:
            painter.setPen(self.palette().text().color()); painter.drawText(12, 25, 'No dated intervals on this page.'); return
        lower, upper = min(pair[0] for pair in dates), max(pair[1] for pair in dates)
        span = max(1, upper - lower)
        width = max(1, self.width() - 200)
        painter.setPen(self.palette().text().color())
        painter.drawText(12, 22, date.fromordinal(lower).isoformat())
        painter.drawText(self.width() - 100, 22, date.fromordinal(upper).isoformat())
        painter.drawLine(90, 28, self.width() - 100, 28)
        for index, row in enumerate(self.rows):
            top = AXIS_HEIGHT + index * ROW_HEIGHT
            if top + ROW_HEIGHT < event.rect().top() or top > event.rect().bottom():
                continue
            painter.setPen(self.palette().text().color()); painter.drawText(5, top + 18, row.get('date') or 'Unresolved')
            if not row.get('date_start') or not row.get('date_end'):
                continue
            start, end = (date.fromisoformat(row[key]).toordinal() for key in ('date_start', 'date_end'))
            left, right = 95 + width * (start - lower) / span, 95 + width * (end - lower) / span
            color = self.palette().highlight().color()
            painter.setPen(QPen(color, 1))
            painter.setBrush(QBrush(color, Qt.BrushStyle.Dense4Pattern if row.get('certainty') != 'confirmed' else Qt.BrushStyle.SolidPattern))
            painter.drawRoundedRect(QRectF(left, top + 5, max(4, right - left), 16), 3, 3)

    def mousePressEvent(self, event):
        row = int((event.position().y() - AXIS_HEIGHT) // ROW_HEIGHT)
        if 0 <= row < len(self.rows):
            self.chosen.emit(row)
        super().mousePressEvent(event)
