# gui/main_window.py
import os
import uuid
import fitz
import shutil
from PyQt6.QtWidgets import (QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, 
                             QPushButton, QLabel, QSplitter, QStackedWidget, 
                             QFileDialog, QFrame, QButtonGroup, QMessageBox, QComboBox, QMenu)
from PyQt6.QtGui import QShortcut, QKeySequence
from PyQt6.QtCore import Qt, QSettings, QTimer, QThread

from core.project_manager import ProjectManager
from core.ai_indexing_worker import AIIndexingWorker
from gui.components.pdf_viewer import PDFViewer
from gui.tabs.ocr_tab import OCRTab
from gui.tabs.tts_tab import TTSTab
from gui.tabs.llm_tab import LLMTab
from gui.tabs.notes_tab import NotesTab
from gui.theme import ThemeManager
from gui.components.help_dialog import HelpDialog
from services.workspace_service import WorkspaceService
from services.pdf_service import PDFService
from services.ocr_service import OCRService
from services.persistence_service import PersistenceService
from services.thread_manager import ThreadManager
from controllers.workspace_controller import WorkspaceController
from controllers.pdf_controller import PDFController
from controllers.ocr_controller import OCRController
from controllers.persistence_controller import PersistenceController


class PreloadWorker(QThread):
    def __init__(self, llm_manager, model, parent=None):
        super().__init__(parent)
        self.llm_manager = llm_manager
        self.model = model

    def run(self):
        self.llm_manager.preload_model(self.model)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PDF Workspace")
        self.resize(1400, 900)
        self.setMinimumSize(1000, 700)
        
        self.theme_manager = ThemeManager()
        self.project_manager = ProjectManager()
        self.workspace_service = WorkspaceService(self.project_manager)
        self.workspace_controller = WorkspaceController(self.workspace_service)
        self.pdf_service = PDFService(self.project_manager)
        self.pdf_controller = PDFController(self.pdf_service, main_window=self)
        self.ocr_service = OCRService(self.project_manager)
        self.ocr_controller = OCRController(self.ocr_service, main_window=self)
        self.persistence_service = PersistenceService(self.project_manager)
        self.persistence_controller = PersistenceController(self.persistence_service)
        self.thread_manager = ThreadManager(self)
        self.ai_indexing_worker = None
        self.current_file_path = None
        self.settings = QSettings("PDFMultitool", "Workspace")

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        self.main_layout = QVBoxLayout(central_widget)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        self.status_bar = self.statusBar()
        self.status_bar.show()
        self.status_bar.setVisible(True)
        self.status_bar.setStyleSheet("padding: 4px 8px;")
        self.indexing_status_label = QLabel("")
        self.indexing_status_label.setStyleSheet("font-weight: bold; color: #ffaa00;")
        self.indexing_status_label.setVisible(False)
        self.status_bar.addPermanentWidget(self.indexing_status_label)
        self.indexing_in_progress = False
        self.status_bar.showMessage("Ready")
        self.argument_map_banner = None
        self.btn_generate_argument_map = None

        self.viewer = PDFViewer()

        self._build_top_menu()
        self._build_ocr_banner()
        self._build_argument_map_banner()
        self._build_workspace()
        self._setup_shortcuts()
        
        # Connect Theme Manager to trigger visual updates
        self.theme_manager.theme_changed.connect(self.update_theme)
        self.update_theme(self.theme_manager.get_theme()) # Initial Apply
        
        self.autosave_timer = QTimer(self)
        self.autosave_timer.timeout.connect(self.autosave_project)
        self.autosave_timer.start(5 * 60 * 1000) 
        
        last_project = self.settings.value("last_project", "")
        if last_project and os.path.exists(last_project):
            self._load_project(last_project)

        QTimer.singleShot(1500, self._trigger_background_preload)
        if self.settings.value("show_help_on_startup", True, type=bool):
            # Use a short timer so the main window finishes rendering before the dialog pops up
            QTimer.singleShot(500, self.show_help_window)
            
    def show_help_window(self):
        # We keep a reference to it so it doesn't get garbage collected
        self.help_dialog = HelpDialog(self)
        self.help_dialog.show()

    def _trigger_background_preload(self):
        try:
            default_model = self.tabs["LLM Chat"].model_combo.currentText()
            llm_manager = self.tabs["LLM Chat"].llm_manager
            
            self.preload_worker = PreloadWorker(llm_manager, default_model, parent=self)
            self.preload_worker.start()
        except Exception as e:
            print(f"Could not trigger preload: {e}")

    def _show_indexing_status(self, message):
        print(f"[DEBUG] Status update: {message}")
        if not message:
            return

        if self.indexing_in_progress:
            self.indexing_status_label.setText(message)
            self.indexing_status_label.setVisible(True)
        if hasattr(self, 'status_bar') and self.status_bar:
            self.status_bar.showMessage(message, 0)
        else:
            self.statusBar().showMessage(message, 0)

    def start_background_indexing(self, pdf_paths=None):
        print("[DEBUG] start_background_indexing called")
        if getattr(self, 'ai_indexing_worker', None) and self.ai_indexing_worker.isRunning():
            print("[DEBUG] AI indexing worker already running")
            return

        if not self.pdf_controller.project_filepath:
            print("[DEBUG] No project filepath set")
            return

        if pdf_paths:
            queue = pdf_paths
        else:
            queue = self.pdf_controller.get_unmapped_pdfs()

        if not queue:
            self._show_indexing_status("✅ No PDFs selected for GraphRAG indexing.")
            return

        self.indexing_in_progress = True
        self.indexing_status_label.setVisible(True)
        model_name = self.tabs["LLM Chat"].model_combo.currentText()
        print(f"[DEBUG] Starting AIIndexingWorker with model={model_name}, filepath={self.pdf_controller.project_filepath}, pdf_paths={queue}")
        self.ai_indexing_worker = AIIndexingWorker(
            self.tabs["LLM Chat"].llm_manager,
            model_name,
            self.pdf_controller.project_filepath,
            pdf_paths=queue,
            parent=self
        )
        self.ai_indexing_worker.progress.connect(self._show_indexing_status)
        self.ai_indexing_worker.pdf_mapped.connect(lambda path: self._show_indexing_status(f"Mapped: {os.path.basename(path)}"))
        self.ai_indexing_worker.finished_all.connect(self._on_indexing_finished)

        if "Workspace" in self.tabs:
            self.tabs["Workspace"].lock_ai_tools()
            print("[DEBUG] Locked workspace AI tools")

        if hasattr(self, 'status_bar'):
            print("[DEBUG] Using status_bar for messages")
        else:
            print("[DEBUG] status_bar attribute missing")

        self._show_indexing_status("⏳ Background AI indexing started...")
        if "LLM Chat" in self.tabs and hasattr(self.tabs["LLM Chat"], 'lock_llm_tools'):
            self.tabs["LLM Chat"].lock_llm_tools()
        self.ai_indexing_worker.start()

    def _on_indexing_finished(self, success, msg):
        print(f"[DEBUG] _on_indexing_finished called success={success} msg={msg}")
        if success:
            self._show_indexing_status("✅ Background AI indexing complete.")
        else:
            self._show_indexing_status(f"❌ Background AI indexing failed: {msg}")

        self.indexing_in_progress = False
        self.indexing_status_label.setVisible(False)
        self._set_argument_map_button_state(running=False)
        self._check_needs_argument_map()

        if "Workspace" in self.tabs:
            self.tabs["Workspace"].unlock_ai_tools()
            print("[DEBUG] Unlocked workspace AI tools")
        if "LLM Chat" in self.tabs and hasattr(self.tabs["LLM Chat"], 'unlock_llm_tools'):
            self.tabs["LLM Chat"].unlock_llm_tools()

    def _setup_shortcuts(self):
        QShortcut(QKeySequence("Ctrl+="), self).activated.connect(self.viewer.zoom_in)
        QShortcut(QKeySequence("Ctrl++"), self).activated.connect(self.viewer.zoom_in)
        QShortcut(QKeySequence("Ctrl+-"), self).activated.connect(self.viewer.zoom_out)
        QShortcut(QKeySequence("Ctrl+0"), self).activated.connect(self.viewer.zoom_reset)
        QShortcut(QKeySequence("Ctrl+F"), self).activated.connect(self.viewer.annot_manager.toggle_search)
        QShortcut(QKeySequence("Ctrl+S"), self).activated.connect(self.save_project)

    def _build_top_menu(self):
        self.top_menu = QFrame()
        self.top_menu.setFixedHeight(55)
        menu_layout = QHBoxLayout(self.top_menu)
        menu_layout.setContentsMargins(10, 5, 10, 5)

        self.btn_project = QPushButton("📁 Project ▼")
        
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

        # Theme Selector
        menu_layout.addWidget(QLabel("Theme:"))
        self.theme_selector = QComboBox()
        self.theme_selector.addItems(self.theme_manager.themes.keys())
        self.theme_selector.setCurrentText(self.theme_manager.current_theme_name)
        self.theme_selector.currentTextChanged.connect(self._on_theme_changed)
        menu_layout.addWidget(self.theme_selector)
        
        self.btn_edit_theme = QPushButton("✏️ Edit Custom")
        self.btn_edit_theme.clicked.connect(lambda: self.theme_manager.edit_custom_theme(self))
        menu_layout.addWidget(self.btn_edit_theme)
        
        menu_layout.addSpacing(15)
        self.btn_help = QPushButton("❓ Help")
        self.btn_help.clicked.connect(self.show_help_window)
        menu_layout.addWidget(self.btn_help)
        menu_layout.addSpacing(15)

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

    def _on_theme_changed(self, theme_name):
        if theme_name == "Custom":
            self.theme_manager.edit_custom_theme(self)
            
        self.settings.setValue("theme", theme_name)
        self.theme_manager.set_theme(theme_name)

    def update_theme(self, theme):
        self.top_menu.setStyleSheet(f"background-color: {theme['bg_panel']}; border-bottom: 1px solid {theme['border']};")
        self.ocr_banner.setStyleSheet(f"background-color: {theme['warning']}; border-bottom: 1px solid {theme['border']};")
        self.lbl_ocr_banner.setStyleSheet(f"font-weight: bold; color: #1e1e1e; border: none;") # Dark text for contrast against yellow/warning
        
        for tab in self.tabs.values():
            if hasattr(tab, "update_theme"):
                tab.update_theme(theme)

        if hasattr(self.viewer, "update_theme"):
            self.viewer.update_theme(theme)

    def _clear_ui_for_new_project(self):
        self.current_file_path = None
        self.pdf_selector.blockSignals(True)
        self.pdf_selector.clear()
        self.pdf_selector.blockSignals(False)
        
        if hasattr(self.viewer, 'scene') and self.viewer.scene:
            self.viewer.scene.clear()
        if hasattr(self.viewer, 'doc'):
            self.viewer.doc = None
            
        if "Notes" in self.tabs:
            for i in reversed(range(self.tabs["Notes"].scroll_layout.count())): 
                widget = self.tabs["Notes"].scroll_layout.itemAt(i).widget()
                if widget: widget.deleteLater()
            
            self.tabs["Notes"].workspace_view.scene_obj.clear()
            self.tabs["Notes"].workspace_view.nodes.clear()
            self.tabs["Notes"].workspace_view.edges.clear()

    def _new_project(self):
        path, _ = QFileDialog.getSaveFileName(self, "Create New Project", "", "PDF Project (*.pdfproj)")
        if path:
            if not path.lower().endswith(".pdfproj"):
                path += ".pdfproj"
                
            if self.project_manager.project_filepath:
                self.save_project()
                
            self._clear_ui_for_new_project()
                
            self.project_manager.create_project(path)
            self.settings.setValue("last_project", self.project_manager.project_filepath)
            self._refresh_pdf_dropdown()
            self.setWindowTitle(f"PDF Workspace - {self.project_manager.project_name}")
            self.tabs["LLM Chat"].refresh_project_ui()

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
                
            old_path = self.project_manager.project_filepath
            old_chroma_dir = old_path + "_chroma_db"
            new_chroma_dir = path + "_chroma_db"
            
            if "Notes" in self.tabs and hasattr(self.tabs["Notes"], "save_workspace_state"):
                self.tabs["Notes"].save_workspace_state()
            self.pdf_controller.save_all_docs()
            
            if self.project_manager._conn:
                self.project_manager._conn.close()
                self.project_manager._conn = None
                
            try:
                shutil.copy2(old_path, path)
                if os.path.exists(old_chroma_dir):
                    if os.path.exists(new_chroma_dir):
                        shutil.rmtree(new_chroma_dir)
                    shutil.copytree(old_chroma_dir, new_chroma_dir)
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to copy project database: {e}")
                self.project_manager._init_db() 
                return

            self.project_manager.project_filepath = path
            self.project_manager.project_name = os.path.basename(path).replace(".pdfproj", "")
            
            self.project_manager._init_db()
            cursor = self.project_manager._conn.cursor()
            cursor.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)", 
                           ("project_name", self.project_manager.project_name))
            self.project_manager._conn.commit()
            
            self.tabs["LLM Chat"].refresh_project_ui()
                
            self.settings.setValue("last_project", path)
            self.setWindowTitle(f"PDF Workspace - {self.project_manager.project_name}")

    def _load_project(self, path):
        if self.project_manager.project_filepath:
            self.save_project()
            
        if self.project_manager.load_project(path):
            self._clear_ui_for_new_project()
            
            self.settings.setValue("last_project", self.project_manager.project_filepath)
            self.setWindowTitle(f"PDF Workspace - {self.project_manager.project_name}")
            self._refresh_pdf_dropdown()
            self.tabs["LLM Chat"].refresh_project_ui()
            pdf_paths = self.pdf_controller.get_pdf_paths()
            if pdf_paths:
                self.switch_to_pdf(pdf_paths[0])
        else:
            QMessageBox.warning(self, "Error", "Failed to load project file.")

    def _add_pdf(self):
        if not self.project_manager.project_filepath:
            QMessageBox.warning(self, "No Project", "Please Create or Open a Project first.")
            return
            
        file_paths, _ = QFileDialog.getOpenFileNames(self, "Add PDFs to Project", "", "PDF Files (*.pdf)")
        if file_paths:
            added_paths = self.pdf_controller.add_pdfs(file_paths)
            print(f"[DEBUG] Added PDFs: {added_paths}")
            self._refresh_pdf_dropdown()
            self.switch_to_pdf(added_paths[-1] if added_paths else file_paths[-1])

    def _refresh_pdf_dropdown(self):
        self.pdf_selector.blockSignals(True)
        self.pdf_selector.clear()
        for path in self.pdf_controller.get_pdf_paths():
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

        if self.current_file_path == pdf_path and self.viewer.doc:
            return

        self.current_file_path = pdf_path
        self.pdf_controller.set_active_file(pdf_path)
        
        doc = self.pdf_controller.get_doc(pdf_path)
        if doc:
            success = self.viewer.load_document(doc)
            if success:
                self._check_needs_ocr()
                self._check_needs_argument_map()
                self._sync_tools_with_file(pdf_path)
            else:
                QMessageBox.warning(self, "Error", "Failed to load the PDF document.")
        else:
            QMessageBox.warning(self, "Error", "Failed to access the file from the filesystem.")

    def autosave_project(self):
        if self.project_manager.project_filepath:
            try:
                if "Notes" in self.tabs and hasattr(self.tabs["Notes"], "save_workspace_state"):
                    self.tabs["Notes"].save_workspace_state()
                self.pdf_controller.save_all_docs()
            except Exception as e:
                print(f"Background autosave failed: {e}")

    def save_project(self):
        if not self.project_manager.project_filepath: return
        try:
            if "Notes" in self.tabs and hasattr(self.tabs["Notes"], "save_workspace_state"):
                self.tabs["Notes"].save_workspace_state()
                
            self.pdf_controller.save_all_docs()
            QMessageBox.information(self, "Success", "Project and all highlights saved successfully!")
        except Exception as e:
            QMessageBox.warning(self, "Save Error", f"Error saving project: {str(e)}")

    def add_ai_annotation(self, quote, note, target_doc_name=None, allowed_paths=None, forced_annot_id=None, emit_signal=True):
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

        search_paths = allowed_paths if allowed_paths else self.pdf_controller.get_pdf_paths()
        
        if target_doc_name:
            filtered_paths = []
            for p in search_paths:
                if target_doc_name.lower().strip() in os.path.basename(p).lower():
                    filtered_paths.append(p)
            if filtered_paths:
                search_paths = filtered_paths

        found_any = False

        for path in search_paths:
            try:
                doc = self.pdf_controller.get_doc(path)
                if not doc: continue
                
                for page_num in range(len(doc)):
                    page = doc.load_page(page_num)
                    
                    rects = page.search_for(clean_quote)
                    
                    if not rects and len(chunks) > 1:
                        rects = []
                        for chunk in chunks:
                            res = page.search_for(chunk)
                            if res: rects.extend(res)
                    
                    if rects:
                        quads = [r.quad for r in rects]
                        annot = page.add_highlight_annot(quads)
                        annot.set_colors(stroke=(0.7, 0.4, 1.0))
                        
                        # Apply forced ID if provided for workspace linking
                        annot_id_to_use = forced_annot_id if forced_annot_id else f"AINote|{uuid.uuid4()}"
                        annot_info = {
                            "title": annot_id_to_use,
                            "content": note,
                            "subject": clean_quote
                        }
                        annot.set_info(info=annot_info)
                        annot.update()
                        
                        found_any = True
                        self.pdf_controller.mark_dirty(path)
                        
                        if path == self.current_file_path:
                            self.viewer.reload_page(page_num)
                            
                        # Break out of page loop to avoid duplicates for the same quote
                        break
                
                if found_any and forced_annot_id:
                    break

            except Exception as e:
                print(f"Error adding AI annotation to {path}: {e}")

        # Suspend UI triggers for batched workspace graph building
        if found_any and emit_signal:
            self.viewer.annot_manager.note_added.emit()
            
        return found_any

    def _mark_current_dirty(self):
        if self.current_file_path:
            self.pdf_controller.mark_dirty(self.current_file_path)

    def _build_ocr_banner(self):
        self.ocr_banner = QFrame()
        self.ocr_banner.setFixedHeight(45)
        banner_layout = QHBoxLayout(self.ocr_banner)
        banner_layout.setContentsMargins(20, 0, 10, 0)
        self.lbl_ocr_banner = QLabel("⚠️ Scanned document detected. Run OCR?")
        banner_layout.addWidget(self.lbl_ocr_banner)
        banner_layout.addStretch()
        btn_run = QPushButton("Run OCR")
        btn_run.setStyleSheet("background-color: white; color: black; border: none;")
        btn_run.clicked.connect(self._trigger_auto_ocr)
        banner_layout.addWidget(btn_run)
        btn_dismiss = QPushButton("Dismiss")
        btn_dismiss.setStyleSheet("background-color: transparent; border: 1px solid #1e1e1e; color: #1e1e1e;")
        btn_dismiss.clicked.connect(self.ocr_banner.hide)
        banner_layout.addWidget(btn_dismiss)
        self.main_layout.addWidget(self.ocr_banner)
        self.ocr_banner.hide()

    def _build_argument_map_banner(self):
        self.argument_map_banner = QFrame()
        self.argument_map_banner.setFixedHeight(50)
        banner_layout = QHBoxLayout(self.argument_map_banner)
        banner_layout.setContentsMargins(20, 0, 10, 0)
        self.lbl_argument_map_banner = QLabel(
            "🧠 This PDF has no argument map. Generate one now — it takes about a minute and greatly improves LLM results."
        )
        banner_layout.addWidget(self.lbl_argument_map_banner)
        banner_layout.addStretch()
        self.btn_generate_argument_map_banner = QPushButton("Generate Argument Map")
        self.btn_generate_argument_map_banner.setStyleSheet("background-color: white; color: black; border: none;")
        self.btn_generate_argument_map_banner.clicked.connect(self._trigger_argument_map_generation)
        banner_layout.addWidget(self.btn_generate_argument_map_banner)
        btn_dismiss = QPushButton("Dismiss")
        btn_dismiss.setStyleSheet("background-color: transparent; border: 1px solid #1e1e1e; color: #1e1e1e;")
        btn_dismiss.clicked.connect(self.argument_map_banner.hide)
        banner_layout.addWidget(btn_dismiss)
        # Keep the banner hidden by default; primary UI is floating icon instead
        self.argument_map_banner.hide()

    def _build_workspace(self):
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_layout.addWidget(self.splitter, 1)

        self.viewer_container = QWidget()
        viewer_layout = QHBoxLayout(self.viewer_container)
        viewer_layout.setContentsMargins(0, 0, 0, 0)
        viewer_layout.addWidget(self.viewer)

        self.splitter.addWidget(self.viewer_container)

        self.argument_map_overlay = QWidget(self.viewer_container)
        self.argument_map_overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.argument_map_overlay.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.argument_map_overlay.setStyleSheet("background: transparent;")
        overlay_layout = QHBoxLayout(self.argument_map_overlay)
        overlay_layout.setContentsMargins(0, 0, 0, 0)
        overlay_layout.addStretch()

        self.btn_generate_argument_map_side = QPushButton("🧠 Generate Argument Map", self.argument_map_overlay)
        self.btn_generate_argument_map_side.setFixedHeight(40)
        self.btn_generate_argument_map_side.setToolTip("Generate Argument Map")
        self.btn_generate_argument_map_side.setStyleSheet(
            "QPushButton { background-color: rgba(255, 255, 255, 0.96); color: #1e1e1e; border: 1px solid rgba(0,0,0,0.16); border-radius: 20px; padding: 8px 14px; font-size: 13px; }"
            "QPushButton:hover { background-color: rgba(255, 255, 255, 1.0); }"
        )
        self.btn_generate_argument_map_side.clicked.connect(self._toggle_argument_map_generation)
        overlay_layout.addWidget(self.btn_generate_argument_map_side)
        self.argument_map_overlay.hide()

        self.tool_panel = QStackedWidget()
        
        self.tabs = {
            "Notes": NotesTab(self.tool_panel, self.viewer, self),
            "OCR": OCRTab(self.tool_panel, self),
            "Audio (TTS)": TTSTab(self.tool_panel, self),
            "LLM Chat": LLMTab(self.tool_panel, self)
        }
        self.tabs["Workspace"] = self.tabs["Notes"].workspace_view
        
        for name, tab in self.tabs.items():
            if name == "Workspace":
                continue
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

    def _check_needs_ocr(self):
        self.ocr_banner.hide()
        if not self.viewer.doc: return
        try:
            pages_to_check = min(3, len(self.viewer.doc))
            total_text = "".join([self.viewer.doc.load_page(i).get_text() for i in range(pages_to_check)])
            if len(total_text.strip()) < 50:
                self.ocr_banner.show()
        except: pass

    def _check_needs_argument_map(self):
        if not self.current_file_path:
            self.argument_map_banner.hide()
            if hasattr(self, 'btn_generate_argument_map_side'):
                self.argument_map_overlay.hide()
            return

        has_map = self.pdf_controller.get_document_map(self.current_file_path) is not None
        if has_map:
            self.argument_map_banner.hide()
            if hasattr(self, 'btn_generate_argument_map_side'):
                self._set_argument_map_button_state(running=False)
                self.argument_map_overlay.hide()
        else:
            self.argument_map_banner.hide()  # Hide banner, rely on floating button
            if hasattr(self, 'btn_generate_argument_map_side'):
                self._set_argument_map_button_state(running=False)
                self.argument_map_overlay.show()
                self._position_argument_map_button()

    def _trigger_auto_ocr(self):
        self.ocr_banner.hide()
        self.tool_buttons["OCR"].setChecked(True)
        self.toggle_tool_panel("OCR")

    def _trigger_argument_map_generation(self):
        if not self.current_file_path:
            return

        self.argument_map_banner.hide()
        self._set_argument_map_button_state(running=True)
        self.start_background_indexing([self.current_file_path])

    def _toggle_argument_map_generation(self):
        if getattr(self, 'ai_indexing_worker', None) and self.ai_indexing_worker.isRunning():
            self.ai_indexing_worker.stop()
            self.ai_indexing_worker.wait(3000)
            self._show_indexing_status("⚠️ Argument map generation canceled.")
            self._set_argument_map_button_state(running=False)
            return

        self._trigger_argument_map_generation()

    def _set_argument_map_button_state(self, running: bool):
        if not hasattr(self, 'btn_generate_argument_map_side'):
            return
        if running:
            self.btn_generate_argument_map_side.setText("✖ Cancel")
            self.btn_generate_argument_map_side.setToolTip("Cancel argument map generation")
            self.btn_generate_argument_map_side.setStyleSheet(
                "QPushButton { background-color: rgba(255, 255, 255, 0.96); color: #d32f2f; border: 1px solid rgba(211, 47, 47, 0.22); border-radius: 20px; padding: 8px 14px; font-size: 13px; }"
                "QPushButton:hover { background-color: rgba(255, 255, 255, 1.0); }"
            )
            self.btn_generate_argument_map_side.show()
            if hasattr(self, 'argument_map_overlay'):
                self.argument_map_overlay.show()
        else:
            self.btn_generate_argument_map_side.setText("🧠 Generate Argument Map")
            self.btn_generate_argument_map_side.setToolTip("Generate Argument Map")
            self.btn_generate_argument_map_side.setStyleSheet(
                "QPushButton { background-color: rgba(255, 255, 255, 0.96); color: #1e1e1e; border: 1px solid rgba(0,0,0,0.16); border-radius: 20px; padding: 8px 14px; font-size: 13px; }"
                "QPushButton:hover { background-color: rgba(255, 255, 255, 1.0); }"
            )
            if self.current_file_path and self.pdf_controller.get_document_map(self.current_file_path) is None:
                self.btn_generate_argument_map_side.show()
                if hasattr(self, 'argument_map_overlay'):
                    self.argument_map_overlay.show()
            else:
                self.btn_generate_argument_map_side.hide()
                if hasattr(self, 'argument_map_overlay'):
                    self.argument_map_overlay.hide()

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

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'btn_generate_argument_map_side') and self.btn_generate_argument_map_side.isVisible():
            self._position_argument_map_button()

    def _position_argument_map_button(self):
        if not hasattr(self, 'btn_generate_argument_map_side'):
            return
        if not hasattr(self, 'argument_map_overlay') or not self.viewer_container:
            return

        margin = 18
        width = self.argument_map_overlay.sizeHint().width()
        height = self.argument_map_overlay.sizeHint().height()
        container_width = self.viewer_container.width()
        self.argument_map_overlay.setGeometry(
            container_width - width - margin,
            margin,
            width,
            height
        )
        self.btn_generate_argument_map_side.raise_()

    def closeEvent(self, event):
        """Ensure all background workers are stopped before closing the application."""
        # Stop all managed workers
        self.thread_manager.stop_all_workers()

        # Stop any remaining unmanaged workers
        if self.ai_indexing_worker and self.ai_indexing_worker.isRunning():
            self.ai_indexing_worker.stop()
            self.ai_indexing_worker.wait(3000)

        # Accept the close event
        event.accept()