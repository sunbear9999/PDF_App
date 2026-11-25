"""Modeless window around the simplified connection review (one connection, or cycle all)."""
from PySide6.QtWidgets import QDialog, QVBoxLayout

from gui.components.connection_review_panel import ConnectionReviewPanel


class ConnectionReviewDialog(QDialog):
    def __init__(self, app_context, parent=None, cycle=True):
        super().__init__(parent)
        self.setWindowTitle("Review connections" if cycle else "Review connection")
        self.resize(760, 360)
        layout = QVBoxLayout(self)
        self.panel = ConnectionReviewPanel(app_context, self, cycle=cycle)
        layout.addWidget(self.panel)
        bus = getattr(app_context, "bus", None)
        self._project_signal = getattr(bus, "project_clearing_started", None)
        if self._project_signal is not None:
            self._project_signal.connect(self.close)

    def closeEvent(self, event):
        self.panel.shutdown()
        if self._project_signal is not None:
            try:
                self._project_signal.disconnect(self.close)
            except (RuntimeError, TypeError):
                pass
            self._project_signal = None
        super().closeEvent(event)


def open_connection_review(ctx, parent, assertion_id=None, source_id=""):
    """Cycle through connections, or show one connection when ``assertion_id`` is given."""
    manager = getattr(ctx, "dialog_manager", None)
    if manager is None:
        return None
    single = assertion_id is not None
    dialog = manager.show(ConnectionReviewDialog, key="intelligence_connection" if single else "intelligence_connections",
                          singleton=True, factory=lambda: ConnectionReviewDialog(ctx, parent, cycle=not single))
    if dialog:
        dialog.panel.show_connection(assertion_id) if single else dialog.panel.start_cycle(source_id)
    return dialog
