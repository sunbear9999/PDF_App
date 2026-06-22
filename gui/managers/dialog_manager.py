"""
gui/managers/dialog_manager.py

Centralized dialog lifecycle and presentation management for Papyrus.

Design goals
------------
* Prevent dialogs from creating separate taskbar/dock entries.
* Prevent dialogs from revealing the OS desktop panel when Papyrus is fullscreen.
* Make ordinary dialogs modeless so the user can interact with the main window.
* Let the main window close while ordinary dialogs are still open.
* Retain strong references to modeless dialogs so they are not garbage-collected.
* Remove references automatically when a dialog is destroyed.
* Support singleton dialogs (reuse-and-focus instead of opening a duplicate).
* Provide an explicit, documented API for dialogs that must remain blocking.

Usage
-----
The manager is available as ``app_context.dialog_manager`` in every dock,
tab, and plugin.  The main window exposes it as ``self.dialog_manager``.

Open a modeless singleton (reuses if already open)::

    app_context.dialog_manager.show(
        HelpDialog,
        key="help",
        singleton=True,
        factory=lambda: HelpDialog(parent=main_window),
    )

Open a fresh modeless dialog (multiple instances allowed)::

    dialog = SomeResultsDialog(data, parent=main_window)
    app_context.dialog_manager.show_instance(dialog)

Execute a blocking modal dialog (use only where blocking is necessary)::

    result = app_context.dialog_manager.exec_modal(ConfirmDeleteDialog(parent=main_window))
    if result == QDialog.DialogCode.Accepted:
        ...

Signal-driven result handling for modeless dialogs::

    dialog = EntityEditorDialog(entity, parent=main_window)
    dialog.accepted.connect(lambda: _apply(dialog.result_entity()))
    app_context.dialog_manager.show_instance(dialog)
"""
from __future__ import annotations

import logging
from typing import Callable, Optional, Set, Type, TypeVar

from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import QApplication, QDialog, QMainWindow, QWidget

log = logging.getLogger(__name__)

T = TypeVar("T", bound=QDialog)

# ---------------------------------------------------------------------------
# Window flags applied to every modeless dialog.
#
# Qt.WindowType.Tool
#   - Marks the window as a utility/tool window (_NET_WM_WINDOW_TYPE_UTILITY
#     on X11).  Most Linux window managers skip utility windows in the taskbar
#     and do not reveal the desktop panel when one appears over a fullscreen
#     window.
#   - The window stays in front of its transient parent without being
#     system-wide always-on-top.
#   - On Windows and macOS the window also gets no taskbar button.
#
# The explicit title-bar / close-button hints are added because Qt.Tool on
# some platforms uses a reduced decoration by default; spelling them out
# preserves the full title bar users expect.
# ---------------------------------------------------------------------------
_MODELESS_FLAGS: Qt.WindowType = (
    Qt.WindowType.Tool
    | Qt.WindowType.WindowTitleHint
    | Qt.WindowType.WindowSystemMenuHint
    | Qt.WindowType.WindowCloseButtonHint
)

# Flags applied to modeless dialogs that were also resizable as QDialog
_MODELESS_FLAGS_RESIZABLE: Qt.WindowType = (
    _MODELESS_FLAGS | Qt.WindowType.WindowMaximizeButtonHint
)


def exec_as_modal(dialog: QDialog) -> int:
    """
    Module-level helper: execute *dialog* with Papyrus-standard window flags.

    This is a drop-in replacement for ``dialog.exec()`` at call sites that
    need the return value synchronously.  It applies:

    * ``Qt.Tool`` flag — no separate taskbar entry, no desktop-panel reveal.
    * ``Qt.WindowModality.WindowModal`` — blocks only the Papyrus main
      window, not the entire application.

    Usage::

        from gui.managers.dialog_manager import exec_as_modal
        ...
        if exec_as_modal(dialog) == QDialog.DialogCode.Accepted:
            apply(dialog.result)
    """
    flags = (
        Qt.WindowType.Tool
        | Qt.WindowType.WindowTitleHint
        | Qt.WindowType.WindowSystemMenuHint
        | Qt.WindowType.WindowCloseButtonHint
    )
    parent = dialog.parent()
    if not isinstance(parent, QMainWindow):
        parent = resolve_main_window(dialog)
    if parent is not None:
        # Combined setParent+flags call: Qt establishes the native XDG /
        # WM_TRANSIENT_FOR parent in a single step, preventing Wayland from
        # losing the parent link when the window handle is recreated.
        dialog.setParent(parent, flags)  # type: ignore[arg-type]
    else:
        dialog.setWindowFlags(flags)
    dialog.setWindowModality(Qt.WindowModality.WindowModal)
    return dialog.exec()


