"""One bounded reference reader per GUI context, shared by all presentation surfaces."""
import hashlib
import json
import weakref
from collections import OrderedDict, deque

from PySide6.QtCore import QObject, QTimer, QEvent
from PySide6.QtWidgets import QApplication, QAbstractScrollArea
from shiboken6 import isValid

from core.utils.bounded_cache import BoundedCache
from core.services.intelligence.reference_hydration import MAX_REFERENCE_BATCH
from gui.components.artifact_tasks import ArtifactTaskRunner

REFRESH_DELAY_MS = 80
SCAN_CHUNK = 64
CACHE_ENTRIES = 128
CACHE_BYTES = 2 * 1024 * 1024
VIEW_EVENTS = {QEvent.Type.Move, QEvent.Type.Resize, QEvent.Type.Show,
               QEvent.Type.Hide, QEvent.Type.LayoutRequest, QEvent.Type.Wheel}


def _projection_bytes(projection):
    return len(json.dumps(projection, ensure_ascii=False, default=str).encode())


def reference_hydrator(ctx):
    coordinator = getattr(ctx, 'reference_hydrator', None)
    if coordinator is None or coordinator.closed:
        coordinator = ReferenceHydrator(ctx)
        ctx.reference_hydrator = coordinator
    return coordinator


def in_viewport(widget):
    if not isValid(widget) or not widget.isVisible():
        return False
    ancestor = widget.parentWidget()
    while ancestor is not None:
        if isinstance(ancestor, QAbstractScrollArea):
            viewport = ancestor.viewport()
            rect = widget.rect()
            rect.moveTopLeft(widget.mapTo(viewport, rect.topLeft()))
            if not viewport.rect().intersects(rect):
                return False
        ancestor = ancestor.parentWidget()
    return True


