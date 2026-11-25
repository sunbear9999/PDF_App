"""Record what the Qt thread was doing whenever the UI freezes.

A heartbeat timer on the Qt thread stamps the time; a watchdog thread notices
when the stamp goes stale and captures the main thread's stack while the
freeze is still happening, then logs the duration and the distinct stacks to
``stall.log`` in the user data folder. Cost: one 100 ms timer and a sleeping
thread.
"""
from __future__ import annotations

import os
import sys
import threading
import time
import traceback

from PySide6.QtCore import QObject, QTimer

HEARTBEAT_MS = 100
STALL_SECONDS = 0.25          # a freeze longer than this is logged
RESAMPLE_SECONDS = 0.25       # capture the stack again every 250 ms of a long freeze
WATCH_INTERVAL_S = 0.05
MAX_LOG_BYTES = 1024 * 1024
MAX_STACKS = 12
ONGOING_REPORT_SECONDS = 5.0  # write long freezes while they last (they may end in a kill)
LOG_NAME = "stall.log"


class UiStallMonitor(QObject):
    def __init__(self, user_data_dir: str, parent=None) -> None:
        super().__init__(parent)
        self._path = os.path.join(user_data_dir, LOG_NAME)
        self._main = threading.main_thread().ident
        self._beat = time.monotonic()
        self._stop = threading.Event()
        self._timer = QTimer(self)
        self._timer.setInterval(HEARTBEAT_MS)
        self._timer.timeout.connect(self._heartbeat)
        self._timer.start()
        self._thread = threading.Thread(target=self._watch, name="ui-stall-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._timer.stop()

    def _heartbeat(self) -> None:
        self._beat = time.monotonic()

    def _watch(self) -> None:
        stacks, started, next_capture, reported = [], 0.0, 0.0, False
        while not self._stop.wait(WATCH_INTERVAL_S):
            beat = self._beat
            lag = time.monotonic() - beat - HEARTBEAT_MS / 1000
            if lag >= STALL_SECONDS:
                if not stacks:
                    started, next_capture, reported = beat, 0.0, False
                if time.monotonic() >= next_capture and len(stacks) < MAX_STACKS:
                    stack = self._main_stack()
                    if stack and stack not in stacks:
                        stacks.append(stack)
                    next_capture = time.monotonic() + RESAMPLE_SECONDS
                if not reported and lag >= ONGOING_REPORT_SECONDS:
                    reported = True
                    self._write(time.monotonic() - started, stacks + self._other_threads(), ongoing=True)
            elif stacks:
                self._write(time.monotonic() - started, stacks)
                stacks = []

    def _other_threads(self):
        """What the workers are doing: a long freeze is often a lock or the GIL."""
        names = {thread.ident: thread.name for thread in threading.enumerate()}
        return [f"[thread {names.get(ident, ident)}]\n" + "".join(traceback.format_stack(frame))
                for ident, frame in sys._current_frames().items()
                if ident not in (self._main, threading.get_ident())]

    def _main_stack(self) -> str:
        frame = sys._current_frames().get(self._main)
        return "".join(traceback.format_stack(frame)) if frame is not None else ""

    def _write(self, seconds: float, stacks, ongoing: bool = False) -> None:
        try:
            if os.path.exists(self._path) and os.path.getsize(self._path) > MAX_LOG_BYTES:
                os.replace(self._path, self._path + ".1")
            with open(self._path, "a", encoding="utf-8") as handle:
                state = " (still frozen)" if ongoing else ""
                handle.write(f"\n=== UI stall {seconds:.2f}s{state} at {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
                for index, stack in enumerate(stacks, 1):
                    handle.write(f"--- stack {index} ---\n{stack}")
            print(f"[UiStallMonitor] UI froze for {seconds:.2f}s — see {self._path}")
        except OSError:
            pass
