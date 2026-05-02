# core/first_launch.py
import shutil
import subprocess
import urllib.request
import webbrowser

from PySide6.QtCore import QProcess, QTimer, Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QPlainTextEdit
)


def is_ollama_reachable() -> bool:
    try:
        urllib.request.urlopen("http://127.0.0.1:11434/", timeout=2)
        return True
    except Exception:
        return False


def _brew_path() -> str | None:
    found = shutil.which("brew")
    if found:
        return found
    for candidate in ("/opt/homebrew/bin/brew", "/usr/local/bin/brew"):
        if shutil.which(candidate) or __import__("os").path.isfile(candidate):
            return candidate
    return None


def try_launch_existing_ollama() -> bool:
    try:
        subprocess.Popen(["open", "-a", "Ollama"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        return False

    import time
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if is_ollama_reachable():
            return True
        time.sleep(0.5)
    return False


class OllamaSetupDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Ollama Setup")
        self.setModal(True)
        self.setFixedSize(500, 350)

        self._brew = _brew_path()
        self._poll_count = 0
        self._proc = None

        layout = QVBoxLayout(self)

        label = QLabel(
            "Papyrus Research uses Ollama to run local AI models.\n"
            "Ollama isn't reachable on this Mac. Install it now via Homebrew?"
        )
        label.setWordWrap(True)
        layout.addWidget(label)

        self._output = QPlainTextEdit()
        self._output.setReadOnly(True)
        font = self._output.font()
        font.setFamily("Menlo")
        font.setPointSize(11)
        self._output.setFont(font)
        self._output.setMinimumHeight(180)
        layout.addWidget(self._output)

        btn_row = QHBoxLayout()
        layout.addLayout(btn_row)

        if self._brew:
            self._primary_btn = QPushButton("Install via Homebrew")
            self._primary_btn.clicked.connect(self._start_install)
        else:
            self._primary_btn = QPushButton("Open Homebrew website")
            self._primary_btn.clicked.connect(self._open_brew_site)

        self._skip_btn = QPushButton("Skip for now")
        self._skip_btn.clicked.connect(self.accept)

        self._continue_btn = QPushButton("Continue")
        self._continue_btn.clicked.connect(self.accept)
        self._continue_btn.setVisible(False)

        btn_row.addWidget(self._primary_btn)
        btn_row.addWidget(self._skip_btn)
        btn_row.addWidget(self._continue_btn)

    def _open_brew_site(self):
        webbrowser.open("https://brew.sh")

    def _start_install(self):
        self._primary_btn.setEnabled(False)
        self._skip_btn.setEnabled(False)
        self._output.appendPlainText(f"Running: {self._brew} install ollama\n")

        self._proc = QProcess(self)
        self._proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._proc.readyReadStandardOutput.connect(self._on_output)
        self._proc.finished.connect(self._on_finished)
        self._proc.start(self._brew, ["install", "ollama"])

    def _on_output(self):
        raw = bytes(self._proc.readAllStandardOutput()).decode("utf-8", errors="replace")
        self._output.appendPlainText(raw.rstrip())

    def _on_finished(self, exit_code, exit_status):
        if exit_code == 0:
            self._output.appendPlainText("\nInstall finished. Launching Ollama daemon...")
            try:
                subprocess.Popen(["open", "-a", "Ollama"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass
            self._poll_count = 0
            QTimer.singleShot(1000, self._poll_daemon)
        else:
            self._output.appendPlainText(f"\nInstall failed (exit code {exit_code}).")
            self._skip_btn.setEnabled(True)

    def _poll_daemon(self):
        if is_ollama_reachable():
            self._output.appendPlainText("Ollama is running. You're all set!")
            self._continue_btn.setVisible(True)
            return

        self._poll_count += 1
        if self._poll_count >= 15:
            self._output.appendPlainText(
                "Ollama didn't start in time. You can start it manually and relaunch."
            )
            self._continue_btn.setVisible(True)
            return

        self._output.appendPlainText(f"Waiting for Ollama daemon... ({self._poll_count}/15)")
        QTimer.singleShot(1000, self._poll_daemon)


def run_first_launch_check(parent=None):
    if is_ollama_reachable():
        return
    if try_launch_existing_ollama():
        return
    dlg = OllamaSetupDialog(parent)
    dlg.exec()