class ReferenceHydrator(QObject):
    def __init__(self, ctx):
        super().__init__(QApplication.instance())
        self.ctx, self.closed = ctx, False
        self.generation = 0
        self.widgets = weakref.WeakValueDictionary()
        self.cache = BoundedCache(CACHE_ENTRIES, CACHE_BYTES, _projection_bytes)
        self.pending, self.batch = OrderedDict(), {}
        self._scan = deque()
        self._watched = weakref.WeakSet()
        self._signals = []
        self._project = self.project_identity()
        self.runner = ArtifactTaskRunner(ctx, self)
        self.runner.completed.connect(self._complete)
        self.runner.failed.connect(self._failed)
        self.runner.busy_changed.connect(self._idle)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(REFRESH_DELAY_MS)
        self.timer.timeout.connect(self._begin_scan)
        self.scan_timer = QTimer(self)
        self.scan_timer.setSingleShot(True)
        self.scan_timer.timeout.connect(self._scan_chunk)
        bus = getattr(ctx, 'bus', None)
        for name in ('intelligence_state_changed', 'source_added', 'source_renamed', 'source_removed',
                     'project_loaded', 'project_saved'):
            signal = getattr(bus, name, None)
            if signal is not None:
                signal.connect(self.invalidate)
                self._signals.append((signal, self.invalidate))
        signal = getattr(bus, 'project_clearing_started', None)
        if signal is not None:
            signal.connect(self.close_project)
            self._signals.append((signal, self.close_project))

    def project_identity(self):
        db = getattr(getattr(self.ctx, 'artifact_service', None), 'db', None)
        return id(db), id(getattr(db, '_conn', None))

    def subscribe(self, widget):
        token = id(widget)
        self.widgets[token] = widget
        widget.destroyed.connect(lambda *_, key=token: self.unsubscribe(key))
        self.watch_viewport(widget)

    def unsubscribe(self, token):
        self.widgets.pop(token, None)
        if not self.widgets:
            self.shutdown()

    def watch_viewport(self, widget):
        ancestor = widget.parentWidget()
        while ancestor is not None:
            if isinstance(ancestor, QAbstractScrollArea):
                for target in (ancestor.viewport(), ancestor.widget() if hasattr(ancestor, 'widget') else None):
                    if target is not None and target not in self._watched:
                        self._watched.add(target)
                        target.installEventFilter(self)
            ancestor = ancestor.parentWidget()

    def eventFilter(self, watched, event):
        if event.type() in VIEW_EVENTS:
            self.schedule()
        return False

    def schedule(self):
        if not self.closed and not self.timer.isActive():
            self.timer.start()

    def _begin_scan(self):
        if self.closed:
            return
        if self.project_identity() != self._project:
            self.invalidate()
        self._scan = deque(weakref.ref(widget) for widget in self.widgets.values())
        self._scan_chunk()

    def _scan_chunk(self):
        if self.closed:
            return
        for _ in range(min(SCAN_CHUNK, len(self._scan))):
            widget = self._scan.popleft()()
            if (widget is None or not in_viewport(widget) or widget._closed
                    or not widget.reference.artifact_id):
                continue
            if self.is_current(widget):
                continue
            widget._reference_dirty()
            self.watch_viewport(widget)
            key = self.key(widget.reference)
            if key in self.cache:
                projection = self.cache.get(key)
                widget._reference_ready(projection)
            elif key in self.batch:
                self.batch[key][1].add(id(widget))
            else:
                entry = self.pending.setdefault(key, (widget.reference, set()))
                entry[1].add(id(widget))
        if self._scan:
            self.scan_timer.start(0)
        self._pump()

    @staticmethod
    def key(reference):
        raw = json.dumps(reference.to_dict(), ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()

    def refresh(self, widget):
        self._evict(self.key(widget.reference))
        widget._reference_dirty()
        self.schedule()

    def is_current(self, widget):
        return (not self.closed and not widget._closed and widget._loaded
                and widget._hydration_generation == self.generation
                and self.project_identity() == self._project)

    def _pump(self):
        if self.closed or self.runner.busy or not self.pending:
            return
        self.batch = {}
        while self.pending and len(self.batch) < MAX_REFERENCE_BATCH:
            key, (reference, tokens) = self.pending.popitem(last=False)
            tokens = {token for token in tokens if (widget := self.widgets.get(token)) is not None
                      and not widget._closed and not self.is_current(widget) and in_viewport(widget)}
            if tokens:
                self.batch[key] = (reference, tokens)
        if self.batch and not self.runner.start('resolve_references',
                references=[ref.to_dict() for ref, _ in self.batch.values()], compact=True):
            self._failed('Open an intelligence-enabled project first.')

    def _complete(self, operation):
        if self.closed or operation != 'resolve_references':
            return
        if self.project_identity() != self._project:
            self.invalidate()
            return
        results, self.runner.result = self.runner.result or [], None
        for (key, (_, tokens)), projection in zip(self.batch.items(), results):
            self._cache(key, projection)
            for token in tokens:
                widget = self.widgets.get(token)
                if widget is not None and not widget._closed and in_viewport(widget) and self.key(widget.reference) == key:
                    widget._reference_ready(projection)
        self.batch.clear()
        self.schedule()

    def _failed(self, error):
        for key, (_, tokens) in self.batch.items():
            for token in tokens:
                widget = self.widgets.get(token)
                if widget is not None and not widget._closed and self.key(widget.reference) == key:
                    widget._failed(error)
        self.batch.clear()

    def _idle(self, busy):
        if not busy:
            if not self.closed and self.project_identity() != self._project:
                self.invalidate()
            self._pump()

    @property
    def cache_bytes(self):
        return self.cache.bytes

    def _evict(self, key):
        self.cache.pop(key)

    def _cache(self, key, projection):
        self.cache.set(key, projection)

    def invalidate(self, *_):
        self.generation += 1
        self._project = self.project_identity()
        self.runner.cancel()
        self.cache.clear()
        self.pending.clear()
        self.batch.clear()
        # Hidden cards are dirtied lazily. Every action also checks this generation,
        # so it cannot use stale data while the bounded viewport scan is pending.
        self.schedule()

    def close_project(self, *_):
        self.closed = True
        self.timer.stop()
        self.scan_timer.stop()
        self._scan.clear()
        self.invalidate()
        for widget in self.widgets.values():
            if isValid(widget):
                widget._project_closed()
        self.runner.shutdown()
        self._disconnect()

    def _disconnect(self):
        for signal, slot in self._signals:
            signal.disconnect(slot)
        self._signals.clear()

    def shutdown(self):
        self.close_project()
        for target in self._watched:
            if isValid(target):
                target.removeEventFilter(self)
        if getattr(self.ctx, 'reference_hydrator', None) is self:
            self.ctx.reference_hydrator = None
        self.deleteLater()
