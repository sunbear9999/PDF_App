from __future__ import annotations

from PySide6.QtCore import QSettings, QTimer, Qt
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QListWidget,
    QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from .controller import WebCompanionController
from .security import private_interfaces


class WebAppSettingsTab(QWidget):
    def __init__(self, app_context, theme=None, parent=None):
        super().__init__(parent)
        self.ctx = app_context
        self.theme = theme or {}
        self.settings = QSettings("PDFMultitool", "Workspace")
        self.controller = WebCompanionController.for_context(app_context)
        self._build()
        self.controller.status_changed.connect(self.refresh)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1500)
        self.refresh()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        title = QLabel("Local Web Companion")
        title.setStyleSheet("font-size:20px;font-weight:700;")
        layout.addWidget(title)
        warning = QLabel("Trusted private networks only. LAN HTTP traffic is not encrypted. Papyrus never enables this server automatically or exposes it through your router.")
        warning.setWordWrap(True)
        warning.setStyleSheet("background:#5b4215;color:#ffe6a3;border:1px solid #a87820;border-radius:7px;padding:10px;")
        layout.addWidget(warning)

        form = QFormLayout()
        self.interfaces = QComboBox()
        selected = self.settings.value("web/preferred_interface", "127.0.0.1")
        for item in private_interfaces():
            self.interfaces.addItem(f"{item['address']} — {item['label']}", item["address"])
            if item["address"] == selected:
                self.interfaces.setCurrentIndex(self.interfaces.count() - 1)
        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setValue(int(self.settings.value("web/preferred_port", 8765)))
        form.addRow("Network interface", self.interfaces)
        form.addRow("Port", self.port)
        layout.addLayout(form)

        buttons = QHBoxLayout()
        self.start_button = QPushButton("Start Web App")
        self.start_button.clicked.connect(self._toggle)
        self.rotate_button = QPushButton("Rotate Pairing Code")
        self.rotate_button.clicked.connect(self.controller.rotate_pairing)
        self.lan_button = QPushButton("Use Local Network")
        self.lan_button.clicked.connect(self._select_lan)
        self.open_button = QPushButton("Open on This Laptop")
        self.open_button.clicked.connect(self._open_local)
        self.test_button = QPushButton("Test Connection")
        self.test_button.clicked.connect(self._test_connection)
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.lan_button)
        buttons.addWidget(self.open_button)
        buttons.addWidget(self.test_button)
        buttons.addWidget(self.rotate_button)
        buttons.addStretch()
        layout.addLayout(buttons)

        body = QHBoxLayout()
        self.qr = QLabel()
        self.qr.setFixedSize(220, 220)
        self.qr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body.addWidget(self.qr)
        details = QVBoxLayout()
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.code = QLabel()
        self.code.setStyleSheet("font-size:28px;font-weight:800;letter-spacing:4px;")
        self.devices = QListWidget()
        self.devices.setMinimumHeight(100)
        self.revoke = QPushButton("Revoke Selected Device")
        self.revoke.clicked.connect(self._revoke_selected)
        self.take_back = QPushButton("Take Back Desktop Control")
        self.take_back.clicked.connect(self.controller.take_back_control)
        details.addWidget(self.status)
        details.addWidget(QLabel("Manual pairing code"))
        details.addWidget(self.code)
        details.addWidget(QLabel("Paired devices"))
        details.addWidget(self.devices)
        details.addWidget(self.revoke)
        details.addWidget(self.take_back)
        body.addLayout(details, 1)
        layout.addLayout(body)
        layout.addStretch()

    def _toggle(self):
        if self.controller.running:
            self.controller.stop()
            return
        try:
            host = self.interfaces.currentData()
            port = self.port.value()
            self.settings.setValue("web/preferred_interface", host)
            self.settings.setValue("web/preferred_port", port)
            self.controller.start(host, port)
        except Exception as exc:
            QMessageBox.critical(self, "Web App", str(exc))
        self.refresh()

    def refresh(self):
        data = self.controller.diagnostics()
        running = data["running"]
        self.start_button.setText("Stop Web App" if running else "Start Web App")
        self.interfaces.setEnabled(not running)
        self.port.setEnabled(not running)
        self.rotate_button.setEnabled(running)
        self.open_button.setEnabled(running)
        self.test_button.setEnabled(running)
        self.take_back.setEnabled(bool(data["lease"].get("active")))
        if running:
            scope = "This address only works on this laptop; a phone will fail to connect." if data["interface"].startswith("127.") else "Phones and laptops on the same private network can connect."
            self.status.setText(f"Running at\n{data['url']}\n\n{scope}")
        else:
            self.status.setText("Server is off. It will remain off after restarting Papyrus. Choose ‘Use Local Network’ before starting if you want to connect a phone.")
        self.code.setText(data.get("pair_code") or "—— ——")
        if running and not data["interface"].startswith("127."):
            pixmap = QPixmap()
            pixmap.loadFromData(self.controller.qr_png(), "PNG")
            self.qr.setPixmap(pixmap.scaled(210, 210, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        elif running:
            self.qr.clear()
            self.qr.setText("Laptop-only mode\n\nUse ‘Open on This Laptop’, or stop and choose ‘Use Local Network’ before scanning from a phone.")
            self.qr.setWordWrap(True)
        else:
            self.qr.clear()
            self.qr.setText("QR code appears after starting")
        self.devices.clear()
        for session in data.get("sessions", []):
            self.devices.addItem(f"{session['device_name']} — {session['remote_ip']} — {session['id']}")

    def _revoke_selected(self):
        item = self.devices.currentItem()
        if item:
            self.controller.revoke_session(item.text().rsplit(" — ", 1)[-1])
            self.refresh()

    def _select_lan(self):
        for index in range(self.interfaces.count()):
            address = str(self.interfaces.itemData(index) or "")
            if address and not address.startswith("127."):
                if self.controller.running:
                    QMessageBox.information(self, "Web App", "Stop the web app before changing its network interface.")
                    return
                self.interfaces.setCurrentIndex(index)
                self.settings.setValue("web/preferred_interface", address)
                return
        QMessageBox.warning(self, "Web App", "No private Wi-Fi or Ethernet IPv4 address was detected. Connect this laptop to the same local network as your other device, then reopen Settings.")

    def _open_local(self):
        if self.controller.running:
            QDesktopServices.openUrl(QUrl(self.controller.pairing_url))

    def _test_connection(self):
        if not self.controller.running:
            return
        try:
            import json
            import urllib.request
            with urllib.request.urlopen(self.controller.url + "api/v1/health", timeout=2) as response:
                data = json.loads(response.read().decode("utf-8"))
            if data.get("status") != "ok":
                raise RuntimeError("Unexpected health response")
            QMessageBox.information(self, "Web App", "The local web server and frontend are reachable on this laptop.")
        except Exception as exc:
            QMessageBox.critical(self, "Web App Connection Failed", f"Papyrus could not reach its own web server:\n\n{exc}")

    def apply_changes(self):
        self.settings.setValue("web/preferred_interface", self.interfaces.currentData())
        self.settings.setValue("web/preferred_port", self.port.value())
