# gui/main_window.py
import os
import uuid
import fitz
import shutil
from PyQt6.QtWidgets import (QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, 
                             QPushButton, QLabel, QSplitter, QStackedWidget, 
                             QFileDialog, QFrame, QButtonGroup, QMessageBox, QComboBox, QMenu,
                             QApplication)
from PyQt6.QtGui import QColor, QShortcut, QKeySequence
from PyQt6.QtCore import Qt, QSettings, QTimer, QThread, QEvent

from core.project_manager import ProjectManager
from gui.components.pdf_viewer import PDFViewer
from gui.tabs.ocr_tab import OCRTab
from gui.tabs.tts_tab import TTSTab
from gui.tabs.llm_tab import LLMTab
from gui.tabs.notes_tab import NotesTab
from gui.theme import ThemeManager
from gui.components.help_dialog import HelpDialog
from gui.components.dialogs.prompt_editor_dialog import PromptEditorDialog
from gui.components.dialogs.tag_manager_dialog import TagManagerDialog, TagAssignmentDialog
from core.prompt_manager import PromptManager


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
        self.setWindowTitle("Papyrus - Ethical, Offline Research Assistant")
        self._apply_smart_window_size()
        
        self.theme_manager = ThemeManager()
        self.project_manager = ProjectManager()
        self.project_manager.main_window = self
        self.current_file_path = None
        self.settings = QSettings("PDFMultitool", "Workspace")

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

    def _apply_smart_window_size(self):
        screen = QApplication.primaryScreen()
        if not screen:
            self.setMinimumSize(800, 600)
            self.resize(1200, 800)
            return

        available = screen.availableGeometry()
        min_width = max(780, int(available.width() * 0.6))
        min_height = max(560, int(available.height() * 0.65))
        self.setMinimumSize(min_width, min_height)

        width = max(min_width, int(available.width() * 0.9))
        height = max(min_height, int(available.height() * 0.9))
        width = min(width, available.width())
        height = min(height, available.height())

        x = available.x() + (available.width() - width) // 2
        y = available.y() + (available.height() - height) // 2
        self.setGeometry(x, y, width, height)

    def toggle_full_screen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()
        self._sync_full_screen_button()

    def _sync_full_screen_button(self):
        if hasattr(self, "btn_fullscreen"):
            is_full = self.isFullScreen()
            icon = "🗗" if is_full else "⛶"
            label = "Exit Full Screen" if is_full else "Enter Full Screen"
            self.btn_fullscreen.setToolTip(label)
            self.btn_fullscreen.setProperty("compact_icon", icon)
            self.btn_fullscreen.setProperty("expanded_text", f"{icon} {label}")
            if not self.btn_fullscreen.property("hover_expanded"):
                self.btn_fullscreen.setText(icon)

    def _configure_hover_expand_button(self, button, icon, label, expanded_width=170, collapsed_width=44):
        button.setText(icon)
        button.setToolTip(label)
        button.setProperty("compact_icon", icon)
        button.setProperty("expanded_text", f"{icon} {label}")
        button.setProperty("collapsed_width", collapsed_width)
        button.setProperty("expanded_width", expanded_width)
        button.setProperty("hover_expanded", False)
        button.setMinimumWidth(collapsed_width)
        button.setMaximumWidth(collapsed_width)
        button.installEventFilter(self)

    def _set_button_hover_state(self, button, expanded):
        icon = button.property("compact_icon")
        expanded_text = button.property("expanded_text")
        collapsed_width = int(button.property("collapsed_width") or 44)
        expanded_width = int(button.property("expanded_width") or 170)

        if expanded:
            button.setText(expanded_text or icon)
            button.setMinimumWidth(expanded_width)
            button.setMaximumWidth(expanded_width)
            button.setProperty("hover_expanded", True)
        else:
            button.setText(icon)
            button.setMinimumWidth(collapsed_width)
            button.setMaximumWidth(collapsed_width)
            button.setProperty("hover_expanded", False)

    def eventFilter(self, watched, event):
        if isinstance(watched, QPushButton) and watched.property("compact_icon"):
            if event.type() == QEvent.Type.Enter:
                self._set_button_hover_state(watched, True)
            elif event.type() == QEvent.Type.Leave:
                self._set_button_hover_state(watched, False)
        return super().eventFilter(watched, event)

    def _trigger_background_preload(self):
        try:
            default_model = self.tabs["LLM Chat"].model_combo.currentText()
            llm_manager = self.tabs["LLM Chat"].llm_manager
            if not llm_manager.ai_enabled: 
                return
            
            self.preload_worker = PreloadWorker(llm_manager, default_model, parent=self)
            self.preload_worker.start()
        except Exception as e:
            print(f"Could not trigger preload: {e}")

    def _setup_shortcuts(self):
        QShortcut(QKeySequence("Ctrl+="), self).activated.connect(self.viewer.zoom_in)
        QShortcut(QKeySequence("Ctrl++"), self).activated.connect(self.viewer.zoom_in)
        QShortcut(QKeySequence("Ctrl+-"), self).activated.connect(self.viewer.zoom_out)
        QShortcut(QKeySequence("Ctrl+0"), self).activated.connect(self.viewer.zoom_reset)
        QShortcut(QKeySequence("Ctrl+F"), self).activated.connect(self.viewer.annot_manager.toggle_search)
        QShortcut(QKeySequence("Ctrl+S"), self).activated.connect(self.save_project)
        QShortcut(QKeySequence("F11"), self).activated.connect(self.toggle_full_screen)

    def _build_top_menu(self):
        from PyQt6.QtWidgets import QScrollArea, QHBoxLayout, QWidget
        self.top_menu_scroll = QScrollArea()
        self.top_menu_scroll.setWidgetResizable(True)
        self.top_menu_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.top_menu_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.top_menu_scroll.setFixedHeight(60)

        self.top_menu = QWidget()
        self.top_menu.setMinimumHeight(55)
        menu_layout = QHBoxLayout(self.top_menu)
        menu_layout.setContentsMargins(10, 5, 10, 5)

        # Feedback Button (opens a placeholder link)
        self.btn_feedback = QPushButton()
        self._configure_hover_expand_button(self.btn_feedback, "💬", "Feedback", expanded_width=120)
        self.btn_feedback.clicked.connect(lambda: self._open_feedback_link())
        menu_layout.addWidget(self.btn_feedback)
        menu_layout.addSpacing(4)

        self.btn_project = QPushButton()
        self._configure_hover_expand_button(self.btn_project, "📁", "Project", expanded_width=120, collapsed_width=56)
        
        project_menu = QMenu(self)
        project_menu.addAction("New Project...", self._new_project)
        project_menu.addAction("Open Project...", self._open_project)
        project_menu.addAction("Save Project As...", self._save_project_as)
        project_menu.addSeparator()
        project_menu.addAction("Add PDF to Project...", self._add_pdf)
        self.btn_project.setMenu(project_menu)
        menu_layout.addWidget(self.btn_project)
        menu_layout.addSpacing(8)

        menu_layout.addWidget(QLabel("Active PDF:"))
        self.pdf_selector = QComboBox()
        self.pdf_selector.setFixedWidth(250)
        self.pdf_selector.currentIndexChanged.connect(self._on_pdf_dropdown_changed)
        self.pdf_selector.view().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.pdf_selector.view().customContextMenuRequested.connect(self._on_pdf_selector_context_menu)
        menu_layout.addWidget(self.pdf_selector)
        
        menu_layout.addSpacing(8)
        self.btn_save = QPushButton()
        self._configure_hover_expand_button(self.btn_save, "💾", "Save Project", expanded_width=140)
        self.btn_save.clicked.connect(self.save_project)
        menu_layout.addWidget(self.btn_save)
        menu_layout.addStretch()

        self.btn_zoom_out = QPushButton("➖")
        self.btn_zoom_out.clicked.connect(self.viewer.zoom_out)
        self.btn_zoom_reset = QPushButton("Fit Width")
        self.btn_zoom_reset.clicked.connect(self.viewer.zoom_reset)
        self.btn_zoom_in = QPushButton("➕")
        self.btn_zoom_in.clicked.connect(self.viewer.zoom_in)

        self.btn_fullscreen = QPushButton()
        self._configure_hover_expand_button(self.btn_fullscreen, "⛶", "Enter Full Screen", expanded_width=180)
        self.btn_fullscreen.clicked.connect(self.toggle_full_screen)
        
        menu_layout.addWidget(self.btn_zoom_out)
        menu_layout.addWidget(self.btn_zoom_reset)
        menu_layout.addWidget(self.btn_zoom_in)
        menu_layout.addWidget(self.btn_fullscreen)
        menu_layout.addStretch()

        # Theme Selector
        menu_layout.addWidget(QLabel("Theme:"))
        self.theme_selector = QComboBox()
        self.theme_selector.addItems(self.theme_manager.themes.keys())
        self.theme_selector.setCurrentText(self.theme_manager.current_theme_name)
        self.theme_selector.currentTextChanged.connect(self._on_theme_changed)
        menu_layout.addWidget(self.theme_selector)
        
        self.btn_edit_theme = QPushButton()
        self._configure_hover_expand_button(self.btn_edit_theme, "✏️", "Edit Custom Theme", expanded_width=170)
        self.btn_edit_theme.clicked.connect(lambda: self.theme_manager.edit_custom_theme(self))
        menu_layout.addWidget(self.btn_edit_theme)

        self.btn_tag_manager = QPushButton()
        self._configure_hover_expand_button(self.btn_tag_manager, "🏷️", "Tag Manager", expanded_width=130)
        self.btn_tag_manager.clicked.connect(self._open_tag_manager)
        menu_layout.addWidget(self.btn_tag_manager)
        
        menu_layout.addSpacing(8)
        self.btn_help = QPushButton()
        self._configure_hover_expand_button(self.btn_help, "❓", "Help", expanded_width=100)
        self.btn_help.clicked.connect(self.show_help_window)
        menu_layout.addWidget(self.btn_help)
        menu_layout.addSpacing(8)

        self.btn_prompt_editor = QPushButton()
        self._configure_hover_expand_button(self.btn_prompt_editor, "🧠", "Prompt Editor", expanded_width=150)
        self.btn_prompt_editor.clicked.connect(self._open_prompt_editor)
        menu_layout.addWidget(self.btn_prompt_editor)
        menu_layout.addSpacing(8)

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

        self.top_menu_scroll.setWidget(self.top_menu)
        self.main_layout.addWidget(self.top_menu_scroll)
    def _open_feedback_link(self):
        import webbrowser
        webbrowser.open("https://docs.google.com/forms/d/e/1FAIpQLSfm3W0Z-79jSJ1uuUgiUoi2CXMkyxLM3S3jyEw931aIDNDFag/viewform?usp=publish-editor")

    def _open_prompt_editor(self):
        prompt_manager = None
        try:
            prompt_manager = self.tabs["LLM Chat"].llm_manager.prompt_manager
        except Exception:
            prompt_manager = PromptManager()

        dialog = PromptEditorDialog(prompt_manager, self)
        dialog.exec()

    def _open_tag_manager(self):
        dialog = TagManagerDialog(self.project_manager, self)
        dialog.exec()
        if "LLM Chat" in self.tabs:
            self.tabs["LLM Chat"].refresh_tag_filters()

    def _on_pdf_selector_context_menu(self, pos):
        view = self.pdf_selector.view()
        index = view.indexAt(pos)
        row = index.row() if index.isValid() else self.pdf_selector.currentIndex()
        if row < 0:
            return

        doc_id = self.pdf_selector.itemData(row)
        if not doc_id:
            return

        menu = QMenu(self)
        manage_tags_action = menu.addAction("🏷️ Manage Tags for This Document")
        chosen = menu.exec(view.mapToGlobal(pos))
        if chosen == manage_tags_action:
            dlg = TagAssignmentDialog(self.project_manager, doc_id, "doc", self)
            dlg.exec()
            if "LLM Chat" in self.tabs:
                self.tabs["LLM Chat"].refresh_tag_filters()

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
            self.setWindowTitle(f"Papyrus - {self.project_manager.project_name}")
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
            self.project_manager.save_all_docs()
            
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
            self.setWindowTitle(f"Papyrus - {self.project_manager.project_name}")

    def _load_project(self, path):
        if self.project_manager.project_filepath:
            self.save_project()
            
        if self.project_manager.load_project(path):
            self._clear_ui_for_new_project()
            
            self.settings.setValue("last_project", self.project_manager.project_filepath)
            self.setWindowTitle(f"Papyrus - {self.project_manager.project_name}")
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

        if self.current_file_path == pdf_path and self.viewer.doc:
            return

        self.current_file_path = pdf_path
        self.project_manager.set_active_file(pdf_path)
        
        doc = self.project_manager.get_doc(pdf_path)
        if doc:
            success = self.viewer.load_document(doc)
            if success:
                self._check_needs_ocr()
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
                self.project_manager.save_all_docs()
            except Exception as e:
                print(f"Background autosave failed: {e}")

    def save_project(self):
        if not self.project_manager.project_filepath: return
        try:
            if "Notes" in self.tabs and hasattr(self.tabs["Notes"], "save_workspace_state"):
                self.tabs["Notes"].save_workspace_state()
                
            self.project_manager.save_all_docs()
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

        search_paths = allowed_paths if allowed_paths else self.project_manager.pdfs
        
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
                doc = self.project_manager.get_doc(path)
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
                        self.viewer.annot_manager.highlight_created.emit({
                            "id": annot_id_to_use,
                            "subject": clean_quote,
                            "content": note,
                            "pdf_path": path,
                            "page_num": page_num,
                            "rect_coords": repr(list(annot.rect)),
                            "color": QColor(179, 102, 255).name(),
                        })
                        
                        found_any = True
                        self.project_manager.mark_dirty(path)
                        
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
            self.project_manager.mark_dirty(self.current_file_path)

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

    def _build_workspace(self):
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_layout.addWidget(self.splitter, 1)

        self.splitter.addWidget(self.viewer)

        self.tool_panel = QStackedWidget()
        self.tool_panel.setMinimumWidth(460)
        
        self.tabs = {
            "Notes": NotesTab(self.tool_panel, self.viewer, self),
            "OCR": OCRTab(self.tool_panel, self),
            "Audio (TTS)": TTSTab(self.tool_panel, self),
            "LLM Chat": LLMTab(self.tool_panel, self)
        }

        if hasattr(self.tabs["LLM Chat"], "llm_manager") and not self.tabs["LLM Chat"].llm_manager.ai_enabled:
            # Disable the LLM Chat top menu button
            QMessageBox.information(self, "Standard Mode","Local Ollama Installation not detected. LLM features will not work without Ollama")
            self.tool_buttons["LLM Chat"].setEnabled(False)
            self.tool_buttons["LLM Chat"].setToolTip("Install local AI models (Ollama) to unlock.")
            
            # Disable the Workspace AI Toolbar button
            if hasattr(self.tabs["Notes"], "workspace_view"):
                self.tabs["Notes"].workspace_view.btn_ai_tools.setEnabled(False)
                self.tabs["Notes"].workspace_view.btn_ai_tools.setToolTip("Install local AI models to unlock.")
        
        for tab in self.tabs.values():
            self.tool_panel.addWidget(tab)
            
        self.splitter.addWidget(self.tool_panel)
        self.tool_panel.hide()
        self.splitter.setSizes([1400, 0])
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)
        
        self.viewer.annot_manager.note_added.connect(self.tabs["Notes"].refresh_notes)
        self.viewer.annot_manager.note_added.connect(self._mark_current_dirty)
        # Save workspace state after new highlight/note is added
        self.viewer.annot_manager.note_added.connect(self.tabs["Notes"].save_workspace_state)
        self.viewer.annot_manager.highlight_created.connect(self.tabs["Notes"].handle_highlight_created)
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
                total = max(1, sum(current_sizes))
                panel_width = max(460, int(total * 0.34))
                if total > 900:
                    panel_width = min(panel_width, total - 420)
                else:
                    panel_width = min(panel_width, total // 2)
                self.splitter.setSizes([max(1, total - panel_width), panel_width])