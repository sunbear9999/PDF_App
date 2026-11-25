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
    "video_player",
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
    "video_player": "Video Player",
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
    "video_player": "Video",
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
        existing = self._specs.get(spec.action_id)
        if existing == spec:
            return
        if existing is not None:
            print(f"[KeybindingRegistry] Duplicate action_id '{spec.action_id}' – overwriting")
        self._specs[spec.action_id] = spec

    def get_spec(self, action_id: str) -> Optional[KeySpec]:
        return self._specs.get(action_id)

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
        sc.destroyed.connect(lambda *_: self._forget_shortcut(action_id, sc))
        return sc

    def _forget_shortcut(self, action_id: str, shortcut: QShortcut) -> None:
        shortcuts = self._shortcuts.get(action_id, [])
        if shortcut in shortcuts:
            shortcuts.remove(shortcut)

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
        KeySpec("viewer.link_back", "Back to reading position", "pdf_viewer", "Return from a reading link", "Alt+Left"),
        KeySpec("viewer.toggle_smart_links", "Show smart links", "pdf_viewer", "Toggle note, reference and contents links", ""),
        KeySpec("viewer.open_link_preview", "Preview reading link", "pdf_viewer", "Open the reading link under the pointer", ""),
        KeySpec("viewer.next_mention", "Next mention of this entity", "pdf_viewer", "Jump to the next mention of the entity last hovered", "Alt+]"),
        KeySpec("viewer.prev_mention", "Previous mention of this entity", "pdf_viewer", "Jump to the previous mention of the entity last hovered", "Alt+["),
        KeySpec("viewer.toggle_entity_links", "Show entity links", "pdf_viewer", "Toggle hover cards for entity mentions", ""),
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
        KeySpec("document_explorer.toggle_tag_folders", "Toggle Tag Folders", "global",
                "Show sources in collapsible tag categories", ""),
        KeySpec("app.prompt_editor",   "Prompt Editor",       "global",
                "Open the AI prompt editor",                      ""),
        KeySpec("app.settings",        "Settings",            "global",
                "Open global application settings",               ""),
        KeySpec("memory.open_panel",   "Memory Panel",        "global",
                "Open Settings → Memory: usage chart, loaded models, automatic management", ""),
        KeySpec("memory.unload_models", "Unload All Models",  "global",
                "Unload every model Papyrus has loaded to free memory", ""),
        KeySpec("memory.free_caches",  "Free Memory Caches",  "global",
                "Drop rebuildable caches (viewer pages, extraction cache, open PDFs)", ""),
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
        KeySpec("pdf.reading_mode", "Toggle Reading Mode", "pdf_viewer",
                "Show or hide the word-by-word reading overlay",  "Ctrl+Shift+R"),
        KeySpec("pdf.copy",         "Copy Selection",    "pdf_viewer",
                "Copy the selected text to the clipboard",        "Ctrl+C"),
        KeySpec("pdf.toggle_viewer", "Toggle Source Viewer", "pdf_viewer",
                "Show or hide the source viewer dock",            ""),

        # ── Video Player ─────────────────────────────────────────────────────
        KeySpec("video.play_pause",        "Play / Pause",       "video_player",
                "Toggle playback for the active video source",    ""),
        KeySpec("video.back_10s",          "Back 10 Seconds",    "video_player",
                "Seek the active video backward ten seconds",     ""),
        KeySpec("video.forward_10s",       "Forward 10 Seconds", "video_player",
                "Seek the active video forward ten seconds",      ""),
        KeySpec("video.toggle_captions",   "Toggle Captions",    "video_player",
                "Show or hide captions in the video player",      ""),
        KeySpec("video.toggle_transcript", "Toggle Transcript",  "video_player",
                "Show or hide the transcript below the video",    ""),
        KeySpec("video.toggle_timestamps", "Toggle Timestamps",  "video_player",
                "Show or hide timestamps in the transcript",      ""),
        KeySpec("video.save_note",         "Save Video Note",    "video_player",
                "Save a quote note from the current video time",  ""),
        KeySpec("video.export_transcript", "Save with Transcript", "video_player",
                "Save audio/video with subtitles or export a TXT, SRT or WebVTT transcript", ""),

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
        KeySpec("data_dock.extract_pdf",     "Extract PDF Data",   "data_dock",
                "Extract tables only from the open PDF",          ""),
        KeySpec("data_dock.jump_to_source",  "Jump To Source",     "data_dock",
                "Open the source document and jump to this dataset's origin", ""),
        KeySpec("data_dock.copy",            "Copy Cells",         "data_dock",
                "Copy selected data dock cells",                  "Ctrl+C"),
        KeySpec("data_dock.cut",             "Cut Cells",          "data_dock",
                "Cut selected data dock cells",                   "Ctrl+X"),
        KeySpec("data_dock.paste",           "Paste Cells",        "data_dock",
                "Paste clipboard data into the grid",             "Ctrl+V"),
        KeySpec("data_dock.undo",            "Undo Grid Edit",     "data_dock",
                "Undo the last data dock edit",                   "Ctrl+Z"),
        KeySpec("data_dock.redo",            "Redo Grid Edit",     "data_dock",
                "Redo the last undone data dock edit",            "Ctrl+Y"),
        KeySpec("data_dock.add_average_column", "Add Average Column", "data_dock",
                "Add an average column for the selected data range", ""),
        KeySpec("data_dock.add_sum_column",  "Add Sum Column",     "data_dock",
                "Add a sum column for the selected data range",   ""),
        KeySpec("data_dock.clear_grid",     "Clear Grid",         "data_dock",
                "Clear the active grid values",                  ""),
        KeySpec("data_dock.generate_chart", "Generate Chart",     "data_dock",
                "Refresh the chart preview",                     ""),
        KeySpec("intelligence.open", "Open Intelligence", "intelligence",
                "Open the universal Intelligence dock",          ""),
        KeySpec("intelligence.jump_to_source", "Jump to Artifact Source", "intelligence",
                "Open the selected artifact's source at its exact recorded location", ""),
        KeySpec("intelligence.save", "Save Artifact", "intelligence",
                "Save Artifact", ""),
        KeySpec("intelligence.verify", "Verify Artifact", "intelligence",
                "Verify Artifact", ""),
        KeySpec("intelligence.reject_artifact", "Reject Artifact", "intelligence",
                "Reject Artifact", ""),
        KeySpec("intelligence.undo", "Undo Artifact Review", "intelligence",
                "Undo Artifact Review", ""),
        KeySpec("intelligence.reset", "Discard Artifact Changes", "intelligence",
                "Discard Artifact Changes", ""),
        KeySpec("intelligence.show_guide", "Intelligence Field Guide", "intelligence",
                "Intelligence Field Guide", ""),
        KeySpec("intelligence.show_provenance", "Repair Artifact Source Location", "intelligence",
                "Repair Artifact Source Location", ""),
        KeySpec("intelligence.show_raw", "Toggle Advanced Artifact JSON", "intelligence",
                "Toggle Advanced Artifact JSON", ""),
        KeySpec("intelligence.help_focused", "Explain Focused Artifact Field", "intelligence",
                "Explain Focused Artifact Field", ""),
        KeySpec("intelligence.review_issues", "Review Intelligence Issues", "intelligence",
                "Review Intelligence Issues", ""),
        KeySpec("intelligence.purge_source", "Purge Source Intelligence", "intelligence",
                "Permanently remove intelligence and embeddings for the selected source, or removed sources", ""),
        KeySpec("intelligence.review_save_next", "Save Review & Next", "intelligence",
                "Save Review & Next", ""),
        KeySpec("intelligence.review_skip", "Skip Review Item", "intelligence",
                "Skip Review Item", ""),
        KeySpec("intelligence.review_defer", "Defer Review Item", "intelligence",
                "Defer Review Item", ""),
        KeySpec("intelligence.review_undo", "Undo Last Queue Review", "intelligence",
                "Undo Last Queue Review", ""),
        KeySpec("intelligence.review_jump", "Jump to Review Source", "intelligence",
                "Jump to Review Source", ""),
        KeySpec("intelligence.review_reset_queue", "Revisit Skipped Reviews", "intelligence",
                "Revisit Skipped Reviews", ""),
        KeySpec("intelligence.entity_review", "Review Entity Grouping", "intelligence",
                "Review Entity Grouping", ""),
        KeySpec("intelligence.entity_merge", "Merge Selected Entities", "intelligence",
                "Merge Selected Entities", ""),
        KeySpec("intelligence.entity_never_merge", "Keep Selected Entities Separate", "intelligence",
                "Keep Selected Entities Separate", ""),
        KeySpec("intelligence.entity_subgroup", "Group Selected Entities Under One", "intelligence",
                "Put the selected entities under a broader one they belong to; each stays its own entity.", ""),
        KeySpec("intelligence.entity_split", "Split Selected Entity Mentions", "intelligence",
                "Split Selected Entity Mentions", ""),
        KeySpec("intelligence.review_connections", "Review Connections", "intelligence",
                "Go through entity connections one sentence at a time, with optional filters.", ""),
        KeySpec("intelligence.connection.save", "Save Connection", "intelligence",
                "Save the shown connection as confirmed, with the chosen type.", "Alt+S"),
        KeySpec("intelligence.connection.reject", "Reject Connection", "intelligence",
                "Reject the shown connection; it is not proposed again.", "Alt+R"),
        KeySpec("intelligence.connection.skip", "Skip Connection", "intelligence",
                "Leave the shown connection for later and show the next one.", "Alt+N"),
        KeySpec("intelligence.connection.undo", "Undo Connection Decision", "intelligence",
                "Undo the last connection you saved or rejected.", "Alt+U"),
        KeySpec("intelligence.connection.jump", "Go to Connection Sentence", "intelligence",
                "Open the source at the sentence the connection is based on.", "Alt+G"),
        KeySpec("intelligence.decision.confirm", "Confirm Knowledge Decision", "intelligence",
                "Confirm Knowledge Decision", ""),
        KeySpec("intelligence.decision.reject", "Reject Knowledge Proposal", "intelligence",
                "Reject Knowledge Proposal", ""),
        KeySpec("intelligence.decision.undo", "Undo Knowledge Decision", "intelligence",
                "Undo Knowledge Decision", ""),
        KeySpec("intelligence.decision.discard", "Discard Knowledge Decision Draft", "intelligence",
                "Discard an uncommitted decision and restore its initial choices.", ""),
        KeySpec("intelligence.decision.reload", "Reload Decision Preview", "intelligence",
                "Reload Decision Preview", ""),
        KeySpec("intelligence.decision.jump_source", "Jump to Decision Source", "intelligence",
                "Jump to Decision Source", ""),
        KeySpec("intelligence.decision.jump_target", "Jump to Decision Target", "intelligence",
                "Jump to Decision Target", ""),
        KeySpec("intelligence.decision.jump_evidence", "Jump to Decision Evidence", "intelligence",
                "Jump to Decision Evidence", ""),
        KeySpec("intelligence.decision.guide", "Knowledge Decision Help", "intelligence",
                "Knowledge Decision Help", ""),
        KeySpec("intelligence.decision.previous_page", "Previous Decision Candidates", "intelligence",
                "Previous Decision Candidates", ""),
        KeySpec("intelligence.decision.next_page", "Next Decision Candidates", "intelligence",
                "Next Decision Candidates", ""),
        KeySpec("intelligence.previous_page", "Previous Intelligence Page", "intelligence",
                "Show the previous Intelligence page.", ""),
        KeySpec("intelligence.expand_inspector", "Expand Artifact Inspector", "intelligence",
                "Open the selected record in a resizable inspector.", ""),
        KeySpec("intelligence.browse_occurrences", "Browse All Artifact Occurrences", "intelligence",
                "Browse every source-scoped entity mention or bibliography reference.", ""),
        KeySpec("intelligence.expanded_jump", "Jump from Expanded Inspector", "intelligence",
                "Highlight the expanded inspector's recorded passage.", ""),
        KeySpec("intelligence.reference.jump", "Jump to Referenced Source", "intelligence",
                "Resolve the recorded artifact span and highlight its source.", ""),
        KeySpec("intelligence.reference.inspect", "Inspect Referenced Artifact", "intelligence",
                "Inspect the current artifact while retaining the saved quote.", ""),
        KeySpec("intelligence.reference.note", "Add Linked Note", "intelligence", "Add project commentary to a recorded source passage.", ""),
        KeySpec("intelligence.note.save", "Save Linked Note", "intelligence", "Save a note with its recorded source span.", ""),
        KeySpec("intelligence.note.reload", "Reload Linked Note Source", "intelligence", "Check the source again without discarding note text.", ""),
        KeySpec("intelligence.chat.previous_page", "Newer Chat History", "intelligence", "Browse a newer persisted chat page.", ""),
        KeySpec("intelligence.chat.next_page", "Older Chat History", "intelligence", "Browse an older persisted chat page.", ""),
        KeySpec("intelligence.citations.previous_page", "Previous Citation Page", "intelligence", "Browse the previous citation page.", ""),
        KeySpec("intelligence.citations.next_page", "Next Citation Page", "intelligence", "Browse the next citation page.", ""),
        KeySpec("intelligence.source_text.previous_page", "Previous Source Text Page", "intelligence", "Read the previous extracted text page.", ""),
        KeySpec("intelligence.source_text.next_page", "Next Source Text Page", "intelligence", "Read the next extracted text page.", ""),
        KeySpec("intelligence.notes.refresh", "Refresh Notes", "intelligence", "Restart bounded Notes browsing after source or project changes.", ""),
        KeySpec('intelligence.event.create','Create Event from Passage',"intelligence",'Author a dated interpretation from recorded sentence evidence.',""),
        KeySpec('intelligence.event.edit','Edit Event and Participants',"intelligence",'Review an event candidate, interval and participant roles.',""),
        KeySpec('intelligence.event.save','Save Event Interpretation',"intelligence",'Save typed source-backed event fields.',""),
        KeySpec('intelligence.timeline.include_event','Personally Include Date Observation',"intelligence",'Include a selected observation while retaining its diagnostic label.',""),
        KeySpec('intelligence.timeline.check_ids','Check Replaced Event IDs',"intelligence",'Inspect confirmed correspondence and unresolved personal event IDs.',""),
        KeySpec('intelligence.timeline.apply_ids','Apply Confirmed Event Replacements',"intelligence",'Explicitly apply confirmed same-source identity correspondence.',""),
        KeySpec('intelligence.network.toggle_views','Expand Saved Network Perspectives','intelligence','Expand or collapse project-local network perspectives.',''),
        KeySpec('intelligence.network.apply','Apply Network Perspective',"intelligence",'Explore current evidence-backed entity connections and personal perspectives.',""),
        KeySpec('intelligence.network.all_entities','Show All Entities',"intelligence",'Explore current evidence-backed entity connections and personal perspectives.',""),
        KeySpec('intelligence.network.expand_source','Expand Network Source Entity',"intelligence",'Explore current evidence-backed entity connections and personal perspectives.',""),
        KeySpec('intelligence.network.expand_target','Expand Network Target Entity',"intelligence",'Explore current evidence-backed entity connections and personal perspectives.',""),
        KeySpec('intelligence.network.review','Review Network Connection',"intelligence",'Explore current evidence-backed entity connections and personal perspectives.',""),
        KeySpec('intelligence.network.load_view','Load Network Perspective',"intelligence",'Explore current evidence-backed entity connections and personal perspectives.',""),
        KeySpec('intelligence.network.save_view','Save Network Perspective',"intelligence",'Explore current evidence-backed entity connections and personal perspectives.',""),
        KeySpec('intelligence.network.new_view','Reset Network Perspective',"intelligence",'Explore current evidence-backed entity connections and personal perspectives.',""),
        KeySpec('intelligence.network.delete_view','Remove Network Perspective',"intelligence",'Explore current evidence-backed entity connections and personal perspectives.',""),
        KeySpec('intelligence.network.hide_event','Hide Selected Network Connection',"intelligence",'Explore current evidence-backed entity connections and personal perspectives.',""),
        KeySpec('intelligence.network.restore_event','Restore Network Connection',"intelligence",'Explore current evidence-backed entity connections and personal perspectives.',""),
        KeySpec('intelligence.inbox.open','Open Workspace Inbox','workspace','Browse canonical notes, entities and saved discovery copies.',''),
        KeySpec('intelligence.inbox.add','Add Inbox Selection',"intelligence",'Browse and explicitly import independent workspace copies.',""),
        KeySpec('intelligence.inbox.refresh','Refresh Workspace Inbox',"intelligence",'Browse and explicitly import independent workspace copies.',""),
        KeySpec('intelligence.inbox.previous_page','Previous Inbox Page',"intelligence",'Browse and explicitly import independent workspace copies.',""),
        KeySpec('intelligence.inbox.next_page','Next Inbox Page',"intelligence",'Browse and explicitly import independent workspace copies.',""),
        KeySpec('intelligence.timeline.toggle_filters','Expand Timeline Filters','intelligence','Expand/collapse temporal filters and personal research views.',''),
        KeySpec('intelligence.timeline.apply','Apply Timeline Filters','intelligence','Apply typed entity, calendar and association filters.',''),
        KeySpec('intelligence.timeline.find_entity','Find Timeline Entity','intelligence','Search current scoped canonical entities.',''),
        KeySpec('intelligence.timeline.add_entity','Add Timeline Entity','intelligence','Insert a typed entity identity into the Boolean expression.',''),
        KeySpec('intelligence.timeline.entities.previous_page','Previous Entity Choices','intelligence','Browse earlier matching entity choices.',''),
        KeySpec('intelligence.timeline.entities.next_page','Next Entity Choices','intelligence','Browse further matching entity choices.',''),
        KeySpec('intelligence.timeline.help','Timeline Help','intelligence','Explain event candidates, Boolean expressions and proximity.',''),
        KeySpec('intelligence.timeline.load_view','Load Timeline Filters','intelligence','Apply a saved project-local timeline filter definition.',''),
        KeySpec('intelligence.timeline.save_view','Save Timeline Filters','intelligence','Save personal filters and exclusions without changing source artifacts.',''),
        KeySpec('intelligence.timeline.new_view','Reset Timeline Filters','intelligence','Start new working filters without deleting saved definitions.',''),
        KeySpec('intelligence.timeline.delete_view','Remove Saved Timeline Filters','intelligence','Remove the selected personal filter definition.',''),
        KeySpec('intelligence.timeline.hide_event','Hide Selected Timeline Events','intelligence','Exclude selected event candidates from the working research view.',''),
        KeySpec('intelligence.timeline.restore_event','Restore Selected Timeline Events','intelligence','Remove selected candidates from personal exclusions.',''),
        KeySpec("intelligence.notes.previous_page", "Previous Notes Page", "intelligence", "Browse the previous bounded Notes page.", ""),
        KeySpec("intelligence.notes.next_page", "Next Notes Page", "intelligence", "Browse the next bounded Notes page.", ""),
        KeySpec("intelligence.history.previous_page", "Previous Revision Page", "intelligence", "Browse the previous revision page.", ""),
        KeySpec("intelligence.history.next_page", "Next Revision Page", "intelligence", "Browse the next revision page.", ""),
        KeySpec("intelligence.note.refresh_copy", "Refresh Workspace Note Copy", "intelligence", "Explicitly replace a workspace copy after reviewing current project commentary.", ""),
        KeySpec("intelligence.note.previous_selected", "Previous Selected Note", "intelligence", "Manage the previous selected project note.", ""),
        KeySpec("intelligence.note.next_selected", "Next Selected Note", "intelligence", "Manage the next selected project note.", ""),
        KeySpec("intelligence.note.adopt", "Adopt Physical Annotation", "intelligence", "Preview and adopt a physical PDF highlight as a guarded project note without changing the source.", ""),
        KeySpec("intelligence.note.inspect", "Edit Saved Linked Note", "intelligence", "Edit saved commentary in the shared artifact inspector.", ""),
        KeySpec("intelligence.note.delete", "Delete Linked Note", "intelligence", "Retire a project-owned linked note and its highlight.", ""),
        KeySpec("intelligence.note.color", "Choose Linked Note Color", "intelligence", "Choose a project highlight color.", ""),
        KeySpec("intelligence.browsing_guide", "Intelligence Browsing Guide", "intelligence",
                "Explain saved layouts, occurrence browsing and run history.", ""),
        KeySpec("intelligence.reset_view_layout", "Reset Intelligence View Layout", "intelligence",
                "Restore backend ordering and the default splitter; collapse rows.", ""),
        KeySpec("intelligence.occurrences.split", "Split Selected Occurrence Mentions", "intelligence",
                "Split selected entity mentions on this occurrence page.", ""),
        KeySpec("intelligence.occurrences.previous_page", "Previous Occurrence Page", "intelligence",
                "Show the previous occurrence page.", ""),
        KeySpec("intelligence.occurrences.next_page", "Next Occurrence Page", "intelligence",
                "Show the next occurrence page.", ""),
        KeySpec("intelligence.next_page", "Next Intelligence Page", "intelligence",
                "Browse the next page of matching artifacts", ""),
        KeySpec("intelligence.processing", "Source Processing and Model Readiness", "intelligence",
                "Source Processing and Model Readiness.", ""),
        KeySpec("intelligence.processing.save_plan", "Save Source Processing Plan", "intelligence",
                "Save Source Processing Plan.", ""),
        KeySpec("intelligence.processing.discard_plan", "Discard Source Plan Draft", "intelligence",
                "Discard unsaved source processing choices.", ""),
        KeySpec("intelligence.processing.run_missing", "Run Missing Source Stages", "intelligence",
                "Run Missing Source Stages.", ""),
        KeySpec("intelligence.processing.rerun_stale", "Rerun Stale Source Stages", "intelligence",
                "Rerun Stale Source Stages.", ""),
        KeySpec("intelligence.processing.run_selected", "Run Selected Source Stage", "intelligence",
                "Run Selected Source Stage.", ""),
        KeySpec("intelligence.processing.force_selected", "Force Rerun Selected Source Stage", "intelligence",
                "Force Rerun Selected Source Stage.", ""),
        KeySpec("intelligence.processing.cancel", "Cancel This Source Processing Run", "intelligence",
                "Cancel This Source Processing Run.", ""),
        KeySpec("intelligence.processing.guide", "Source Processing Help", "intelligence",
                "Source Processing Help.", ""),
        KeySpec("intelligence.model.install", "Install or Repair Model", "intelligence",
                "Install or Repair Model.", ""),
        KeySpec("intelligence.model.import_model", "Import Model Folder", "intelligence",
                "Import Model Folder.", ""),
        KeySpec("intelligence.model.validate_model", "Validate Model", "intelligence",
                "Validate Model.", ""),
        KeySpec("analysis.run", "Run Selected Analysis", "intelligence",
                "Run the selected grounded analysis mode",       "Ctrl+Shift+A"),
        KeySpec("analysis.template.add_group", "Add Analysis Requirement Group", "intelligence",
                "Add an all/any artifact prerequisite in the Modes Builder", ""),
        KeySpec("analysis.template.remove_group", "Remove Analysis Requirement Group", "intelligence",
                "Remove the selected artifact prerequisite group in the Modes Builder", ""),
        KeySpec("csv_preview.open_in_data_dock", "Open in Data Dock", "csv_preview",
                "Send the currently previewed CSV/TSV/XLSX file into the Data Dock for editing", ""),

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
        KeySpec("ocr.required.run_replace", "Run OCR and Update File", "ocr",
                "OCR a scanned document in place so intelligence can process it", ""),
        KeySpec("ocr.required.run_copy", "Run OCR to a New Copy", "ocr",
                "OCR a scanned document into a new searchable copy", ""),
        KeySpec("ocr.required.install_engine", "Install OCR Engine", "ocr",
                "Install the Tesseract OCR engine through the system package manager", ""),
        KeySpec("ocr.required.cancel", "Cancel OCR", "ocr",
                "Stop the OCR run started from the OCR prompt", ""),
        KeySpec("ocr.required.help", "OCR Requirement Help", "ocr",
                "Explain why intelligence needs OCR for scanned documents", ""),

        # ── Audio / TTS ───────────────────────────────────────────────────────
        KeySpec("tts.extract", "Extract Text to Audio", "tts",
                "Extract page text and prepare for text-to-speech", ""),
    ]
