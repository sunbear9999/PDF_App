"""
core/help/progress_store.py

Persists tutorial completion status across restarts using QSettings.
Stored under: PDFMultitool / Workspace / help/tutorial_progress/<tutorial_id>
"""
from __future__ import annotations

from PySide6.QtCore import QSettings

_ORG = "PDFMultitool"
_APP = "Workspace"
_PREFIX = "help/tutorial_progress/"


class ProgressStore:
    """
    Tracks which tutorials the user has completed.

    Uses the same QSettings org/app as the rest of Papyrus so all user
    state lives in one place.
    """

    def __init__(self) -> None:
        self._settings = QSettings(_ORG, _APP)

    def mark_completed(self, tutorial_id: str) -> None:
        self._settings.setValue(f"{_PREFIX}{tutorial_id}", True)
        self._settings.sync()

    def is_completed(self, tutorial_id: str) -> bool:
        return self._settings.value(f"{_PREFIX}{tutorial_id}", False, type=bool)

    def get_all_completed(self) -> list[str]:
        self._settings.beginGroup("help/tutorial_progress")
        ids = list(self._settings.childKeys())
        self._settings.endGroup()
        return ids

    def reset_tutorial(self, tutorial_id: str) -> None:
        self._settings.remove(f"{_PREFIX}{tutorial_id}")
        self._settings.sync()

    def reset_all(self) -> None:
        self._settings.beginGroup("help/tutorial_progress")
        self._settings.remove("")
        self._settings.endGroup()
        self._settings.sync()
