"""Bounded flat artifact pages without one Qt item or payload copy per cell."""
from PySide6.QtCore import QAbstractTableModel,QModelIndex,Qt


class ArtifactPageModel(QAbstractTableModel):
    def __init__(self,headers,parent=None):
        super().__init__(parent)
        self.headers=list(headers)
        self.rows,self.cells=[],[]
        self._sort=None

    def rowCount(self,parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self,parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.headers)

    def data(self,index,role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0<=index.row()<len(self.rows): return None
        if role==Qt.ItemDataRole.UserRole and index.column()==0:
            return self.rows[index.row()]
        if role==Qt.ItemDataRole.ToolTipRole and self.rows[index.row()].get('explanation'):
            row=self.rows[index.row()]
            return (row.get('text') or '')+'\n\n'+row['explanation']+'\n'+(row.get('association_reason') or '')
        if role in (Qt.ItemDataRole.DisplayRole,Qt.ItemDataRole.AccessibleTextRole,Qt.ItemDataRole.ToolTipRole):
            return self.cells[index.row()][index.column()] if index.column()<len(self.cells[index.row()]) else None
        return None

    def headerData(self,section,orientation,role=Qt.ItemDataRole.DisplayRole):
        if orientation==Qt.Orientation.Horizontal and role==Qt.ItemDataRole.DisplayRole and 0<=section<len(self.headers):
            return self.headers[section]
        return None

    def setHorizontalHeaderLabels(self,labels):
        if list(labels)==self.headers:
            return  # Reapplying identical presentation must preserve the selected connection.
        self.beginResetModel()
        self.headers=list(labels)
        if self._sort is not None and self._sort[0]>=len(self.headers):
            self._sort=None
        self.endResetModel()

    def replace(self,rows,cells):
        self.beginResetModel()
        self.rows,self.cells=list(rows),list(cells)
        if self._sort is not None:
            self._order(*self._sort)
        self.endResetModel()

    def removeRows(self,row,count,parent=QModelIndex()):
        if row==0 and count==len(self.rows) and not parent.isValid():
            self.replace([],[]); return True
        return False

    def sort(self,column,order=Qt.SortOrder.AscendingOrder):
        if not 0<=column<len(self.headers): return
        self.layoutAboutToBeChanged.emit()
        old=self.persistentIndexList()
        identities=[id(self.rows[index.row()]) for index in old]
        self._sort=(column,order)
        self._order(column,order)
        positions={id(row):index for index,row in enumerate(self.rows)}
        self.changePersistentIndexList(old,[self.index(positions[identity],index.column()) for index,identity in zip(old,identities)])
        self.layoutChanged.emit()

    def _order(self,column,order):
        pairs=sorted(zip(self.rows,self.cells),key=lambda pair:pair[1][column].casefold(),reverse=order==Qt.SortOrder.DescendingOrder)
        self.rows=[pair[0] for pair in pairs]; self.cells=[pair[1] for pair in pairs]
