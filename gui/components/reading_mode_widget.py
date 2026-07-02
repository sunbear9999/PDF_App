from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPalette
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


class ReadingModeWidget(QWidget):
    exit_requested = Signal()
    play_pause_requested = Signal()
    previous_word_requested = Signal()
    next_word_requested = Signal()
    previous_sentence_requested = Signal()
    next_sentence_requested = Signal()
    speed_changed = Signal(int)
    seek_requested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ReadingModeWidget")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAutoFillBackground(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._tokens = []
        self._current_index = 0
        self._total = 0
        self._scrubbing = False
        self._base_word_font_size = 46

        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 28, 32, 24)
        outer.setSpacing(18)

        top = QHBoxLayout()
        self.page_label = QLabel("Page -")
        self.page_label.setObjectName("ReadingPageLabel")
        self.exit_btn = QPushButton("Exit")
        self.exit_btn.setObjectName("ReadingExitButton")
        self.exit_btn.clicked.connect(self.exit_requested.emit)
        top.addWidget(self.page_label)
        top.addStretch()
        top.addWidget(self.exit_btn)
        outer.addLayout(top)

        self.stage = QWidget(self)
        self.stage.setObjectName("ReadingStage")
        self.stage.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.stage.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.left_context = QLabel("")
        self.left_context.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.left_context.setWordWrap(False)
        self.left_context.setObjectName("ReadingContextLeft")
        self.left_context.setParent(self.stage)
        self.current_word = QLabel("")
        self.current_word.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.current_word.setObjectName("ReadingCurrentWord")
        self.current_word.setParent(self.stage)
        self.current_word.setMinimumSize(220, 96)
        self.current_word.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.right_context = QLabel("")
        self.right_context.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.right_context.setWordWrap(False)
        self.right_context.setObjectName("ReadingContextRight")
        self.right_context.setParent(self.stage)
        outer.addStretch(1)

        self.empty_label = QLabel("")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setObjectName("ReadingEmptyLabel")
        self.empty_label.hide()
        outer.addWidget(self.empty_label)

        outer.addStretch(2)

        controls = QFrame(self)
        controls.setObjectName("ReadingControls")
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(14, 12, 14, 12)
        controls_layout.setSpacing(10)

        self.progress = QSlider(Qt.Orientation.Horizontal)
        self.progress.setRange(0, 0)
        self.progress.sliderPressed.connect(self._start_scrub)
        self.progress.sliderReleased.connect(self._finish_scrub)
        self.progress.valueChanged.connect(self._preview_scrub)
        controls_layout.addWidget(self.progress)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.prev_sentence_btn = QPushButton("<<")
        self.prev_word_btn = QPushButton("<")
        self.play_btn = QPushButton("Play")
        self.play_btn.setObjectName("ReadingPlayButton")
        self.next_word_btn = QPushButton(">")
        self.next_sentence_btn = QPushButton(">>")
        self.speed_label = QLabel("WPM")
        self.speed_spin = QSpinBox()
        self.speed_spin.setRange(80, 900)
        self.speed_spin.setSingleStep(10)
        self.speed_spin.setValue(300)
        self.speed_spin.valueChanged.connect(self.speed_changed.emit)

        self.prev_sentence_btn.clicked.connect(self.previous_sentence_requested.emit)
        self.prev_word_btn.clicked.connect(self.previous_word_requested.emit)
        self.play_btn.clicked.connect(self.play_pause_requested.emit)
        self.next_word_btn.clicked.connect(self.next_word_requested.emit)
        self.next_sentence_btn.clicked.connect(self.next_sentence_requested.emit)

        for button in [
            self.prev_sentence_btn,
            self.prev_word_btn,
            self.play_btn,
            self.next_word_btn,
            self.next_sentence_btn,
        ]:
            buttons.addWidget(button)
        buttons.addStretch()
        buttons.addWidget(self.speed_label)
        buttons.addWidget(self.speed_spin)
        controls_layout.addLayout(buttons)
        outer.addWidget(controls)
        self.controls = controls

    def set_tokens(self, tokens) -> None:
        self._tokens = list(tokens or [])
        self._total = len(self._tokens)
        self.progress.setRange(0, max(0, self._total - 1))
        self.set_empty_message("" if self._tokens else "No readable text found. Try OCR for scanned PDFs.")

    def set_empty_message(self, message: str) -> None:
        has_message = bool(message)
        self.empty_label.setText(message)
        self.empty_label.setVisible(has_message)
        self.left_context.setVisible(not has_message)
        self.current_word.setVisible(not has_message)
        self.right_context.setVisible(not has_message)

    def set_playing(self, playing: bool) -> None:
        self.play_btn.setText("Pause" if playing else "Play")

    def set_speed(self, wpm: int) -> None:
        if self.speed_spin.value() != wpm:
            self.speed_spin.blockSignals(True)
            self.speed_spin.setValue(wpm)
            self.speed_spin.blockSignals(False)

    def show_token(self, token, index: int, total: int) -> None:
        self._current_index = index
        self._total = total
        if not token:
            return
        self.set_empty_message("")
        self.current_word.setText(token.text)
        self.page_label.setText(f"Page {token.page_num + 1}  -  {index + 1} / {total}")
        if not self._scrubbing and self.progress.value() != index:
            self.progress.blockSignals(True)
            self.progress.setValue(index)
            self.progress.blockSignals(False)
        self._update_context(index)

    def apply_theme(self, theme: dict) -> None:
        bg = theme.get("bg_main", "#1e1e1e")
        panel = theme.get("bg_panel", "#2b2b2b")
        input_bg = theme.get("bg_input", "#333333")
        text = theme.get("text_main", "#ffffff")
        muted = theme.get("text_muted", "#aaaaaa")
        border = theme.get("border", "#555555")
        accent = theme.get("accent", "#0078D7")
        self.setStyleSheet(f"""
            QWidget#ReadingModeWidget {{
                background-color: {bg};
                color: {text};
            }}
            QWidget#ReadingStage {{
                background: transparent;
            }}
            QLabel#ReadingCurrentWord {{
                color: {text};
                font-size: 46px;
                font-weight: 700;
                padding: 14px 18px;
                background: transparent;
            }}
            QLabel#ReadingContextLeft, QLabel#ReadingContextRight {{
                color: {muted};
                font-size: 22px;
                background: transparent;
            }}
            QLabel#ReadingPageLabel, QLabel#ReadingEmptyLabel, QLabel {{
                color: {muted};
            }}
            QFrame#ReadingControls {{
                background-color: {panel};
                border: 1px solid {border};
                border-radius: 8px;
            }}
            QPushButton {{
                background-color: {input_bg};
                color: {text};
                border: 1px solid {border};
                border-radius: 4px;
                padding: 7px 12px;
                min-width: 42px;
            }}
            QPushButton:hover {{
                border-color: {accent};
            }}
            QPushButton#ReadingPlayButton {{
                background-color: {accent};
                color: #ffffff;
                min-width: 72px;
            }}
            QSpinBox {{
                background-color: {input_bg};
                color: {text};
                border: 1px solid {border};
                border-radius: 4px;
                padding: 5px 8px;
            }}
            QSlider::groove:horizontal {{
                background: {input_bg};
                height: 6px;
                border-radius: 3px;
            }}
            QSlider::handle:horizontal {{
                background: {accent};
                width: 16px;
                margin: -5px 0;
                border-radius: 8px;
            }}
        """)
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor(bg))
        self.setPalette(palette)
        self.current_word.setFont(QFont(self.font().family(), self._base_word_font_size, QFont.Weight.Bold))
        self._fit_current_word_font()

    def keyPressEvent(self, event):
        key = event.key()
        modifiers = event.modifiers()
        if key == Qt.Key.Key_Escape:
            self.exit_requested.emit()
            event.accept()
            return
        if key == Qt.Key.Key_Space:
            self.play_pause_requested.emit()
            event.accept()
            return
        if key == Qt.Key.Key_Left:
            if modifiers & Qt.KeyboardModifier.ShiftModifier:
                self.previous_sentence_requested.emit()
            else:
                self.previous_word_requested.emit()
            event.accept()
            return
        if key == Qt.Key.Key_Right:
            if modifiers & Qt.KeyboardModifier.ShiftModifier:
                self.next_sentence_requested.emit()
            else:
                self.next_word_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_stage()
        self._update_context(self._current_index)

    def showEvent(self, event):
        super().showEvent(event)
        self._layout_stage()
        self._update_context(self._current_index)

    def _context_radius(self) -> int:
        width = self.width()
        if width < 700:
            return 3
        if width < 1100:
            return 7
        return 18

    def _update_context(self, index: int) -> None:
        if not self._tokens or index >= len(self._tokens):
            return
        radius = self._context_radius()
        current_sentence = self._tokens[index].sentence_index
        left_start = max(0, index - radius)
        right_end = min(len(self._tokens), index + radius + 1)
        if radius >= 18:
            left_start = min(
                [i for i, token in enumerate(self._tokens[:index + 1]) if token.sentence_index == current_sentence] or [left_start]
            )
            sentence_end = [
                i for i in range(index + 1, len(self._tokens))
                if self._tokens[i].sentence_index != current_sentence
            ]
            right_end = sentence_end[0] if sentence_end else right_end
        self.left_context.setText(" ".join(token.text for token in self._tokens[left_start:index]))
        self.right_context.setText(" ".join(token.text for token in self._tokens[index + 1:right_end]))
        self._fit_current_word_font()

    def _layout_stage(self) -> None:
        width = max(1, self.width())
        height = max(1, self.height())
        center_x = width // 2
        center_y = height // 2
        word_width = min(max(360, int(width * 0.28)), max(220, width - 96))
        word_height = 116
        gap = 22
        margin = 36

        self.stage.setGeometry(0, 0, width, height)
        self.current_word.setGeometry(
            center_x - word_width // 2,
            center_y - word_height // 2,
            word_width,
            word_height,
        )
        left_width = max(0, center_x - word_width // 2 - gap - margin)
        right_x = center_x + word_width // 2 + gap
        right_width = max(0, width - right_x - margin)
        context_height = 70
        context_y = center_y - context_height // 2
        self.left_context.setGeometry(margin, context_y, left_width, context_height)
        self.right_context.setGeometry(right_x, context_y, right_width, context_height)
        self.stage.raise_()
        if hasattr(self, "controls"):
            self.controls.raise_()
        self.exit_btn.raise_()
        self.page_label.raise_()

    def _fit_current_word_font(self) -> None:
        text = self.current_word.text()
        if not text:
            return
        available = max(80, self.current_word.width() - 36)
        size = self._base_word_font_size
        font = QFont(self.font().family(), size, QFont.Weight.Bold)
        while size > 22 and QFontMetrics(font).horizontalAdvance(text) > available:
            size -= 2
            font = QFont(self.font().family(), size, QFont.Weight.Bold)
        self.current_word.setFont(font)

    def _start_scrub(self):
        self._scrubbing = True

    def _finish_scrub(self):
        self._scrubbing = False
        self.seek_requested.emit(self.progress.value())

    def _preview_scrub(self, value: int):
        if self._scrubbing and 0 <= value < len(self._tokens):
            self.show_token(self._tokens[value], value, len(self._tokens))
