"""
gui/help/tutorial_overlay.py

Spotlight overlay and tutorial card for interactive tutorials.

Architecture
------------
Both TutorialOverlay and TutorialCard are top-level Tool windows parented to
MainWindow.  Qt guarantees Tool windows always stay above their parent,
including QDockWidget children, so the card is always visible and clickable
regardless of which docks are open.

TutorialOverlay  — frameless, fully transparent for mouse events, covers the
                   entire main-window area, draws dim + spotlight cutout.

TutorialCard     — frameless, interactive (buttons fire engine methods),
                   positioned near the spotlight in screen coordinates.

Because both are tool windows their positions are in screen (global)
coordinates.  Calls to mapToGlobal() convert main-window-local rects to
screen coords for placement.
"""
from __future__ import annotations

import logging
import re
from typing import Optional, TYPE_CHECKING

from PySide6.QtCore import QEvent, QPoint, Qt, QRect, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget,
)

if TYPE_CHECKING:
    from core.help.models import TutorialStep
    from core.help.tutorial_engine import TutorialEngine

log = logging.getLogger(__name__)

_DIM_ALPHA = 140
_SPOTLIGHT_PADDING = 8
_CARD_MIN_W = 360
_CARD_MAX_W = 440


# ---------------------------------------------------------------------------
# Tutorial card
# ---------------------------------------------------------------------------

