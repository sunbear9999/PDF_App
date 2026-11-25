"""Reusable "jump to source" controller for any artifact-listing surface.

Resolves an artifact through the shared ``SourceAnchorResolver`` on a worker
thread (resolution can parse a PDF page), then hands the typed target to
``SourceViewerHost.navigate_to_target`` and reports the precision through a
status callback. Docks, note cards and workspace nodes can all reuse it so
every surface jumps the same way.
"""
from __future__ import annotations

from typing import Callable, List

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import QApplication, QLabel

from gui.components.intelligence_view_builders import (
    NAV_STATUS_RESOLVING, navigation_status_text,
)


class _SourceNavigationWorker(QThread):
    resolved = Signal(object)
    failed = Signal(str)

    def __init__(self, resolve, generation, parent=None):
        super().__init__(parent)
        self._resolve = resolve
        self.generation = generation

    def run(self):
        try:
            self.resolved.emit(self._resolve().to_dict())
        except Exception as exc:
            self.failed.emit(str(exc))


class ArtifactSourceNavigator(QObject):
    def __init__(self, app_context, report: Callable[[str], None], parent=None):
        super().__init__(parent)
        self._ctx = app_context
        self._report = report
        self._workers: List[_SourceNavigationWorker] = []
        self._closing = False
        # Results resolved for a previous project must not navigate the next.
        self._generation = 0
        self._bus = getattr(app_context, "bus", None)
        if self._bus is not None and hasattr(self._bus, "project_clearing_started"):
            self._bus.project_clearing_started.connect(self._on_project_closing)

    @staticmethod
    def status_label(parent=None) -> QLabel:
        """Accessible, wrapping label for the navigator's precision reports."""
        label = QLabel("", parent)
        label.setWordWrap(True)
        label.setAccessibleName("Source location status")
        return label

    def navigate(self, artifact_id: str) -> bool:
        resolver = getattr(self._ctx, "source_anchor_resolver", None)
        if not artifact_id or not resolver:
            return False
        return self._start(lambda: resolver.resolve(artifact_id), None)

    def navigate_quote(self, hint: dict, quote: str, fallback: Callable[[], None]) -> bool:
        """Resolve a free-text quote; run ``fallback`` (text search) only when
        it cannot be placed in exactly one recorded passage."""
        resolver = getattr(self._ctx, "source_anchor_resolver", None)
        if not resolver or not hasattr(resolver, "resolve_quote"):
            return False
        return self._start(lambda: resolver.resolve_quote(hint, quote), fallback)

    def _start(self, resolve, fallback) -> bool:
        if self._closing:
            return False
        self._report(NAV_STATUS_RESOLVING)
        # Parented to the application so a closing dock never destroys a
        # running thread; the finished signal releases it.
        worker = _SourceNavigationWorker(resolve, self._generation, QApplication.instance())
        worker.resolved.connect(lambda target, w=worker: self._navigate_resolved(target, w, fallback))
        worker.failed.connect(lambda error, w=worker: self._failed(error, w, fallback))
        worker.finished.connect(lambda: self._workers.remove(worker))
        worker.finished.connect(worker.deleteLater)
        self._workers.append(worker)
        worker.start()
        return True

    def shutdown(self) -> None:
        if self._closing:
            return
        self._closing = True
        if self._bus is not None and hasattr(self._bus, "project_clearing_started"):
            try:
                self._bus.project_clearing_started.disconnect(self._on_project_closing)
            except (RuntimeError, TypeError):
                pass

    def _on_project_closing(self, *_args) -> None:
        self._generation += 1

    def _current(self, worker) -> bool:
        return not self._closing and (worker is None or worker.generation == self._generation)

    def _navigate_resolved(self, target: dict, worker=None, fallback=None) -> None:
        if not self._current(worker):
            return
        viewer = getattr(self._ctx, "viewer", None)
        if target.get("navigable") and viewer and hasattr(viewer, "navigate_to_target"):
            self._report(navigation_status_text(target))
            viewer.navigate_to_target(target)
        elif fallback is not None:
            self._report(FALLBACK_STATUS)
            fallback()
        else:
            self._report(navigation_status_text(target))

    def _failed(self, error: str, worker=None, fallback=None) -> None:
        if not self._current(worker):
            return
        if fallback is not None:
            fallback()
            return
        self._report(f"Could not locate source: {error}")


FALLBACK_STATUS = "No recorded location matched exactly; searching the text instead."
