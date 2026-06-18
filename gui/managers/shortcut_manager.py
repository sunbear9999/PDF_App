"""
gui/managers/shortcut_manager.py

Registers keyboard shortcuts with the window and with the KeybindingRegistry.

Core shortcuts (viewer zoom, save, fullscreen) are bound via KeybindingRegistry
so they appear in the Settings → Shortcuts editor and are user-editable.

Plugin shortcuts (ShortcutSpec) are also converted to KeySpec entries and
routed through the registry for the same reason.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut

if TYPE_CHECKING:
    from gui.main_window import MainWindow


class ShortcutManager:
    def __init__(self, main_window: "MainWindow") -> None:
        self._w = main_window

    # ------------------------------------------------------------------
    # Core shortcuts
    # ------------------------------------------------------------------

    def register_core_shortcuts(self) -> None:
        """Bind built-in shortcuts via KeybindingRegistry (editable, persisted)."""
        w = self._w
        kr = getattr(w, "keybinding_registry", None)
        viewer = getattr(w, "viewer", None)

        from core.events.domains.project_events import ProjectIntent, ProjectPayload

        if viewer:
            # PDF viewer zoom/search – use the viewer's own widget-scoped shortcuts
            # where they exist; also bind window-scope fallbacks so these keys work
            # even when focus is outside the PDF viewer.
            if hasattr(viewer, "_zoom_in_sc"):
                kr and kr.claim_shortcut("pdf.zoom_in",     viewer._zoom_in_sc)
            if hasattr(viewer, "_zoom_out_sc"):
                kr and kr.claim_shortcut("pdf.zoom_out",    viewer._zoom_out_sc)
            if hasattr(viewer, "copy_shortcut"):
                kr and kr.claim_shortcut("pdf.copy",        viewer.copy_shortcut)

            # Window-scope fallbacks (active even when focus is not in the viewer)
            if kr:
                kr.bind("pdf.zoom_in",    viewer.zoom_in,    w)
                kr.bind("pdf.zoom_in_alt",viewer.zoom_in,    w)
                kr.bind("pdf.zoom_out",   viewer.zoom_out,   w)
                kr.bind("pdf.zoom_reset", viewer.zoom_reset, w)
            else:
                self._raw_bind("Ctrl+=",  viewer.zoom_in)
                self._raw_bind("Ctrl++",  viewer.zoom_in)
                self._raw_bind("Ctrl+-",  viewer.zoom_out)
                self._raw_bind("Ctrl+0",  viewer.zoom_reset)

            if hasattr(viewer, "annot_manager") and kr:
                kr.bind("pdf.search", viewer.annot_manager.toggle_search, w)
            elif hasattr(viewer, "annot_manager"):
                self._raw_bind("Ctrl+F", viewer.annot_manager.toggle_search)

        # Global shortcuts
        save_cb = lambda: w.bus.project_action_requested.emit(
            ProjectIntent.SAVE, ProjectPayload()
        )
        if kr:
            kr.bind("app.save",      save_cb,               w)
            kr.bind("app.fullscreen", w.toggle_full_screen, w)
            kr.bind("app.tag_manager",    w._open_tag_manager,    w)
            kr.bind("app.prompt_editor",  w._open_prompt_editor,  w)
            kr.bind("app.settings",       w._open_settings,       w)
            kr.bind("app.help",           w.show_help_window,     w)
            kr.bind("app.help_f1",        self._help_f1_cb(),     w)
            kr.bind("app.whats_this",     self._whats_this_cb(),  w)
            kr.bind("app.add_pdf",
                    lambda: w.top_toolbar._trigger_add_pdf()
                    if hasattr(getattr(w, "top_toolbar", None), "_trigger_add_pdf") else None,
                    w)
            kr.bind("data_dock.open", lambda: w.dock_manager.spawn("data_dock"), w)
        else:
            self._raw_bind("Ctrl+S",  save_cb)
            self._raw_bind("F11",     w.toggle_full_screen)

    # ------------------------------------------------------------------
    # Plugin shortcuts
    # ------------------------------------------------------------------

    def register_plugin_shortcuts(self) -> None:
        """Convert ShortcutSpec entries to KeySpecs and bind via KeybindingRegistry."""
        ctx = getattr(self._w, "app_context", None)
        plugin_registry = getattr(ctx, "plugin_extension_registry", None)
        kr = getattr(self._w, "keybinding_registry", None)
        if not plugin_registry:
            return

        from gui.managers.keybinding_registry import KeySpec

        for spec in plugin_registry.get_shortcuts():
            if not spec.callback:
                continue
            action_id = f"plugin.{spec.shortcut_id}"
            key_spec = KeySpec(
                action_id=action_id,
                label=spec.label or spec.shortcut_id,
                scope="plugins",
                description=f"Plugin shortcut: {spec.label or spec.shortcut_id}",
                default_key=spec.key_sequence or "",
                plugin_id=getattr(spec, "plugin_id", ""),
            )
            if kr:
                kr.register(key_spec)
                try:
                    kr.bind(action_id, spec.callback, self._w)
                except Exception as exc:
                    print(f"[ShortcutManager] Failed to bind plugin shortcut '{spec.shortcut_id}': {exc}")
            else:
                # Fallback: raw QShortcut if no registry
                if spec.key_sequence:
                    try:
                        self._raw_bind(spec.key_sequence, spec.callback)
                    except Exception as exc:
                        print(f"[ShortcutManager] Failed to register shortcut '{spec.shortcut_id}': {exc}")

    # ------------------------------------------------------------------
    # Internal helper
    # ------------------------------------------------------------------

    def _help_f1_cb(self):
        from core.events.domains.help_events import HelpIntent, HelpPayload
        bus = getattr(self._w, "bus", None)
        def cb():
            if bus:
                bus.help_action_requested.emit(HelpIntent.SHOW_F1_HELP, HelpPayload())
        return cb

    def _whats_this_cb(self):
        from core.events.domains.help_events import HelpIntent, HelpPayload
        bus = getattr(self._w, "bus", None)
        def cb():
            if bus:
                bus.help_action_requested.emit(HelpIntent.SHOW_WHATS_THIS, HelpPayload())
        return cb

    def _raw_bind(self, key: str, callback) -> None:
        """Create a raw QShortcut on the main window (fallback, not in registry)."""
        sc = QShortcut(QKeySequence(key), self._w)
        sc.activated.connect(callback)
