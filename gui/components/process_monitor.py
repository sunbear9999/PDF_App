from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QMenu, QSizePolicy
from PySide6.QtCore import Qt
import PySide6.QtWidgets as QtWidgets

class ProcessMonitorWidget(QPushButton):
    """A clickable dropdown button that displays all active background tasks."""
    def __init__(self, registry, theme=None):
        super().__init__()
        self.registry = registry
        self.theme = theme
        self.active_jobs = {}
        
        self.setObjectName("ProcessTrackerDropdown")
        self.setText("🟢 0 Active Processes")
         # Hidden by default if 0 processes
        
        self.clicked.connect(self._show_menu)
        
        self.registry.job_added.connect(self._on_job_added)
        self.registry.job_updated.connect(self._on_job_updated)
        self.registry.job_removed.connect(self._on_job_removed)
        
    def set_theme(self, theme):
        self.theme = theme
        self.setStyleSheet(f"""
            QPushButton#ProcessTrackerDropdown {{
                background-color: {theme.get('bg_panel', '#333')};
                color: #a8ff9d; font-weight: bold;
                border: 1px solid #4CAF50; border-radius: 4px; padding: 4px 8px;
            }}
            QPushButton#ProcessTrackerDropdown:hover {{ background-color: #4CAF50; color: #000; }}
        """)

    def _update_ui(self):
        count = len(self.active_jobs)
        if count == 0:
            self.setText(f"🟢 {count} Active Process{'es' if count > 1 else ''}")
        else:
            self.setText(f"🔴 {count} Active Process{'es' if count > 1 else ''}")
        self.show()
            
    def _on_job_added(self, job):
        self.active_jobs[job.id] = job
        self._update_ui()

    def _on_job_updated(self, job):
        if job.id in self.active_jobs:
            self.active_jobs[job.id] = job

    def _on_job_removed(self, job_id):
        self.active_jobs.pop(job_id, None)
        self._update_ui()
        
    def _show_menu(self):
        menu = QMenu(self)
        if self.theme:
            menu.setStyleSheet(f"background-color: {self.theme.get('bg_input', '#2b2b2b')}; color: {self.theme.get('text_main', '#fff')}; border: 1px solid {self.theme.get('border', '#444')};")
        
        for job_id, job in self.active_jobs.items():
            job_widget = QWidget()
            job_layout = QHBoxLayout(job_widget)
            job_layout.setContentsMargins(8, 4, 8, 4)
            
            lbl = QLabel(f"{job.name} - {job.status}")
            if self.theme: lbl.setStyleSheet(f"color: {self.theme.get('text_main', '#fff')};")
            
            btn_stop = QPushButton("🛑 Stop")
            btn_stop.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_stop.clicked.connect(lambda checked, jid=job_id: self.registry.kill_job(jid))
            if self.theme: btn_stop.setStyleSheet(f"background-color: {self.theme.get('error', '#ff4444')}; color: white; border: none; padding: 2px 6px; border-radius: 4px;")
            
            job_layout.addWidget(lbl)
            job_layout.addWidget(btn_stop)
            
            wa = QtWidgets.QWidgetAction(menu)
            wa.setDefaultWidget(job_widget)
            menu.addAction(wa)
            
        menu.exec(self.mapToGlobal(self.rect().bottomLeft()))