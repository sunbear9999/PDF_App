import os
import uuid
import fitz
import shutil
from PyQt6.QtWidgets import (QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, 
                             QPushButton, QLabel, QSplitter, QStackedWidget, 
                             QFileDialog, QFrame, QButtonGroup, QMessageBox, QComboBox, QMenu)
from PyQt6.QtGui import QShortcut, QKeySequence
from PyQt6.QtCore import Qt, QSettings, QTimer

from core.project_manager import ProjectManager
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
        
        self.project_manager = ProjectManager()
        self.current_file_path = None
        self.settings = QSettings("PDFMultitool", "Workspace")

        self.setStyleSheet("""
            QMainWindow { background-color: #1e1e1e; }
            QWidget { color: white; font-family: Arial; }
            QPushButton { background-color: transparent; border-radius: 4px; padding: 6px 12px; font-weight: bold; border: 1px solid #444; }
            QPushButton:hover { background-color: #333333; }
            QPushButton:checked { background-color: #0078D7; border: 1px solid #0055ff; }
            QComboBox { background-color: #333; border: 1px solid #555; padding: 4px; border-radius: 4px; color: white;}
            QMenu { background-color: #2b2b2b; color: white; border: 1px solid #444; }
            QMenu::item:selected { background-color: #0078D7; }
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
        
        # AUTOSAVE SYSTEM: Silently runs every 5 minutes (300,000 ms)
        self.autosave_timer = QTimer(self)
        self.autosave_timer.timeout.connect(self.autosave_project)
        self.autosave_timer.start(5 * 60 * 1000) 
        
        last_project = self.settings.value("last_project", "")
        if last_project and os.path.exists(last_project):
            self._load_project(last_project)

    def _setup_shortcuts(self):
        QShortcut(QKeySequence("Ctrl+="), self).activated.connect(self.viewer.zoom_in)
        QShortcut(QKeySequence("Ctrl++"), self).activated.connect(self.viewer.zoom_in)
        QShortcut(QKeySequence("Ctrl+-"), self).activated.connect(self.viewer.zoom_out)
        QShortcut(QKeySequence("Ctrl+0"), self).activated.connect(self.viewer.zoom_reset)
        QShortcut(QKeySequence("Ctrl+F"), self).activated.connect(self.viewer.annot_manager.toggle_search)
        QShortcut(QKeySequence("Ctrl+S"), self).activated.connect(self.save_project)

    def _build_top_menu(self):
        self.top_menu = QFrame()
        self.top_menu.setStyleSheet("background-color: #1a1a1a; border-bottom: 1px solid #333;")
        self.top_menu.setFixedHeight(55)
        menu_layout = QHBoxLayout(self.top_menu)
        menu_layout.setContentsMargins(10, 5, 10, 5)

        self.btn_project = QPushButton("📁 Project ▼")
        self.btn_project.setStyleSheet("background-color: #333; padding: 6px 15px;")
        
        project_menu = QMenu(self)
        project_menu.addAction("New Project...", self._new_project)
        project_menu.addAction("Open Project...", self._open_project)
        project_menu.addAction("Save Project As...", self._save_project_as)
        project_menu.addSeparator()
        project_menu.addAction("Add PDF to Project...", self._add_pdf)
        self.btn_project.setMenu(project_menu)
        menu_layout.addWidget(self.btn_project)
        menu_layout.addSpacing(15)

        menu_layout.addWidget(QLabel("Active PDF:"))
        self.pdf_selector = QComboBox()
        self.pdf_selector.setFixedWidth(250)
        self.pdf_selector.currentIndexChanged.connect(self._on_pdf_dropdown_changed)
        menu_layout.addWidget(self.pdf_selector)
        
        menu_layout.addSpacing(15)
        self.btn_save = QPushButton("💾 Save Project")
        self.btn_save.setStyleSheet("background-color: #333;")
        self.btn_save.clicked.connect(self.save_project)
        menu_layout.addWidget(self.btn_save)
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
            if name == "Close Tool": btn.setChecked(True)
            self.tool_group.addButton(btn)
            btn.clicked.connect(lambda checked, n=name: self.toggle_tool_panel(n))
            menu_layout.addWidget(btn)
            self.tool_buttons[name] = btn

        self.main_layout.addWidget(self.top_menu)

    def _new_project(self):
        path, _ = QFileDialog.getSaveFileName(self, "Create New Project", "", "PDF Project (*.pdfproj)")
        if path:
            if not path.lower().endswith(".pdfproj"):
                path += ".pdfproj"
                
            self.project_manager.create_project(path)
            self.settings.setValue("last_project", self.project_manager.project_filepath)
            self._refresh_pdf_dropdown()
            self.setWindowTitle(f"PDF Workspace - {self.project_manager.project_name}")

    def _open_project(self):
        dialog = QFileDialog(self, "Open Project")
        dialog.setNameFilter("PDF Project (*.pdfproj);;All Files (*)")
        
        if dialog.exec():
            path = dialog.selectedFiles()[0]
            self._load_project(path)

    def _save_project_as(self):
        if not self.project_manager.project_filepath:
            QMessageBox.warning(self, "No Project", "Create or open a project first.")
            return
            
        path, _ = QFileDialog.getSaveFileName(self, "Save Project As", "", "PDF Project (*.pdfproj)")
        if path:
            if not path.lower().endswith(".pdfproj"):
                path += ".pdfproj"
                
            old_index = self.project_manager.project_filepath + ".index.json"
            
            self.project_manager.project_filepath = path
            self.project_manager.project_name = os.path.basename(path).replace(".pdfproj", "")
            
            self.project_manager.save_all_docs()
            self.project_manager.save_project()
            
            new_index = path + ".index.json"
            if os.path.exists(old_index):
                try: shutil.copy(old_index, new_index)
                except: pass
                
            self.settings.setValue("last_project", path)
            self.setWindowTitle(f"PDF Workspace - {self.project_manager.project_name}")
            QMessageBox.information(self, "Success", "Project duplicated successfully!")

    def _load_project(self, path):
        if self.project_manager.load_project(path):
            self.settings.setValue("last_project", self.project_manager.project_filepath)
            self.setWindowTitle(f"PDF Workspace - {self.project_manager.project_name}")
            self._refresh_pdf_dropdown()
            self.tabs["LLM Chat"].refresh_project_ui()
            if self.project_manager.pdfs:
                self.switch_to_pdf(self.project_manager.pdfs[0])
        else:
            QMessageBox.warning(self, "Error", "Failed to load project file.")

    def _add_pdf(self):
        if not self.project_manager.project_filepath:
            QMessageBox.warning(self, "No Project", "Please Create or Open a Project first.")
            return
            
        file_paths, _ = QFileDialog.getOpenFileNames(self, "Add PDFs to Project", "", "PDF Files (*.pdf)")
        for path in file_paths:
            self.project_manager.add_pdf(path)
            
        if file_paths:
            self._refresh_pdf_dropdown()
            self.switch_to_pdf(file_paths[-1])

    def _refresh_pdf_dropdown(self):
        self.pdf_selector.blockSignals(True)
        self.pdf_selector.clear()
        for path in self.project_manager.pdfs:
            self.pdf_selector.addItem(os.path.basename(path), userData=path)
        self.pdf_selector.blockSignals(False)

    def _on_pdf_dropdown_changed(self, index):
        if index >= 0:
            pdf_path = self.pdf_selector.itemData(index)
            self.switch_to_pdf(pdf_path)

    def switch_to_pdf(self, pdf_path):
        if not os.path.exists(pdf_path): return
        
        idx = self.pdf_selector.findData(pdf_path)
        if idx >= 0 and self.pdf_selector.currentIndex() != idx:
            self.pdf_selector.blockSignals(True)
            self.pdf_selector.setCurrentIndex(idx)
            self.pdf_selector.blockSignals(False)

        self.current_file_path = pdf_path
        
        self.project_manager.set_active_file(pdf_path)
        
        doc = self.project_manager.get_doc(pdf_path)
        success = self.viewer.load_document(doc)
        
        if success:
            self._check_needs_ocr()
            self._sync_tools_with_file(pdf_path)
        else:
            QMessageBox.warning(self, "Error", "Failed to load the PDF document.")

    def autosave_project(self):
        if self.project_manager.project_filepath:
            try:
                self.project_manager.save_all_docs()
                self.project_manager.save_project()
            except Exception as e:
                print(f"Background autosave failed: {e}")

    def save_project(self):
        try:
            self.project_manager.save_all_docs()
            self.project_manager.save_project()
            QMessageBox.information(self, "Success", "Project and all highlights saved successfully!")
        except Exception as e:
            QMessageBox.warning(self, "Save Error", f"Error saving project: {str(e)}")

    def add_ai_annotation(self, quote, note, target_doc_name=None):
        if not quote: return False
        clean_quote = quote.strip()
        words = clean_quote.split()
        if not words: return False
        
        chunks = []
        if len(words) <= 6:
            chunks = [" ".join(words)]
        else:
            for i in range(0, len(words), 4):
                chunk = " ".join(words[i:i+6])
                if chunk.strip(): chunks.append(chunk)

        found_any = False

        search_paths = []
        target_path = None
        if target_doc_name:
            for p in self.project_manager.pdfs:
                if os.path.basename(p).lower() == target_doc_name.strip().lower():
                    target_path = p
                    break
                    
        if target_path:
            search_paths = [target_path] + [p for p in self.project_manager.pdfs if p != target_path]
        else:
            search_paths = self.project_manager.pdfs

        for path in search_paths:
            doc = self.project_manager.get_doc(path)
            
            for page_num in range(len(doc)):
                page = doc.load_page(page_num)
                rects = page.search_for(" ".join(words))
                
                if not rects and len(chunks) > 1:
                    rects = []
                    for chunk in chunks:
                        res = page.search_for(chunk)
                        if res: rects.extend(res)
                
                if rects:
                    annot = page.add_highlight_annot(rects)
                    annot.set_colors(stroke=(0.7, 0.4, 1.0))
                    annot.set_info(title=f"AINote|{uuid.uuid4()}", content=note, subject=clean_quote)
                    annot.update()
                    
                    found_any = True
                    
                    self.project_manager.mark_dirty(path)
                    
                    if path == self.current_file_path:
                        self.viewer.reload_page(page_num)

            if found_any:
                break 

        if found_any:
            self.viewer.annot_manager.note_added.emit()
            
        return found_any

    def _mark_current_dirty(self):
        if self.current_file_path:
            self.project_manager.mark_dirty(self.current_file_path)

    def _build_ocr_banner(self):
        self.ocr_banner = QFrame()
        self.ocr_banner.setStyleSheet("background-color: #cc8800; border-bottom: 1px solid #aa6600;")
        self.ocr_banner.setFixedHeight(45)
        banner_layout = QHBoxLayout(self.ocr_banner)
        banner_layout.setContentsMargins(20, 0, 10, 0)
        lbl = QLabel("⚠️ Scanned document detected. Run OCR?")
        lbl.setStyleSheet("font-weight: bold; color: white; border: none;")
        banner_layout.addWidget(lbl)
        banner_layout.addStretch()
        btn_run = QPushButton("Run OCR")
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
            "Notes": NotesTab(self.tool_panel, self.viewer, self),
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
        self.viewer.annot_manager.note_added.connect(self._mark_current_dirty)
        self.viewer.annotation_clicked.connect(self._on_annotation_clicked)

    def _on_annotation_clicked(self, annot_id):
        self.tool_buttons["Notes"].setChecked(True)
        self.toggle_tool_panel("Notes")
        self.tabs["Notes"].scroll_to_note(annot_id)

    # --- MISSING METHODS RESTORED BELOW ---
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
        self.tabs["LLM Chat"].refresh_project_ui()
        for t in ["OCR", "Audio (TTS)"]:
            if hasattr(self.tabs[t], "sync_file"):
                self.tabs[t].sync_file(file_path)

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