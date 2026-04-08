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
    QTabWidget,
    QWidget,
    QPushButton,
    QToolBar,
    QMenu,
)

from gui.dock_panels.ocr_dock import OCRDockWidget
from gui.dock_panels.tts_dock import TTSDockWidget
from gui.dock_panels.llm_dock import LLMDockWidget
from gui.dock_panels.notes_dock import NotesDockWidget
from gui.menu_builder import MenuBuilder
from gui.components.pdf_viewer import PDFViewer


class MainWindowUI:
    """UI/layout construction for MainWindow."""

    def __init__(self, main_window):
        self.main_window = main_window

    @property
    def w(self):
        return self.main_window

    def build_all(self):
        self.build_status_bar()
        self.w.viewer = PDFViewer()
        self.build_central_splitter()
        self.build_dock_widgets()
        self.build_menu()
        self.build_custom_toolbar()
        self.setup_shortcuts()

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
        w.viewer_container = QWidget()
        viewer_layout = QHBoxLayout(w.viewer_container)
        viewer_layout.setContentsMargins(0, 0, 0, 0)
        viewer_layout.addWidget(w.viewer)
        w.setCentralWidget(w.viewer_container)

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

    def build_custom_toolbar(self):
        from gui.main_window.toolbar import MainToolbar
        from PyQt6.QtWidgets import QVBoxLayout, QWidget
        w = self.w
        self.toolbar_widget = MainToolbar(w, w.theme_manager)
        # Remove any QToolBar/QMenuBar if present
        for tb in w.findChildren(QToolBar):
            w.removeToolBar(tb)
        if hasattr(w, 'menuBar'):
            mb = w.menuBar()
            if mb:
                mb.hide()
        # Place the toolbar at the top using a layout
        # Always use a vertical layout: toolbar on top, main content below
        old_central = w.centralWidget()
        if old_central is not None and not isinstance(old_central, QWidget):
            # Defensive: should always be QWidget
            return
        # If the central widget is already a wrapper, extract the main content
        main_content = None
        if old_central is not None:
            # If the wrapper already exists, try to extract the main content
            if hasattr(w, '_main_layout'):
                # Already wrapped, just update toolbar
                w._main_layout.insertWidget(0, self.toolbar_widget)
                return
            # If the central widget is the viewer_container, keep it
            main_content = old_central
        # Create a new wrapper widget with vertical layout
        from PyQt6.QtWidgets import QVBoxLayout, QWidget
        wrapper = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        wrapper.setLayout(layout)
        w._main_layout = layout
        layout.addWidget(self.toolbar_widget)
        if main_content is not None:
            layout.addWidget(main_content)
        w.setCentralWidget(wrapper)
        # Connect theme change
        w.theme_manager.theme_changed.connect(self.toolbar_widget.apply_theme)
        # Populate project dropdown with PDFs
        self.toolbar_widget.project_combo.clear()
        pdf_paths = []
        if hasattr(w, 'pdf_controller'):
            pdf_paths = w.pdf_controller.get_pdf_paths()
            for i, pdf in enumerate(pdf_paths):
                self.toolbar_widget.project_combo.addItem(os.path.basename(pdf), pdf)
        # Set current index to match current file if possible
        if hasattr(w, 'current_file_path') and w.current_file_path:
            for i, pdf in enumerate(pdf_paths):
                if pdf == w.current_file_path:
                    self.toolbar_widget.project_combo.setCurrentIndex(i)
                    break
        # Connect project dropdown to PDF switch
        def on_project_combo_changed(idx):
            if idx >= 0:
                pdf_path = self.toolbar_widget.project_combo.itemData(idx)
                if pdf_path:
                    w.switch_to_pdf(pdf_path)
        self.toolbar_widget.project_combo.currentIndexChanged.connect(on_project_combo_changed)

        # Wire up toolbar buttons to main window actions
        tb = self.toolbar_widget
        tb.zoom_in_btn.clicked.connect(w.viewer.zoom_in)
        tb.zoom_out_btn.clicked.connect(w.viewer.zoom_out)
        tb.fit_width_btn.clicked.connect(w.viewer.zoom_reset)
        tb.notes_btn.clicked.connect(lambda: w.dock_widgets["Notes"].show())
        tb.ocr_btn.clicked.connect(lambda: w.dock_widgets["OCR"].show())
        tb.audio_btn.clicked.connect(lambda: w.dock_widgets["Audio (TTS)"].show())
        tb.llm_btn.clicked.connect(lambda: w.dock_widgets["LLM Chat"].show())
        tb.help_btn.clicked.connect(lambda: HelpDialog(w).exec())
        tb.theme_combo.currentTextChanged.connect(w._on_theme_changed)


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
        
        # Update toolbar styling
        if hasattr(w, 'toolbar'):
            w.toolbar.setStyleSheet(f"""
                QToolBar {{
                    background-color: {theme['bg_panel']};
                    border-bottom: 2px solid {theme['border']};
                    padding: 8px;
                    spacing: 8px;
                }}
                QToolBar::separator {{
                    background-color: {theme['border']};
                    width: 2px;
                    margin: 0px 5px;
                }}
                QLabel {{
                    color: {theme['text_main']};
                    font-weight: bold;
                    font-size: 12px;
                }}
                QComboBox {{
                    background-color: {theme['bg_input']};
                    color: {theme['text_main']};
                    border: 1px solid {theme['border']};
                    border-radius: 4px;
                    padding: 6px;
                    font-weight: bold;
                }}
                QComboBox::drop-down {{
                    border: none;
                    background-color: {theme['accent']};
                    width: 20px;
                }}
                QComboBox QAbstractItemView {{
                    background-color: {theme['bg_panel']};
                    color: {theme['text_main']};
                    border: 1px solid {theme['border']};
                    selection-background-color: {theme['accent']};
                    padding: 4px;
                }}
                QPushButton {{
                    background-color: {theme['accent']};
                    color: #ffffff;
                    border: none;
                    border-radius: 4px;
                    font-weight: bold;
                    padding: 8px 12px;
                    font-size: 11px;
                }}
                QPushButton:hover {{
                    background-color: {theme['accent_hover']};
                }}
                QPushButton:pressed {{
                    background-color: {theme['accent']};
                }}
            """)
        
        for dock in w.dock_widgets.values():
            if hasattr(dock, "update_theme"):
                dock.update_theme(theme)
        if hasattr(w.viewer, "update_theme"):
            w.viewer.update_theme(theme)

