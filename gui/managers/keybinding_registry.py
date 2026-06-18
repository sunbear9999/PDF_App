"""
gui/managers/keybinding_registry.py

Manages keyboard shortcut bindings across the entire application.

This is distinct from gui/registry/action_spec.ActionRegistry, which handles
mount-point routing (toolbar, context menus, command palette). This module
is specifically about keyboard shortcuts: what keys trigger what, persistence
of user overrides, and live QShortcut management.

Key concepts
------------
KeySpec   – Metadata for one bindable action (scope, label, description,
            default key). No callback – supplied at bind()/claim_shortcut()
            time so specs can be registered before their widgets exist.

scope     – Groups actions for the settings editor:
              "global"      active anywhere in the window
              "pdf_viewer"  active when PDF viewer has focus
              "workspace"   active when workspace canvas has focus
              "research"    research assistant dock
              "notes"       notes dock
              "citations"   citations dock
              "dictionary"  dictionary dock
              "ocr"         OCR dock
              "tts"         audio / TTS dock
              "writing"     essay / writing dock
              "plugins"     plugin-contributed shortcuts
            Custom scope strings are supported; the editor picks them up.

KeybindingRegistry – Stores KeySpecs, manages QShortcut objects, persists
                     user overrides to ~/.papyrus_data/keybindings.json.
"""

from __future__ import annotations

import json
import os
import threading
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QWidget

# ------------------------------------------------------------------
# Scope metadata
# ------------------------------------------------------------------

BUILTIN_SCOPE_ORDER: List[str] = [
    "global",
    "pdf_viewer",
    "workspace",
    "research",
    "notes",
    "citations",
    "writing",
    "dictionary",
    "ocr",
    "data_dock",
    "tts",
    "plugins",
]

SCOPE_LABELS: Dict[str, str] = {
    "global":     "Global",
    "pdf_viewer": "PDF Viewer",
    "workspace":  "Workspace",
    "research":   "Research Assistant",
    "notes":      "Notes",
    "citations":  "Citations",
    "writing":    "Writing",
    "dictionary": "Dictionary",
    "ocr":        "OCR Scanner",
    "data_dock":  "Data Dock",
    "tts":        "Audio / TTS",
    "plugins":    "Plugins",
}

SCOPE_ICONS: Dict[str, str] = {
    "global":     "🌐",
    "pdf_viewer": "📄",
    "workspace":  "🕸️",
    "research":   "🔬",
    "notes":      "📝",
    "citations":  "📚",
    "writing":    "✍️",
    "dictionary": "📖",
    "ocr":        "👁️",
    "data_dock":  "▦",
    "tts":        "🔊",
    "plugins":    "🔌",
}


# ------------------------------------------------------------------
# KeySpec
# ------------------------------------------------------------------

@dataclass
class KeySpec:
    """Metadata for one bindable keyboard action. No callback field."""
    action_id: str      # unique  e.g. "app.save", "pdf.zoom_in"
    label: str          # display e.g. "Save Project"
    scope: str          # scope   e.g. "global", "pdf_viewer"
    description: str    # one-line description for the editor
    default_key: str    # e.g. "Ctrl+S"  –  "" means unbound by default
    plugin_id: str = "" # non-empty for plugin-contributed specs

    @property
    def scope_label(self) -> str:
        return SCOPE_LABELS.get(self.scope, self.scope.replace("_", " ").title())

    @property
    def scope_icon(self) -> str:
        return SCOPE_ICONS.get(self.scope, "🔧")


# ------------------------------------------------------------------
# KeybindingRegistry
# ------------------------------------------------------------------

