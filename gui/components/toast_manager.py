"""
gui/components/toast_manager.py

Slide-in toast notifications displayed in the bottom-right corner of the main window.
Triggered by EventBus.plugin_notification_requested(message, level, duration_ms).
"""
from __future__ import annotations

from typing import List, Optional, TYPE_CHECKING

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QRect, Qt, QTimer
from PySide6.QtCore import QObject
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget,
)

if TYPE_CHECKING:
    from gui.main_window import MainWindow


_LEVEL_STYLES = {
    "info":    ("2px solid #2d7dd2", "#e8f4ff", "#1a3a5c"),
    "success": ("2px solid #2d8c5a", "#e8f5ee", "#1a3c26"),
    "warning": ("2px solid #d2832d", "#fff8e8", "#5c3a10"),
    "error":   ("2px solid #cc3333", "#ffe8e8", "#5c1010"),
}


class ToastNotification(QFrame):
    HEIGHT = 56
    WIDTH = 360

    def __init__(
        self,
        message: str,
        level: str,
        duration: int,
        parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ToastNotification")
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowFlags(Qt.WindowType.SubWindow)

        border, bg, fg = _LEVEL_STYLES.get(level, _LEVEL_STYLES["info"])
        self.setStyleSheet(
            f"QFrame#ToastNotification {{"
            f"  background-color: {bg};"
            f"  border: {border};"
            f"  border-radius: 8px;"
            f"  color: {fg};"
            f"}}"
            f"QLabel {{ color: {fg}; background: transparent; border: none; }}"
            f"QPushButton {{ color: {fg}; background: transparent; border: none; font-weight: bold; }}"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 8, 8)
        layout.setSpacing(8)

        lbl = QLabel(message)
        lbl.setWordWrap(True)
        lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout.addWidget(lbl)

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(22, 22)
        close_btn.clicked.connect(self.dismiss)
        layout.addWidget(close_btn)

        self._anim: Optional[QPropertyAnimation] = None
        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setSingleShot(True)
        self._dismiss_timer.timeout.connect(self.dismiss)
        if duration > 0:
            self._dismiss_timer.start(duration)

    def show_at(self, y_pos: int) -> None:
        parent = self.parent()
        if not parent:
            return
        pw = parent.width()
        end_rect = QRect(pw - self.WIDTH - 14, y_pos, self.WIDTH, self.HEIGHT)
        start_rect = QRect(pw + 4, y_pos, self.WIDTH, self.HEIGHT)
        self.setGeometry(start_rect)
        self.raise_()
        self.show()

        self._anim = QPropertyAnimation(self, b"geometry", self)
        self._anim.setDuration(240)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.setStartValue(start_rect)
        self._anim.setEndValue(end_rect)
        self._anim.start()

    def dismiss(self) -> None:
        self._dismiss_timer.stop()
        self.close()
        self.deleteLater()


class ToastManager(QObject):
    MAX_TOASTS = 4
    GAP = 8
    BOTTOM_MARGIN = 36  # clearance above status bar

    def __init__(self, main_window: "MainWindow") -> None:
        super().__init__(main_window)
        self._main_window = main_window
        self._toasts: List[ToastNotification] = []

    def _on_notification(self, message: str, level: str, duration: int) -> None:
        if len(self._toasts) >= self.MAX_TOASTS:
            oldest = self._toasts.pop(0)
            oldest.dismiss()

        toast = ToastNotification(message, level, duration, self._main_window)
        toast.destroyed.connect(lambda _obj=None, t=toast: self._on_toast_destroyed(t))
        self._toasts.append(toast)
        self._reposition()

    def _on_toast_destroyed(self, toast: ToastNotification) -> None:
        try:
            self._toasts.remove(toast)
        except ValueError:
            pass
        self._reposition()

    def _reposition(self) -> None:
        mw = self._main_window
        base_y = mw.height() - self.BOTTOM_MARGIN - ToastNotification.HEIGHT
        for toast in reversed(self._toasts):
            try:
                if not toast.isVisible():
                    toast.show_at(base_y)
                else:
                    toast.move(mw.width() - ToastNotification.WIDTH - 14, base_y)
            except RuntimeError:
                pass
            base_y -= (ToastNotification.HEIGHT + self.GAP)

    def update_theme(self, theme: dict) -> None:
        pass  # Toast colors are semantic (level-based), not theme-derived
