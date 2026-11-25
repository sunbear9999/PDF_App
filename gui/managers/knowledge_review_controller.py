"""UI intent routing for shared modeless knowledge-decision dialogs."""
from PySide6.QtCore import QObject
from core.events.domains.intelligence_events import IntelligenceIntent
from gui.components.dialogs.knowledge_decision_dialog import KnowledgeDecisionDialog


class KnowledgeReviewController(QObject):
    def __init__(self, app_context, parent=None):
        super().__init__(parent)
        self.ctx = app_context
        self.ctx.bus.intelligence_action_requested.connect(self._open)
        self.destroyed.connect(self._disconnect)

    def _open(self, intent, payload):
        if intent != IntelligenceIntent.REVIEW_DECISION:
            return
        if str(payload.command).startswith('relation_'):
            from gui.components.dialogs.connection_review_dialog import open_connection_review
            open_connection_review(self.ctx, self.parent(), assertion_id=payload.artifact_id)
            return
        manager = getattr(self.ctx,'dialog_manager',None)
        if manager:
            dialog = manager.show(KnowledgeDecisionDialog, singleton=False,
                factory=lambda:KnowledgeDecisionDialog(self.ctx, self.parent()))
            if dialog:
                dialog.panel.configure(payload.artifact_id,payload.command,payload.values)

    def _disconnect(self,*_):
        try:
            self.ctx.bus.intelligence_action_requested.disconnect(self._open)
        except (RuntimeError,TypeError):
            pass
