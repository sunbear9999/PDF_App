"""
gui/components/command_palette.py

Ctrl+P floating command palette with fuzzy search over built-in and plugin commands.
"""
from __future__ import annotations

from typing import List, TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QVBoxLayout,
)

if TYPE_CHECKING:
    from gui.main_window import MainWindow
    from core.plugins.extension_registry import CommandSpec


class CommandPalette(QDialog):
    def __init__(self, main_window: "MainWindow") -> None:
        super().__init__(
            main_window,
            Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint,
        )
        self._main_window = main_window
        self._theme: dict = {}
        self._build_ui()

    def _build_ui(self) -> None:
        self.setFixedWidth(520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        hint = QLabel("  Type to filter commands   •   ↑↓ navigate   •   Enter run   •   Esc close")
        hint.setFixedHeight(24)
        hint.setStyleSheet("font-size: 11px; padding-left: 8px; color: #888;")
        layout.addWidget(hint)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search commands…")
        self._search.setFixedHeight(44)
        layout.addWidget(self._search)

        self._list = QListWidget()
        self._list.setFixedHeight(320)
        layout.addWidget(self._list)

        self._search.textChanged.connect(self._on_search_changed)
        self._list.itemActivated.connect(self._on_activated)
        self._search.returnPressed.connect(self._run_selected)

    def show_palette(self) -> None:
        self._search.clear()
        self._populate(self._all_commands())
        mw = self._main_window
        cx = mw.x() + (mw.width() - self.width()) // 2
        cy = mw.y() + 90
        self.move(cx, cy)
        self.show()
        self._search.setFocus()

    def _all_commands(self) -> List["CommandSpec"]:
        commands = list(self._get_builtin_commands())
        registry = getattr(
            getattr(self._main_window, "app_context", None),
            "plugin_extension_registry",
            None,
        )
        if registry:
            commands.extend(registry.get_commands())
        return commands

    def _get_builtin_commands(self) -> List["CommandSpec"]:
        from core.plugins.extension_registry import CommandSpec
        from core.events.domains.project_events import ProjectIntent, ProjectPayload

        mw = self._main_window
        bus = getattr(mw, "bus", None)

        def _save():
            if bus:
                bus.project_action_requested.emit(ProjectIntent.SAVE, ProjectPayload())

        def _cmd(cid, label, cb, shortcut="", category="General"):
            return CommandSpec(
                command_id=cid, label=label, callback=cb,
                shortcut=shortcut, category=category,
            )

        toolbar = getattr(mw, "top_toolbar", None)
        dm = getattr(mw, "dock_manager", None)

        cmds = []
        if toolbar:
            cmds += [
                _cmd("new_project", "New Project…", toolbar._trigger_new_project, category="Project"),
                _cmd("open_project", "Open Project…", toolbar._trigger_open_project, category="Project"),
                _cmd("save_project", "Save Project", _save, "Ctrl+S", "Project"),
                _cmd("add_pdf", "Add PDF to Project…", toolbar._trigger_add_pdf, category="Project"),
            ]
        if dm:
            cmds += [
                _cmd("spawn_research", "Open Research Assistant", lambda: dm.spawn("research"), category="View"),
                _cmd("spawn_workspace", "Open Workspace", lambda: dm.spawn("workspaces"), category="View"),
                _cmd("spawn_notes", "Open Notes List", lambda: dm.spawn("notes"), category="View"),
                _cmd("spawn_essays", "Open Essay Writer", lambda: dm.spawn("essays"), category="View"),
                _cmd("spawn_citations", "Open Citation Manager", lambda: dm.spawn("citations"), category="View"),
                _cmd("spawn_dict", "Open Dictionary", lambda: dm.spawn("dicts"), category="View"),
                _cmd("spawn_ocr", "Open OCR Scanner", lambda: dm.spawn("ocrs"), category="View"),
                _cmd("spawn_tts", "Open Audio (TTS)", lambda: dm.spawn("audios"), category="View"),
            ]
        cmds += [
            _cmd("toggle_fullscreen", "Toggle Full Screen", mw.toggle_full_screen, "F11", "View"),
            _cmd("open_prompt_editor", "Open Prompt Editor", mw._open_prompt_editor, category="Tools"),
            _cmd("open_tag_manager", "Open Tag Manager", mw._open_tag_manager, category="Tools"),
        ]
        return cmds

    def _populate(self, commands: List["CommandSpec"], filter_text: str = "") -> None:
        self._list.clear()
        query = filter_text.lower()
        prev_category = None
        for cmd in commands:
            label_lower = cmd.label.lower()
            desc_lower = (cmd.description or "").lower()
            if query and query not in label_lower and query not in desc_lower:
                continue
            # Category separator
            if not query and cmd.category != prev_category:
                sep_item = QListWidgetItem(f"  {cmd.category}")
                sep_item.setFlags(Qt.ItemFlag.NoItemFlags)
                font = sep_item.font()
                font.setBold(True)
                sep_item.setFont(font)
                self._list.addItem(sep_item)
                prev_category = cmd.category

            display = cmd.label
            if cmd.shortcut:
                display = f"{cmd.label}  [{cmd.shortcut}]"
            item = QListWidgetItem(f"   {display}")
            item.setData(Qt.ItemDataRole.UserRole, cmd)
            self._list.addItem(item)

        # Select first runnable item
        for i in range(self._list.count()):
            it = self._list.item(i)
            if it and it.data(Qt.ItemDataRole.UserRole) is not None:
                self._list.setCurrentItem(it)
                break

    def _on_search_changed(self, text: str) -> None:
        self._populate(self._all_commands(), text)

    def _on_activated(self, item: QListWidgetItem) -> None:
        cmd = item.data(Qt.ItemDataRole.UserRole)
        if not cmd:
            return
        self.close()
        if cmd.callback:
            QTimer.singleShot(50, cmd.callback)

    def _run_selected(self) -> None:
        item = self._list.currentItem()
        if item:
            self._on_activated(item)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.close()
        elif key == Qt.Key.Key_Down:
            self._move_selection(1)
        elif key == Qt.Key.Key_Up:
            self._move_selection(-1)
        else:
            super().keyPressEvent(event)

    def _move_selection(self, delta: int) -> None:
        count = self._list.count()
        if count == 0:
            return
        row = self._list.currentRow()
        new_row = max(0, min(count - 1, row + delta))
        # Skip category headers
        for _ in range(count):
            item = self._list.item(new_row)
            if item and item.data(Qt.ItemDataRole.UserRole) is not None:
                break
            new_row = max(0, min(count - 1, new_row + delta))
        self._list.setCurrentRow(new_row)

    def update_theme(self, theme: dict) -> None:
        self._theme = theme
        bg = theme.get("bg_panel", "#1e1e2e")
        fg = theme.get("text_primary", "#e0e0e0")
        border = theme.get("border", "#444")
        accent = theme.get("accent", "#4a4a8a")
        self.setStyleSheet(
            f"QDialog {{ background: {bg}; border: 1px solid {border}; border-radius: 8px; }}"
            f"QLabel {{ color: {fg}; background: {bg}; }}"
            f"QLineEdit {{ background: {bg}; color: {fg}; border: none;"
            f"  border-bottom: 1px solid {border}; padding: 8px 14px; font-size: 14px; }}"
            f"QListWidget {{ background: {bg}; color: {fg}; border: none; }}"
            f"QListWidget::item:selected {{ background: {accent}; }}"
            f"QListWidget::item:hover {{ background: {accent}44; }}"
        )
