"""Registered, weakly owned commands for reusable intelligence components."""
import weakref

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from gui.registry.action_spec import ActionSpec

_BINDINGS = weakref.WeakKeyDictionary()


def _register_shared(registry, owner, identity, label, callback, mount):
    bindings = _BINDINGS.setdefault(registry, {})
    state = bindings.get(identity)
    if state is None:
        state = {"entries": []}
        def dispatch(*_):
            focus = QApplication.focusWidget()
            visible = [(widget, method) for reference, method in state["entries"]
                       if (widget := reference()) is not None and widget.isVisible()]
            for widget, method in reversed(visible):
                if focus and (focus is widget or widget.isAncestorOf(focus)):
                    if callback := method():
                        callback()
                    return
            if visible and (callback := visible[-1][1]()):
                callback()
        state["spec"] = ActionSpec(identity, label, callback=dispatch, mounts=[mount])
        bindings[identity] = state
        registry.register(state["spec"])
    elif mount not in state["spec"].mounts:
        state["spec"].mounts.append(mount)
    entry = (weakref.ref(owner), weakref.WeakMethod(callback))
    state["entries"].append(entry)
    def cleanup(*_):
        state["entries"].remove(entry)
        if not state["entries"]:
            registry.unregister(identity, state["spec"])
            bindings.pop(identity, None)
    owner.destroyed.connect(cleanup)


def bind_artifact_action(ctx, owner, identity, label, callback, mount="toolbar:intelligence:inspector", widget=None):
    """Register the command and its shortcut; returns the hover description, which is
    also applied to ``widget`` (a button or QAction) when given."""
    reference = weakref.WeakMethod(callback)
    def invoke(*_):
        method = reference()
        if method is not None:
            method()
    registry = getattr(ctx, "action_registry", None)
    if registry is not None:
        _register_shared(registry, owner, identity, label, callback, mount)
    keys = getattr(ctx, "keybinding_registry", None)
    if keys is not None:
        keys.bind(identity, invoke, owner, Qt.ShortcutContext.WidgetWithChildrenShortcut)
    from gui.components.action_tooltips import tooltip
    text = tooltip(ctx, identity, label)
    if widget is not None:
        widget.setToolTip(text)
        if hasattr(widget, "setStatusTip"):
            widget.setStatusTip(text)
        from gui.components.icon_buttons import iconize, symbol_for
        iconize(widget, symbol_for(identity), label, text)
    return text


def inspector_actions(ctx, owner):
    for name, label in (("save", "Save Artifact"), ("verify", "Verify Artifact"), ("reject_artifact", "Reject Artifact"),
                        ("undo", "Undo Artifact Review"), ("reset", "Discard Artifact Changes"),
                        ("show_guide", "Intelligence Field Guide"), ("show_provenance", "Repair Artifact Source Location"),
                        ("show_raw", "Toggle Advanced Artifact JSON"), ("help_focused", "Explain Focused Artifact Field")):
        bind_artifact_action(ctx, owner, f"intelligence.{name}", label, getattr(owner, name))
