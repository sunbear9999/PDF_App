"""Tree widget compatibility helpers for the Document Explorer."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem


def tag_folder_icon(color):
    """Return a small folder silhouette filled with the tag's own color."""
    fill = QColor(color or "#808080")
    if not fill.isValid():
        fill = QColor("#808080")
    pixmap = QPixmap(18, 16)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(fill.darker(145), 1))
    painter.setBrush(fill)
    painter.drawRoundedRect(1, 4, 16, 11, 2, 2)
    painter.drawRoundedRect(2, 2, 8, 6, 2, 2)
    painter.end()
    return QIcon(pixmap)


class DocumentTreeItem(QTreeWidgetItem):
    """Accept both the old list-item role API and the tree-item column API."""

    def data(self, column_or_role, role=None):
        return super().data(0, column_or_role) if role is None else super().data(column_or_role, role)

    def setData(self, column_or_role, role_or_value, value=None):
        if value is None:
            return super().setData(0, column_or_role, role_or_value)
        return super().setData(column_or_role, role_or_value, value)


class DocumentSourceTree(QTreeWidget):
    """A one-column tree with the small list API used by older explorer code."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setIndentation(18)

    def addItem(self, item):
        self.addTopLevelItem(item)

    def count(self):
        return self.topLevelItemCount()

    def item(self, index):
        return self.topLevelItem(index)

    def setItemWidget(self, item, column_or_widget, widget=None):
        if widget is None:
            super().setItemWidget(item, 0, column_or_widget)
        else:
            super().setItemWidget(item, column_or_widget, widget)

    def first_source_item(self, kind_role):
        for index in range(self.topLevelItemCount()):
            found = self._first_source(self.topLevelItem(index), kind_role)
            if found is not None:
                return found
        return None

    def _first_source(self, item, kind_role):
        if item.data(0, kind_role) == "source":
            return item
        for index in range(item.childCount()):
            found = self._first_source(item.child(index), kind_role)
            if found is not None:
                return found
        return None
