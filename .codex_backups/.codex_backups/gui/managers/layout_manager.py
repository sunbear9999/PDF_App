# gui/managers/layout_manager.py
import json
from PySide6.QtCore import QByteArray, QSettings
from PySide6.QtWidgets import QDockWidget

class LayoutManager:
    FACTORY_STATE = "AAAA/wAAAAD9AAAAAgAAAAAAAAQRAAAD+fwCAAAAAfwAAAApAAAD+QAAAREA/////AEAAAAD/AAAAAAAAADIAAAAnAD////8AgAAAAT7AAAAHgBEAG8AYwBFAHgAcABsAG8AcgBlAHIARABvAGMAawEAAAApAAADMAAAAKoA////+wAAABoAUwBjAHIAYQB0AGMAaABEAG8AYwBrAF8AMQEAAANfAAAAwwAAAGEA////+wAAABoAUwBjAHIAYQB0AGMAaABEAG8AYwBrAF8AMQEAAALfAAABQwAAAAAAAAAA+wAAABQAQwBoAGEAdABEAG8AYwBrAF8AMQEAAADgAAADWAAAAAAAAAAA/AAAAM4AAANDAAAB5AD////6AAAAAQIAAAAC+wAAABYARQBzAHMAYQB5AEQAbwBjAGsAXwAxAQAAAAD/////AAAAWAD////7AAAAGgBQAEQARgBWAGkAZQB3AGUAcgBEAG8AYwBrAQAAACkAAAQPAAAAXQD////8AAADSwAAAT0AAAAAAP////wCAAAAAfsAAAAeAFcAbwByAGsAcwBwAGEAYwBlAEQAbwBjAGsAXwAyAQAAAjIAAAIGAAAAAAAAAAAAAAABAAADYwAAA/n8AgAAAAj7AAAAFgBOAG8AdABlAHMARABvAGMAawBfADICAAAGEwAAABkAAAFkAAAD9vsAAAAeAFMAaQBuAGcAbABlAEEAdQBkAGkAbwBEAG8AYwBrAAAAAdQAAAJkAAAAAAAAAAD7AAAAGgBTAGMAcgBhAHQAYwBoAEQAbwBjAGsAXwAyAAAAA7MAAACFAAAAAAAAAAD7AAAAFgBOAG8AdABlAHMARABvAGMAawBfADEBAAAAKQAAAIcAAAAAAAAAAPsAAAAWAE4AbwB0AGUAcwBEAG8AYwBrAF8AMQAAAAApAAAEDwAAAAAAAAAA/AAAACkAAAP5AAABmAEAACH6AAAAAAEAAAAF+wAAAB4AVwBvAHIAawBzAHAAYQBjAGUARABvAGMAawBfADEBAAAAAP////8AAABKAP////sAAAAoAFMAaQBuAGcAbABlAEIAcgBhAGkAbgBzAHQAbwByAG0ARABvAGMAawEAAAAA/////wAAAikA////+wAAACoAUgBlAHMAZQBhAHIAYwBoAEEAcwBzAGkAcwB0AGEAbgB0AEQAbwBjAGsBAAAAAP////8AAAIoAP////sAAAAcAFMAaQBuAGcAbABlAEMAaABhAHQARABvAGMAawEAAAAA/////wAAAEoA////+wAAAB4AVwBvAHIAawBzAHAAYQBjAGUARABvAGMAawBfADEBAAAD8gAAA44AAAAAAAAAAPsAAAAaAFMAaQBuAGcAbABlAE8AQwBSAEQAbwBjAGsBAAADpQAAAJMAAAAAAAAAAPsAAAAoAFMAaQBuAGcAbABlAEQAaQBjAHQAaQBvAG4AYQByAHkARABvAGMAawAAAANdAAAA2wAAAAAAAAAAAAAAAAAAA/kAAAAEAAAABAAAAAgAAAAI/AAAAAEAAAACAAAAAQAAABYATQBhAGkAbgBUAG8AbwBsAGIAYQByAQAAAAD/////AAAAAAAAAAA="

    def __init__(self, main_window):
        self.window = main_window
        self.settings = QSettings("PDFMultitool", "Workspace")

    def _get_dock_widget(self, view):
        if isinstance(view, QDockWidget): return view
        if view and view.parentWidget() and isinstance(view.parentWidget(), QDockWidget): return view.parentWidget()
        return None

    def get_current_dock_counts(self) -> dict:
        # Now uses the safe memory counter
        return self.window.dock_manager.get_all_active_counts()

    def sync_dock_counts(self, counts: dict):
        for dock_id, target_count in counts.items():
            if dock_id not in self.window.dock_manager.registry: 
                continue
                
            # Use get_instances so it auto-cleans dead C++ objects
            current_instances = self.window.dock_manager.get_instances(dock_id)

            # SPAWN missing docks
            while len(current_instances) < target_count:
                dock = self.window.dock_manager.spawn(dock_id)
                current_instances.append(dock)

            # DESTROY excess docks safely via native close
            while len(current_instances) > target_count:
                dock = current_instances.pop()
                dock.close()

    

    FACTORY_COUNTS = {'workspaces': 1, 'notes': 1, 'research': 1} 

    def _apply_state(self, state_str: str, counts: dict):
        try:
            self.sync_dock_counts(counts)
            if state_str:
                self.window.restoreState(QByteArray.fromBase64(state_str.encode('utf-8')))
            # REMOVED the for dock in findChildren loop entirely! Let Qt handle visibility.
        except Exception as e:
            print(f"Error applying layout state: {e}")
    def reset_default_layout(self):
        """Clears the saved session layout and restores the hardcoded startup layout."""
        self.settings.remove("last_session_layout")
        self.settings.remove("last_session_counts")
        self.settings.sync()
        if hasattr(self, 'apply_startup_layout'):
            self.apply_startup_layout()
    def apply_factory_default(self):
        # We must pass BOTH the string and the counts so it knows what to spawn!
        self._apply_state(self.FACTORY_STATE, self.FACTORY_COUNTS)

    def apply_startup_layout(self):
        default_state = str(self.settings.value("default_startup_layout", ""))
        counts_str = str(self.settings.value("default_startup_counts", ""))

        if not default_state or not counts_str or default_state == "None":
            self.apply_factory_default()
            return

        try:
            counts = json.loads(counts_str)
            self._apply_state(default_state, counts)
        except Exception as e:
            print(f"Error parsing layout counts: {e}")
            self.apply_factory_default()

    

    def save_current_as_default(self):
        state_bytes = self.window.saveState().toBase64().data().decode('utf-8')
        counts = self.get_current_dock_counts()
        self.settings.setValue("default_startup_layout", state_bytes)
        self.settings.setValue("default_startup_counts", json.dumps(counts))
        self.settings.sync()

    def save_template(self, name: str):
        state_bytes = self.window.saveState().toBase64().data().decode('utf-8')
        counts = self.get_current_dock_counts()
        payload = json.dumps({"state": state_bytes, "counts": counts})
        self.settings.setValue(f"layouts/{name.strip()}", payload)
        self.settings.sync()

    def load_template(self, name: str):
        payload_str = self.settings.value(f"layouts/{name}")
        if not payload_str: return
        try:
            if payload_str.startswith("{"):
                payload = json.loads(payload_str)
                self._apply_state(payload.get("state", ""), payload.get("counts", {}))
            else:
                self._apply_state(payload_str, {})
        except Exception as e:
            print(f"Failed to apply custom layout '{name}': {e}")

    def delete_template(self, name: str):
        self.settings.remove(f"layouts/{name}")
        self.settings.sync()

    def get_template_names(self) -> list:
        self.settings.beginGroup("layouts")
        keys = self.settings.childKeys()
        self.settings.endGroup()
        return keys
        
    def save_current_session(self):
        state_bytes = self.window.saveState().toBase64().data().decode('utf-8')
        counts = self.get_current_dock_counts()
        self.settings.setValue("last_session_layout", state_bytes)
        self.settings.setValue("last_session_counts", json.dumps(counts))
        self.settings.sync()

    def restore_last_session(self):
        state_str = str(self.settings.value("last_session_layout", ""))
        counts_str = str(self.settings.value("last_session_counts", ""))
        
        if not state_str or not counts_str or state_str == "None":
            self.apply_startup_layout()
            return
            
        try:
            counts = json.loads(counts_str)
            self._apply_state(state_str, counts)
        except Exception as e:
            print(f"Error restoring session: {e}")
            self.apply_startup_layout()