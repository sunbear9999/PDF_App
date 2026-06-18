# gui/managers/dock_manager.py
from dataclasses import dataclass
from typing import Callable, List, Dict, Optional
from PySide6.QtWidgets import QDockWidget
from PySide6.QtCore import Qt, QTimer
import shiboken6

@dataclass
class DockDefinition:
    id: str
    object_name_prefix: str
    menu_name: str
    area: Qt.DockWidgetArea
    is_singleton: bool
    factory: Callable[['MainWindow'], QDockWidget]
    plugin_id: str = ""

class DockManager:
    def __init__(self, main_window):
        self.window = main_window
        self.registry: Dict[str, DockDefinition] = {}
        self.instances: Dict[str, List[QDockWidget]] = {}

    def register(self, definition: DockDefinition):
        self.registry[definition.id] = definition
        self.instances[definition.id] = []

    @staticmethod
    def configure_dock(dock: QDockWidget, closable: bool = True):
        features = (
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        if closable:
            features |= QDockWidget.DockWidgetFeature.DockWidgetClosable
        dock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        dock.setFeatures(features)
        dock.setMinimumSize(280, 200)

    def get_instances(self, dock_id: str) -> List[QDockWidget]:
        """Safely returns alive docks, automatically stripping out deleted C++ objects."""
        if dock_id not in self.instances: return []
        # Filter out docks that were closed/deleted by the user
        valid_docks = [d for d in self.instances[dock_id] if shiboken6.isValid(d)]
        self.instances[dock_id] = valid_docks
        return valid_docks

    def spawn(self, dock_id: str) -> QDockWidget:
        if dock_id not in self.registry:
            raise ValueError(f"Dock '{dock_id}' is not registered.")

        defn = self.registry[dock_id]
        inst_list = self.get_instances(dock_id)

        # 1. Singleton Check
        if defn.is_singleton and inst_list:
            dock = inst_list[0]
            self.configure_dock(dock, closable=True)
            if dock.isFloating():
                dock.setFloating(False)
            if self.window.dockWidgetArea(dock) == Qt.DockWidgetArea.NoDockWidgetArea:
                self.window.addDockWidget(defn.area, dock)
            dock.show()
            dock.raise_()
            QTimer.singleShot(0, lambda d=dock, df=defn: self._arrange_right_side(d, df))
            return dock

        # 2. Instantiate via Factory
        dock = defn.factory(self.window)
        if defn.plugin_id:
            dock.setProperty("papyrus_plugin_id", defn.plugin_id)
        self._inject_plugin_toolbar(dock, dock_id)
        self.configure_dock(dock, closable=True)

        # 3. Object Naming for Layout Serialization
        if defn.is_singleton:
            dock.setObjectName(defn.object_name_prefix)
        else:
            dock.setObjectName(f"{defn.object_name_prefix}_{len(inst_list) + 1}")

        # 4. NATIVE Memory Cleanup (Replaces the dangerous visibility toggle)
        dock.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        dock.resize(420, max(360, self.window.height() - 140))
        self.window.addDockWidget(defn.area, dock)
        inst_list.append(dock)
        
        # 5. Apply Theme
        if hasattr(self.window, 'theme_manager'):
            theme = self.window.theme_manager.get_theme()
            inner = dock.widget()
            if dock_id == "scratchpads" and inner:
                inner.setStyleSheet(f"background-color: {theme['bg_input']}; color: {theme['text_main']}; border: none;")
            elif inner and hasattr(inner, 'update_theme'):
                inner.update_theme(theme)
            elif hasattr(dock, 'update_theme'):
                dock.update_theme(theme)

        dock.show()
        dock.raise_()
        QTimer.singleShot(0, lambda d=dock, df=defn: self._arrange_right_side(d, df))
        return dock

    def _arrange_right_side(self, dock: QDockWidget, defn: DockDefinition):
        if not shiboken6.isValid(dock):
            return
        self.configure_dock(dock, closable=True)
        dock.setFloating(False)
        if self.window.dockWidgetArea(dock) == Qt.DockWidgetArea.NoDockWidgetArea:
            self.window.addDockWidget(defn.area, dock)
        anchor = self._right_side_anchor(exclude=dock)
        pdf_dock = getattr(self.window, "pdf_dock", None)
        if anchor and anchor is not dock:
            self.window.tabifyDockWidget(anchor, dock)
            dock.raise_()
        elif pdf_dock and shiboken6.isValid(pdf_dock) and pdf_dock is not dock:
            self.window.splitDockWidget(pdf_dock, dock, Qt.Orientation.Horizontal)
            try:
                self.window.resizeDocks([pdf_dock, dock], [max(600, self.window.width() * 2 // 3), 420], Qt.Orientation.Horizontal)
            except Exception:
                pass
        else:
            self.window.addDockWidget(defn.area, dock)
        dock.show()
        dock.raise_()

    def _right_side_anchor(self, exclude: Optional[QDockWidget] = None) -> Optional[QDockWidget]:
        preferred_prefixes = (
            "SingleResearchDock",
            "SingleDataDock",
            "WorkspaceDock",
            "NotesDock",
            "SingleDictionaryDock",
            "EssayDock",
            "SingleCitationDock",
            "SingleOCRDock",
            "SingleAudioDock",
            "ScratchDock",
        )
        docks = [
            dock for dock in self.window.findChildren(QDockWidget)
            if dock is not exclude
            and shiboken6.isValid(dock)
            and dock.isVisible()
            and not dock.isFloating()
            and dock.objectName() not in {"DocExplorerDock", "PDFViewerDock"}
        ]
        for prefix in preferred_prefixes:
            for dock in docks:
                if dock.objectName().startswith(prefix):
                    return dock
        return docks[0] if docks else None

    def get_inner_widgets(self, dock_id: str) -> list:
        return [dock.widget() for dock in self.get_instances(dock_id) if dock.widget()]

    def get_all_active_counts(self) -> dict:
        """Counts how many valid docks exist in memory, regardless of visibility."""
        counts = {}
        for dock_id in self.registry.keys():
            counts[dock_id] = len(self.get_instances(dock_id))
        return counts
        
    def _inject_plugin_toolbar(self, dock: QDockWidget, dock_id: str) -> None:
        """Inject ToolbarButtonSpecs registered for this dock_id by plugins."""
        registry = getattr(getattr(self.window, "app_context", None), "plugin_extension_registry", None)
        if not registry:
            return
        specs = registry.get_toolbar_buttons(dock_target=dock_id)
        if not specs:
            return

        inner = dock.widget()
        if not inner:
            return

        specs = sorted(specs, key=lambda s: s.position)

        # Preferred: dock exposes _plugin_toolbar_layout (a QHBoxLayout)
        toolbar_layout = getattr(inner, "_plugin_toolbar_layout", None)
        if toolbar_layout is not None:
            for spec in specs:
                btn = self._make_plugin_btn(spec)
                toolbar_layout.addWidget(btn)
            return

        # Fallback: prepend a thin strip at top of the widget's main layout
        main_layout = inner.layout()
        if main_layout is None:
            return
        from PySide6.QtWidgets import QHBoxLayout, QFrame
        strip = QFrame()
        strip.setObjectName("PluginToolbarStrip")
        strip_layout = QHBoxLayout(strip)
        strip_layout.setContentsMargins(6, 3, 6, 3)
        strip_layout.setSpacing(4)
        for spec in specs:
            strip_layout.addWidget(self._make_plugin_btn(spec))
        strip_layout.addStretch()
        main_layout.insertWidget(0, strip)

    @staticmethod
    def _make_plugin_btn(spec) -> "QPushButton":
        from PySide6.QtWidgets import QPushButton
        label = f"{spec.icon} {spec.label}".strip() if getattr(spec, "icon", "") else spec.label
        btn = QPushButton(label)
        btn.setToolTip(getattr(spec, "tooltip", "") or "")
        plugin_id = getattr(spec, "plugin_id", "")
        if plugin_id:
            btn.setProperty("papyrus_plugin_id", plugin_id)
        cb = getattr(spec, "callback", None)
        if cb:
            btn.clicked.connect(cb)
        return btn

    def sweep_plugin_toolbar_buttons(self, plugin_id: str) -> None:
        """Remove injected ToolbarButtonSpec buttons tagged with plugin_id from all live docks."""
        from PySide6.QtWidgets import QPushButton
        for dock_id in list(self.instances.keys()):
            for dock in self.get_instances(dock_id):
                try:
                    if not shiboken6.isValid(dock):
                        continue
                    inner = dock.widget()
                    if inner:
                        for btn in inner.findChildren(QPushButton):
                            if btn.property("papyrus_plugin_id") == plugin_id:
                                btn.setVisible(False)
                                btn.deleteLater()
                except RuntimeError:
                    pass

    def broadcast_theme(self, theme: dict) -> None:
        """Push a theme update to all live docks and their inner widgets."""
        for dock_id in self.registry.keys():
            for dock in self.get_instances(dock_id):
                try:
                    inner = dock.widget()
                    if inner and hasattr(inner, "update_theme"):
                        inner.update_theme(theme)
                    elif hasattr(dock, "update_theme"):
                        dock.update_theme(theme)
                except RuntimeError:
                    pass

    def toggle_by_menu_name(self, menu_name: str, checked: bool) -> None:
        """Show or hide the first singleton dock whose menu_name matches."""
        for defn in self.registry.values():
            if defn.menu_name == menu_name:
                instances = self.get_instances(defn.id)
                if checked:
                    if instances:
                        instances[0].show()
                        instances[0].raise_()
                    else:
                        self.spawn(defn.id)
                else:
                    for dock in instances:
                        dock.hide()
                return

    def remove_plugin_docks(self, plugin_id: str) -> None:
        """Close and destroy all docks belonging to plugin_id; purge from registry."""
        for dock in self.window.findChildren(QDockWidget):
            if not shiboken6.isValid(dock):
                continue
            if dock.property("papyrus_plugin_id") == plugin_id:
                dock.close()
                dock.deleteLater()
        stale_ids = [did for did, defn in self.registry.items()
                     if getattr(defn, "plugin_id", "") == plugin_id]
        for did in stale_ids:
            self.registry.pop(did, None)
            self.instances.pop(did, None)

    def clear_all(self):
        for dock_id in self.registry.keys():
            for dock in self.get_instances(dock_id):
                dock.close()
            self.instances[dock_id].clear()
