"""
gui/help/tutorial_overlay.py

Spotlight overlay and tutorial card for interactive tutorials.

Architecture
------------
TutorialOverlay  — embedded child QWidget of MainWindow (NOT a separate OS
                   window).  Qt renders it above all dock/content children
                   when raise_() is called.  Because it is an in-process child
                   widget, WA_TransparentForMouseEvents correctly passes all
                   pointer events through to the dock/viewer content beneath it.
                   This avoids the Wayland "separate surface" problem where two
                   xdg_toplevel surfaces with WA_TransparentForMouseEvents could
                   silently swallow clicks.

TutorialCard     — frameless Tool window parented to MainWindow.  It is the
                   ONLY separate OS surface, so the Wayland compositor routes
                   pointer events directly to it with no ambiguity.  The card
                   is positioned in screen (global) coordinates.

The overlay uses main-window-local coordinates throughout; the card converts to
global coords via mapToGlobal when placing itself.
"""
from __future__ import annotations

import logging
import re
from typing import Optional, TYPE_CHECKING

from PySide6.QtCore import QEvent, QPoint, Qt, QRect, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
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

    Created as a plain frameless Tool window (no WindowDoesNotAcceptFocus so
    mouse events are delivered normally).  Parented to MainWindow so the WM
    treats it as transient-for the main surface.
    """

    next_clicked = Signal()
    back_clicked = Signal()
    skip_clicked = Signal()
    exit_clicked = Signal()

    def __init__(self, theme: dict, parent=None) -> None:
        super().__init__(
            parent,
            Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint,
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
# Overlay (paint layer) — embedded child widget, NOT a separate OS window
# ---------------------------------------------------------------------------

class TutorialOverlay(QWidget):
    """
    Full-window dim + spotlight paint layer embedded directly in MainWindow.

    Being an ordinary child widget (no Tool/top-level flags) means:
    * WA_TransparentForMouseEvents correctly forwards pointer events to the
      dock/viewer widgets that are stacked below it — on both X11 and Wayland.
    * No new Wayland xdg_toplevel surface is created, so the GNOME dock does
      not surface when the tutorial starts.
    * raise_() places it above all other QMainWindow children reliably.

    The TutorialCard (separate Tool window) is the only new OS-level surface;
    the compositor routes its pointer events directly with no ambiguity.
    """

    def __init__(
        self,
        main_window,
        engine: "TutorialEngine",
        theme: dict,
    ) -> None:
        # NO window-type flags → embedded child widget of main_window
        super().__init__(main_window)
        self._main_window = main_window
        self._engine = engine
        self._spotlight_rect: Optional[QRect] = None   # main-window-local coords

        # Card is still a separate tool window so it receives pointer events
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

        # Transparent to mouse so the underlying docks/viewer are still usable
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setObjectName("TutorialOverlay")
        self.hide()

        main_window.installEventFilter(self)

    # ------------------------------------------------------------------
    # Engine signal handlers
    # ------------------------------------------------------------------

    def _on_step_ready(self, step: "TutorialStep", target_widget) -> None:
        self._spotlight_rect = (
            self._local_widget_rect(target_widget)
            if target_widget is not None
            else None
        )
        self._card.update_step(step, self._engine.current_step_index, self._engine.step_count)
        self._sync_overlay_geometry()
        self._position_card()
        self.show()
        self.raise_()          # above all main-window child widgets
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
    # Geometry helpers
    # ------------------------------------------------------------------

    def _sync_overlay_geometry(self) -> None:
        """Stretch overlay to fill main window using LOCAL coordinates."""
        self.setGeometry(0, 0, self._main_window.width(), self._main_window.height())

    def _local_widget_rect(self, widget: QWidget) -> QRect:
        """
        Return the rect of *widget* in main-window-local coordinates.
        We map the widget's top-left to global then back to main-window-local.
        """
        global_pos = widget.mapToGlobal(QPoint(0, 0))
        local_pos = self._main_window.mapFromGlobal(global_pos)
        return QRect(local_pos.x(), local_pos.y(), widget.width(), widget.height())

    def _position_card(self) -> None:
        """Place the card near the spotlight in screen (global) coordinates."""
        self._card.adjustSize()
        hint = self._card.sizeHint()
        cw = max(_CARD_MIN_W, min(_CARD_MAX_W, hint.width()))

        mw = self._main_window
        win_w, win_h = mw.width(), mw.height()

        # Use screen available geometry to avoid the OS taskbar
        origin = mw.mapToGlobal(QPoint(0, 0))
        screen = QApplication.screenAt(origin) or QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else None

        max_ch = int(win_h * 0.45)
        if avail:
            max_screen_ch = max(100, avail.bottom() - origin.y() - 12)
            max_ch = min(max_ch, max_screen_ch)
        ch = max(220, min(max_ch, hint.height() + 20))

        if self._spotlight_rect:
            sr = self._spotlight_rect          # local coords
            cx = sr.left()
            cy = sr.bottom() + 16
            cx = min(cx, win_w - cw - 12)
            cx = max(cx, 12)
            if cy + ch > win_h - 12:
                cy = sr.top() - ch - 16
            if cy < 12:                        # no room → bottom-right corner
                cx = win_w - cw - 12
                cy = win_h - ch - 12
        else:
            cx = (win_w - cw) // 2
            cy = (win_h - ch) // 2

        # Hard clamp within the window area
        cx = max(4, min(cx, win_w - cw - 4))
        cy = max(4, min(cy, win_h - ch - 4))

        self._card.setFixedSize(cw, ch)

        # Convert local → global for the separate card window
        card_x = origin.x() + cx
        card_y = origin.y() + cy

        # Clamp against screen available geometry (OS taskbar safety)
        if avail:
            card_x = max(avail.left() + 4, min(card_x, avail.right() - cw - 4))
            card_y = max(avail.top() + 4, min(card_y, avail.bottom() - ch - 4))

        self._card.move(card_x, card_y)

    def _refresh_spotlight(self) -> None:
        """Re-map the spotlight rect after window move/resize."""
        step = self._engine.current_step
        if step:
            target = self._engine._targets.resolve(step.target_id)
            if target:
                self._spotlight_rect = self._local_widget_rect(target)

    # ------------------------------------------------------------------
    # Qt overrides
    # ------------------------------------------------------------------

    def eventFilter(self, obj, event) -> bool:
        """Resize/reposition overlay and card when the main window changes."""
        if obj is self._main_window and self.isVisible():
            t = event.type()
            if t in (QEvent.Type.Resize, QEvent.Type.Move):
                self._sync_overlay_geometry()
                self._refresh_spotlight()
                self._position_card()
                self.raise_()
                self._card.raise_()
                self.update()
            elif t in (QEvent.Type.ChildAdded, QEvent.Type.ZOrderChange):
                # A dock/dialog opened — keep overlay and card on top
                self.raise_()
                self._card.raise_()
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
