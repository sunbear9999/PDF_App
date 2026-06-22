# gui/components/workflow_panel.py
"""
WorkflowPanel — a compact, self-contained widget that lets any dock surface
blueprints registered at a given mount point.

Usage inside a dock::

    panel = WorkflowPanel(
        app_context=self.app_context,
        mount_point="notes_dock",
        output_target_id="notes_dock_workflow",
        parent=self,
    )
    layout.addWidget(panel)

The panel:
- Reads ``blueprint_registry.iter_mount(mount_point)`` at construction time
- Repopulates if plugins are loaded / unloaded at runtime
- Emits ``bus.workflow_action_requested(WorkflowIntent.RUN_BLUEPRINT, payload)``
  with the selected blueprint and ``target_id`` pointing back to itself
- Renders incoming AI output in a collapsible inline scroll area via
  ``receive_ai_payload(payload)``  (registered with UIRouter on creation)
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from gui.app_context import AppContext


class WorkflowPanel(QWidget):
    """Compact blueprint launcher + output viewer for any dock."""

    def __init__(
        self,
        app_context: "AppContext",
        mount_point: str,
        output_target_id: str,
        label: str = "Run Workflow",
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.app_context = app_context
        self.mount_point = mount_point
        self.output_target_id = output_target_id
        self._label = label
        self._theme: dict = {}

        self._build_ui()
        self._populate_blueprints()
        self._connect_signals()

        # Register with UIRouter so step output lands here
        try:
            self.app_context.ui_router.register_target(output_target_id, self)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(2)

        # -- Toolbar row -------------------------------------------------
        toolbar = QFrame()
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(4, 4, 4, 4)
        toolbar_layout.setSpacing(6)

        self._label_widget = QLabel(self._label)
        self._label_widget.setStyleSheet("font-size: 11px; font-weight: 600;")
        toolbar_layout.addWidget(self._label_widget)

        self._combo = QComboBox()
        self._combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        toolbar_layout.addWidget(self._combo)

        self._run_btn = QPushButton("Run")
        self._run_btn.setFixedWidth(48)
        self._run_btn.clicked.connect(self._on_run)
        toolbar_layout.addWidget(self._run_btn)

        self._toggle_btn = QPushButton("▸")
        self._toggle_btn.setFixedWidth(24)
        self._toggle_btn.setToolTip("Show / hide output")
        self._toggle_btn.clicked.connect(self._toggle_output)
        toolbar_layout.addWidget(self._toggle_btn)

        root.addWidget(toolbar)

        # -- Output area -------------------------------------------------
        self._output_frame = QFrame()
        self._output_frame.setVisible(False)
        output_layout = QVBoxLayout(self._output_frame)
        output_layout.setContentsMargins(4, 0, 4, 4)
        output_layout.setSpacing(0)

        self._output = QTextEdit()
        self._output.setReadOnly(True)
        self._output.setFixedHeight(160)
        self._output.setPlaceholderText("Workflow output will appear here…")
        output_layout.addWidget(self._output)

        root.addWidget(self._output_frame)

    # ------------------------------------------------------------------
    # Blueprint population
    # ------------------------------------------------------------------

    def _populate_blueprints(self) -> None:
        self._combo.clear()
        try:
            registry = self.app_context.blueprint_registry
            for defn in sorted(registry.iter_mount(self.mount_point), key=lambda d: d.label):
                self._combo.addItem(defn.label, userData=defn.id)
        except Exception:
            pass

    def _connect_signals(self) -> None:
        try:
            bus = self.app_context.bus
            if hasattr(bus, "plugin_loaded"):
                bus.plugin_loaded.connect(lambda *_: self._populate_blueprints())
            if hasattr(bus, "plugin_unloaded"):
                bus.plugin_unloaded.connect(lambda *_: self._populate_blueprints())
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Interaction
    # ------------------------------------------------------------------

    def _on_run(self) -> None:
        blueprint_id: str = self._combo.currentData()
        if not blueprint_id:
            return

        try:
            from core.events.domains.workflow_events import WorkflowIntent, WorkflowPayload
            payload = WorkflowPayload(
                blueprint_id=blueprint_id,
                target_id=self.output_target_id,
                initial_state={},
            )
            self.app_context.bus.workflow_action_requested.emit(
                WorkflowIntent.RUN_BLUEPRINT, payload
            )
            self._show_output()
            self._output.setPlaceholderText("Running…")
        except Exception as exc:
            self._show_output()
            self._output.setPlainText(f"[Error launching workflow: {exc}]")

    def _toggle_output(self) -> None:
        visible = self._output_frame.isVisible()
        self._output_frame.setVisible(not visible)
        self._toggle_btn.setText("▾" if not visible else "▸")

    def _show_output(self) -> None:
        self._output_frame.setVisible(True)
        self._toggle_btn.setText("▾")

    # ------------------------------------------------------------------
    # UIRouter contract — called by UIRouter when output arrives
    # ------------------------------------------------------------------

    def receive_ai_payload(self, payload: dict) -> None:
        """Called by UIRouter when workflow output targets this panel."""
        if not payload:
            return

        payload_type = payload.get("type", "")
        content = payload.get("content", "")
        delta = payload.get("delta", "")

        self._show_output()

        if payload_type == "stream_start":
            self._output.clear()
        elif payload_type == "stream_delta" and delta:
            cursor = self._output.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            cursor.insertText(delta)
            self._output.setTextCursor(cursor)
        elif payload_type == "stream_end":
            pass
        elif content:
            self._output.setPlainText(str(content))

    # ------------------------------------------------------------------
    # Theming
    # ------------------------------------------------------------------

    def apply_theme(self, theme: dict) -> None:
        self._theme = theme
        bg = theme.get("bg_panel", "#2b2b2b")
        bg_input = theme.get("bg_input", "#333333")
        text = theme.get("text_main", "#ffffff")
        text_muted = theme.get("text_muted", "#aaaaaa")
        border = theme.get("border", "#555555")
        accent = theme.get("accent", "#0078D7")

        self.setStyleSheet(
            f"background-color: {bg}; color: {text}; border-radius: 6px;"
        )
        self._combo.setStyleSheet(
            f"background-color: {bg_input}; color: {text}; "
            f"border: 1px solid {border}; border-radius: 4px; padding: 2px 6px;"
        )
        self._run_btn.setStyleSheet(
            f"background-color: {accent}; color: #ffffff; border: none; "
            f"border-radius: 4px; padding: 4px 8px; font-weight: 600;"
        )
        self._toggle_btn.setStyleSheet(
            f"background: transparent; color: {text_muted}; border: none; font-weight: bold;"
        )
        self._label_widget.setStyleSheet(
            f"color: {text_muted}; font-size: 11px; font-weight: 600;"
        )
        self._output.setStyleSheet(
            f"background-color: {bg_input}; color: {text}; "
            f"border: 1px solid {border}; border-radius: 4px; font-size: 12px;"
        )