class TutorialCard(QFrame):
    """
    Interactive floating card showing step text and Next/Back/Exit buttons.

    Created as a Tool window so Qt keeps it above all parent-window contents,
    including dock widgets.
    """

    next_clicked = Signal()
    back_clicked = Signal()
    skip_clicked = Signal()
    exit_clicked = Signal()

    def __init__(self, theme: dict, parent=None) -> None:
        super().__init__(
            parent,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setObjectName("TutorialCard")
        self._build_ui()
        self.apply_theme(theme)
        self.hide()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._step_label = QLabel()
        self._step_label.setObjectName("TutorialStepLabel")
        self._step_label.setWordWrap(True)
        self._step_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._step_label.setTextFormat(Qt.TextFormat.RichText)
        self._step_label.setMinimumWidth(_CARD_MIN_W - 32)
        self._step_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self._scroll.setWidget(self._step_label)
        layout.addWidget(self._scroll, 1)

        self._counter_label = QLabel()
        self._counter_label.setObjectName("TutorialCounter")
        layout.addWidget(self._counter_label)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        self._back_btn = QPushButton("← Back")
        self._back_btn.setObjectName("TutCardBack")
        self._back_btn.clicked.connect(self.back_clicked)

        self._next_btn = QPushButton("Next →")
        self._next_btn.setObjectName("TutCardNext")
        self._next_btn.clicked.connect(self.next_clicked)

        self._skip_btn = QPushButton("Skip")
        self._skip_btn.setObjectName("TutCardSkip")
        self._skip_btn.clicked.connect(self.skip_clicked)

        self._exit_btn = QPushButton("Exit Tutorial")
        self._exit_btn.setObjectName("TutCardExit")
        self._exit_btn.clicked.connect(self.exit_clicked)

        btn_row.addWidget(self._back_btn)
        btn_row.addWidget(self._next_btn)
        btn_row.addStretch()
        btn_row.addWidget(self._skip_btn)
        btn_row.addWidget(self._exit_btn)
        layout.addLayout(btn_row)

    def update_step(self, step: "TutorialStep", index: int, total: int) -> None:
        html = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", step.text)
        html = html.replace("\n\n", "<br><br>").replace("\n", "<br>")
        html = re.sub(r"(?m)^- (.+)(?:<br>|$)", r"• \1<br>", html)
        html = re.sub(r"(?m)^## (.+?)(?:<br>|$)", r"<b style='font-size:14px'>\1</b><br>", html)
        self._step_label.setText(html)
        self._counter_label.setText(f"Step {index + 1} of {total}")
        self._back_btn.setEnabled(index > 0)
        self._next_btn.setText("Finish" if index == total - 1 else "Next →")
        self._scroll.verticalScrollBar().setValue(0)

    def apply_theme(self, theme: dict) -> None:
        bg = theme.get("bg_panel", "#2b2b2b")
        text = theme.get("text_main", "#ffffff")
        accent = theme.get("accent", "#0078D7")
        accent_hover = theme.get("accent_hover", "#0055ff")
        muted = theme.get("text_muted", "#aaaaaa")
        border = theme.get("border", "#555555")
        self.setStyleSheet(f"""
            QFrame#TutorialCard {{
                background-color: {bg};
                border: 2px solid {accent};
                border-radius: 10px;
            }}
            QScrollArea {{ background: transparent; border: none; }}
            QScrollArea > QWidget > QWidget {{ background: transparent; }}
            QLabel#TutorialStepLabel {{
                color: {text}; font-size: 13px;
                line-height: 1.5; background: transparent;
            }}
            QLabel#TutorialCounter {{ color: {muted}; font-size: 11px; }}
            QPushButton {{
                background-color: {bg}; color: {text};
                border: 1px solid {border}; border-radius: 4px;
                padding: 5px 12px; font-size: 12px;
            }}
            QPushButton:hover {{ border-color: {accent}; }}
            QPushButton#TutCardNext {{
                background-color: {accent}; color: #ffffff;
                border: none; font-weight: bold;
            }}
            QPushButton#TutCardNext:hover {{ background-color: {accent_hover}; }}
            QPushButton#TutCardExit {{ color: {muted}; }}
            QPushButton:disabled {{ color: {muted}; border-color: {border}; }}
        """)


# ---------------------------------------------------------------------------
# Overlay (paint layer)
# ---------------------------------------------------------------------------

class TutorialOverlay(QWidget):
    """
    Full-window dim + spotlight paint layer.

    Rendered as a frameless Tool window with WA_TransparentForMouseEvents so it
    sits above all dock widgets visually but never consumes any input.
    """

    def __init__(
        self,
        main_window,
        engine: "TutorialEngine",
        theme: dict,
    ) -> None:
        super().__init__(
            main_window,
            Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint,
        )
        self._main_window = main_window
        self._engine = engine
        self._spotlight_rect: Optional[QRect] = None   # in overlay-local coords

        # Card is also a tool window (separate from this overlay)
        self._card = TutorialCard(theme, parent=main_window)
        self._card.next_clicked.connect(engine.advance)
        self._card.back_clicked.connect(engine.go_back)
        self._card.skip_clicked.connect(engine.advance)
        self._card.exit_clicked.connect(engine.cancel)

        # Wire engine signals
        engine.step_ready.connect(self._on_step_ready)
        engine.tutorial_completed.connect(lambda _: self._hide_overlay())
        engine.tutorial_cancelled.connect(lambda _: self._hide_overlay())
        engine.tutorial_failed.connect(self._on_tutorial_failed)

        # Click-through transparent paint layer
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setObjectName("TutorialOverlay")
        self.hide()

        # Track parent window moves and resizes
        main_window.installEventFilter(self)

    # ------------------------------------------------------------------
    # Engine signal handlers
    # ------------------------------------------------------------------

    def _on_step_ready(self, step: "TutorialStep", target_widget) -> None:
        self._spotlight_rect = (
            self._widget_rect_in_overlay(target_widget)
            if target_widget is not None
            else None
        )
        self._card.update_step(step, self._engine.current_step_index, self._engine.step_count)
        self._sync_overlay_geometry()
        self._position_card()
        self.show()
        self.update()
        self._card.show()
        self._card.raise_()

    def _on_tutorial_failed(self, tutorial_id: str, reason: str) -> None:
        self._hide_overlay()
        try:
            bus = getattr(self._engine, "_bus", None)
            if bus:
                bus.status_message_requested.emit(
                    f"Tutorial step unavailable: {reason[:80]}", 5000
                )
        except Exception:
            pass

    def _hide_overlay(self) -> None:
        self.hide()
        self._card.hide()
        self._spotlight_rect = None

    # ------------------------------------------------------------------
    # Geometry helpers (all positions are in screen/global coordinates)
    # ------------------------------------------------------------------

    def _mw_global_origin(self) -> QPoint:
        """Top-left of main_window in screen coordinates."""
        return self._main_window.mapToGlobal(QPoint(0, 0))

    def _sync_overlay_geometry(self) -> None:
        """Make overlay exactly cover the main window."""
        origin = self._mw_global_origin()
        self.move(origin)
        self.resize(self._main_window.size())

    def _widget_rect_in_overlay(self, widget: QWidget) -> QRect:
        """
        Return the rect of `widget` in overlay-local coordinates.
        The overlay is positioned at mw_global_origin, so:
          local = global_widget_pos - mw_global_origin
        """
        widget_global = widget.mapToGlobal(QPoint(0, 0))
        origin = self._mw_global_origin()
        return QRect(
            widget_global.x() - origin.x(),
            widget_global.y() - origin.y(),
            widget.width(),
            widget.height(),
        )

    def _position_card(self) -> None:
        """Place the card near the spotlight in screen coordinates."""
        self._card.adjustSize()
        hint = self._card.sizeHint()
        cw = max(_CARD_MIN_W, min(_CARD_MAX_W, hint.width()))
        ch = max(200, min(int(self._main_window.height() * 0.45), hint.height() + 20))

        mw = self._main_window
        win_w, win_h = mw.width(), mw.height()
        origin = self._mw_global_origin()

        if self._spotlight_rect:
            sr = self._spotlight_rect          # overlay-local coords
            cx = sr.left()
            cy = sr.bottom() + 16
            cx = min(cx, win_w - cw - 12)
            cx = max(cx, 12)
            if cy + ch > win_h - 12:
                cy = sr.top() - ch - 16
            if cy < 12:                        # no room above or below → corner
                cx = win_w - cw - 12
                cy = win_h - ch - 12
        else:
            cx = (win_w - cw) // 2
            cy = (win_h - ch) // 2

        self._card.setFixedSize(cw, ch)
        # Card is a top-level window → needs global screen coords
        self._card.move(origin.x() + cx, origin.y() + cy)

    def _refresh_spotlight(self) -> None:
        """Re-map the spotlight rect after window move/resize."""
        if self._engine.current_step:
            target = self._engine._targets.resolve(self._engine.current_step.target_id)
            if target:
                self._spotlight_rect = self._widget_rect_in_overlay(target)

    # ------------------------------------------------------------------
    # Qt overrides
    # ------------------------------------------------------------------

    def eventFilter(self, obj, event) -> bool:
        """Track main-window resize and move to keep overlay and card in sync."""
        if obj is self._main_window and self.isVisible():
            t = event.type()
            if t in (QEvent.Type.Resize, QEvent.Type.Move):
                self._sync_overlay_geometry()
                self._refresh_spotlight()
                self._position_card()
                self.update()
        return False

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        full = QPainterPath()
        full.addRect(0.0, 0.0, float(self.width()), float(self.height()))

        if self._spotlight_rect:
            p = _SPOTLIGHT_PADDING
            cutout = QPainterPath()
            cutout.addRoundedRect(
                float(self._spotlight_rect.x() - p),
                float(self._spotlight_rect.y() - p),
                float(self._spotlight_rect.width() + p * 2),
                float(self._spotlight_rect.height() + p * 2),
                6.0, 6.0,
            )
            dim_path = full.subtracted(cutout)
        else:
            dim_path = full

        painter.fillPath(dim_path, QBrush(QColor(0, 0, 0, _DIM_ALPHA)))
        painter.end()

    def apply_theme(self, theme: dict) -> None:
        self._card.apply_theme(theme)
