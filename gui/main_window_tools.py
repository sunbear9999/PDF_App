
class MainWindowTools:
    """Tool panel + syncing helpers for MainWindow."""

    def __init__(self, main_window):
        self.main_window = main_window

    @property
    def w(self):
        return self.main_window

    def sync_tools_with_file(self, file_path):
        w = self.w
        w.dock_widgets["Notes"].refresh_notes()
        w.dock_widgets["LLM Chat"].refresh_project_ui()
        for t in ["OCR", "Audio (TTS)"]:
            if hasattr(w.dock_widgets[t], "sync_file"):
                w.dock_widgets[t].sync_file(file_path)

    def toggle_tool_panel(self, tool_name):
        w = self.w
        if tool_name == "Close Tool":
            for dock in w.dock_widgets.values():
                dock.hide()
            return

        if tool_name in w.dock_widgets:
            dock = w.dock_widgets[tool_name]
            if dock.isVisible():
                dock.hide()
            else:
                dock.show()
                dock.raise_()

