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
    menu_name: str  # <--- NEW: Allows dynamic UI toggle mapping
    area: Qt.DockWidgetArea
    is_singleton: bool
    factory: Callable[['MainWindow'], QDockWidget]

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
        
    def clear_all(self):
        for dock_id in self.registry.keys():
            for dock in self.get_instances(dock_id):
                dock.close()
            self.instances[dock_id].clear()
