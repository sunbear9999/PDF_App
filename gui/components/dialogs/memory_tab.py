"""Settings → Memory: see what uses memory, unload it, and tune automatic management.

Everything shown comes from MemoryManagerService through the bus; buttons send
MemoryIntent requests that run on the manager's own thread, so nothing here
blocks the UI. While visible the tab asks for 2-second sampling.
"""
from __future__ import annotations

import os
import time

from PySide6.QtCore import QStringListModel, Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout,
    QHeaderView, QLabel, QListView, QPushButton, QScrollArea, QSlider, QSpinBox, QTableView,
    QVBoxLayout, QWidget,
)

from core.events.domains.help_events import HelpIntent, HelpPayload
from core.events.domains.memory_events import MemoryEvent, MemoryIntent, MemoryPayload
from core.models.memory_models import ConsumerKind, ManagerMode, MemorySnapshot, PreloadPolicy
from gui.registry.action_spec import ActionSpec
from gui.components.artifact_tasks import ArtifactTaskRunner
from gui.components.memory_consumer_model import MemoryConsumerModel, format_bytes
from gui.components.memory_donut_widget import MemoryDonutWidget, memory_slices
from gui.components.settings_styles import (
    btn_default, btn_primary, card_style, divider, label_style, section_header, theme_value,
)

HELP_TOPIC = "core.memory.manager"
PALETTE_MOUNT = "command_palette"
MAX_ACTIONS_SHOWN = 50
RESERVE_RANGE = (5, 40)
IDLE_RANGE = (1, 240)
WORKER_CHOICES = (("Automatic", 0), ("1", 1), ("2", 2), ("3", 3), ("4", 4))
MODE_CHOICES = (("Balanced (recommended)", ManagerMode.BALANCED.value),
                ("Aggressive — keep more free", ManagerMode.AGGRESSIVE.value),
                ("Off — monitor only", ManagerMode.OFF.value))
PRELOAD_CHOICES = (("When memory allows", PreloadPolicy.WHEN_MEMORY_ALLOWS.value),
                   ("Always", PreloadPolicy.ALWAYS.value), ("Never", PreloadPolicy.NEVER.value))
PRESSURE_TEXT = {"normal": ("● Normal", "success"), "elevated": ("● Elevated", "warning"),
                 "critical": ("● Critical", "error")}
ENV_OVERRIDES = ("PAPYRUS_OLLAMA_KEEP_ALIVE", "PAPYRUS_OLLAMA_EMBED_KEEP_ALIVE", "PAPYRUS_PRELOAD_AI")


def request_memory(app_context, intent, **fields) -> None:
    bus = getattr(app_context, "bus", None)
    if bus is not None:
        bus.memory_action_requested.emit(intent, MemoryPayload(**fields))


def register_memory_actions(app_context, window) -> None:
    """App-wide shortcuts (KeySpecs in build_builtin_key_specs) and palette entries."""
    actions = {
        "memory.open_panel": ("Memory Panel", lambda: open_memory_panel(app_context, window)),
        "memory.unload_models": ("Unload All Models",
                                 lambda: request_memory(app_context, MemoryIntent.UNLOAD_ALL_MODELS)),
        "memory.free_caches": ("Free Memory Caches",
                               lambda: request_memory(app_context, MemoryIntent.TRIM_CACHES)),
    }
    keys = getattr(app_context, "keybinding_registry", None)
    registry = getattr(app_context, "action_registry", None)
    for identity, (label, callback) in actions.items():
        if keys is not None:
            keys.bind(identity, callback, window)
        if registry is not None:
            registry.register(ActionSpec(action_id=identity, label=label, callback=callback,
                                         mounts=[PALETTE_MOUNT]))


def open_memory_panel(app_context, parent=None):
    """Open Settings on the Memory tab (process monitor, shortcuts, palette)."""
    from gui.components.dialogs.settings_dialog import GlobalSettingsDialog
    manager = getattr(app_context, "dialog_manager", None)
    if manager is None:
        return None
    dialog = manager.show(GlobalSettingsDialog, key="global_settings", singleton=True,
                          factory=lambda: GlobalSettingsDialog(app_context, parent=parent))
    if hasattr(dialog, "focus_memory"):
        dialog.focus_memory()
    return dialog


