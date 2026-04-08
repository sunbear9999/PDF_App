# gui/main_window.py
import os
import uuid
import fitz
import shutil
from PyQt6.QtWidgets import (QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
                             QPushButton, QLabel, QSplitter,
                             QFileDialog, QFrame, QButtonGroup, QMessageBox, QComboBox, QMenu, QDockWidget)
from PyQt6.QtGui import QShortcut, QKeySequence
from PyQt6.QtCore import Qt, QSettings, QTimer, QThread

from core.project_manager import ProjectManager
from core.ai_indexing_worker import AIIndexingWorker
from gui.components.pdf_viewer import PDFViewer
from gui.components.workspace_view import WorkspaceView
from gui.dock_panels.ocr_dock import OCRDockWidget
from gui.dock_panels.tts_dock import TTSDockWidget
from gui.dock_panels.llm_dock import LLMDockWidget
from gui.dock_panels.notes_dock import NotesDockWidget
from gui.theme import ThemeManager
from gui.components.help_dialog import HelpDialog
from gui.menu_builder import MenuBuilder
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

class FloatingToolPalette(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setObjectName("FloatingToolPalette")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._drag_position = None
        self.is_minimized = False
        self.setFixedSize(260, 180)

        self.container = QFrame(self)
        self.container.setObjectName("ToolPaletteContainer")
        self.container.setStyleSheet(
            "QFrame#ToolPaletteContainer { background-color: rgba(28, 38, 48, 0.96); border: 1px solid rgba(255,255,255,0.12); border-radius: 12px; }"
            "QPushButton { background-color: rgba(255,255,255,0.06); color: #ffffff; border: 1px solid rgba(255,255,255,0.14); border-radius: 6px; padding: 6px 8px; }"
            "QPushButton:hover { background-color: rgba(255,255,255,0.14); }"
        )

        self.container.setGeometry(0, 0, 260, 180)
        self.container_layout = QVBoxLayout(self.container)
        self.container_layout.setContentsMargins(10, 10, 10, 10)
        self.container_layout.setSpacing(8)

        title_layout = QHBoxLayout()
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.addWidget(QLabel("Tools"))
        title_layout.addStretch()

        self.btn_minimize = QPushButton("—")
        self.btn_minimize.setFixedSize(24, 24)
        self.btn_minimize.clicked.connect(self.toggle_minimize)
        title_layout.addWidget(self.btn_minimize)

        self.container_layout.addLayout(title_layout)

        self.body = QWidget(self.container)
        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(6)

        tool_row = QHBoxLayout()
        self.tool_buttons = {}
        for label, name in [("Notes", "Notes"), ("OCR", "OCR"), ("Audio", "Audio (TTS)"), ("LLM", "LLM Chat")]:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked, n=name: self.on_tool_clicked(n))
            btn.setFixedHeight(32)
            tool_row.addWidget(btn)
            self.tool_buttons[name] = btn
        body_layout.addLayout(tool_row)

        zoom_row = QHBoxLayout()
        self.btn_zoom_out = QPushButton("➖")
        self.btn_zoom_out.setFixedHeight(32)
        self.btn_zoom_out.clicked.connect(lambda: self.parent().viewer.zoom_out())
        self.btn_zoom_reset = QPushButton("Fit")
        self.btn_zoom_reset.setFixedHeight(32)
        self.btn_zoom_reset.clicked.connect(lambda: self.parent().viewer.zoom_reset())
        self.btn_zoom_in = QPushButton("➕")
        self.btn_zoom_in.setFixedHeight(32)
        self.btn_zoom_in.clicked.connect(lambda: self.parent().viewer.zoom_in())
        zoom_row.addWidget(self.btn_zoom_out)
        zoom_row.addWidget(self.btn_zoom_reset)
        zoom_row.addWidget(self.btn_zoom_in)
        body_layout.addLayout(zoom_row)

        theme_row = QHBoxLayout()
        theme_label = QLabel("Theme:")
        theme_label.setStyleSheet("color: #e5e5e5; font-weight: bold;")
        self.theme_combo = QComboBox(self.body)
        self.theme_combo.addItems(list(ThemeManager().themes.keys()))
        self.theme_combo.setCurrentText(ThemeManager().current_theme_name)
        self.theme_combo.currentTextChanged.connect(self.on_theme_changed)
        self.theme_combo.setFixedHeight(32)
        theme_row.addWidget(theme_label)
        theme_row.addWidget(self.theme_combo)

        self.btn_edit_theme = QPushButton("Edit")
        self.btn_edit_theme.setFixedHeight(32)
        self.btn_edit_theme.clicked.connect(self.on_edit_theme)
        theme_row.addWidget(self.btn_edit_theme)
        body_layout.addLayout(theme_row)

        self.container_layout.addWidget(self.body)

    def on_tool_clicked(self, tool_name):
        if self.parent() and hasattr(self.parent(), 'toggle_tool_panel'):
            self.parent().toggle_tool_panel(tool_name)
            self.update_tool_button(tool_name, self.parent().dock_widgets.get(tool_name).isVisible() if self.parent() else False)

    def on_theme_changed(self, theme_name):
        if self.parent() and hasattr(self.parent(), '_on_theme_changed'):
            self.parent()._on_theme_changed(theme_name)

    def on_edit_theme(self):
        if self.parent() and hasattr(self.parent(), '_on_theme_changed'):
            self.parent()._on_theme_changed("Custom")
            self.theme_combo.setCurrentText("Custom")

    def set_tool_state(self, tool_name, visible):
        if tool_name in self.tool_buttons:
            self.tool_buttons[tool_name].setChecked(visible)

    def update_tool_button(self, tool_name, visible):
        self.set_tool_state(tool_name, visible)

    def toggle_minimize(self):
        self.is_minimized = not self.is_minimized
        self.body.setVisible(not self.is_minimized)
        self.btn_minimize.setText("+" if self.is_minimized else "—")
        self.setFixedHeight(44 if self.is_minimized else 220)
        self.container.setFixedHeight(44 if self.is_minimized else 220)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_position and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_position)
            event.accept()

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

        self.status_bar = self.statusBar()
        self.status_bar.show()
        self.status_bar.setVisible(True)
        self.indexing_status_label = QLabel("")
        self.indexing_status_label.setVisible(False)
        self.status_bar.addPermanentWidget(self.indexing_status_label)
        self.indexing_in_progress = False
        self.status_bar.showMessage("Ready")

        self.viewer = PDFViewer()

        # Build central splitter with PDFViewer and WorkspaceView
        self._build_central_splitter()

        # Build dock widgets
        self._build_dock_widgets()

        # Build menu
        self.menu_builder = MenuBuilder(self)
        self.menu_builder.build_menu()

        # Create a compact top toolbar with project selector only
        self.toolbar = self.addToolBar("Main")
        self.toolbar.setMovable(False)
        self.toolbar.setFloatable(False)
        title_label = QLabel("<b>PDF Workspace</b>")
        self.toolbar.addWidget(title_label)
        self.toolbar.addSeparator()
        self.toolbar.addWidget(QLabel("Active PDF:"))
        self.pdf_selector = QComboBox()
        self.pdf_selector.setFixedWidth(250)
        self.pdf_selector.currentIndexChanged.connect(self._on_pdf_dropdown_changed)
        self.toolbar.addWidget(self.pdf_selector)

        self._setup_shortcuts()
        self._build_tool_palette()

        # Connect the palette to dock widget visibility states
        for name, dock in self.dock_widgets.items():
            dock.visibilityChanged.connect(lambda visible, n=name: self.tool_palette.set_tool_state(n, visible))
        self._sync_tool_palette_buttons()

        # Connect Theme Manager to trigger visual updates
        self.theme_manager.theme_changed.connect(self.update_theme)
        self.update_theme(self.theme_manager.get_theme())  # Initial Apply

        self.autosave_timer = QTimer(self)
        self.autosave_timer.timeout.connect(self.autosave_project)
        self.autosave_timer.start(5 * 60 * 1000)

        last_project = self.settings.value("last_project", "")
        if last_project and os.path.exists(last_project):
            self._load_project(last_project)

        QTimer.singleShot(1500, self._trigger_background_preload)
        if self.settings.value("show_help_on_startup", True, type=bool):
            QTimer.singleShot(500, self.show_help_window)
            
    def show_help_window(self):
        # We keep a reference to it so it doesn't get garbage collected
        self.help_dialog = HelpDialog(self)
        self.help_dialog.show()

    def _trigger_background_preload(self):
        try:
            default_model = self.dock_widgets["LLM Chat"].model_combo.currentText()
            llm_manager = self.dock_widgets["LLM Chat"].llm_manager
            
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
        model_name = self.dock_widgets["LLM Chat"].model_combo.currentText()
        print(f"[DEBUG] Starting AIIndexingWorker with model={model_name}, filepath={self.pdf_controller.project_filepath}, pdf_paths={queue}")
        self.ai_indexing_worker = AIIndexingWorker(
            self.dock_widgets["LLM Chat"].llm_manager,
            model_name,
            self.pdf_controller.project_filepath,
            pdf_paths=queue,
            parent=self
        )
        self.ai_indexing_worker.progress.connect(self._show_indexing_status)
        self.ai_indexing_worker.pdf_mapped.connect(lambda path: self._show_indexing_status(f"Mapped: {os.path.basename(path)}"))
        self.ai_indexing_worker.finished_all.connect(self._on_indexing_finished)

        if hasattr(self, 'workspace_view'):
            self.workspace_view.lock_ai_tools()
            print("[DEBUG] Locked workspace AI tools")

        if hasattr(self, 'status_bar'):
            print("[DEBUG] Using status_bar for messages")
        else:
            print("[DEBUG] status_bar attribute missing")

        self._show_indexing_status("⏳ Background AI indexing started...")
        if "LLM Chat" in self.dock_widgets and hasattr(self.dock_widgets["LLM Chat"], 'lock_llm_tools'):
            self.dock_widgets["LLM Chat"].lock_llm_tools()
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

        if hasattr(self, 'workspace_view'):
            self.workspace_view.unlock_ai_tools()
            print("[DEBUG] Unlocked workspace AI tools")
        if "LLM Chat" in self.dock_widgets and hasattr(self.dock_widgets["LLM Chat"], 'unlock_llm_tools'):
            self.dock_widgets["LLM Chat"].unlock_llm_tools()

    def _setup_shortcuts(self):
        QShortcut(QKeySequence("Ctrl+="), self).activated.connect(self.viewer.zoom_in)
        QShortcut(QKeySequence("Ctrl++"), self).activated.connect(self.viewer.zoom_in)
        QShortcut(QKeySequence("Ctrl+-"), self).activated.connect(self.viewer.zoom_out)
        QShortcut(QKeySequence("Ctrl+0"), self).activated.connect(self.viewer.zoom_reset)
        QShortcut(QKeySequence("Ctrl+F"), self).activated.connect(self.viewer.annot_manager.toggle_search)
        QShortcut(QKeySequence("Ctrl+S"), self).activated.connect(self.save_project)



    def _on_theme_changed(self, theme_name):
        if theme_name == "Custom":
            self.theme_manager.edit_custom_theme(self)
            
        self.settings.setValue("theme", theme_name)
        self.theme_manager.set_theme(theme_name)

    def update_theme(self, theme):
        # Theme is now applied globally via QSS, but we can still update specific components
        for dock in self.dock_widgets.values():
            if hasattr(dock, "update_theme"):
                dock.update_theme(theme)

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
            
        if "Notes" in self.dock_widgets:
            notes_dock = self.dock_widgets["Notes"]
            for i in reversed(range(notes_dock.scroll_layout.count())): 
                widget = notes_dock.scroll_layout.itemAt(i).widget()
                if widget: widget.deleteLater()
            
            if hasattr(self, 'workspace_view'):
                self.workspace_view.scene_obj.clear()
                self.workspace_view.nodes.clear()
                self.workspace_view.edges.clear()

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
            self.dock_widgets["LLM Chat"].refresh_project_ui()

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
            
            if "Notes" in self.dock_widgets and hasattr(self.dock_widgets["Notes"], "save_workspace_state"):
                self.dock_widgets["Notes"].save_workspace_state()
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
            
            self.dock_widgets["LLM Chat"].refresh_project_ui()
                
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
            self.dock_widgets["LLM Chat"].refresh_project_ui()
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
                if "Notes" in self.dock_widgets and hasattr(self.dock_widgets["Notes"], "save_workspace_state"):
                    self.dock_widgets["Notes"].save_workspace_state()
                self.pdf_controller.save_all_docs()
            except Exception as e:
                print(f"Background autosave failed: {e}")

    def save_project(self):
        if not self.project_manager.project_filepath: return
        try:
            if "Notes" in self.dock_widgets and hasattr(self.dock_widgets["Notes"], "save_workspace_state"):
                self.dock_widgets["Notes"].save_workspace_state()
                
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

    def _build_central_splitter(self):
        # Central widget is a splitter with PDFViewer (left) and WorkspaceView (right)
        self.central_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.setCentralWidget(self.central_splitter)

        # Left: PDF Viewer
        self.viewer_container = QWidget()
        viewer_layout = QHBoxLayout(self.viewer_container)
        viewer_layout.setContentsMargins(0, 0, 0, 0)
        viewer_layout.addWidget(self.viewer)
        self.central_splitter.addWidget(self.viewer_container)

        # Right: Workspace View (from notes dock)
        self.workspace_view = WorkspaceView(self)
        self.workspace_container = QWidget()
        workspace_layout = QHBoxLayout(self.workspace_container)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.addWidget(self.workspace_view)
        self.central_splitter.addWidget(self.workspace_container)

        self.central_splitter.setSizes([800, 600])

    def _build_dock_widgets(self):
        self.dock_widgets = {}

        # OCR Dock
        ocr_dock = OCRDockWidget(main_window=self)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, ocr_dock)
        self.dock_widgets["OCR"] = ocr_dock
        ocr_dock.hide()

        # TTS Dock
        tts_dock = TTSDockWidget(main_window=self)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, tts_dock)
        self.dock_widgets["Audio (TTS)"] = tts_dock
        tts_dock.hide()

        # LLM Dock
        llm_dock = LLMDockWidget(main_window=self)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, llm_dock)
        self.dock_widgets["LLM Chat"] = llm_dock
        llm_dock.hide()

        # Notes Dock
        notes_dock = NotesDockWidget(viewer=self.viewer, main_window=self)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, notes_dock)
        self.dock_widgets["Notes"] = notes_dock
        notes_dock.hide()

        # Connect signals
        self.viewer.annot_manager.note_added.connect(notes_dock.refresh_notes)
        self.viewer.annot_manager.note_added.connect(self._mark_current_dirty)
        self.viewer.annotation_clicked.connect(self._on_annotation_clicked)

    def _build_tool_palette(self):
        self.tool_palette = FloatingToolPalette(self)
        self.tool_palette.move(self.width() - self.tool_palette.width() - 40, 110)
        self.tool_palette.show()
        # Start the palette in a compact state to keep the workspace clean
        self.tool_palette.toggle_minimize()

    def _on_annotation_clicked(self, annot_id):
        self.toggle_tool_panel("Notes")
        self.dock_widgets["Notes"].scroll_to_note(annot_id)

    def _check_needs_ocr(self):
        # OCR check - could show OCR dock if needed
        if not self.viewer.doc: return
        try:
            pages_to_check = min(3, len(self.viewer.doc))
            total_text = "".join([self.viewer.doc.load_page(i).get_text() for i in range(pages_to_check)])
            if len(total_text.strip()) < 50:
                # Could show OCR dock here
                pass
        except: pass

    def _check_needs_argument_map(self):
        if not self.current_file_path:
            return

        has_map = self.pdf_controller.get_document_map(self.current_file_path) is not None
        if has_map:
            self._set_argument_map_button_state(running=False)
        else:
            self._set_argument_map_button_state(running=False)
            # Could show some UI indicator here

    def _trigger_auto_ocr(self):
        # Show OCR dock
        self.toggle_tool_panel("OCR")

    def _trigger_argument_map_generation(self):
        if not self.current_file_path:
            return

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
        # TODO: Implement UI feedback for argument map generation status
        pass

    def _sync_tools_with_file(self, file_path):
        self.dock_widgets["Notes"].refresh_notes()
        self.dock_widgets["LLM Chat"].refresh_project_ui()
        for t in ["OCR", "Audio (TTS)"]:
            if hasattr(self.dock_widgets[t], "sync_file"):
                self.dock_widgets[t].sync_file(file_path)
        self._sync_tool_palette_buttons()

    def toggle_tool_panel(self, tool_name):
        if tool_name == "Close Tool":
            for dock in self.dock_widgets.values():
                dock.hide()
        else:
            if tool_name in self.dock_widgets:
                dock = self.dock_widgets[tool_name]
                if dock.isVisible():
                    dock.hide()
                else:
                    dock.show()
                    dock.raise_()
        self._sync_tool_palette_buttons()

        # Automatically minimize the palette when no tool panels are active
        if not any(dock.isVisible() for dock in self.dock_widgets.values()):
            if not self.tool_palette.is_minimized:
                self.tool_palette.toggle_minimize()
        else:
            if self.tool_palette.is_minimized:
                self.tool_palette.toggle_minimize()

    def _sync_tool_palette_buttons(self):
        if not hasattr(self, 'tool_palette'):
            return
        for name, dock in self.dock_widgets.items():
            self.tool_palette.set_tool_state(name, dock.isVisible())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # TODO: Handle resize events for any floating UI elements



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