class KeybindingRegistry(QObject):
    """
    Central shortcut registry.

    Lifecycle
    ---------
    1. Register KeySpecs early (no widgets needed).
    2. When a widget is ready, call bind() or claim_shortcut() to attach
       live QShortcut objects.
    3. User edits in the settings dialog call set_override() which updates
       all attached QShortcuts for that action_id and persists to JSON.
    """

    shortcuts_changed = Signal()

    def __init__(self, user_data_dir: str, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._path = os.path.join(user_data_dir, "keybindings.json")
        self._specs: Dict[str, KeySpec] = {}
        # One action may have multiple QShortcuts (widget-scoped + window-scoped).
        self._shortcuts: Dict[str, List[QShortcut]] = defaultdict(list)
        self._overrides: Dict[str, str] = {}
        self._lock = threading.Lock()
        self._load_overrides()

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, spec: KeySpec) -> None:
        if spec.action_id in self._specs:
            print(f"[KeybindingRegistry] Duplicate action_id '{spec.action_id}' – overwriting")
        self._specs[spec.action_id] = spec

    def register_many(self, specs: List[KeySpec]) -> None:
        for spec in specs:
            self.register(spec)

    # ------------------------------------------------------------------
    # Shortcut installation
    # ------------------------------------------------------------------

    def bind(
        self,
        action_id: str,
        callback: Callable,
        parent: QWidget,
        context: Qt.ShortcutContext = Qt.ShortcutContext.WindowShortcut,
    ) -> Optional[QShortcut]:
        """
        Create a QShortcut for *action_id* on *parent*.

        Actions without a current key still get a live QShortcut with an empty
        sequence, so later settings changes can take effect immediately.
        """
        spec = self._specs.get(action_id)
        if not spec:
            print(f"[KeybindingRegistry] bind() called for unknown action_id '{action_id}'")
            return None
        effective_key = self.get_effective_key(action_id)
        sc = QShortcut(QKeySequence(effective_key), parent, context=context)
        sc.activated.connect(callback)
        self._shortcuts[action_id].append(sc)
        return sc

    def claim_shortcut(self, action_id: str, shortcut: QShortcut) -> None:
        """
        Register an existing QShortcut so the registry can update its key
        when the user rebinds.  Applies any saved override immediately.
        """
        self._shortcuts[action_id].append(shortcut)
        effective_key = self.get_effective_key(action_id)
        spec = self._specs.get(action_id)
        if spec and effective_key and effective_key != spec.default_key:
            try:
                shortcut.setKey(QKeySequence(effective_key))
            except RuntimeError:
                pass

    # ------------------------------------------------------------------
    # Override management
    # ------------------------------------------------------------------

    def get_effective_key(self, action_id: str) -> str:
        override = self._overrides.get(action_id)
        if override is not None:
            return override
        spec = self._specs.get(action_id)
        return spec.default_key if spec else ""

    def set_override(self, action_id: str, key_str: str) -> None:
        self._overrides[action_id] = key_str
        self._save_overrides()
        for sc in list(self._shortcuts.get(action_id, [])):
            try:
                sc.setKey(QKeySequence(key_str))
            except RuntimeError:
                pass
        self.shortcuts_changed.emit()

    def clear_override(self, action_id: str) -> None:
        if action_id not in self._overrides:
            return
        del self._overrides[action_id]
        self._save_overrides()
        spec = self._specs.get(action_id)
        default = spec.default_key if spec else ""
        for sc in list(self._shortcuts.get(action_id, [])):
            try:
                sc.setKey(QKeySequence(default))
            except RuntimeError:
                pass
        self.shortcuts_changed.emit()

    def reset_all(self) -> None:
        self._overrides.clear()
        self._save_overrides()
        for action_id, shortcuts in self._shortcuts.items():
            spec = self._specs.get(action_id)
            default = spec.default_key if spec else ""
            for sc in shortcuts:
                try:
                    sc.setKey(QKeySequence(default))
                except RuntimeError:
                    pass
        self.shortcuts_changed.emit()

    def has_override(self, action_id: str) -> bool:
        return action_id in self._overrides

    # ------------------------------------------------------------------
    # Conflict detection
    # ------------------------------------------------------------------

    def find_conflicts(
        self,
        key_str: str,
        exclude_id: str = "",
        scope: str = "",
    ) -> List[KeySpec]:
        """Return KeySpecs whose effective key equals key_str (same-scope only if scope given)."""
        if not key_str:
            return []
        results = []
        for aid, spec in self._specs.items():
            if aid == exclude_id:
                continue
            if scope and spec.scope != scope:
                continue
            if self.get_effective_key(aid) == key_str:
                results.append(spec)
        return results

    # ------------------------------------------------------------------
    # Query helpers for the settings editor
    # ------------------------------------------------------------------

    def get_all_specs(self) -> List[KeySpec]:
        return sorted(
            self._specs.values(),
            key=lambda s: (_scope_sort_key(s.scope), s.label.lower()),
        )

    def get_scopes(self) -> List[Tuple[str, str]]:
        """Return (scope, scope_label) pairs in display order."""
        present: Dict[str, str] = {}
        for spec in self._specs.values():
            present[spec.scope] = spec.scope_label
        ordered: List[Tuple[str, str]] = []
        for scope in BUILTIN_SCOPE_ORDER:
            if scope in present:
                ordered.append((scope, present[scope]))
        for scope, label in sorted(present.items()):
            if scope not in BUILTIN_SCOPE_ORDER:
                ordered.append((scope, label))
        return ordered

    def get_specs_for_scope(self, scope: str) -> List[KeySpec]:
        return sorted(
            [s for s in self._specs.values() if s.scope == scope],
            key=lambda s: s.label.lower(),
        )

    # ------------------------------------------------------------------
    # Plugin hot-reload support
    # ------------------------------------------------------------------

    def unregister_plugin(self, plugin_id: str) -> None:
        to_remove = [
            aid for aid, spec in self._specs.items()
            if spec.plugin_id == plugin_id
        ]
        for aid in to_remove:
            del self._specs[aid]
            for sc in self._shortcuts.pop(aid, []):
                try:
                    sc.setParent(None)
                    sc.deleteLater()
                except RuntimeError:
                    pass

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load_overrides(self) -> None:
        if not os.path.exists(self._path):
            return
        with self._lock:
            try:
                with open(self._path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, dict):
                    self._overrides = data
            except (json.JSONDecodeError, OSError):
                self._overrides = {}

    def _save_overrides(self) -> None:
        with self._lock:
            parent_dir = os.path.dirname(self._path)
            if parent_dir:
                os.makedirs(parent_dir, exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as fh:
                json.dump(self._overrides, fh, indent=2)


# ------------------------------------------------------------------
# Scope sort helper
# ------------------------------------------------------------------

def _scope_sort_key(scope: str) -> Tuple[int, str]:
    try:
        return (BUILTIN_SCOPE_ORDER.index(scope), "")
    except ValueError:
        return (len(BUILTIN_SCOPE_ORDER), scope)


# ------------------------------------------------------------------
# Built-in action catalog  (single source of truth)
# ------------------------------------------------------------------

def build_builtin_key_specs() -> List[KeySpec]:
    """
    Return KeySpecs for every built-in action.
    Add new entries here; no other file needs changing.
    Callbacks are NOT included – they are supplied at bind()/claim_shortcut() time.
    """
    return [
        # ── Global ────────────────────────────────────────────────────────────
        KeySpec("app.save",            "Save Project",        "global",
                "Save the current project to disk",               "Ctrl+S"),
        KeySpec("app.command_palette", "Command Palette",     "global",
                "Open the searchable command palette",            "Ctrl+P"),
        KeySpec("app.fullscreen",      "Toggle Full Screen",  "global",
                "Enter or exit full-screen mode",                 "F11"),
        KeySpec("app.add_pdf",         "Add PDF",             "global",
                "Add a PDF file to the current project",          "Ctrl+I"),
        KeySpec("app.tag_manager",     "Tag Manager",         "global",
                "Open the tag management dialog",                 ""),
        KeySpec("app.prompt_editor",   "Prompt Editor",       "global",
                "Open the AI prompt editor",                      ""),
        KeySpec("app.settings",        "Settings",            "global",
                "Open global application settings",               ""),
        KeySpec("app.help",            "Show Help",           "global",
                "Open the help / getting-started guide",          ""),
        KeySpec("app.help_f1",         "Context Help (F1)",   "global",
                "Open help for the currently focused control",    "F1"),
        KeySpec("app.whats_this",      "What's This?",        "global",
                "Click a control to see its help topic",          "Shift+F1"),

        # ── PDF Viewer ────────────────────────────────────────────────────────
        KeySpec("pdf.zoom_in",      "Zoom In",           "pdf_viewer",
                "Increase the zoom level of the PDF viewer",      "Ctrl+="),
        KeySpec("pdf.zoom_in_alt",  "Zoom In (+ key)",   "pdf_viewer",
                "Increase zoom via the plus key",                 "Ctrl++"),
        KeySpec("pdf.zoom_out",     "Zoom Out",          "pdf_viewer",
                "Decrease the zoom level of the PDF viewer",      "Ctrl+-"),
        KeySpec("pdf.zoom_reset",   "Zoom Reset",        "pdf_viewer",
                "Reset zoom to fit-width",                        "Ctrl+0"),
        KeySpec("pdf.search",       "Toggle Search Bar", "pdf_viewer",
                "Show or hide the in-document text search bar",   "Ctrl+F"),
        KeySpec("pdf.copy",         "Copy Selection",    "pdf_viewer",
                "Copy the selected text to the clipboard",        "Ctrl+C"),

        # ── Workspace ─────────────────────────────────────────────────────────
        KeySpec("workspace.copy",             "Copy",              "workspace",
                "Copy selected nodes to clipboard",               "Ctrl+C"),
        KeySpec("workspace.cut",              "Cut",               "workspace",
                "Cut selected nodes to clipboard",                "Ctrl+X"),
        KeySpec("workspace.paste",            "Paste",             "workspace",
                "Paste nodes from clipboard",                     "Ctrl+V"),
        KeySpec("workspace.new_node",         "New Node",          "workspace",
                "Create a new node on the canvas",                "Ctrl+N"),
        KeySpec("workspace.refresh",          "Refresh View",      "workspace",
                "Reload and refresh the workspace canvas",        "Ctrl+R"),
        KeySpec("workspace.clear_filters",    "Clear Filters",     "workspace",
                "Remove all active tag and search filters",       "Ctrl+W"),
        KeySpec("workspace.declutter",        "Declutter Layout",  "workspace",
                "Auto-arrange nodes to reduce overlap",           "Ctrl+D"),
        KeySpec("workspace.undo",             "Undo",              "workspace",
                "Undo the last workspace change",                 "Ctrl+Z"),
        KeySpec("workspace.redo",             "Redo",              "workspace",
                "Redo the last undone workspace change",          "Ctrl+Y"),
        KeySpec("workspace.recenter",         "Recenter View",     "workspace",
                "Pan and zoom to fit all nodes in view",          ""),
        KeySpec("workspace.export",           "Export Workspace",  "workspace",
                "Export the workspace graph to a file",           ""),
        KeySpec("workspace.delete_selection", "Delete Selection",  "workspace",
                "Delete all currently selected nodes/edges",      "Delete"),

        # ── Data Dock ────────────────────────────────────────────────────────
        KeySpec("data_dock.open",           "Open Data Dock",      "data_dock",
                "Open the Data Dock",                           ""),
        KeySpec("data_dock.save_dataset",   "Save Dataset",       "data_dock",
                "Save the active dataset to the project",        "Ctrl+Shift+S"),
        KeySpec("data_dock.clear_grid",     "Clear Grid",         "data_dock",
                "Clear the active grid values",                  ""),
        KeySpec("data_dock.generate_chart", "Generate Chart",     "data_dock",
                "Refresh the chart preview",                     ""),

        # ── Research Assistant ────────────────────────────────────────────────
        KeySpec("research.send",          "Send Message",      "research",
                "Submit the message in the chat input",           ""),
        KeySpec("research.clear_history", "Clear History",     "research",
                "Clear all messages in the current chat",         ""),

        # ── Notes ─────────────────────────────────────────────────────────────
        KeySpec("notes.new",    "New Note",    "notes",
                "Create a new note",                              ""),
        KeySpec("notes.search", "Search Notes","notes",
                "Focus the notes search field",                   ""),

        # ── Citations ─────────────────────────────────────────────────────────
        KeySpec("citations.refresh",     "Refresh Citations",    "citations",
                "Reload citations from the project database",     ""),
        KeySpec("citations.works_cited", "Generate Works Cited", "citations",
                "Generate a formatted works-cited list",          ""),

        # ── Writing ───────────────────────────────────────────────────────────
        KeySpec("writing.new",  "New Essay",  "writing",
                "Create a new essay document",                    ""),
        KeySpec("writing.save", "Save Essay", "writing",
                "Save the current essay to the project",          ""),

        # ── Dictionary ────────────────────────────────────────────────────────
        KeySpec("dictionary.search", "Search Dictionary", "dictionary",
                "Focus the dictionary search field",              ""),

        # ── OCR ───────────────────────────────────────────────────────────────
        KeySpec("ocr.run", "Run OCR", "ocr",
                "Run OCR on the current page",                    ""),

        # ── Audio / TTS ───────────────────────────────────────────────────────
        KeySpec("tts.extract", "Extract Text to Audio", "tts",
                "Extract page text and prepare for text-to-speech", ""),
    ]
