"""Modeless host for the shared entity/migration/relation decision panel."""
from PySide6.QtWidgets import QDialog, QVBoxLayout
from gui.components.knowledge_decision_panel import KnowledgeDecisionPanel


class KnowledgeDecisionDialog(QDialog):
    def __init__(self, app_context, parent=None):
        super().__init__(parent)
        self.ctx = app_context
        self.setWindowTitle('Review knowledge decision')
        self.resize(1060,780)
        layout = QVBoxLayout(self)
        self.panel = KnowledgeDecisionPanel(app_context,self)
        layout.addWidget(self.panel)
        self._project_signal = getattr(getattr(app_context,'bus',None),'project_clearing_started',None)
        if self._project_signal is not None:
            self._project_signal.connect(self._project_closed)

    def _project_closed(self,*_):
        self.close()

    def closeEvent(self,event):
        self.panel.shutdown()
        if self._project_signal is not None:
            self._project_signal.disconnect(self._project_closed)
            self._project_signal = None
        super().closeEvent(event)
