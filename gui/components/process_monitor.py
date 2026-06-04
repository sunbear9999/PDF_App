# gui/components/process_monitor.py
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame, QScrollArea, QSizePolicy
from PySide6.QtCore import Qt, QPoint

class ProcessMonitorPopup(QFrame):
    def __init__(self, registry, theme, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.registry = registry
        self.theme = theme
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setMinimumWidth(340)
        self.setMaximumWidth(400)
        
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(8, 8, 8, 8)
        self.layout.setSpacing(6)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("border: none; background: transparent;")
        
        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(self.content)
        
        self.layout.addWidget(self.scroll)
        
        # Hooks
        self.registry.job_added.connect(self.rebuild_ui)
        self.registry.job_updated.connect(self.rebuild_ui)
        self.registry.job_removed.connect(self.rebuild_ui)
        self.registry.queue_updated.connect(self.rebuild_ui)
        self.apply_theme()

    def apply_theme(self):
        if not self.theme: return
        self.setStyleSheet(f"""
            QFrame#ProcessPopup {{
                background-color: {self.theme.get('bg_panel', '#333')};
                border: 1px solid {self.theme.get('border', '#444')};
                border-radius: 6px;
            }}
        """)
        self.setObjectName("ProcessPopup")

    def rebuild_ui(self):
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
                
        active = self.registry.active_job
        queue = self.registry.pending_queue
        
        if not active and not queue:
            lbl = QLabel("<i>No active processes.</i>")
            if self.theme: lbl.setStyleSheet(f"color: {self.theme.get('text_muted', '#aaa')};")
            self.content_layout.addWidget(lbl)
            return

        if active:
            lbl_active = QLabel("<b>Running:</b>")
            if self.theme: lbl_active.setStyleSheet(f"color: {self.theme.get('text_main', '#fff')};")
            self.content_layout.addWidget(lbl_active)
            self.content_layout.addWidget(self._create_job_widget(active, is_active=True))
            
        if queue:
            sep = QFrame()
            sep.setFrameShape(QFrame.Shape.HLine)
            if self.theme: sep.setStyleSheet(f"background-color: {self.theme.get('border', '#444')};")
            self.content_layout.addWidget(sep)
            
            lbl_q = QLabel("<b>Queued:</b>")
            if self.theme: lbl_q.setStyleSheet(f"color: {self.theme.get('text_main', '#fff')};")
            self.content_layout.addWidget(lbl_q)
            
            for idx, job in enumerate(queue):
                self.content_layout.addWidget(self._create_job_widget(job, is_active=False, idx=idx, total=len(queue)))

    def _create_job_widget(self, job, is_active=False, idx=0, total=0):
        w = QWidget()
        lyt = QHBoxLayout(w)
        lyt.setContentsMargins(4, 4, 4, 4)
        
        info_lyt = QVBoxLayout()
        name_lbl = QLabel(f"<b>{job.name}</b>" if is_active else job.name)
        status_lbl = QLabel(job.status)
        status_lbl.setWordWrap(True)
        if self.theme:
            name_lbl.setStyleSheet(f"color: {self.theme.get('text_main', '#fff')};")
            status_lbl.setStyleSheet(f"color: {self.theme.get('accent', '#b366ff') if is_active else self.theme.get('text_muted', '#aaa')}; font-size: 11px;")
        
        info_lyt.addWidget(name_lbl)
        info_lyt.addWidget(status_lbl)
        lyt.addLayout(info_lyt, 1)
        
        if not is_active:
            btn_up = QPushButton("▲")
            btn_up.setFixedSize(24, 24)
            btn_up.setEnabled(idx > 0)
            btn_up.clicked.connect(lambda _, j=job.id: self.registry.move_job_up(j))
            
            btn_down = QPushButton("▼")
            btn_down.setFixedSize(24, 24)
            btn_down.setEnabled(idx < total - 1)
            btn_down.clicked.connect(lambda _, j=job.id: self.registry.move_job_down(j))
            
            if self.theme:
                btn_style = f"background-color: transparent; color: {self.theme.get('text_main', '#fff')}; border: 1px solid {self.theme.get('border', '#444')}; border-radius: 4px;"
                btn_up.setStyleSheet(btn_style)
                btn_down.setStyleSheet(btn_style)
            
            lyt.addWidget(btn_up)
            lyt.addWidget(btn_down)
            
        btn_stop = QPushButton("✖")
        btn_stop.setFixedSize(24, 24)
        btn_stop.setToolTip("Cancel Action")
        if self.theme: btn_stop.setStyleSheet(f"background-color: {self.theme.get('error', '#ff4444')}; color: white; border: none; border-radius: 4px; font-weight:bold;")
        btn_stop.clicked.connect(lambda _, j=job.id: self.registry.cancel_job(j))
        lyt.addWidget(btn_stop)
        
        return w


class ProcessMonitorWidget(QPushButton):
    """The visible button on the main toolbar that dynamically scales and opens the popup."""
    def __init__(self, registry, theme=None):
        super().__init__()
        self.registry = registry
        self.theme = theme
        self.setObjectName("ProcessTrackerDropdown")
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        
        self.registry.job_added.connect(self._update_ui)
        self.registry.job_removed.connect(self._update_ui)
        self.registry.queue_updated.connect(self._update_ui)
        
        self.popup = ProcessMonitorPopup(registry, theme, self.window())
        self.clicked.connect(self._toggle_popup)
        self._update_ui()

    def set_theme(self, theme):
        self.theme = theme
        self.popup.theme = theme
        self.popup.apply_theme()
        self._update_ui()

    def _update_ui(self):
        active = 1 if self.registry.active_job else 0
        queued = len(self.registry.pending_queue)
        total = active + queued
        
        if total == 0:
            self.setText("🟢 Idle")
            self.setStyleSheet(f"""
                QPushButton#ProcessTrackerDropdown {{
                    background-color: transparent;
                    color: {self.theme.get('success', '#4CAF50') if self.theme else '#4CAF50'};
                    font-weight: bold;
                    border: 1px solid {self.theme.get('border', '#444') if self.theme else '#444'};
                    border-radius: 4px; padding: 4px 12px;
                }}
                QPushButton#ProcessTrackerDropdown:hover {{ background-color: rgba(76, 175, 80, 0.1); }}
            """)
        else:
            txt = f"🔴 {active} Running"
            if queued > 0: txt += f" ({queued} Queued)"
            self.setText(txt)
            self.setStyleSheet(f"""
                QPushButton#ProcessTrackerDropdown {{
                    background-color: {self.theme.get('error', '#ff4444') if self.theme else '#ff4444'};
                    color: white; font-weight: bold;
                    border: none; border-radius: 4px; padding: 4px 12px;
                }}
                QPushButton#ProcessTrackerDropdown:hover {{ background-color: #cc0000; }}
            """)

    def _toggle_popup(self):
        if self.popup.isVisible():
            self.popup.hide()
        else:
            self.popup.rebuild_ui()
            pos = self.mapToGlobal(QPoint(0, self.height() + 4))
            self.popup.move(pos)
            self.popup.show()