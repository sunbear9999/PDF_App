"""
gui/help/help_center_dialog.py

Searchable Help Center — replaces the old HelpDialog.

Opens via:
    app_context.dialog_manager.show(
        HelpCenterDialog, key="help_center", singleton=True,
        factory=lambda: HelpCenterDialog(app_context, parent=main_window),
    )

Accepts AppContext, never MainWindow directly.
"""
from __future__ import annotations

import logging
from typing import Optional, TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPushButton, QSplitter, QTextBrowser,
    QVBoxLayout, QWidget, QFrame,
)

from gui.base.core import UnifiedThemedMixin

if TYPE_CHECKING:
    from gui.app_context import AppContext
    from core.help.models import HelpTopic

log = logging.getLogger(__name__)


class HelpCenterDialog(QDialog, UnifiedThemedMixin):
    """
    Searchable help center with category navigation and full topic view.

    Content is loaded dynamically from HelpRegistry; no hardcoded topic text lives here.
    """

    def __init__(self, app_context: "AppContext", parent=None) -> None:
        super().__init__(parent)
        self._ctx = app_context
        self._current_topic: Optional["HelpTopic"] = None
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self._run_search)

        self.setWindowTitle("Papyrus Help Center")
        self.setMinimumSize(700, 500)
        self.resize(920, 640)

        self._build_ui()
        self._load_categories()
        self._apply_current_theme()

        # Live theme updates
        app_context.bus.theme_changed.connect(self._on_theme_changed)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # Search bar
        self._search_bar = QLineEdit()
        self._search_bar.setPlaceholderText("Search help topics…")
        self._search_bar.textChanged.connect(self._on_search_changed)
        self._search_bar.setClearButtonEnabled(True)
        root.addWidget(self._search_bar)

        # Splitter: left = category / results list | right = topic view
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left panel
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(4)
        self._list_label = QLabel("Categories")
        self._list_label.setObjectName("HelpListLabel")
        left_layout.addWidget(self._list_label)
        self._topic_list = QListWidget()
        self._topic_list.setObjectName("HelpTopicList")
        self._topic_list.currentItemChanged.connect(self._on_list_selection_changed)
        left_layout.addWidget(self._topic_list, stretch=1)
        splitter.addWidget(left)

        # Right panel
        right = QFrame()
        right.setObjectName("HelpRightPanel")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 0, 0, 0)
        right_layout.setSpacing(6)
        self._topic_title = QLabel()
        self._topic_title.setObjectName("HelpTopicTitle")
        self._topic_title.setWordWrap(True)
        right_layout.addWidget(self._topic_title)
        self._topic_body = QTextBrowser()
        self._topic_body.setObjectName("HelpTopicBody")
        self._topic_body.setOpenExternalLinks(False)
        right_layout.addWidget(self._topic_body, stretch=1)
        self._tutorial_btn = QPushButton("▶  Start Related Tutorial")
        self._tutorial_btn.setObjectName("HelpTutorialBtn")
        self._tutorial_btn.hide()
        self._tutorial_btn.clicked.connect(self._on_start_tutorial)
        right_layout.addWidget(self._tutorial_btn)
        splitter.addWidget(right)

        splitter.setSizes([220, 700])
        root.addWidget(splitter, stretch=1)

        # Bottom bar
        bar = QHBoxLayout()
        bar.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setObjectName("HelpCloseBtn")
        close_btn.clicked.connect(self.accept)
        bar.addWidget(close_btn)
        root.addLayout(bar)

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _get_registry(self):
        hs = getattr(self._ctx, "help_service", None)
        return hs.help_registry if hs else None

    def _load_categories(self) -> None:
        reg = self._get_registry()
        if not reg:
            return
        self._topic_list.clear()
        all_item = QListWidgetItem("All Topics")
        all_item.setData(Qt.ItemDataRole.UserRole, None)  # None = all categories
        self._topic_list.addItem(all_item)
        for cat in sorted(reg.get_categories()):
            item = QListWidgetItem(cat)
            item.setData(Qt.ItemDataRole.UserRole, cat)
            self._topic_list.addItem(item)
        self._list_label.setText("Categories")
        if self._topic_list.count() > 0:
            self._topic_list.setCurrentRow(0)

    def _populate_list_with_topics(self, topics: list) -> None:
        """Switch list to show individual topics (during search)."""
        self._topic_list.clear()
        self._list_label.setText(f"Results ({len(topics)})")
        for t in topics:
            item = QListWidgetItem(t.title)
            item.setData(Qt.ItemDataRole.UserRole, t)
            self._topic_list.addItem(item)
        if self._topic_list.count() > 0:
            self._topic_list.setCurrentRow(0)

    # ------------------------------------------------------------------
    # Interaction handlers
    # ------------------------------------------------------------------

    def _on_search_changed(self, text: str) -> None:
        self._search_timer.start()

    def _run_search(self) -> None:
        text = self._search_bar.text().strip()
        reg = self._get_registry()
        if not reg:
            return
        if not text:
            self._load_categories()
            return
        results = reg.search(text)
        self._populate_list_with_topics(results)

    def _on_list_selection_changed(self, current: Optional[QListWidgetItem], _prev) -> None:
        if not current:
            return
        data = current.data(Qt.ItemDataRole.UserRole)
        if data is None:
            # "All Topics" category row
            reg = self._get_registry()
            if reg:
                topics = reg.all_topics()
                if topics:
                    self._display_topic(topics[0])
        elif isinstance(data, str):
            # Category name
            reg = self._get_registry()
            if reg:
                topics = reg.get_by_category(data)
                if topics:
                    self._display_topic(topics[0])
        else:
            # HelpTopic object (from search results)
            self._display_topic(data)

    def _display_topic(self, topic: "HelpTopic") -> None:
        self._current_topic = topic
        self._topic_title.setText(f"<b>{topic.title}</b>")
        body_text = topic.body
        # Render markdown if QTextBrowser supports it (PySide6 ≥ 6.4)
        if hasattr(self._topic_body, "setMarkdown"):
            self._topic_body.setMarkdown(body_text)
        else:
            self._topic_body.setPlainText(body_text)
        if topic.tutorial_ids:
            self._tutorial_btn.setProperty("_tutorial_ids", topic.tutorial_ids)
            self._tutorial_btn.show()
        else:
            self._tutorial_btn.hide()

    def _on_start_tutorial(self) -> None:
        ids = self._tutorial_btn.property("_tutorial_ids") or []
        if ids:
            from core.events.domains.help_events import HelpIntent, HelpPayload
            self._ctx.bus.help_action_requested.emit(
                HelpIntent.START_TUTORIAL, HelpPayload(tutorial_id=ids[0])
            )
            self.accept()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def show_topic(self, topic_id: str) -> None:
        """Jump directly to a topic by ID. Called by HelpGUICoordinator for F1/What's This?."""
        reg = self._get_registry()
        if not reg:
            return
        topic = reg.get(topic_id)
        if topic:
            self._display_topic(topic)

    # ------------------------------------------------------------------
    # Theming
    # ------------------------------------------------------------------

    def _on_theme_changed(self, _intent, theme) -> None:
        if isinstance(theme, dict):
            self.apply_theme(theme)

    def _apply_current_theme(self) -> None:
        tm = getattr(self._ctx, "theme_manager", None)
        if tm:
            self.apply_theme(tm.get_theme())

    def apply_theme(self, theme: dict) -> None:
        self._theme = theme
        bg = self._t("bg_main")
        bg_panel = self._t("bg_panel")
        text = self._t("text_main")
        text_muted = self._t("text_muted")
        accent = self._t("accent")
        border = self._t("border")
        self.setStyleSheet(f"""
            QDialog {{ background-color: {bg}; color: {text}; }}
            QLineEdit {{ background-color: {bg_panel}; color: {text}; border: 1px solid {border};
                         border-radius: 4px; padding: 6px; }}
            QListWidget {{ background-color: {bg_panel}; color: {text}; border: 1px solid {border}; }}
            QListWidget::item:selected {{ background-color: {accent}; color: #ffffff; }}
            QTextBrowser {{ background-color: {bg_panel}; color: {text}; border: 1px solid {border};
                            padding: 8px; }}
            QLabel#HelpTopicTitle {{ color: {text}; font-size: 15px; font-weight: bold;
                                     padding: 4px 0; }}
            QLabel#HelpListLabel {{ color: {text_muted}; font-size: 11px; font-weight: bold;
                                    padding: 2px 0; }}
            QPushButton {{ background-color: {bg_panel}; color: {text}; border: 1px solid {border};
                           border-radius: 4px; padding: 6px 14px; }}
            QPushButton:hover {{ border-color: {accent}; }}
            QPushButton#HelpTutorialBtn {{ background-color: {accent}; color: #ffffff;
                                           border: none; font-weight: bold; }}
            QPushButton#HelpTutorialBtn:hover {{ background-color: {self._t("accent_hover")}; }}
        """)
