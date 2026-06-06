from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
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

from gui.docks.unified_research.components.chat_streamer import ChatMessageWidget
from gui.docks.unified_research.tabs.base_tab import BaseTab
from core.events.domains.research_agent_events import ResearchAgentEvent, ResearchAgentPayload


class ResearchAgentTab(BaseTab):
    def __init__(self, main_window, parent=None):
        super().__init__(main_window, target_id="research_agent", parent=parent)
        self.agent_service = getattr(main_window, "research_agent_service", None)
        self._rendered_keys = set()
        self._build_ui()
        self._connect_service()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        self.header = QFrame()
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(10, 8, 10, 8)
        header_layout.setSpacing(8)
        title_layout = QVBoxLayout()
        self.title_lbl = QLabel("Research Agent")
        self.title_lbl.setStyleSheet("font-weight: bold;")
        self.status_lbl = QLabel("Describe a research goal to begin.")
        self.status_lbl.setWordWrap(True)
        title_layout.addWidget(self.title_lbl)
        title_layout.addWidget(self.status_lbl)
        header_layout.addLayout(title_layout, 1)
        self.session_state_lbl = QLabel("Idle")
        self.session_state_lbl.setObjectName("ResearchAgentStatusPill")
        self.session_state_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.session_state_lbl.setFixedWidth(92)
        header_layout.addWidget(self.session_state_lbl)
        layout.addWidget(self.header)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.chat_container = QWidget()
        self.chat_layout = QVBoxLayout(self.chat_container)
        self.chat_layout.setContentsMargins(0, 0, 0, 0)
        self.chat_layout.setSpacing(8)
        self.chat_layout.addStretch()
        self.scroll_area.setWidget(self.chat_container)
        layout.addWidget(self.scroll_area, 1)

        self.input_wrapper = QFrame()
        self.input_wrapper.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        input_layout = QVBoxLayout(self.input_wrapper)
        input_layout.setContentsMargins(8, 8, 8, 8)

        self.input_field = QTextEdit()
        self.input_field.setPlaceholderText("Research goal, direction choice, source confirmation, or new instruction...")
        self.input_field.setMaximumHeight(74)
        input_layout.addWidget(self.input_field)

        button_layout = QHBoxLayout()
        self.btn_reset = QPushButton("New")
        self.btn_reset.clicked.connect(self._reset_session)
        self.btn_start = QPushButton("Start")
        self.btn_start.clicked.connect(self._start_or_send)
        self.btn_continue = QPushButton("Continue")
        self.btn_continue.clicked.connect(self._continue_planning)
        button_layout.addStretch()
        button_layout.addWidget(self.btn_reset)
        button_layout.addWidget(self.btn_continue)
        button_layout.addWidget(self.btn_start)
        input_layout.addLayout(button_layout)
        layout.addWidget(self.input_wrapper)

    def _connect_service(self):
        if not self.agent_service:
            self.status_lbl.setText("Research agent service is unavailable.")
            self.btn_start.setEnabled(False)
            self.btn_continue.setEnabled(False)
            return
        self.agent_service.status_changed.connect(self._handle_status_event)
        self.agent_service.session_updated.connect(self._handle_session_event)
        self.agent_service.checkpoint_requested.connect(self._handle_checkpoint_event)
        self.agent_service.error.connect(self._handle_error_event)
        session = getattr(self.agent_service, "session", None)
        if session:
            self._render_session(session.to_dict())

    def _handle_status_event(self, event: ResearchAgentEvent, payload: ResearchAgentPayload):
        if event == ResearchAgentEvent.STATUS_CHANGED:
            self._set_status(payload.message)

    def _handle_session_event(self, event: ResearchAgentEvent, payload: ResearchAgentPayload):
        if event == ResearchAgentEvent.SESSION_UPDATED:
            self._render_session(payload.session)

    def _handle_checkpoint_event(self, event: ResearchAgentEvent, payload: ResearchAgentPayload):
        if event == ResearchAgentEvent.CHECKPOINT_REQUESTED:
            self._render_checkpoint(payload.checkpoint)

    def _handle_error_event(self, event: ResearchAgentEvent, payload: ResearchAgentPayload):
        if event == ResearchAgentEvent.ERROR:
            self._render_error(payload.message)

    def _start_or_send(self):
        text = self.input_field.toPlainText().strip()
        if not text or not self.agent_service:
            return
        self.input_field.clear()
        session = getattr(self.agent_service, "session", None)
        if not session:
            self._add_message("You", text, is_user=True)
            self.agent_service.start_session(text)
        else:
            self._add_message("You", text, is_user=True)
            self.agent_service.add_user_input(text)

    def _continue_planning(self):
        if self.agent_service:
            self.agent_service.plan_next()

    def _reset_session(self):
        self._clear_messages()
        self._rendered_keys.clear()
        self.btn_start.setText("Start")
        if self.agent_service:
            self.agent_service.reset_session()

    def _set_status(self, text: str):
        self.status_lbl.setText(text)

    def _render_session(self, session: dict):
        if not session:
            self.btn_start.setText("Start")
            self.btn_continue.setEnabled(False)
            self.session_state_lbl.setText("Idle")
            return
        self.btn_start.setText("Send")
        status = session.get("status", "planning")
        self.session_state_lbl.setText(status.replace("_", " ").title())
        self.btn_continue.setEnabled(status not in {"planning", "running_tool"})
        goal_key = f"goal:{session.get('session_id')}"
        if goal_key not in self._rendered_keys:
            self._rendered_keys.add(goal_key)
            self._add_card("Research Goal", session.get("goal", ""), "Session", accent=True)
        for artifact in session.get("artifacts", []):
            key = f"artifact:{artifact.get('created_at')}:{artifact.get('title')}"
            if key in self._rendered_keys:
                continue
            self._rendered_keys.add(key)
            title = artifact.get("title") or artifact.get("kind") or "Artifact"
            body = self._format_content(artifact.get("content", ""))
            self._add_card(title, body, artifact.get("source") or artifact.get("kind") or "Agent")
        for run in session.get("tool_runs", []):
            key = f"tool:{run.get('created_at')}:{run.get('status')}:{run.get('completed_at')}"
            if key in self._rendered_keys:
                continue
            self._rendered_keys.add(key)
            title = f"{run.get('blueprint_id', 'Tool')} [{run.get('status', 'queued')}]"
            reason = run.get("reason", "")
            result = run.get("result_summary", "")
            body = reason
            if result:
                body += f"\n\nResult:\n{result}"
            self._add_card(title, body.strip(), "Tool Run")
        for checkpoint in session.get("checkpoints", []):
            if checkpoint.get("status") == "pending":
                self._render_checkpoint(checkpoint)
            else:
                key = f"checkpoint_resolved:{checkpoint.get('created_at')}:{checkpoint.get('resolved_at')}"
                if key in self._rendered_keys:
                    continue
                self._rendered_keys.add(key)
                body = f"{checkpoint.get('prompt', '')}\n\nResponse:\n{checkpoint.get('response', '')}"
                self._add_card("Resolved Checkpoint", body.strip(), "Human Input")

    def _render_checkpoint(self, checkpoint: dict):
        key = f"checkpoint:{checkpoint.get('created_at')}:{checkpoint.get('prompt')}"
        if key in self._rendered_keys:
            return
        self._rendered_keys.add(key)
        prompt = checkpoint.get("prompt", "")
        options = checkpoint.get("options") or []
        self._add_checkpoint_card(prompt, options)

    def _render_error(self, message: str):
        self._add_message("Agent", f"Error: {message}", is_user=False)

    def _add_message(self, sender: str, text: str, is_user: bool = False):
        widget = ChatMessageWidget(sender, theme=self.theme, is_user=is_user)
        widget.append_chunk(text)
        if not is_user and hasattr(widget, "hide_status"):
            widget.hide_status()
        self.receive_ai_widget(widget)
        QTimer.singleShot(50, self._scroll_bottom)

    def _add_card(self, title: str, body: str, source: str = "", accent: bool = False):
        card = QFrame()
        card.setObjectName("ResearchAgentCard")
        card.setProperty("accent", accent)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(5)
        meta_layout = QHBoxLayout()
        meta_layout.setContentsMargins(0, 0, 0, 0)
        header = QLabel(title)
        header.setObjectName("ResearchAgentCardTitle")
        header.setWordWrap(True)
        meta_layout.addWidget(header, 1)
        if source:
            source_lbl = QLabel(source)
            source_lbl.setObjectName("ResearchAgentCardSource")
            source_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            meta_layout.addWidget(source_lbl)
        layout.addLayout(meta_layout)
        body_lbl = QLabel(body or "")
        body_lbl.setObjectName("ResearchAgentCardBody")
        body_lbl.setWordWrap(True)
        body_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(body_lbl)
        self.receive_ai_widget(card)
        self._style_card(card)
        QTimer.singleShot(50, self._scroll_bottom)

    def _add_checkpoint_card(self, prompt: str, options: list):
        card = QFrame()
        card.setObjectName("ResearchAgentCard")
        card.setProperty("accent", True)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(7)
        title = QLabel("Checkpoint")
        title.setObjectName("ResearchAgentCardTitle")
        layout.addWidget(title)
        body = QLabel(prompt)
        body.setObjectName("ResearchAgentCardBody")
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(body)
        if options:
            option_layout = QVBoxLayout()
            for option in options:
                btn = QPushButton(option)
                btn.setObjectName("ResearchAgentOptionButton")
                btn.clicked.connect(lambda checked=False, text=option: self._submit_option(text))
                option_layout.addWidget(btn)
            layout.addLayout(option_layout)
        self.receive_ai_widget(card)
        self._style_card(card)
        QTimer.singleShot(50, self._scroll_bottom)

    def _submit_option(self, option: str):
        self._add_message("You", option, is_user=True)
        if self.agent_service:
            self.agent_service.add_user_input(option)

    def _clear_messages(self):
        while self.chat_layout.count() > 1:
            item = self.chat_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _format_content(self, value):
        if isinstance(value, str):
            return value
        try:
            import json
            return json.dumps(value, indent=2)
        except Exception:
            return str(value)

    def _style_card(self, card):
        if not self.theme:
            return
        bg = self._theme_color("bg_panel", "#333")
        border = self._theme_color("accent", "#b366ff") if card.property("accent") else self._theme_color("border", "#444")
        text = self._theme_color("text_main", "#fff")
        muted = self._theme_color("text_muted", "#aaa")
        input_bg = self._theme_color("bg_input", "#2b2b2b")
        accent = self._theme_color("accent", "#b366ff")
        card.setStyleSheet(
            f"background-color: {bg}; border: 1px solid {border}; border-radius: 6px;"
        )
        for label in card.findChildren(QLabel):
            if label.objectName() == "ResearchAgentCardTitle":
                label.setStyleSheet(f"color: {text}; font-weight: bold;")
            elif label.objectName() == "ResearchAgentCardSource":
                label.setStyleSheet(f"color: {muted}; font-size: 11px;")
            else:
                label.setStyleSheet(f"color: {text};")
        for button in card.findChildren(QPushButton):
            button.setStyleSheet(
                f"background-color: {input_bg}; color: {text}; border: 1px solid {border};"
                f"border-radius: 4px; padding: 6px;"
            )
            button.setCursor(Qt.CursorShape.PointingHandCursor)

    def _scroll_bottom(self):
        self.scroll_area.verticalScrollBar().setValue(self.scroll_area.verticalScrollBar().maximum())

    def update_theme(self, theme):
        super().update_theme(theme)
        self.header.setStyleSheet(
            f"background-color: {self._theme_color('bg_panel', '#333')};"
            f"border: 1px solid {self._theme_color('border', '#444')};"
            "border-radius: 6px;"
        )
        self.title_lbl.setStyleSheet(f"color: {self._theme_color('text_main', '#fff')}; font-weight: bold;")
        self.status_lbl.setStyleSheet(f"color: {self._theme_color('text_muted', '#aaa')};")
        self.session_state_lbl.setStyleSheet(
            f"background-color: {self._theme_color('bg_input', '#2b2b2b')};"
            f"color: {self._theme_color('text_main', '#fff')};"
            f"border: 1px solid {self._theme_color('border', '#444')};"
            "border-radius: 6px; padding: 4px 6px;"
        )
        self.input_wrapper.setStyleSheet(
            f"background-color: {self._theme_color('bg_input', '#2b2b2b')};"
            f"border: 1px solid {self._theme_color('border', '#444')};"
            "border-radius: 8px;"
        )
        self.input_field.setStyleSheet(f"background-color: transparent; color: {self._theme_color('text_main', '#fff')}; border: none;")
        button_style = (
            f"background-color: {self._theme_color('accent', '#b366ff')};"
            "font-weight: bold; color: white; border: none; border-radius: 6px; padding: 6px 12px;"
        )
        self.btn_start.setStyleSheet(button_style)
        self.btn_reset.setStyleSheet(
            f"background-color: {self._theme_color('bg_panel', '#333')};"
            f"color: {self._theme_color('text_main', '#fff')};"
            f"border: 1px solid {self._theme_color('border', '#444')};"
            "border-radius: 6px; padding: 6px 12px;"
        )
        self.btn_continue.setStyleSheet(
            f"background-color: {self._theme_color('bg_panel', '#333')};"
            f"color: {self._theme_color('text_main', '#fff')};"
            f"border: 1px solid {self._theme_color('border', '#444')};"
            "border-radius: 6px; padding: 6px 12px;"
        )
        for i in range(self.chat_layout.count()):
            widget = self.chat_layout.itemAt(i).widget()
            if widget and widget.objectName() == "ResearchAgentCard":
                self._style_card(widget)

    def _theme_color(self, key: str, fallback: str) -> str:
        value = self.theme.get(key, fallback) if self.theme else fallback
        return value if isinstance(value, str) and value.strip() else fallback
