from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMainWindow, QPushButton, QVBoxLayout, QWidget


class DesktopEditGuard(QFrame):
    """A reversible interaction shield owned entirely by the web subsystem."""
    def __init__(self, main_window: QMainWindow, release_callback):
        super().__init__(main_window)
        self._window = main_window
        self._release_callback = release_callback
        self.setObjectName("PapyrusWebEditGuard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            "#PapyrusWebEditGuard{background:rgba(12,14,20,215);border:2px solid #b366ff;}"
            "QLabel{color:white;background:transparent;}"
            "QPushButton{background:#b366ff;color:white;border:0;border-radius:7px;padding:10px 18px;font-weight:700;}"
        )
        layout = QVBoxLayout(self)
        layout.addStretch()
        card = QWidget(self)
        row = QHBoxLayout(card)
        row.addStretch()
        column = QVBoxLayout()
        title = QLabel("Papyrus is being edited from the Web App")
        title.setStyleSheet("font-size:20px;font-weight:700;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail = QLabel("")
        self.detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        button = QPushButton("Take Back Control")
        button.clicked.connect(self._release_callback)
        column.addWidget(title)
        column.addWidget(self.detail)
        column.addSpacing(12)
        column.addWidget(button, 0, Qt.AlignmentFlag.AlignCenter)
        row.addLayout(column)
        row.addStretch()
        layout.addWidget(card)
        layout.addStretch()
        main_window.installEventFilter(self)
        self.hide()

    def set_locked(self, locked: bool, device_name: str = "") -> None:
        if locked:
            self.detail.setText(f"Exclusive edit lease: {device_name or 'paired browser'}")
            self.setGeometry(self._window.rect())
            self.show()
            self.raise_()
        else:
            self.hide()

    def eventFilter(self, watched, event):
        if watched is self._window and event.type() in {QEvent.Type.Resize, QEvent.Type.Move} and self.isVisible():
            self.setGeometry(self._window.rect())
            self.raise_()
        return False
