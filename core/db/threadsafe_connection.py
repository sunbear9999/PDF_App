"""Serialized access to the single project sqlite3 connection.

The project connection is opened with ``check_same_thread=False`` and shared by
the GUI thread and every background worker (intelligence stages, discovery
loaders, RAG indexing, embedding jobs).  Python's ``sqlite3`` module does not
serialize concurrent use of one connection: an ``execute`` running in a worker
while another thread calls ``commit`` or ``close`` is a use-after-free that
segfaults the whole process rather than raising.

``ThreadSafeConnection`` wraps the raw connection so every statement, fetch and
lifecycle call is taken under one re-entrant lock.  It is a transparent proxy,
so no call site changes: ``manager._conn.execute(...)``,
``manager._conn.cursor()`` and ``cursor.fetchall()`` keep working.  The lock is
owned by ``ProjectManager`` so that a close/reopen sequence (project load,
safe save) can be held atomically against in-flight worker queries.
"""
from __future__ import annotations

import sqlite3
import threading


class _LockedCursor:
    """Cursor proxy that holds the connection lock for the duration of each call."""

    def __init__(self, cursor: sqlite3.Cursor, lock: threading.RLock):
        object.__setattr__(self, "_cursor", cursor)
        object.__setattr__(self, "_lock", lock)

    def __getattr__(self, name):
        attr = getattr(self._cursor, name)
        if not callable(attr):
            return attr

        def guarded(*args, **kwargs):
            with self._lock:
                result = attr(*args, **kwargs)
            # execute() returns the cursor itself; keep the proxy in play.
            return self if result is self._cursor else result

        return guarded

    def __setattr__(self, name, value):
        setattr(self._cursor, name, value)

    def __iter__(self):
        return self

    def __next__(self):
        with self._lock:
            row = self._cursor.fetchone()
        if row is None:
            raise StopIteration
        return row


class ThreadSafeConnection:
    """Transparent ``sqlite3.Connection`` proxy that serializes every call."""

    def __init__(self, connection: sqlite3.Connection, lock: threading.RLock):
        object.__setattr__(self, "_connection", connection)
        object.__setattr__(self, "_lock", lock)

    @property
    def raw(self) -> sqlite3.Connection:
        """The underlying connection, for APIs that require the real object."""
        return self._connection

    def cursor(self, *args, **kwargs) -> _LockedCursor:
        with self._lock:
            return _LockedCursor(self._connection.cursor(*args, **kwargs), self._lock)

    def execute(self, *args, **kwargs) -> _LockedCursor:
        with self._lock:
            return _LockedCursor(self._connection.execute(*args, **kwargs), self._lock)

    def executemany(self, *args, **kwargs) -> _LockedCursor:
        with self._lock:
            return _LockedCursor(self._connection.executemany(*args, **kwargs), self._lock)

    def executescript(self, *args, **kwargs) -> _LockedCursor:
        with self._lock:
            return _LockedCursor(self._connection.executescript(*args, **kwargs), self._lock)

    def backup(self, target, *args, **kwargs):
        with self._lock:
            return self._connection.backup(getattr(target, "raw", target), *args, **kwargs)

    def __getattr__(self, name):
        attr = getattr(self._connection, name)
        if not callable(attr):
            return attr

        def guarded(*args, **kwargs):
            with self._lock:
                return attr(*args, **kwargs)

        return guarded

    def __setattr__(self, name, value):
        setattr(self._connection, name, value)

    def __enter__(self):
        self._lock.acquire()
        try:
            self._connection.__enter__()
        except BaseException:
            self._lock.release()
            raise
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            return self._connection.__exit__(exc_type, exc, tb)
        finally:
            self._lock.release()
