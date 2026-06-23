# gui/docks/unified_research/components/tab_context.py
"""
TabContextContributor — interface for objects that inject extra state into a tab's
pipeline call and optionally surface UI controls in the tab's input bar.

Built-in contributor:  ChatHistoryContextWidget (checkbox + depth spinner).
Plugin-contributed:    Register via PluginExtensionRegistry.add_tab_context_contributor().
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QSpinBox, QWidget,
)
from PySide6.QtCore import Qt

from core.utils.json_utils import strip_tagged_block

if TYPE_CHECKING:
    from core.project_manager import ProjectManager


class TabContextContributor:
    """
    Interface for objects that inject state variables into a research tab's
    pipeline initial_state.  Optionally exposes a QWidget for display in the
    tab's input / options bar.

    Not using ABC because QWidget subclasses have a Qt metaclass that conflicts
    with ABCMeta.  Subclasses must implement get_state().
    """

    def get_state(self) -> dict:
        """Return a dict of key→value pairs to merge into initial_state."""
        raise NotImplementedError

    def get_widget(self) -> QWidget | None:
        """Return a widget to embed in the tab's options bar, or None."""
        return None

    def target_ids(self) -> list[str]:
        """
        Tab target IDs this contributor applies to.
        Empty list means the contributor declared itself — the tab decides.
        """
        return []


class ChatHistoryContextWidget(QWidget, TabContextContributor):
    """
    Checkbox + depth spinner that prepends N recent conversation turns to every
    pipeline call as the ``chat_history`` state variable.

    When unchecked: ``chat_history`` = ``""``  (blueprint sees nothing)
    When checked:   ``chat_history`` = a formatted block the LLM reads before
                    the document context and user query.
    """

    def __init__(
        self,
        project_manager: "ProjectManager",
        target_id: str,
        theme: dict | None = None,
        parent: QWidget | None = None,
    ):
        QWidget.__init__(self, parent)
        TabContextContributor.__init__(self)
        self._pm = project_manager
        self._target_id = target_id

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._checkbox = QCheckBox("History")
        self._checkbox.setToolTip("Include recent conversation turns as context for the LLM")
        self._checkbox.setChecked(False)
        layout.addWidget(self._checkbox)

        self._spin = QSpinBox()
        self._spin.setRange(1, 20)
        self._spin.setValue(5)
        self._spin.setFixedWidth(42)
        self._spin.setToolTip("Number of recent messages to include")
        self._spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._spin)

        lbl = QLabel("msgs")
        layout.addWidget(lbl)

        self._checkbox.toggled.connect(self._spin.setEnabled)
        self._spin.setEnabled(False)

        if theme:
            self.apply_theme(theme)

    # ------------------------------------------------------------------
    # TabContextContributor implementation
    # ------------------------------------------------------------------

    def get_state(self) -> dict:
        if not self._checkbox.isChecked() or not self._pm:
            return {"chat_history": ""}

        depth = self._spin.value()
        history_data = self._pm.get_chat_history(self._target_id)
        lines: list[str] = []
        for msg in history_data[-depth:]:
            role = "User" if msg["role"] == "user" else "AI"
            if msg.get("ui_format") in ("live_stream", "text"):
                content = strip_tagged_block(msg.get("content") or "", "UPDATE_MANIFEST").strip()
                if content:
                    lines.append(f"{role}: {content}")

        if not lines:
            return {"chat_history": ""}

        block = "CONVERSATION HISTORY:\n" + "\n\n".join(lines) + "\n\n"
        return {"chat_history": block}

    def get_widget(self) -> QWidget:
        return self

    def target_ids(self) -> list[str]:
        return [self._target_id]

    # ------------------------------------------------------------------
    # Theme support
    # ------------------------------------------------------------------

    def apply_theme(self, theme: dict) -> None:
        text = theme.get("text_main", "#fff")
        muted = theme.get("text_muted", "#aaa")
        border = theme.get("border", "#444")
        bg = theme.get("bg_input", "#2b2b2b")
        self._checkbox.setStyleSheet(f"color: {muted};")
        self._spin.setStyleSheet(
            f"background-color: {bg}; color: {text}; border: 1px solid {border}; border-radius: 3px;"
        )
        for child in self.findChildren(QLabel):
            child.setStyleSheet(f"color: {muted};")