class MemoryTab(QWidget):
    def __init__(self, app_context, theme: dict, parent=None) -> None:
        super().__init__(parent)
        self.ctx, self.t = app_context, dict(theme or {})
        self.service = getattr(app_context, "memory_manager_service", None)
        self.bus = getattr(app_context, "bus", None)
        self._fast = False
        self.runner = ArtifactTaskRunner(app_context, self, service_name="memory_manager_service")
        self.runner.failed.connect(lambda error: self.status.setText(f"Could not save: {error}"))
        self._build()
        self._register_help()
        if self.bus is not None:
            self.bus.memory_state_changed.connect(self._on_state, Qt.ConnectionType.QueuedConnection)
        self.destroyed.connect(lambda *_: self._disconnect())
        self._load_settings()
        if self.service is not None:
            self._apply_snapshot(self.service.latest_snapshot())
            for action in reversed(self.service.recent_actions()):
                self._add_action(action)

    # ----------------------------------------------------------------- layout

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        body.setStyleSheet(f"background: {theme_value(self.t, 'bg_main')};")
        self.layout_ = QVBoxLayout(body)
        self.layout_.setContentsMargins(16, 12, 16, 16)
        self.layout_.setSpacing(8)
        for section in (self._overview(), self._components(), self._automatic(), self._activity()):
            self.layout_.addWidget(section)
            self.layout_.addWidget(divider(self.t))
        self.layout_.addStretch()
        scroll.setWidget(body)
        outer.addWidget(scroll)

    def _card(self) -> QFrame:
        card = QFrame()
        card.setStyleSheet(f"QFrame {{ {card_style(self.t)} }} QLabel {{ border: none; }}")
        return card

    def _button(self, text: str, slot, primary: bool = False) -> QPushButton:
        button = QPushButton(text)
        button.setStyleSheet(btn_primary(self.t) if primary else btn_default(self.t))
        button.clicked.connect(slot)
        return button

    def _overview(self) -> QWidget:
        wrapper = QWidget()
        lay = QVBoxLayout(wrapper)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(section_header("System memory", self.t))
        card = self._card()
        row = QHBoxLayout(card)
        self.donut = MemoryDonutWidget(card)
        self.donut.set_theme(self.t)
        row.addWidget(self.donut)
        side = QVBoxLayout()
        self.summary = QLabel("Measuring…")
        self.summary.setStyleSheet(label_style(self.t, size=14))
        self.pressure = QLabel("")
        self.legend = QGridLayout()
        self.legend.setHorizontalSpacing(10)
        self.legend.setColumnStretch(3, 1)
        self._legend_rows = []
        side.addWidget(self.summary)
        side.addWidget(self.pressure)
        side.addLayout(self.legend)
        side.addStretch()
        row.addLayout(side, 1)
        lay.addWidget(card)
        buttons = QHBoxLayout()
        buttons.addWidget(self._button("Unload all models", self.unload_models, primary=True))
        buttons.addWidget(self._button("Free caches now", self.free_caches))
        buttons.addWidget(self._button("Refresh", self.refresh))
        buttons.addStretch()
        lay.addLayout(buttons)
        return wrapper

    def _components(self) -> QWidget:
        wrapper = QWidget()
        lay = QVBoxLayout(wrapper)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(section_header("Loaded components", self.t))
        self.model = MemoryConsumerModel(self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setMinimumHeight(170)
        self.table.selectionModel().selectionChanged.connect(lambda *_: self._selection_changed())
        lay.addWidget(self.table)
        row = QHBoxLayout()
        self.unload_button = self._button("Unload / trim selected", self.unload_selected)
        self.load_button = self._button("Load selected", self.load_selected)
        row.addWidget(self.unload_button)
        row.addWidget(self.load_button)
        row.addStretch()
        lay.addLayout(row)
        self._selection_changed()
        return wrapper

    def _automatic(self) -> QWidget:
        wrapper = QWidget()
        lay = QVBoxLayout(wrapper)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(section_header("Automatic management", self.t))
        form = QFormLayout()
        self.mode = self._combo(MODE_CHOICES)
        self.reserve = QSlider(Qt.Orientation.Horizontal)
        self.reserve.setRange(*RESERVE_RANGE)
        self.reserve_label = QLabel()
        self.reserve.valueChanged.connect(lambda value: self.reserve_label.setText(f"{value}% kept free"))
        reserve_row = QHBoxLayout()
        reserve_row.addWidget(self.reserve, 1)
        reserve_row.addWidget(self.reserve_label)
        self.idle_chat, self.idle_embed = self._spin(), self._spin()
        self.preload = self._combo(PRELOAD_CHOICES)
        self.workers = self._combo(WORKER_CHOICES)
        self.pause = QCheckBox("Hold new background jobs while memory is critical")
        self.exit_unload = QCheckBox("Unload models Papyrus loaded when it closes")
        for label, field in (("Mode", self.mode), ("Memory reserve", reserve_row),
                             ("Unload chat model after (min idle)", self.idle_chat),
                             ("Unload embedding model after (min idle)", self.idle_embed),
                             ("Preload chat model at startup", self.preload),
                             ("Background workers", self.workers), ("", self.pause), ("", self.exit_unload)):
            form.addRow(label, field)
        lay.addLayout(form)
        self.env_note = QLabel(self._env_text())
        self.env_note.setWordWrap(True)
        self.env_note.setStyleSheet(label_style(self.t, muted=True, size=11))
        self.env_note.setVisible(bool(self._env_text()))
        lay.addWidget(self.env_note)
        row = QHBoxLayout()
        row.addWidget(self._button("Apply", self.apply_changes, primary=True))
        row.addWidget(self._button("Help", self.show_help))
        self.status = QLabel("")
        self.status.setStyleSheet(label_style(self.t, muted=True, size=11))
        row.addWidget(self.status, 1)
        lay.addLayout(row)
        return wrapper

    def _activity(self) -> QWidget:
        wrapper = QWidget()
        lay = QVBoxLayout(wrapper)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(section_header("Recent actions", self.t))
        self.actions = QStringListModel(self)
        view = QListView()
        view.setModel(self.actions)
        view.setMinimumHeight(110)
        view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        lay.addWidget(view)
        return wrapper

    @staticmethod
    def _combo(choices) -> QComboBox:
        combo = QComboBox()
        for text, value in choices:
            combo.addItem(text, value)
        return combo

    @staticmethod
    def _spin() -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(*IDLE_RANGE)
        return spin

    # ------------------------------------------------------------- live data

    def _on_state(self, event, payload) -> None:
        if event in (MemoryEvent.SNAPSHOT, MemoryEvent.PRESSURE_CHANGED) and payload.snapshot:
            self._apply_snapshot(payload.snapshot)
        elif event == MemoryEvent.ACTION_TAKEN and payload.action:
            self._add_action(payload.action)
        elif event == MemoryEvent.SETTINGS_CHANGED:
            self._load_settings()
            self.status.setText("Saved.")

    def _apply_snapshot(self, snapshot: MemorySnapshot) -> None:
        if not snapshot or not snapshot.total:
            return
        slices = memory_slices(snapshot, self.t)
        percent = int(round(snapshot.used_fraction * 100))
        self.donut.set_data(slices, f"{snapshot.used / 1024 ** 3:.1f} / {snapshot.total / 1024 ** 3:.1f} GB\n{percent}%")
        self.summary.setText(f"{percent}% of {format_bytes(snapshot.total)} in use · "
                             f"{format_bytes(snapshot.available)} free · swap {format_bytes(snapshot.swap_used)}")
        text, key = PRESSURE_TEXT.get(snapshot.pressure, PRESSURE_TEXT["normal"])
        self.pressure.setText(f"Memory pressure: {text}")
        self.pressure.setStyleSheet(f"color: {theme_value(self.t, key)}; font-weight: 700;")
        self._fill_legend(slices, snapshot.total)
        self.model.set_rows([item for item in snapshot.consumers if item.bytes or item.can_load])
        self._selection_changed()

    def _fill_legend(self, slices, total: int) -> None:
        """Rows are reused across snapshots (every 2 s while visible)."""
        while len(self._legend_rows) < len(slices):
            row = (QLabel("■"), QLabel(), QLabel())
            row[1].setStyleSheet(label_style(self.t, size=12))
            row[2].setStyleSheet(label_style(self.t, muted=True, size=12))
            for column, widget in enumerate(row):
                self.legend.addWidget(widget, len(self._legend_rows), column)
            self._legend_rows.append(row)
        for index, (swatch, name, amount) in enumerate(self._legend_rows):
            visible = index < len(slices)
            for widget in (swatch, name, amount):
                widget.setVisible(visible)
            if visible:
                label, size, color = slices[index]
                swatch.setStyleSheet(f"color: {color.name()}; font-size: 14px;")
                name.setText(label)
                amount.setText(f"{format_bytes(size)}  ·  {size * 100 / total:.0f}%")

    def _add_action(self, action) -> None:
        stamp = time.strftime("%H:%M", time.localtime(action.at))
        freed = f" ({format_bytes(action.freed)})" if action.freed else ""
        who = "" if action.automatic else " [you]"
        rows = [f"{stamp}  {action.action} {action.label}{freed} — {action.reason}{who}"] + self.actions.stringList()
        self.actions.setStringList(rows[:MAX_ACTIONS_SHOWN])

    def _selection_changed(self) -> None:
        row = self._selected()
        releasable = bool(row and row.bytes and row.kind in (ConsumerKind.MODEL.value, ConsumerKind.CACHE.value))
        self.unload_button.setEnabled(releasable)
        self.unload_button.setText("Trim selected" if row and row.kind == ConsumerKind.CACHE.value else "Unload selected")
        self.load_button.setVisible(bool(row and row.can_load))

    def _selected(self):
        indexes = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        return self.model.row_at(indexes[0].row()) if indexes else None

    # --------------------------------------------------------------- actions

    def _request(self, intent, **fields) -> None:
        request_memory(self.ctx, intent, **fields)

    def unload_models(self) -> None:
        self._request(MemoryIntent.UNLOAD_ALL_MODELS)

    def free_caches(self) -> None:
        self._request(MemoryIntent.TRIM_CACHES)

    def refresh(self) -> None:
        self._request(MemoryIntent.REFRESH)

    def unload_selected(self) -> None:
        row = self._selected()
        if row is not None:
            level = "trim" if row.kind == ConsumerKind.CACHE.value else "unload"
            self._request(MemoryIntent.UNLOAD, consumer_id=row.id, level=level)

    def load_selected(self) -> None:
        row = self._selected()
        if row is not None and row.can_load:
            self._request(MemoryIntent.LOAD, consumer_id=row.id)

    def show_help(self) -> None:
        if self.bus is not None:
            self.bus.help_action_requested.emit(HelpIntent.SHOW_TOPIC, HelpPayload(topic_id=HELP_TOPIC))

    # -------------------------------------------------------------- settings

    def _load_settings(self) -> None:
        if self.service is None:
            return
        values = self.service.get_settings()
        self._select(self.mode, values["mode"])
        self.reserve.setValue(int(values["reserve_percent"]))
        self.reserve_label.setText(f"{values['reserve_percent']}% kept free")
        self.idle_chat.setValue(int(values["idle_minutes_chat"]))
        self.idle_embed.setValue(int(values["idle_minutes_embed"]))
        self._select(self.preload, values["preload_policy"])
        self._select(self.workers, int(values["max_workers"]))
        self.pause.setChecked(bool(values["pause_background_on_critical"]))
        self.exit_unload.setChecked(bool(values["unload_models_on_exit"]))

    @staticmethod
    def _select(combo: QComboBox, value) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(max(0, index))

    def form_values(self) -> dict:
        return {
            "mode": self.mode.currentData(), "reserve_percent": self.reserve.value(),
            "idle_minutes_chat": self.idle_chat.value(), "idle_minutes_embed": self.idle_embed.value(),
            "preload_policy": self.preload.currentData(), "max_workers": self.workers.currentData(),
            "pause_background_on_critical": self.pause.isChecked(),
            "unload_models_on_exit": self.exit_unload.isChecked(),
        }

    def apply_changes(self) -> None:
        """Settings dialog Apply/OK and the tab's own Apply button (file I/O off-thread)."""
        self.status.setText("Saving…")
        self.runner.start("save_settings", values=self.form_values())

    @staticmethod
    def _env_text() -> str:
        names = [name for name in ENV_OVERRIDES if os.environ.get(name, "").strip()]
        return ("Set by environment, overriding these settings: " + ", ".join(names)) if names else ""

    # ------------------------------------------------------------- lifecycle

    def _register_help(self) -> None:
        registry = getattr(getattr(self.ctx, "help_service", None), "ui_target_registry", None)
        if registry:
            identity = f"settings.memory.{id(self)}"
            registry.register_widget(identity, self, HELP_TOPIC)
            self.destroyed.connect(lambda *_, r=registry, key=identity: r.unregister(key))

    def _set_fast(self, fast: bool) -> None:
        if fast != self._fast:
            self._fast = fast
            self._request(MemoryIntent.SET_SAMPLING, fast=fast)

    def showEvent(self, event) -> None:
        self._set_fast(True)
        super().showEvent(event)

    def hideEvent(self, event) -> None:
        self._set_fast(False)
        super().hideEvent(event)

    def _disconnect(self) -> None:
        # Qt drops bound-method connections of a destroyed receiver itself;
        # touching the dead object to disconnect raises, so only end fast sampling.
        if self._fast and self.bus is not None:
            self._fast = False
            self.bus.memory_action_requested.emit(MemoryIntent.SET_SAMPLING, MemoryPayload(fast=False))