def resolve_main_window(hint: QWidget | None = None) -> QWidget | None:
    """
    Return the nearest QMainWindow ancestor of *hint*.

    Walk up the parent chain from *hint*. If no QMainWindow is found there,
    fall back to scanning top-level widgets.  As a last resort, use
    QApplication.activeWindow() (which may itself be a dialog — use the
    returned value carefully).

    :param hint: Any widget in the application, or None.
    :returns: The resolved QMainWindow, or None if the application has not
              created a main window yet.
    """
    widget: QWidget | None = hint
    while widget is not None:
        if isinstance(widget, QMainWindow):
            return widget
        parent = widget.parent()
        widget = parent if isinstance(parent, QWidget) else None

    for w in QApplication.topLevelWidgets():
        if isinstance(w, QMainWindow):
            return w

    active = QApplication.activeWindow()
    if active is not None:
        log.warning(
            "DialogManager.resolve_main_window: no QMainWindow found; "
            "using QApplication.activeWindow() as parent fallback."
        )
        return active

    log.error(
        "DialogManager.resolve_main_window: could not resolve any parent widget."
    )
    return None


def get_for_widget(hint: "QWidget | None") -> "DialogManager | None":
    """
    Return the DialogManager for any widget without a direct main-window reference.

    Callers pass *any* widget that lives in the same window (``self`` is fine).
    The lookup is done via :func:`resolve_main_window`, so no caller needs to
    know how the manager is stored on MainWindow or import MainWindow itself.

    Usage::

        from gui.managers.dialog_manager import get_for_widget

        dm = get_for_widget(self)
        if dm:
            dialog.accepted.connect(_on_accepted)
            dm.show_instance(dialog)
        else:
            if exec_as_modal(dialog):
                _on_accepted()
    """
    mw = resolve_main_window(hint)
    return getattr(mw, "dialog_manager", None)


