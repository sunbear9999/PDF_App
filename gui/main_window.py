import os
import fitz
from PyQt6.QtWidgets import (QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, 
                             QPushButton, QLabel, QSplitter, QStackedWidget, 
                             QFileDialog, QFrame, QButtonGroup, QMessageBox)
from PyQt6.QtGui import QShortcut, QKeySequence
from PyQt6.QtCore import Qt

# Import your custom components
from gui.components.pdf_viewer import PDFViewer
from gui.tabs.ocr_tab import OCRTab
from gui.tabs.tts_tab import TTSTab
from gui.tabs.llm_tab import LLMTab
from gui.tabs.notes_tab import NotesTab

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PDF Workspace")
        self.resize(1400, 900)
        self.setMinimumSize(1000, 700)
        self.current_file_path = None

        # Global Dark Theme Styling
        self.setStyleSheet("""
            QMainWindow { background-color: #1e1e1e; }
            QWidget { color: white; font-family: Arial; }
            QPushButton { 
                background-color: transparent; border-radius: 4px; 
                padding: 6px 12px; font-weight: bold; border: 1px solid #444; 
            }
            QPushButton:hover { background-color: #333333; }
            QPushButton:checked { background-color: #0078D7; border: 1px solid #0055ff; }
        """)

        # 1. Main Central Widget & Layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        self.main_layout = QVBoxLayout(central_widget)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        # 2. Build the UI Components
        self._build_top_menu()
        self._build_ocr_banner()
        self._build_workspace()
        
        # 3. Wire up global keyboard shortcuts
        self._setup_shortcuts()

    def _setup_shortcuts(self):
        """Wires up global hotkeys so they work regardless of where the user clicks."""
        QShortcut(QKeySequence("Ctrl+="), self).activated.connect(self.viewer.zoom_in)
        QShortcut(QKeySequence("Ctrl++"), self).activated.connect(self.viewer.zoom_in)
        QShortcut(QKeySequence("Ctrl+-"), self).activated.connect(self.viewer.zoom_out)
        QShortcut(QKeySequence("Ctrl+0"), self).activated.connect(self.viewer.zoom_reset)
        QShortcut(QKeySequence("Ctrl+F"), self).activated.connect(self.viewer.annot_manager.toggle_search)
        QShortcut(QKeySequence("Ctrl+O"), self).activated.connect(self.open_file)

    def _build_top_menu(self):
        """Builds the main top toolbar with file, view, zoom, and tool controls."""
        self.top_menu = QFrame()
        self.top_menu.setStyleSheet("background-color: #1a1a1a; border-bottom: 1px solid #333;")
        self.top_menu.setFixedHeight(55)
        menu_layout = QHBoxLayout(self.top_menu)
        menu_layout.setContentsMargins(10, 5, 10, 5)

        # -- File Operations --
        self.btn_open = QPushButton("📂 Open PDF")
        self.btn_open.setStyleSheet("background-color: #333;")
        self.btn_open.clicked.connect(self.open_file)
        menu_layout.addWidget(self.btn_open)

        menu_layout.addSpacing(20)

        # -- View Mode Toggle --
        self.view_group = QButtonGroup(self)
        self.view_group.setExclusive(True)
        
        self.btn_read = QPushButton("Read")
        self.btn_read.setCheckable(True)
        self.btn_read.setChecked(True)
        self.btn_overview = QPushButton("Overview")
        self.btn_overview.setCheckable(True)
        
        self.view_group.addButton(self.btn_read)
        self.view_group.addButton(self.btn_overview)
        
        self.btn_read.clicked.connect(lambda: self.toggle_view("read"))
        self.btn_overview.clicked.connect(lambda: self.toggle_view("overview"))
        
        menu_layout.addWidget(self.btn_read)
        menu_layout.addWidget(self.btn_overview)

        menu_layout.addSpacing(15)
        hint_label = QLabel("(Shift + Drag to Highlight)")
        hint_label.setStyleSheet("color: #888; font-size: 12px; border: none;")
        menu_layout.addWidget(hint_label)

        menu_layout.addStretch()

        # -- Zoom Controls --
        self.btn_zoom_out = QPushButton("➖")
        self.btn_zoom_out.clicked.connect(self.viewer.zoom_out)
        self.btn_zoom_reset = QPushButton("Fit Width")
        self.btn_zoom_reset.clicked.connect(self.viewer.zoom_reset)
        self.btn_zoom_in = QPushButton("➕")
        self.btn_zoom_in.clicked.connect(self.viewer.zoom_in)
        
        menu_layout.addWidget(self.btn_zoom_out)
        menu_layout.addWidget(self.btn_zoom_reset)
        menu_layout.addWidget(self.btn_zoom_in)

        menu_layout.addStretch()

        # -- Tools Toggle --
        self.tool_group = QButtonGroup(self)
        self.tool_group.setExclusive(True)
        tool_names = ["Notes", "OCR", "Audio (TTS)", "LLM Chat", "Close Tool"]
        self.tool_buttons = {}
        
        for name in tool_names:
            btn = QPushButton(name)
            btn.setCheckable(True)
            if name == "Close Tool":
                btn.setChecked(True)
            self.tool_group.addButton(btn)
            
            # Use a lambda with default argument to capture the correct name in the loop
            btn.clicked.connect(lambda checked, n=name: self.toggle_tool_panel(n))
            menu_layout.addWidget(btn)
            self.tool_buttons[name] = btn

        self.main_layout.addWidget(self.top_menu)

    def _build_ocr_banner(self):
        """Builds the warning banner that appears for scanned documents."""
        self.ocr_banner = QFrame()
        self.ocr_banner.setStyleSheet("background-color: #cc8800; border-bottom: 1px solid #aa6600;")
        self.ocr_banner.setFixedHeight(45)
        banner_layout = QHBoxLayout(self.ocr_banner)
        banner_layout.setContentsMargins(20, 0, 10, 0)
        
        lbl = QLabel("⚠️ This document appears to be scanned and lacks selectable text. Would you like to run OCR?")
        lbl.setStyleSheet("font-weight: bold; color: white; border: none;")
        banner_layout.addWidget(lbl)
        
        banner_layout.addStretch()
        
        btn_run = QPushButton("Run OCR Now")
        btn_run.setStyleSheet("background-color: white; color: black; border: none;")
        btn_run.clicked.connect(self._trigger_auto_ocr)
        banner_layout.addWidget(btn_run)
        
        btn_dismiss = QPushButton("Dismiss")
        btn_dismiss.setStyleSheet("border: 1px solid white; color: white;")
        btn_dismiss.clicked.connect(self.ocr_banner.hide)
        banner_layout.addWidget(btn_dismiss)
        
        self.main_layout.addWidget(self.ocr_banner)
        self.ocr_banner.hide() # Hidden by default

    def _build_workspace(self):
        """Builds the main content area (PDF Viewer on left, Tools on right)."""
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_layout.addWidget(self.splitter, 1) # 1 ensures it expands to fill space

        # Left Side: PDF Viewer
        self.viewer = PDFViewer()
        self.splitter.addWidget(self.viewer)

        # Right Side: Tools Panel (Stacked Widget)
        self.tool_panel = QStackedWidget()
        self.tool_panel.setStyleSheet("QStackedWidget { background-color: #222222; border-left: 1px solid #444; }")
        
        # Instantiate all tool tabs
        self.tabs = {
            "Notes": NotesTab(self.tool_panel, self.viewer),
            "OCR": OCRTab(self.tool_panel, self),
            "Audio (TTS)": TTSTab(self.tool_panel, self),
            "LLM Chat": LLMTab(self.tool_panel, self)
        }
        
        # Add them to the stack
        for tab in self.tabs.values():
            self.tool_panel.addWidget(tab)
            
        self.splitter.addWidget(self.tool_panel)
        self.tool_panel.hide() # Tools hidden by default
        
        # Ensure the PDF viewer takes up all space initially
        self.splitter.setSizes([1400, 0])
        
        # Connect signals
        self.viewer.annot_manager.note_added.connect(self.tabs["Notes"].refresh_notes)

    def open_file(self):
        """Triggers file picker and loads the document."""
        file_path, _ = QFileDialog.getOpenFileName(self, "Open PDF", "", "PDF Files (*.pdf)")
        if file_path:
            self.current_file_path = file_path
            self.setWindowTitle(f"PDF Workspace - {os.path.basename(file_path)}")
            
            success = self.viewer.load_document(file_path)
            if success:
                self._check_needs_ocr()
                self._sync_tools_with_file(file_path)
            else:
                QMessageBox.warning(self, "Error", "Failed to load the PDF document.")

    def _check_needs_ocr(self):
        """Checks if the document is primarily images without text."""
        self.ocr_banner.hide()
        if not self.viewer.doc: return
        
        pages_to_check = min(3, len(self.viewer.doc))
        total_text = "".join([self.viewer.doc.load_page(i).get_text() for i in range(pages_to_check)])
        
        if len(total_text.strip()) < 50:
            self.ocr_banner.show()

    def _trigger_auto_ocr(self):
        """Hides the banner and jumps immediately to the OCR tab."""
        self.ocr_banner.hide()
        self.tool_buttons["OCR"].setChecked(True)
        self.toggle_tool_panel("OCR")

    def _sync_tools_with_file(self, file_path):
        """Alerts all tabs that a new file was opened so they can reset their state."""
        self.tabs["Notes"].refresh_notes()
        for t in ["OCR", "Audio (TTS)", "LLM Chat"]:
            if hasattr(self.tabs[t], "sync_file"):
                self.tabs[t].sync_file(file_path)

    def toggle_view(self, mode):
        """Switches the PDF viewer mode and handles UI button states."""
        self.viewer.set_view_mode(mode)
        
        # Disable zoom controls in overview mode
        is_read_mode = (mode == "read")
        self.btn_zoom_in.setEnabled(is_read_mode)
        self.btn_zoom_out.setEnabled(is_read_mode)
        self.btn_zoom_reset.setEnabled(is_read_mode)

    def toggle_tool_panel(self, tool_name):
        """Shows/hides the right-hand panel and swaps to the correct tab."""
        if tool_name == "Close Tool":
            self.tool_panel.hide()
            self.splitter.setSizes([1400, 0]) # Snap PDF to full width
        else:
            self.tool_panel.show()
            self.tool_panel.setCurrentWidget(self.tabs[tool_name])
            
            # If the panel was closed, give it some width. If it was already open, keep current sizes.
            current_sizes = self.splitter.sizes()
            if current_sizes[1] == 0:
                self.splitter.setSizes([1000, 400])