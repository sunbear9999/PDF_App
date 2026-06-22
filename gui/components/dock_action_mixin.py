"""
gui/components/dock_action_mixin.py

DockActionMixin — shared logic for any dock widget that exposes DockActionSpec mount points.

Usage::

    class MyDock(QDockWidget, DockActionMixin):
        def __init__(self, app_context, ...):
            super().__init__(...)
            self._dock_action_app_context = app_context

        def _on_cell_right_click(self, row, col, value):
            from core.engine.dock_mounts import DATA_DOCK_CONTEXT_MENU_CELL
            ctx = self._make_dock_context(
                "data_dock",
                DATA_DOCK_CONTEXT_MENU_CELL,
                extra={"row": row, "col": col, "value": value},
            )
            menu = self.build_context_menu_from_mount(DATA_DOCK_CONTEXT_MENU_CELL, ctx)
            menu.exec(QCursor.pos())
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu

if TYPE_CHECKING:
    from gui.app_context import AppContext
    from core.plugins.extension_registry import DockActionContext, DockActionSpec


def fire_dock_action_spec(spec: "DockActionSpec", ctx: "DockActionContext") -> None:
    """Module-level helper: fire a DockActionSpec without needing a mixin instance.

    Usable by any code that has the spec and context but doesn't inherit DockActionMixin
    (e.g., AnnotationManager, DataDock).
    """
    if spec.callback is not None:
        try:
            spec.callback(ctx)
        except Exception as exc:
            print(f"[dock_action] Callback error for '{spec.action_id}': {exc}")
        return

    if spec.blueprint_id or spec.blueprint_factory:
        ac = ctx.app_context
        if ac is None:
            return
        # Build initial state
        if spec.initial_state_builder:
            try:
                initial_state = spec.initial_state_builder(ctx)
            except Exception:
                initial_state = {}
        else:
            initial_state = {
                "selected_text": ctx.selection or "",
                "source_path": ctx.source_path or "",
                **ctx.extra,
            }
        target_id = spec.action_id + "_output"
        initial_state["target_id"] = target_id

        blueprint = None
        if spec.blueprint_id:
            registry = getattr(ac, "blueprint_registry", None)
            if registry:
                blueprint = registry.create(spec.blueprint_id)
        if blueprint is None and spec.blueprint_factory:
            try:
                blueprint = spec.blueprint_factory(ctx)
            except Exception:
                pass
        if blueprint is None:
            return

        bus = getattr(ac, "bus", None)
        if bus:
            try:
                from core.events.domains.workflow_events import WorkflowIntent, WorkflowPayload
                bus.workflow_action_requested.emit(
                    WorkflowIntent.RUN_BLUEPRINT,
                    WorkflowPayload(blueprint=blueprint, initial_state=initial_state, target_id=target_id),
                )
            except Exception as exc:
                print(f"[dock_action] Blueprint launch error for '{spec.action_id}': {exc}")


def inject_dock_actions_into_menu(
    menu: "QMenu",
    mount: str,
    dock_id: str,
    zone: str,
    app_context: Any,
    *,
    selection: Optional[str] = None,
    source_path: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Inject registered DockActionSpec items for *mount* into an existing QMenu.

    This is the lightweight alternative to DockActionMixin for code that
    already owns a QMenu and just needs dock actions appended.
    """
    from core.plugins.extension_registry import DockActionContext
    if app_context is None:
        return
    ext_reg = (
        getattr(app_context, "plugin_extension_registry", None)
        or getattr(app_context, "gui_extensions", None)
    )
    if ext_reg is None:
        return
    specs = ext_reg.get_dock_actions(mount)
    if not specs:
        return

    menu.addSeparator()
    for spec in specs:
        ctx = DockActionContext(
            dock_id=dock_id,
            zone=zone,
            selection=selection,
            source_path=source_path,
            extra=extra or {},
            app_context=app_context,
        )
        from PySide6.QtGui import QAction as _QAction
        action = _QAction(spec.label, menu)
        if spec.tooltip:
            action.setToolTip(spec.tooltip)
        action.triggered.connect(lambda _=False, s=spec, c=ctx: fire_dock_action_spec(s, c))
        menu.addAction(action)


