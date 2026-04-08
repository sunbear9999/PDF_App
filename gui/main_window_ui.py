from PyQt6.QtCore import Qt, QSettings, QTimer, QThread
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QLabel,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QMainWindow,
    QSizePolicy,
    QSplitter,
    QWidget,
)

from gui.dock_panels.ocr_dock import OCRDockWidget
from gui.dock_panels.tts_dock import TTSDockWidget
from gui.dock_panels.llm_dock import LLMDockWidget
from gui.dock_panels.notes_dock import NotesDockWidget
from gui.menu_builder import MenuBuilder


class MainWindowUI:
    """UI/layout construction for MainWindow."""

    def __init__(self, main_window):
        self.main_window = main_window

    @property
    def w(self):
        return self.main_window

    def build_status_bar(self):
        w = self.w
        w.status_bar = w.statusBar()
        w.status_bar.show()
        w.status_bar.setVisible(True)
        w.indexing_status_label = QLabel("")
        w.indexing_status_label.setVisible(False)
        w.status_bar.addPermanentWidget(w.indexing_status_label)
        w.indexing_in_progress = False
        w.status_bar.showMessage("Ready")

    def build_central_splitter(self):
        w = self.w
        w.central_splitter = QSplitter(Qt.Orientation.Horizontal)
        w.setCentralWidget(w.central_splitter)

        w.viewer_container = QWidget()
        viewer_layout = QHBoxLayout(w.viewer_container)
        viewer_layout.setContentsMargins(0, 0, 0, 0)
        viewer_layout.addWidget(w.viewer)
        w.central_splitter.addWidget(w.viewer_container)

        w.workspace_view = w.workspace_view if hasattr(w, "workspace_view") else None
        if w.workspace_view is None:
            from gui.components.workspace_view import WorkspaceView

            w.workspace_view = WorkspaceView(w)

        w.workspace_container = QWidget()
        workspace_layout = QHBoxLayout(w.workspace_container)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.addWidget(w.workspace_view)
        w.central_splitter.addWidget(w.workspace_container)

        w.central_splitter.setSizes([800, 600])

    def build_dock_widgets(self):
        w = self.w
        w.dock_widgets = {}

        ocr_dock = OCRDockWidget(main_window=w)
        w.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, ocr_dock)
        w.dock_widgets["OCR"] = ocr_dock
        ocr_dock.hide()

        tts_dock = TTSDockWidget(main_window=w)
        w.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, tts_dock)
        w.dock_widgets["Audio (TTS)"] = tts_dock
        tts_dock.hide()

        llm_dock = LLMDockWidget(main_window=w)
        w.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, llm_dock)
        w.dock_widgets["LLM Chat"] = llm_dock
        llm_dock.hide()

        notes_dock = NotesDockWidget(viewer=w.viewer, main_window=w)
        w.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, notes_dock)
        w.dock_widgets["Notes"] = notes_dock
        notes_dock.hide()

        w.tabs = w.dock_widgets

        w.viewer.annot_manager.note_added.connect(notes_dock.refresh_notes)
        w.viewer.annot_manager.note_added.connect(w._mark_current_dirty)
        w.viewer.annotation_clicked.connect(w._on_annotation_clicked)

    def build_menu(self):
        w = self.w
        w.menu_builder = MenuBuilder(w)
        w.menu_builder.build_menu()

    def build_toolbar(self):
        w = self.w
        w.toolbar = w.addToolBar("Main Toolbar")
        w.toolbar.setMovable(False)
        w.toolbar.setFloatable(False)
        w.toolbar.addWidget(QLabel("Active PDF:"))

        w.pdf_selector = QComboBox()
        w.pdf_selector.setFixedWidth(250)
        w.pdf_selector.currentIndexChanged.connect(w._on_pdf_dropdown_changed)
        w.toolbar.addWidget(w.pdf_selector)

        w.toolbar.addSeparator()
        w.toolbar.addAction(w.dock_widgets["Notes"].toggleViewAction())
        w.toolbar.addAction(w.dock_widgets["OCR"].toggleViewAction())
        w.toolbar.addAction(w.dock_widgets["Audio (TTS)"].toggleViewAction())
        w.toolbar.addAction(w.dock_widgets["LLM Chat"].toggleViewAction())

        spacer = QWidget()
        spacer.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        w.toolbar.addWidget(spacer)

        w.theme_selector = QComboBox()
        w.theme_selector.addItems(list(w.theme_manager.themes.keys()))
        w.theme_selector.setCurrentText(w.theme_manager.current_theme_name)
        w.theme_selector.currentTextChanged.connect(w._on_theme_changed)
        w.theme_selector.setFixedWidth(180)
        w.toolbar.addWidget(w.theme_selector)

    def setup_shortcuts(self):
        w = self.w
        QShortcut(QKeySequence("Ctrl+="), w).activated.connect(w.viewer.zoom_in)
        QShortcut(QKeySequence("Ctrl++"), w).activated.connect(w.viewer.zoom_in)
        QShortcut(QKeySequence("Ctrl+-"), w).activated.connect(w.viewer.zoom_out)
        QShortcut(QKeySequence("Ctrl+0"), w).activated.connect(w.viewer.zoom_reset)
        QShortcut(QKeySequence("Ctrl+F"), w).activated.connect(w.viewer.annot_manager.toggle_search)
        QShortcut(QKeySequence("Ctrl+S"), w).activated.connect(w.save_project)

    def update_theme(self, theme):
        w = self.w
        for dock in w.dock_widgets.values():
            if hasattr(dock, "update_theme"):
                dock.update_theme(theme)
        if hasattr(w.viewer, "update_theme"):
            w.viewer.update_theme(theme)

