"""Bounded page diagram; evidence and keyboard actions remain in the shared table."""
import math
from PySide6.QtCore import Qt,Signal,QPointF
from PySide6.QtGui import QPen,QPainterPath
from PySide6.QtWidgets import QGraphicsView,QGraphicsScene,QGraphicsSimpleTextItem

MAX_DIAGRAM_NODES=40


class EntityNetworkView(QGraphicsView):
    entity_selected=Signal(str)

    def __init__(self,app_context,parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setMinimumHeight(170); self.setMaximumHeight(260)
        self.setAccessibleName('Entity network diagram; all connections and source passages are available in the table below')
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)

    def set_network(self,snapshot):
        self.scene().clear()
        rows=snapshot.get('network') or []
        focus=snapshot.get('focus_entity_id')
        nodes=sorted(snapshot.get('nodes') or [],key=lambda row:(row['artifact_id']!=focus,row['artifact_id']))[:MAX_DIAGRAM_NODES]
        positions={row['artifact_id']:QPointF(180*math.cos(index*2*math.pi/max(1,len(nodes))),
            130*math.sin(index*2*math.pi/max(1,len(nodes)))) for index,row in enumerate(nodes)}
        for row in rows:
            start,end=positions.get(row['source_entity_id']),positions.get(row['target_entity_id'])
            if start is None or end is None: continue
            pen=QPen(self.palette().highlight().color(),2)
            if row['relation_state']!='Accepted relationship': pen.setStyle(Qt.PenStyle.DashLine)
            path=QPainterPath(start); path.lineTo(end)
            edge=self.scene().addPath(path,pen)
            edge.setToolTip(row['display_text']+'\n'+row['explanation'])
            if row['relation_state']=='Co-occurrence observation': continue
            direction=end-start; length=max(1,math.hypot(direction.x(),direction.y()))
            tip=start+direction*0.60; unit=direction/length; normal=QPointF(-unit.y(),unit.x())
            for side in (-1,1):
                arrow=QPainterPath(tip); arrow.lineTo(tip-unit*9+normal*side*4)
                self.scene().addPath(arrow,pen)
        for row in nodes:
            point=positions[row['artifact_id']]
            item=self.scene().addEllipse(point.x()-8,point.y()-8,16,16,QPen(self.palette().highlight().color()),self.palette().highlight())
            item.setData(0,row['artifact_id']); item.setToolTip(row.get('exact_text') or '')
            label=QGraphicsSimpleTextItem((row.get('exact_text') or 'Unnamed entity')[:48])
            label.setBrush(self.palette().text()); label.setPos(point+QPointF(10,-9)); label.setData(0,row['artifact_id'])
            self.scene().addItem(label)
        self.scene().setSceneRect(self.scene().itemsBoundingRect().adjusted(-20,-20,20,20))
        self.fitInView(self.sceneRect(),Qt.AspectRatioMode.KeepAspectRatio)
        self.setToolTip(f'Diagram: {len(nodes)} of {len(snapshot.get("nodes") or [])} entities on this page (maximum 40). Dashed: co-occurrence/candidate; solid: accepted semantic assertion. Click an entity to focus. Use table paging for all connections.')

    def mousePressEvent(self,event):
        item=self.itemAt(event.position().toPoint())
        if item is not None and item.data(0): self.entity_selected.emit(str(item.data(0)))
        super().mousePressEvent(event)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if not self.sceneRect().isEmpty(): self.fitInView(self.sceneRect(),Qt.AspectRatioMode.KeepAspectRatio)
