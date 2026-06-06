# core/services/project_app_service.py
import os
import shutil
from PySide6.QtCore import QObject, QSettings
from core.events.event_bus import EventBus
from core.events.domains.project_events import ProjectEvent, ProjectEventPayload, ProjectIntent, ProjectPayload
class ProjectAppService(QObject):
    """Handles the lifecycle of the project database entirely in the background."""
    def __init__(self, project_manager, llm_manager):
        super().__init__()
        self.pm = project_manager
        self.llm_manager = llm_manager
        self.bus = EventBus.get_instance()
        self.settings = QSettings("PDFMultitool", "Workspace")

        self.bus.project_action_requested.connect(self._handle_intent)

    def _handle_intent(self, intent: ProjectIntent, payload: ProjectPayload):
        if intent == ProjectIntent.CREATE: self._create_project(payload.path)
        elif intent == ProjectIntent.LOAD: self._load_project(payload.path)
        elif intent == ProjectIntent.SAVE_AS: self._save_project_as(payload.new_path)
        elif intent == ProjectIntent.SAVE: self._save_project()
        elif intent == ProjectIntent.EXPORT_LOG: self._export_llm_log(payload.path)

    def _save_project(self):
        if not getattr(self.pm, 'project_filepath', None): return
        # Flush UI states (Window layout, Dock sizes, Scratchpads)
        self.bus.project_action_requested.emit(ProjectIntent.FLUSH_UI_STATES, ProjectPayload())
        # Commit DB and physical PDF annotations
        self.pm.save_all_docs()
        if hasattr(self.pm, "save_project"):
            self.pm.save_project()
        self.bus.project_action_requested.emit(ProjectIntent.SAVE_COMPLETED, ProjectPayload())

    def _export_llm_log(self, path: str):
        if not path or not self.pm.project_filepath:
            self.bus.project_action_requested.emit(ProjectIntent.EXPORT_LOG_RESULT, ProjectPayload(success=False, msg="No project open."))
            return

        from core.llm_log import LlmLogGenerator
        generator = LlmLogGenerator(self.pm.project_filepath, self.pm.project_name)
        success = generator.generate_pdf(path)

        self.bus.project_action_requested.emit(ProjectIntent.EXPORT_LOG_RESULT, ProjectPayload(success=success, path=path))

    def _create_project(self, path: str):
        if not path: return
        if not path.lower().endswith(".pdfproj"):
            path += ".pdfproj"

        if self.pm.project_filepath:
            self.pm.save_all_docs() # Ensure previous is saved

        # Tell all UI components (like the Viewer and Doc Explorer) to clear themselves!
        self.bus.project_clearing_started.emit(ProjectEvent.CLEARING_STARTED, ProjectEventPayload())

        self.pm.create_project(path)
        self.settings.setValue("last_project", self.pm.project_filepath)

        # Tell the UI the new project is ready to draw
        self.bus.project_loaded.emit(ProjectEvent.LOADED, ProjectEventPayload())

    def _load_project(self, path: str):
        if not path: return
        if self.pm.project_filepath:
            self.pm.save_all_docs()

        if self.pm.load_project(path):
            if self.llm_manager:
                self.llm_manager.set_project_database(self.pm.project_filepath)

            self.bus.project_clearing_started.emit(ProjectEvent.CLEARING_STARTED, ProjectEventPayload())
            self.settings.setValue("last_project", self.pm.project_filepath)
            self.bus.project_loaded.emit(ProjectEvent.LOADED, ProjectEventPayload())

    def _save_project_as(self, new_path: str):
        if not self.pm.project_filepath or not new_path: return
        if not new_path.lower().endswith(".pdfproj"):
            new_path += ".pdfproj"

        old_path = self.pm.project_filepath
        old_chroma_dir = old_path + "_chroma_db"
        new_chroma_dir = new_path + "_chroma_db"

        # Trigger all UI components to save their state to the DB before copying
        self.bus.project_action_requested.emit(ProjectIntent.FLUSH_UI_STATES, ProjectPayload())
        self.pm.save_all_docs()

        if self.pm._conn:
            self.pm._conn.close()
            self.pm._conn = None

        try:
            shutil.copy2(old_path, new_path)
            if os.path.exists(old_chroma_dir):
                if os.path.exists(new_chroma_dir): shutil.rmtree(new_chroma_dir)
                shutil.copytree(old_chroma_dir, new_chroma_dir)
        except Exception as e:
            print(f"Save As Failed: {e}")
            self.pm._init_db()
            return

        self.pm.project_filepath = new_path
        self.pm.project_name = os.path.basename(new_path).replace(".pdfproj", "")
        self.pm._init_db()

        # Update metadata
        cursor = self.pm._conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)", ("project_name", self.pm.project_name))
        self.pm._conn.commit()

        self.settings.setValue("last_project", new_path)
        self.bus.project_loaded.emit(ProjectEvent.LOADED, ProjectEventPayload())
