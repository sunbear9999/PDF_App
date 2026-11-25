# gui/components/process_monitor.py
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame, QScrollArea,
                               QSizePolicy, QProgressBar)
from PySide6.QtCore import Qt, QPoint, QTimer
from collections import Counter

from core.engine.process_manager import WAITING_FOR_MEMORY
from core.events.domains.memory_events import MemoryEvent, MemoryIntent
from gui.components.dialogs.memory_tab import open_memory_panel, request_memory

PRESSURE_COLORS = {"normal": "success", "elevated": "warning", "critical": "error"}
# Hundreds of queued jobs (a bulk import) must not mean hundreds of widgets rebuilt per change.
MAX_QUEUED_SHOWN = 30
REBUILD_DELAY_MS = 200

class ProcessMonitorPopup(QFrame):
    def __init__(self, registry, theme, app_context=None, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.registry = registry
        self.theme = theme
        self._ctx = app_context
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
        
        self.layout.addWidget(self._memory_header())
        self.layout.addWidget(self.scroll)
        
        # Hooks: changes arrive in bursts; rebuild once they pause, and only while visible.
        self._rebuild_timer = QTimer(self)
        self._rebuild_timer.setSingleShot(True)
        self._rebuild_timer.setInterval(REBUILD_DELAY_MS)
        self._rebuild_timer.timeout.connect(self._rebuild_if_visible)
        for signal in (self.registry.job_added, self.registry.job_updated,
                       self.registry.job_removed, self.registry.queue_updated):
            signal.connect(self.schedule_rebuild)
        self.apply_theme()

    def schedule_rebuild(self, *_):
        self._rebuild_timer.start()

    def _rebuild_if_visible(self):
        if self.isVisible():
            self.rebuild_ui()

    def _memory_header(self):
        """Memory at a glance, linked to the full panel (Settings → Memory)."""
        header = QWidget()
        lay = QVBoxLayout(header)
        lay.setContentsMargins(0, 0, 0, 4)
        row = QHBoxLayout()
        self.memory_label = QLabel("Memory: measuring…")
        self.memory_button = QPushButton("Open memory panel")
        self.memory_button.clicked.connect(self._open_memory_panel)
        row.addWidget(self.memory_label, 1)
        row.addWidget(self.memory_button)
        self.memory_bar = QProgressBar()
        self.memory_bar.setRange(0, 100)
        self.memory_bar.setTextVisible(False)
        self.memory_bar.setFixedHeight(8)
        lay.addLayout(row)
        lay.addWidget(self.memory_bar)
        return header

    def set_memory(self, snapshot):
        if not snapshot or not snapshot.total:
            return
        gb = 1024 ** 3
        top = max((c for c in snapshot.consumers if c.bytes), key=lambda c: c.bytes, default=None)
        self.memory_label.setText(
            f"Memory {snapshot.used / gb:.1f} / {snapshot.total / gb:.1f} GB · {snapshot.pressure}"
            + (f" · largest: {top.label}" if top else ""))
        self.memory_bar.setValue(int(snapshot.used_fraction * 100))
        if self.theme:
            color = self.theme.get(PRESSURE_COLORS.get(snapshot.pressure, "success"), "#4CAF50")
            self.memory_bar.setStyleSheet(
                f"QProgressBar {{ background: {self.theme.get('bg_input', '#333')}; border: none; border-radius: 4px; }}"
                f"QProgressBar::chunk {{ background: {color}; border-radius: 4px; }}")

    def _open_memory_panel(self):
        self.hide()
        open_memory_panel(self._ctx, self.parent())

    def showEvent(self, event):
        request_memory(self._ctx, MemoryIntent.SET_SAMPLING, fast=True)
        super().showEvent(event)

    def hideEvent(self, event):
        request_memory(self._ctx, MemoryIntent.SET_SAMPLING, fast=False)
        super().hideEvent(event)

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
        express = getattr(self.registry, "express_jobs", [])
        queue = self.registry.pending_queue + list(getattr(self.registry, "express_waiting", []))
        recent = getattr(self.registry, "recent_jobs", [])
        
        if not active and not express and not queue and not recent:
            lbl = QLabel("<i>No active processes.</i>")
            if self.theme: lbl.setStyleSheet(f"color: {self.theme.get('text_muted', '#aaa')};")
            self.content_layout.addWidget(lbl)
            return

        if active:
            lbl_active = QLabel("<b>Running:</b>")
            if self.theme: lbl_active.setStyleSheet(f"color: {self.theme.get('text_main', '#fff')};")
            self.content_layout.addWidget(lbl_active)
            self.content_layout.addWidget(self._create_job_widget(active, is_active=True))

        if express:
            lbl_express = QLabel("<b>Express:</b>")
            if self.theme: lbl_express.setStyleSheet(f"color: {self.theme.get('text_main', '#fff')};")
            self.content_layout.addWidget(lbl_express)
            for job in express:
                self.content_layout.addWidget(self._create_job_widget(job, is_active=True))
            
        if queue:
            sep = QFrame()
            sep.setFrameShape(QFrame.Shape.HLine)
            if self.theme: sep.setStyleSheet(f"background-color: {self.theme.get('border', '#444')};")
            self.content_layout.addWidget(sep)
            
            lbl_q = QLabel("<b>Queued:</b>")
            if self.theme: lbl_q.setStyleSheet(f"color: {self.theme.get('text_main', '#fff')};")
            self.content_layout.addWidget(lbl_q)
            
            for idx, job in enumerate(queue[:MAX_QUEUED_SHOWN]):
                self.content_layout.addWidget(self._create_job_widget(job, is_active=False, idx=idx, total=len(queue)))
            if len(queue) > MAX_QUEUED_SHOWN:
                self.content_layout.addWidget(self._more_label(queue[MAX_QUEUED_SHOWN:]))

        if recent:
            sep = QFrame()
            sep.setFrameShape(QFrame.Shape.HLine)
            if self.theme: sep.setStyleSheet(f"background-color: {self.theme.get('border', '#444')};")
            self.content_layout.addWidget(sep)

            lbl_recent = QLabel("<b>Recent Traces:</b>")
            if self.theme: lbl_recent.setStyleSheet(f"color: {self.theme.get('text_main', '#fff')};")
            self.content_layout.addWidget(lbl_recent)
            for job in recent:
                self.content_layout.addWidget(self._create_job_widget(job, is_active=False, is_recent=True))

    def _more_label(self, jobs):
        kinds = Counter(str(job.name).split(":")[0].split(" — ")[0].strip() for job in jobs)
        detail = ", ".join(f"{count} {name}" for name, count in kinds.most_common(4))
        label = QLabel(f"… and {len(jobs):,} more queued ({detail}). They run in order; cancel a source's work "
                       "from Intelligence → Processing.")
        label.setWordWrap(True)
        if self.theme:
            label.setStyleSheet(f"color: {self.theme.get('text_muted', '#aaa')};")
        return label

    def _create_job_widget(self, job, is_active=False, idx=0, total=0, is_recent=False):
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
        
        if not is_active and not is_recent:
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

        trace_id = getattr(job, "trace_id", None) or getattr(getattr(job, "runner", None), "trace_id", None)
        if trace_id:
            from gui.components.prompt_trace_button import PromptTraceButton
            trace_record = getattr(getattr(job, "runner", None), "prompt_trace", None)
            btn_trace = PromptTraceButton(trace_id=trace_id, trace_record=trace_record, app_context=self._ctx, theme=self.theme, parent=w)
            lyt.addWidget(btn_trace)
            
        if getattr(job, "status", "") == WAITING_FOR_MEMORY:
            btn_run = QPushButton("Run anyway")
            btn_run.setToolTip("Start now even though memory is short")
            btn_run.clicked.connect(lambda _, j=job.id: self.registry.run_anyway(j))
            lyt.addWidget(btn_run)

        if not is_recent:
            btn_stop = QPushButton("✖")
            btn_stop.setFixedSize(24, 24)
            btn_stop.setToolTip("Cancel Action")
            if self.theme: btn_stop.setStyleSheet(f"background-color: {self.theme.get('error', '#ff4444')}; color: white; border: none; border-radius: 4px; font-weight:bold;")
            btn_stop.clicked.connect(lambda _, j=job.id: self.registry.cancel_job(j))
            lyt.addWidget(btn_stop)
        
        return w


class ProcessMonitorWidget(QPushButton):
    """The visible button on the main toolbar that dynamically scales and opens the popup."""
    def __init__(self, registry, theme=None, app_context=None, parent=None):
        super().__init__()
        self.registry = registry
        self.theme = theme
        self.setObjectName("ProcessTrackerDropdown")
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)

        self._update_timer = QTimer(self)
        self._update_timer.setSingleShot(True)
        self._update_timer.setInterval(REBUILD_DELAY_MS)
        self._update_timer.timeout.connect(self._update_ui)
        for signal in (self.registry.job_added, self.registry.job_removed, self.registry.queue_updated):
            signal.connect(self._update_timer.start)
        self._snapshot = None
        self._styled_busy = None
        bus = getattr(app_context, "bus", None)
        if bus is not None:
            bus.memory_state_changed.connect(self._on_memory, Qt.ConnectionType.QueuedConnection)

        self.popup = ProcessMonitorPopup(registry, theme, app_context=app_context, parent=parent or self.window())
        self.clicked.connect(self._toggle_popup)
        self._update_ui()

    def set_theme(self, theme):
        self.theme = theme
        self.popup.theme = theme
        self.popup.apply_theme()
        self._styled_busy = None
        self._update_ui()

    def _on_memory(self, event, payload):
        if event in (MemoryEvent.SNAPSHOT, MemoryEvent.PRESSURE_CHANGED) and payload.snapshot:
            self._snapshot = payload.snapshot
            self.popup.set_memory(payload.snapshot)
            self._update_ui()

    def _memory_suffix(self):
        snapshot = self._snapshot
        if not snapshot or not snapshot.total:
            return ""
        marker = {"elevated": " ⚠", "critical": " ⛔"}.get(snapshot.pressure, "")
        return f" · {int(snapshot.used_fraction * 100)}% RAM{marker}"

    def _update_ui(self):
        active = 1 if self.registry.active_job else 0
        express = len(getattr(self.registry, "express_jobs", []))
        queued = len(self.registry.pending_queue) + len(getattr(self.registry, "express_waiting", []))
        total = active + express + queued
        self.setToolTip("Background processes and memory — click for details")
        
        busy = total > 0
        restyle = busy != self._styled_busy       # restyling re-polishes the widget: only on a change
        self._styled_busy = busy
        if total == 0:
            self.setText("🟢 Idle" + self._memory_suffix())
            if restyle: self.setStyleSheet(f"""
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
            running = active + express
            txt = f"🔴 {running} Running"
            if queued > 0: txt += f" ({queued} Queued)"
            self.setText(txt + self._memory_suffix())
            if restyle: self.setStyleSheet(f"""
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