class DialogManager(QObject):
    """
    Centralized service for opening, tracking, and closing Papyrus dialogs.

    Instantiated once by MainWindow and stored as both
    ``main_window.dialog_manager`` and ``app_context.dialog_manager``.

    Thread safety: all methods must be called from the GUI thread.
    """

    def __init__(self, main_window: QWidget, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._main_window: QWidget = main_window
        # key → open dialog instance (strong reference keeps it alive while open)
        self._keyed: dict[str, QDialog] = {}
        # open unkeyed dialog instances (strong references)
        self._unkeyed: Set[QDialog] = set()

    # ------------------------------------------------------------------ #
    # Modeless dialog API
    # ------------------------------------------------------------------ #

    def show(
        self,
        dialog_class: Type[T],
        key: str | None = None,
        singleton: bool = False,
        *,
        factory: Callable[[], T] | None = None,
        **kwargs,
    ) -> T:
        """
        Construct and open *dialog_class* modelessly.

        :param dialog_class: QDialog subclass to instantiate.
        :param key: Stable string identifier.  Required when ``singleton=True``.
        :param singleton: If True and a dialog with *key* is already open, raise
                          and focus that instance instead of creating another.
        :param factory: Optional zero-argument callable that returns the
                        constructed dialog.  Use this when the constructor
                        requires non-standard arguments:
                        ``factory=lambda: MyDialog(data, parent=main_window)``
        :param kwargs: Keyword arguments forwarded to ``dialog_class()`` when
                       *factory* is not provided.  ``parent`` defaults to the
                       main window if not supplied.
        :returns: The open dialog instance (new or existing singleton).
        """
        if key and singleton:
            existing = self._keyed.get(key)
            if existing is not None:
                try:
                    existing.raise_()
                    existing.activateWindow()
                    log.debug(
                        "DialogManager.show: reusing singleton %s (key=%r)",
                        dialog_class.__name__, key,
                    )
                    return existing  # type: ignore[return-value]
                except RuntimeError:
                    # C++ object already deleted — fall through to create fresh
                    log.debug(
                        "DialogManager.show: stale singleton for key=%r; creating new",
                        key,
                    )
                    self._keyed.pop(key, None)

        if factory is not None:
            dialog: T = factory()
        else:
            kwargs.setdefault("parent", self._main_window)
            dialog = dialog_class(**kwargs)

        return self.show_instance(dialog, key=key)  # type: ignore[return-value]

    def show_instance(self, dialog: QDialog, *, key: str | None = None) -> QDialog:
        """
        Track and display an already-constructed dialog modelessly.

        Use this when you construct the dialog yourself (non-standard args,
        pre-connected signals, etc.)::

            dialog = EntityEditorDialog(entity, parent=main_window)
            dialog.accepted.connect(on_accepted)
            dialog_manager.show_instance(dialog)

        :param dialog: Constructed but not yet shown QDialog.
        :param key: Optional stable identifier for later reuse or singleton check.
        :returns: *dialog* (for chaining).
        """
        self._apply_modeless_config(dialog)

        if key:
            self._install_keyed(key, dialog)
        else:
            self._unkeyed.add(dialog)
            # Use a default-argument capture to avoid closure over mutable set
            dialog.destroyed.connect(lambda _, d=dialog: self._unkeyed.discard(d))

        log.debug(
            "DialogManager.show_instance: opened %s modelessly (key=%r)",
            type(dialog).__name__, key,
        )
        dialog.show()
        dialog.raise_()
        # Do NOT call activateWindow() here — on Linux (GNOME Shell / Ubuntu Dock)
        # activating a new window while in fullscreen causes the desktop panel to
        # surface.  For modeless dialogs the user decides when to interact; just
        # raising the window is enough.
        return dialog

    # ------------------------------------------------------------------ #
    # Modal dialog API  (use sparingly — see docstring)
    # ------------------------------------------------------------------ #

    def exec_modal(self, dialog: QDialog) -> int:
        """
        Execute *dialog* modally using window-modal semantics.

        Window-modal blocks only the Papyrus main window, not the entire
        application.  This is preferred over the default application-modal
        behavior of QDialog.exec().

        Reserve this for:
        - Destructive confirmation prompts (delete, overwrite).
        - Unsaved-work prompts during application shutdown.
        - Situations where the next step cannot safely proceed until the
          user makes a decision.

        The caller retains ownership of the dialog and may read dialog state
        after exec() returns.  Do NOT set WA_DeleteOnClose on dialogs passed
        here — the dialog must survive until after exec() returns and the
        caller finishes reading it.

        :param dialog: Constructed QDialog.  Should be parented to the main
                       window or one of its children.
        :returns: QDialog.DialogCode (Accepted=1, Rejected=0).
        """
        flags = (
            Qt.WindowType.Tool
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        # Combined setParent+flags: establishes the Wayland XDG transient
        # parent atomically, preventing the desktop panel from surfacing.
        dialog.setParent(self._main_window, flags)  # type: ignore[arg-type]
        dialog.setWindowModality(Qt.WindowModality.WindowModal)

        log.debug(
            "DialogManager.exec_modal: executing %s as window-modal",
            type(dialog).__name__,
        )
        return dialog.exec()

    # ------------------------------------------------------------------ #
    # Application shutdown
    # ------------------------------------------------------------------ #

    def close_all(self) -> None:
        """
        Close all managed dialogs.

        Call this during application shutdown (e.g. connected to
        QApplication.instance().aboutToQuit) so modeless dialogs do not
        outlive the main window.
        """
        log.debug("DialogManager.close_all: closing %d keyed + %d unkeyed dialogs",
                  len(self._keyed), len(self._unkeyed))
        for dialog in list(self._keyed.values()):
            try:
                dialog.close()
            except RuntimeError:
                pass
        for dialog in list(self._unkeyed):
            try:
                dialog.close()
            except RuntimeError:
                pass
        self._keyed.clear()
        self._unkeyed.clear()

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _apply_modeless_config(self, dialog: QDialog) -> None:
        """Apply standard modeless presentation to *dialog* before showing."""
        old_flags = dialog.windowFlags()
        resizable = bool(old_flags & Qt.WindowType.WindowMaximizeButtonHint)
        flags = _MODELESS_FLAGS_RESIZABLE if resizable else _MODELESS_FLAGS
        # Use the combined setParent(parent, flags) overload so Qt establishes
        # the native Wayland XDG / X11 WM_TRANSIENT_FOR parent link atomically.
        # Calling setParent() and setWindowFlags() as two separate steps can
        # lose the parent link on Wayland when Qt recreates the native handle.
        dialog.setParent(self._main_window, flags)  # type: ignore[arg-type]
        dialog.setWindowModality(Qt.WindowModality.NonModal)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

    def _install_keyed(self, key: str, dialog: QDialog) -> None:
        """Replace any existing keyed entry and track the new dialog."""
        old = self._keyed.get(key)
        if old is not None:
            try:
                old.close()
            except RuntimeError:
                pass
        self._keyed[key] = dialog
        dialog.destroyed.connect(lambda _, k=key: self._keyed.pop(k, None))
