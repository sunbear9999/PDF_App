# gui/main_window.py
import os
import sys
import json
from PySide6.QtWidgets import (QMainWindow, QApplication, QFileDialog, QMessageBox,
                               QWidget, QVBoxLayout)
from PySide6.QtGui import QShortcut, QKeySequence
from PySide6.QtCore import Qt, QSettings, QTimer

from core.engine.ui_router import BlueprintUIRouter
from gui.components.pdf_viewer import PDFViewer
from gui.components.process_monitor import ProcessMonitorWidget
from gui.theme.theme import ThemeManager
from gui.components.help_dialog import HelpDialog
from gui.components.dialogs.prompt_editor_dialog import PromptEditorDialog
from gui.components.dialogs.tag_manager_dialog import TagManagerDialog
from gui.components.universal_overlay import UniversalInternalOverlay
from gui.managers.layout_manager import LayoutManager
from gui.components.main_toolbar import MainToolbar
from core.events.domains.project_events import ProjectIntent, ProjectPayload
class MainWindow(QMainWindow):
    # ADD `core` to the signature
    def __init__(self, core):
        super().__init__()

        self.core = core # Save the reference
        self.setObjectName("PapyrusMainWindow")
        self.setWindowTitle("Papyrus - Ethical, Offline Research Assistant")
        self._apply_smart_window_size()
        self.setMinimumSize(800, 600)
        self.settings = QSettings("PDFMultitool", "Workspace")
        self.theme_manager = ThemeManager()
        self.current_file_path = None

        # 1. Map pointers from PapyrusCore (Replaces all instantiation!)
        self.bus = core.bus
        self.process_registry = core.process_registry
        self.project_manager = core.project_manager
        self.shared_llm_manager = core.llm_manager
        self.prompt_manager = core.prompt_manager
        self.step_manager = core.step_manager
        self.blueprint_registry = core.blueprint_registry
        self.blueprint_manager = core.blueprint_manager
        self.workflow_node_type_registry = core.workflow_node_type_registry
        self.dictionary_manager = core.dictionary_manager
        self.citation_manager = core.citation_manager
        self.active_ai_model = self._load_active_ai_model()

        self.workspace_ai_tools_registry = core.workspace_ai_tools_registry
        self.workspace_node_type_registry = core.workspace_node_type_registry
        self.ontology_registry = getattr(core, "ontology_registry", None)
        self.workspace_service = core.workspace_service
        self.workspace_graph_service = core.workspace_graph_service

        # --- IMPORTANT LEGACY HOOK ---
        # Keep this for now so the ProjectManager can still warn the Viewer before saving
        self.project_manager.main_window = self

        # Build AppContext early so dock registration and MainToolbar can access it
        from gui.app_context import AppContext
        self.app_context = AppContext.from_core(core)

        # 2. Setup the GUI Managers
        from gui.managers.dock_manager import DockManager
        self.dock_manager = DockManager(self)
        self.layout_manager = LayoutManager(self)
        from gui.managers.dock_registry import register_default_docks, register_plugin_docks
        register_default_docks(self.dock_manager, self)
        register_plugin_docks(self.dock_manager, self.app_context)

        # 3. Initialize GUI-dependent services locally
        from core.services.gui_bridge.workspace_annotation_service import WorkspaceAnnotationService
        from core.services.gui_bridge.workspace_ai_service import WorkspaceAIService
        self.workspace_annotation_service = WorkspaceAnnotationService(self, self.bus)
        self.workspace_ai_service = WorkspaceAIService(
            self, self.workspace_service, self.workspace_graph_service,
            self.workspace_annotation_service, self.workspace_ai_tools_registry,
            event_bus=self.bus,
            parent=self,
        )
        self.workspace_ai_service.error.connect(lambda msg: QMessageBox.warning(self, "AI Error", msg))

        from core.services.gui_bridge.ai_bootstrap_service import AIBootstrapService
        self.ai_bootstrap_service = AIBootstrapService(self)

        self.viewer = PDFViewer()


        # 3. CONFIGURE DOCKS
        self.setDockOptions(
            QMainWindow.DockOption.AllowNestedDocks |
            QMainWindow.DockOption.AnimatedDocks |
            QMainWindow.DockOption.AllowTabbedDocks |
            QMainWindow.DockOption.GroupedDragging
        )
        self.setDockNestingEnabled(True)
        # 4. SET CENTRAL WIDGET PROPERLY
        self.central_wrapper = QWidget()
        self.central_layout = QVBoxLayout(self.central_wrapper)
        self.central_layout.setContentsMargins(0, 0, 0, 0)
        self.central_layout.setSpacing(0)
        self.setCentralWidget(self.central_wrapper)


        # 6. BUILD UI
        self.process_monitor = ProcessMonitorWidget(self.process_registry, self.theme_manager.get_theme())
        self.top_toolbar = MainToolbar(self)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.top_toolbar)
        from gui.managers.workspace_builder import WorkspaceBuilder
        WorkspaceBuilder(self).build()

        # Connect Theme Manager
        self.theme_manager.theme_changed.connect(self.update_theme)
        self.update_theme(self.theme_manager.get_theme())

        # Timers
        self.autosave_timer = QTimer(self)
        self.autosave_timer.timeout.connect(lambda: self.bus.project_action_requested.emit(ProjectIntent.SAVE, ProjectPayload()))
        self.autosave_timer.start(5 * 60 * 1000)

        # Unload plugins cleanly on exit
        QApplication.instance().aboutToQuit.connect(self.core._unload_plugins)


        if self.settings.value("show_help_on_startup", True, type=bool):
            QTimer.singleShot(500, self.show_help_window)

        # DELAY THE STARTUP: Wait 50ms for the 0x0 window to physically draw on screen
        # before attempting to calculate tabbed dock layouts.
        QTimer.singleShot(50, self._run_startup_sequence)
        self.universal_overlay = UniversalInternalOverlay(self, self.theme_manager.get_theme())
        self.ui_router = BlueprintUIRouter(self)
        self.ui_router.register_target("floating", self.universal_overlay)

        # Wire GUI-only references into AppContext so docks/tabs don't need MainWindow
        self.app_context.ui_router = self.ui_router
        self.app_context.theme_manager = self.theme_manager
        self.app_context.viewer = self.viewer
        # plugin_extension_registry is already set from from_core(), but sync it in case
        # plugins registered dock specs before app_context was created
        if not self.app_context.plugin_extension_registry:
            self.app_context.plugin_extension_registry = getattr(core, "plugin_extension_registry", None)

        # Register any AI renderers contributed by plugins
        if self.app_context.plugin_extension_registry:
            from gui.components.base.ai_output_factory import AIOutputWidgetFactory
            for payload_type, spec in self.app_context.plugin_extension_registry.get_ai_renderers().items():
                AIOutputWidgetFactory.register(payload_type, spec.factory)

        # Wire UIEventCoordinator (replaces direct bus connections from MainWindow)
        from gui.managers.ui_event_coordinator import UIEventCoordinator
        self._ui_coordinator = UIEventCoordinator(self, self.app_context, parent=self)

        # Wire GUI-dependent pieces into the services that now live in PapyrusCore
        self.workflow_runner_service = core.workflow_runner_service
        self.workflow_runner_service.set_ui_router(self.ui_router)
        self.workflow_runner_service.set_model_provider(self._get_active_ai_model)
        self.research_agent_service = core.research_agent_service
        self.research_agent_service.model_provider = self._get_active_ai_model

        if hasattr(self, "workspace_ai_service"):
            self.workspace_ai_service.workflow_runner_service = self.workflow_runner_service
        self.bus.status_message_requested.connect(
            lambda msg, duration=3000: self.statusBar().showMessage(msg, duration)
        )

        # Keyboard shortcuts (core + plugin)
        from gui.managers.shortcut_manager import ShortcutManager
        self._shortcut_manager = ShortcutManager(self)
        self._shortcut_manager.register_core_shortcuts()
        self._shortcut_manager.register_plugin_shortcuts()

        # ActionRegistry + ContextMenuRegistry
        from gui.registry.action_spec import ActionRegistry
        from gui.registry.context_menu_registry import ContextMenuRegistry
        from gui.registry.extension_spec_bridge import bridge_plugin_extensions
        self._action_registry = ActionRegistry()
        self._context_menu_registry = ContextMenuRegistry(self._action_registry)
        if self.app_context.plugin_extension_registry:
            bridge_plugin_extensions(
                self.app_context.plugin_extension_registry,
                self._action_registry,
            )
        self.app_context.action_registry = self._action_registry
        self.app_context.context_menu_registry = self._context_menu_registry

        # Toast notifications for api.notify()
        from gui.components.toast_manager import ToastManager
        self._toast_manager = ToastManager(self)
        self.bus.plugin_notification_requested.connect(self._toast_manager._on_notification)

        # Plugin lifecycle: sweep widgets when a plugin is unloaded (hot-reload)
        self.bus.plugin_unloaded.connect(self.dock_manager.remove_plugin_docks)
        self.bus.plugin_unloaded.connect(self._sweep_plugin_toolbar_widgets)

        # Active controller timer management
        from gui.managers.plugin_controller_manager import PluginControllerManager
        self._plugin_controller_manager = PluginControllerManager(
            core.ontology_registry, self.bus, parent=self
        )
        self.bus.plugin_loaded.connect(self._plugin_controller_manager.on_plugin_loaded)
        self.bus.plugin_unloaded.connect(self._plugin_controller_manager.teardown_plugin)

        # Ctrl+P command palette
        from gui.components.command_palette import CommandPalette
        self._command_palette = CommandPalette(self)
        QShortcut(QKeySequence("Ctrl+P"), self).activated.connect(
            self._command_palette.show_palette
        )


    def _halt_pdf_viewer(self, path):
        """Called by ProjectManager before saving the active PDF to stop the render worker."""
        if getattr(self, 'current_file_path', None) == path:
            viewer = getattr(self, 'viewer', None)
            if viewer:
                if viewer.worker and viewer.worker.isRunning():
                    viewer.worker.stop()
                    viewer.worker.wait()
                return viewer
        return None

    def _get_active_ai_model(self):
        if getattr(self, "active_ai_model", None):
            return self.active_ai_model
        models = self.shared_llm_manager.get_available_models() or []
        return models[0] if models else ""

    def _load_active_ai_model(self):
        try:
            settings = json.loads(self.project_manager.get_metadata("global_ai_settings", "{}"))
            model = settings.get("selected_model") or settings.get("active_model")
            if model:
                return model
        except Exception:
            pass
        models = self.shared_llm_manager.get_available_models() or []
        preferred = "gemma4:e2b"
        if preferred in models:
            return preferred
        return models[0] if models else preferred

    def _run_startup_sequence(self):
        settings = QSettings("PDFMultitool", "Workspace")
        last_project_path = settings.value("last_project_path", "")
        import os
        if last_project_path and os.path.exists(last_project_path):
            # 🔥 FIX: Use the background service to load the project!
            self.bus.project_action_requested.emit(ProjectIntent.LOAD, ProjectPayload(path=last_project_path))
        else:
            if hasattr(self, 'layout_manager'):
                self.layout_manager.restore_last_session()

    def show_help_window(self,initial_tab_index=0):
        # We keep a reference to it so it doesn't get garbage collected
        self.help_dialog = HelpDialog(self,initial_tab_index=initial_tab_index)
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

    def _sweep_plugin_toolbar_widgets(self, plugin_id: str) -> None:
        """Remove main-toolbar buttons tagged with papyrus_plugin_id == plugin_id."""
        from PySide6.QtWidgets import QPushButton
        toolbar = getattr(self, "toolbar", None)
        if toolbar is None:
            return
        for btn in toolbar.findChildren(QPushButton):
            if btn.property("papyrus_plugin_id") == plugin_id:
                toolbar.removeAction(toolbar.widgetForAction(btn))
                btn.deleteLater()

    def toggle_full_screen(self):
        from PySide6.QtCore import Qt

        if self.isFullScreen():
            # 1. Forcefully strip ALL window states (including maximized and fullscreen)
            self.setWindowState(Qt.WindowState.WindowNoState)

            # 2. Tell the OS to restore the window
            self.showNormal()

            # 3. THE X11 WAKEUP HACK:
            # Because the Chromium OpenGL context makes XFCE stubborn, we force a
            # microscopic resize. This forces the OS window manager to redraw the borders.
            self.resize(self.width() - 1, self.height())
            self.resize(self.width() + 1, self.height())
        else:
            self.setWindowState(Qt.WindowState.WindowFullScreen)
            self.showFullScreen()

        # 4. Safely update the UI Button
        if hasattr(self, 'btn_fullscreen'):
            now_full = self.isFullScreen()
            icon = "🗗" if now_full else "⛶"
            label = "Exit Full Screen" if now_full else "Full Screen"
            self.btn_fullscreen.setProperty("compact_icon", icon)
            self.btn_fullscreen.setProperty("expanded_text", f"{icon} {label}")
            self.top_toolbar._set_button_hover_state(self.btn_fullscreen, self.btn_fullscreen.property("hover_expanded"))


    def _open_prompt_editor(self):
        dialog = PromptEditorDialog(self.prompt_manager, self)
        dialog.exec()

    def _open_tag_manager(self):
        dialog = TagManagerDialog(self)
        dialog.exec()

        # 1. Update the Document Explorer's dropdown
        if hasattr(self, 'doc_explorer'):
            self.doc_explorer.refresh_tag_filter()

        # 2. Tell the unified research dock to refresh its filters via the registry
        for r in self.dock_manager.get_instances("research"):
            if hasattr(r, 'refresh_project_ui'):
                r.refresh_project_ui()

    def _on_theme_changed(self, theme_name):
        if theme_name == "Custom":
            self.theme_manager.edit_custom_theme(self)

        self.settings.setValue("theme", theme_name)
        self.theme_manager.set_theme(theme_name)

    def update_theme(self, theme):
        self.top_toolbar.setStyleSheet(f"background-color: {theme['bg_panel']}; border-bottom: 1px solid {theme['border']};")
        if hasattr(self, "ocr_banner"):
            self.ocr_banner.setStyleSheet(f"background-color: {theme['warning']}; border-bottom: 1px solid {theme['border']};")
            self.lbl_ocr_banner.setStyleSheet("font-weight: bold; color: #1e1e1e; border: none;")
        self.dock_manager.broadcast_theme(theme)
        if hasattr(self.viewer, "update_theme"):
            try:
                self.viewer.update_theme(theme)
            except RuntimeError:
                pass
        if hasattr(self, "process_monitor"):
            self.process_monitor.set_theme(theme)
        from gui.theme.global_styles import get_global_stylesheet
        self.setStyleSheet(get_global_stylesheet(theme))
        if hasattr(self, "_toast_manager"):
            self._toast_manager.update_theme(theme)
        if hasattr(self, "_command_palette"):
            self._command_palette.update_theme(theme)

    def broadcast_note_added(self):
        self._mark_current_dirty()
        for notes_view in self.dock_manager.get_inner_widgets("notes"):
            notes_view.refresh_notes()
        for ws_view in self.dock_manager.get_inner_widgets("workspaces"):
            ws_view.save_workspace_state()

    def _show_save_indicator(self):
        """Displays visual confirmation that the master database successfully committed."""
        self.statusBar().showMessage("💾 Project saved successfully.", 3000)
        if hasattr(self, 'btn_save'):
            original_text = self.btn_save.text()
            self.btn_save.setText("✅ Saved!")
            from PySide6.QtCore import QTimer
            QTimer.singleShot(2000, lambda: self.btn_save.setText(original_text))

    def _mark_current_dirty(self):
        if self.current_file_path:
            self.project_manager.mark_dirty(self.current_file_path)


    def closeEvent(self, event):
        """Intercepts the window closing to check for unsaved changes, save session state, and clean up threads."""
        from PySide6.QtWidgets import QMessageBox
        from PySide6.QtCore import QSettings

        # 1. Check if there is anything actually waiting to be saved
        has_unsaved_changes = hasattr(self, 'project_manager') and bool(self.project_manager.dirty_docs)

        if has_unsaved_changes:
            # Pop up the native OS warning dialog
            reply = QMessageBox.question(
                self,
                "Unsaved Changes",
                "You have unsaved changes in your project. Do you want to save before exiting?",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save # Default button
            )

            if reply == QMessageBox.StandardButton.Save:
                # Attempt to save the project
                if hasattr(self, 'bus'):
                    self.bus.project_action_requested.emit(ProjectIntent.SAVE, ProjectPayload())
                    if hasattr(self, 'project_manager') and self.project_manager.dirty_docs:
                        self.project_manager.save_all_docs()
            elif reply == QMessageBox.StandardButton.Cancel:
                # User hit Cancel, abort the close sequence entirely!
                event.ignore()
                return
            # If Discard, we just skip the project save and proceed to shutdown.

        # 2. Explicitly save the last project path to the OS Registry so it re-opens on boot
        if hasattr(self, 'project_manager') and getattr(self.project_manager, 'project_filepath', None):
            settings = QSettings("PDFMultitool", "Workspace")
            settings.setValue("last_project_path", self.project_manager.project_filepath)
            settings.sync()

        # 3. Save the global UI layout session
        if hasattr(self, 'layout_manager'):
            self.layout_manager.save_current_session()

        # 4. Clean up background workers so the app doesn't leave ghost processes running in Task Manager
        if hasattr(self, 'autosave_timer') and self.autosave_timer.isActive():
            self.autosave_timer.stop()

        if hasattr(self, 'viewer') and hasattr(self.viewer, 'worker') and self.viewer.worker:
            self.viewer.worker._is_running = False
            self.viewer.worker.wait()

        if hasattr(self, 'quick_note_popup') and self.quick_note_popup:
            try:
                self.quick_note_popup.close()
            except RuntimeError:
                pass

        event.accept()
    def _trigger_auto_ocr(self):
        self.ocr_banner.hide()
        self.dock_manager.spawn("ocrs")
    def toggle_tool_panel(self, tool_name, checked):
        self.dock_manager.toggle_by_menu_name(tool_name, checked)