class DockActionMixin:
    """Mixin for dock widgets that expose DockActionSpec mount points.

    The host widget must set ``self._dock_action_app_context`` to the
    live AppContext instance before calling any mixin methods.
    """

    _dock_action_app_context: Optional["AppContext"] = None

    # ------------------------------------------------------------------
    # Context helpers
    # ------------------------------------------------------------------

    def _make_dock_context(
        self,
        dock_id: str,
        zone: str,
        *,
        selection: Optional[str] = None,
        source_path: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> "DockActionContext":
        from core.plugins.extension_registry import DockActionContext
        return DockActionContext(
            dock_id=dock_id,
            zone=zone,
            selection=selection,
            source_path=source_path,
            extra=extra or {},
            app_context=self._dock_action_app_context,
        )

    # ------------------------------------------------------------------
    # Menu building
    # ------------------------------------------------------------------

    def build_context_menu_from_mount(
        self,
        mount: str,
        dock_ctx: "DockActionContext",
        parent_menu: Optional[QMenu] = None,
    ) -> QMenu:
        """Return a QMenu populated with registered DockActionSpecs for *mount*.

        If *parent_menu* is provided the actions are appended to it (with a
        separator if the menu already has items).  Otherwise a new QMenu is
        returned ready to be ``exec()``'d.
        """
        specs = self._get_dock_action_specs(mount)
        if not specs:
            return parent_menu or QMenu()

        menu = parent_menu or QMenu()
        if parent_menu and not parent_menu.isEmpty():
            menu.addSeparator()

        for spec in specs:
            action = QAction(spec.label, menu)
            if spec.tooltip:
                action.setToolTip(spec.tooltip)
            _spec = spec
            _ctx = dock_ctx
            action.triggered.connect(lambda _=False, s=_spec, c=_ctx: self._fire_dock_action(s, c))
            menu.addAction(action)

        return menu

    # ------------------------------------------------------------------
    # Toolbar building
    # ------------------------------------------------------------------

    def build_toolbar_actions(
        self,
        mount: str,
        dock_ctx: "DockActionContext",
    ) -> List[QAction]:
        """Return a list of QAction objects suitable for a dock toolbar."""
        specs = self._get_dock_action_specs(mount)
        actions = []
        for spec in specs:
            action = QAction(spec.label)
            if spec.tooltip:
                action.setToolTip(spec.tooltip)
            _spec = spec
            _ctx = dock_ctx
            action.triggered.connect(lambda _=False, s=_spec, c=_ctx: self._fire_dock_action(s, c))
            actions.append(action)
        return actions

    # ------------------------------------------------------------------
    # Action firing
    # ------------------------------------------------------------------

    def _fire_dock_action(self, spec: "DockActionSpec", ctx: "DockActionContext") -> None:
        """Execute spec.callback or fire spec.blueprint_id as a workflow run."""
        if spec.callback is not None:
            try:
                spec.callback(ctx)
            except Exception as exc:
                print(f"[DockActionMixin] Callback error for '{spec.action_id}': {exc}")
            return

        if spec.blueprint_id or spec.blueprint_factory:
            ac = self._dock_action_app_context
            if ac is None:
                return
            self._run_blueprint_action(spec, ctx, ac)

    def _run_blueprint_action(
        self,
        spec: "DockActionSpec",
        ctx: "DockActionContext",
        app_context: "AppContext",
    ) -> None:
        """Fire a workflow run for a blueprint-backed dock action."""
        from core.events.domains.workflow_events import WorkflowIntent, WorkflowPayload

        # Build initial state from the context builder or fallback to defaults
        if spec.initial_state_builder:
            try:
                initial_state = spec.initial_state_builder(ctx)
            except Exception:
                initial_state = {}
        else:
            initial_state = {
                "selected_text": ctx.selection or "",
                "source_path": ctx.source_path or "",
                **ctx.extra,
            }

        # Output target: a floating overlay keyed by action_id
        target_id = spec.action_id + "_output"
        initial_state["target_id"] = target_id

        # Resolve blueprint
        blueprint = None
        if spec.blueprint_id:
            registry = getattr(app_context, "blueprint_registry", None)
            if registry:
                blueprint = registry.create(spec.blueprint_id)
        if blueprint is None and spec.blueprint_factory:
            try:
                blueprint = spec.blueprint_factory(ctx)
            except Exception:
                pass

        if blueprint is None:
            return

        bus = getattr(app_context, "bus", None)
        if bus:
            bus.workflow_action_requested.emit(
                WorkflowIntent.RUN_BLUEPRINT,
                WorkflowPayload(blueprint=blueprint, initial_state=initial_state, target_id=target_id),
            )

    # ------------------------------------------------------------------
    # Registry access
    # ------------------------------------------------------------------

    def _get_dock_action_specs(self, mount: str) -> List["DockActionSpec"]:
        ac = self._dock_action_app_context
        if ac is None:
            return []
        ext_reg = getattr(ac, "plugin_extension_registry", None) or getattr(ac, "gui_extensions", None)
        if ext_reg is None:
            return []
        return ext_reg.get_dock_actions(mount)
