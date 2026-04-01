import os
import uuid # ADDED UUID IMPORT
import fitz
from PyQt6.QtWidgets import (QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, 
                             QPushButton, QLabel, QSplitter, QStackedWidget, 
                             QFileDialog, QFrame, QButtonGroup, QMessageBox)
from PyQt6.QtGui import QShortcut, QKeySequence
from PyQt6.QtCore import Qt, QSettings

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
        
        self.settings = QSettings("PDFMultitool", "Workspace")

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

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        self.main_layout = QVBoxLayout(central_widget)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        self.viewer = PDFViewer()

        self._build_top_menu()
        self._build_ocr_banner()
        self._build_workspace()
        self._setup_shortcuts()
        
        last_file = self.settings.value("last_file", "")
        if last_file and os.path.exists(last_file):
            self._load_file(last_file)

    def add_ai_annotation(self, quote, note):
        """Called autonomously by the LLM agent to inject highlights directly into the document."""
        if not self.viewer.doc: return
        found = False
        
        for page_num in range(len(self.viewer.doc)):
            page = self.viewer.doc.load_page(page_num)
            rects = page.search_for(quote)
            
            if rects:
                annot = page.add_highlight_annot(rects)
                annot.set_colors(stroke=(0.7, 0.4, 1.0)) # Purple AI Highlight
                # Tag it specifically as an AINote
                annot.set_info(title=f"AINote|{uuid.uuid4()}", content=note, subject=quote)
                annot.update()
                
                self.viewer.reload_page(page_num)
                found = True
                break # Just highlight the first occurrence so we don't spam the PDF
                
        if found:
            self.viewer.annot_manager.note_added.emit()

    def _setup_shortcuts(self):
        QShortcut(QKeySequence("Ctrl+="), self).activated.connect(self.viewer.zoom_in)
        QShortcut(QKeySequence("Ctrl++"), self).activated.connect(self.viewer.zoom_in)
        QShortcut(QKeySequence("Ctrl+-"), self).activated.connect(self.viewer.zoom_out)
        QShortcut(QKeySequence("Ctrl+0"), self).activated.connect(self.viewer.zoom_reset)
        QShortcut(QKeySequence("Ctrl+F"), self).activated.connect(self.viewer.annot_manager.toggle_search)
        QShortcut(QKeySequence("Ctrl+O"), self).activated.connect(self.open_file)
        QShortcut(QKeySequence("Ctrl+S"), self).activated.connect(self.save_file)

    def _build_top_menu(self):
        self.top_menu = QFrame()
        self.top_menu.setStyleSheet("background-color: #1a1a1a; border-bottom: 1px solid #333;")
        self.top_menu.setFixedHeight(55)
        menu_layout = QHBoxLayout(self.top_menu)
        menu_layout.setContentsMargins(10, 5, 10, 5)

        self.btn_open = QPushButton("📂 Open")
        self.btn_open.setStyleSheet("background-color: #333;")
        self.btn_open.clicked.connect(self.open_file)
        menu_layout.addWidget(self.btn_open)
        
        self.btn_save = QPushButton("💾 Save PDF")
        self.btn_save.setStyleSheet("background-color: #333;")
        self.btn_save.clicked.connect(self.save_file)
        menu_layout.addWidget(self.btn_save)

        menu_layout.addSpacing(20)

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
            
            btn.clicked.connect(lambda checked, n=name: self.toggle_tool_panel(n))
            menu_layout.addWidget(btn)
            self.tool_buttons[name] = btn

        self.main_layout.addWidget(self.top_menu)

    def _build_ocr_banner(self):
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
        self.ocr_banner.hide()

    def _build_workspace(self):
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_layout.addWidget(self.splitter, 1)

        self.splitter.addWidget(self.viewer)

        self.tool_panel = QStackedWidget()
        self.tool_panel.setStyleSheet("QStackedWidget { background-color: #222222; border-left: 1px solid #444; }")
        
        self.tabs = {
            "Notes": NotesTab(self.tool_panel, self.viewer),
            "OCR": OCRTab(self.tool_panel, self),
            "Audio (TTS)": TTSTab(self.tool_panel, self),
            "LLM Chat": LLMTab(self.tool_panel, self)
        }
        
        for tab in self.tabs.values():
            self.tool_panel.addWidget(tab)
            
        self.splitter.addWidget(self.tool_panel)
        self.tool_panel.hide()
        
        self.splitter.setSizes([1400, 0])
        
        self.viewer.annot_manager.note_added.connect(self.tabs["Notes"].refresh_notes)
        self.viewer.annotation_clicked.connect(self._on_annotation_clicked)

    def open_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Open PDF", "", "PDF Files (*.pdf)")
        if file_path:
            self._load_file(file_path)
            
    def _load_file(self, file_path):
        self.current_file_path = file_path
        self.setWindowTitle(f"PDF Workspace - {os.path.basename(file_path)}")
        
        success = self.viewer.load_document(file_path)
        if success:
            self._check_needs_ocr()
            self._sync_tools_with_file(file_path)
            self.settings.setValue("last_file", file_path)
        else:
            QMessageBox.warning(self, "Error", "Failed to load the PDF document.")

    def save_file(self):
        if self.current_file_path and self.viewer.doc:
            try:
                self.viewer.doc.saveIncr()
                QMessageBox.information(self, "Success", "Annotations saved to PDF successfully!")
            except Exception as e:
                QMessageBox.warning(self, "Save Error", f"Could not save file directly. Ensure it isn't locked or read-only.\nError: {str(e)}")

    def _on_annotation_clicked(self, annot_id):
        self.tool_buttons["Notes"].setChecked(True)
        self.toggle_tool_panel("Notes")
        self.tabs["Notes"].scroll_to_note(annot_id)

    def _check_needs_ocr(self):
        self.ocr_banner.hide()
        if not self.viewer.doc: return
        
        pages_to_check = min(3, len(self.viewer.doc))
        total_text = "".join([self.viewer.doc.load_page(i).get_text() for i in range(pages_to_check)])
        
        if len(total_text.strip()) < 50:
            self.ocr_banner.show()

    def _trigger_auto_ocr(self):
        self.ocr_banner.hide()
        self.tool_buttons["OCR"].setChecked(True)
        self.toggle_tool_panel("OCR")

    def _sync_tools_with_file(self, file_path):
        self.tabs["Notes"].refresh_notes()
        for t in ["OCR", "Audio (TTS)", "LLM Chat"]:
            if hasattr(self.tabs[t], "sync_file"):
                self.tabs[t].sync_file(file_path)

    def toggle_view(self, mode):
        pass

    def toggle_tool_panel(self, tool_name):
        if tool_name == "Close Tool":
            self.tool_panel.hide()
            self.splitter.setSizes([1400, 0])
        else:
            self.tool_panel.show()
            self.tool_panel.setCurrentWidget(self.tabs[tool_name])
            
            current_sizes = self.splitter.sizes()
            if current_sizes[1] == 0:
                self.splitter.setSizes([1000, 400])