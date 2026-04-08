# gui/menu_builder.py
from PyQt6.QtWidgets import QMenu, QMenuBar
from PyQt6.QtGui import QAction, QShortcut, QKeySequence

class MenuBuilder:
    def __init__(self, main_window):
        self.main_window = main_window
        self.menubar = main_window.menuBar()

    def build_menu(self):
        self._build_file_menu()
        self._build_view_menu()
        self._build_tools_menu()
        self._build_help_menu()

    def _build_file_menu(self):
        file_menu = self.menubar.addMenu("File")

        new_action = QAction("New Project...", self.main_window)
        new_action.triggered.connect(self.main_window._new_project)
        file_menu.addAction(new_action)

        open_action = QAction("Open Project...", self.main_window)
        open_action.triggered.connect(self.main_window._open_project)
        file_menu.addAction(open_action)

        save_action = QAction("Save Project", self.main_window)
        save_action.triggered.connect(self.main_window.save_project)
        save_action.setShortcut(QKeySequence.StandardKey.Save)
        file_menu.addAction(save_action)

        save_as_action = QAction("Save Project As...", self.main_window)
        save_as_action.triggered.connect(self.main_window._save_project_as)
        file_menu.addAction(save_as_action)

        file_menu.addSeparator()

        add_pdf_action = QAction("Add PDF to Project...", self.main_window)
        add_pdf_action.triggered.connect(self.main_window._add_pdf)
        file_menu.addAction(add_pdf_action)

    def _build_view_menu(self):
        view_menu = self.menubar.addMenu("View")

        zoom_in_action = QAction("Zoom In", self.main_window)
        zoom_in_action.triggered.connect(self.main_window.viewer.zoom_in)
        zoom_in_action.setShortcut(QKeySequence.StandardKey.ZoomIn)
        view_menu.addAction(zoom_in_action)

        zoom_out_action = QAction("Zoom Out", self.main_window)
        zoom_out_action.triggered.connect(self.main_window.viewer.zoom_out)
        zoom_out_action.setShortcut(QKeySequence.StandardKey.ZoomOut)
        view_menu.addAction(zoom_out_action)

        fit_width_action = QAction("Fit Width", self.main_window)
        fit_width_action.triggered.connect(self.main_window.viewer.zoom_reset)
        view_menu.addAction(fit_width_action)

        view_menu.addSeparator()

        search_action = QAction("Search", self.main_window)
        search_action.triggered.connect(self.main_window.viewer.annot_manager.toggle_search)
        search_action.setShortcut(QKeySequence("Ctrl+F"))
        view_menu.addAction(search_action)

    def _build_tools_menu(self):
        tools_menu = self.menubar.addMenu("Tools")

        ocr_action = QAction("OCR", self.main_window)
        ocr_action.triggered.connect(lambda: self._toggle_dock("OCR"))
        tools_menu.addAction(ocr_action)

        tts_action = QAction("Text-to-Speech", self.main_window)
        tts_action.triggered.connect(lambda: self._toggle_dock("Audio (TTS)"))
        tools_menu.addAction(tts_action)

        llm_action = QAction("LLM Chat", self.main_window)
        llm_action.triggered.connect(lambda: self._toggle_dock("LLM Chat"))
        tools_menu.addAction(llm_action)

        notes_action = QAction("Notes", self.main_window)
        notes_action.triggered.connect(lambda: self._toggle_dock("Notes"))
        tools_menu.addAction(notes_action)

    def _build_help_menu(self):
        help_menu = self.menubar.addMenu("Help")

        help_action = QAction("Help", self.main_window)
        help_action.triggered.connect(self.main_window.show_help_window)
        help_menu.addAction(help_action)

    def _toggle_dock(self, name):
        if name in self.main_window.dock_widgets:
            dock = self.main_window.dock_widgets[name]
            if dock.isVisible():
                dock.hide()
            else:
                dock.show()