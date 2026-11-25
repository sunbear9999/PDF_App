"""Run GUI-owned memory releases (e.g. viewer pixmaps) on the Qt thread.

The memory manager decides on its own thread; widgets may only be touched on
the Qt thread (rules.md §10.2), so its releases are handed over here.
"""
from core.services._shared.qt_tasks import MainThreadCaller


class MemoryConsumerBridge(MainThreadCaller):
    def __init__(self, memory_manager, parent=None) -> None:
        super().__init__(parent)
        if memory_manager is not None:
            memory_manager.set_gui_dispatcher(self.call)
