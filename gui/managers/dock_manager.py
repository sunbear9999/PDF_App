# gui/managers/dock_manager.py
from dataclasses import dataclass
from typing import Callable, List, Dict
from PySide6.QtWidgets import QDockWidget
from PySide6.QtCore import Qt
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
            dock.show()
            dock.raise_()
            return dock

        # 2. Instantiate via Factory
        dock = defn.factory(self.window)

        # 3. Object Naming for Layout Serialization
        if defn.is_singleton:
            dock.setObjectName(defn.object_name_prefix)
        else:
            dock.setObjectName(f"{defn.object_name_prefix}_{len(inst_list) + 1}")

        # 4. NATIVE Memory Cleanup (Replaces the dangerous visibility toggle)
        dock.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

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
        return dock

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