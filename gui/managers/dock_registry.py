# gui/managers/dock_registry.py
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDockWidget, QTextEdit
from gui.managers.dock_manager import DockDefinition

def register_default_docks(dock_manager, window_ref):
    """Registers all standard application plugins/docks."""
    R = Qt.DockWidgetArea.RightDockWidgetArea

    def make_research(w):
        from gui.docks.unified_research.unified_dock import UnifiedResearchDock
        return UnifiedResearchDock(w, w.project_manager, w.shared_llm_manager, w)
        
    def make_workspace(w):
        dock = QDockWidget("🧠 Workspace", w)
        from gui.components.workspace_view import WorkspaceView
        dock.setWidget(WorkspaceView(w))
        return dock
        
    def make_notes(w):
        dock = QDockWidget("📝 Notes List", w)
        from gui.docks.notes_dock import NotesTab
        dock.setWidget(NotesTab(parent=None, main_window=w)) # <-- Fixed
        return dock

    def make_dict(w):
        dock = QDockWidget("📖 Dictionary", w)
        from gui.docks.dictionary_dock import DictionaryTab
        dock.setWidget(DictionaryTab(w.dictionary_manager, w))
        return dock

    def make_essay(w):
        dock = QDockWidget("📝 Essay Writer", w)
        from gui.docks.essay_dock import EssayTab
        dock.setWidget(EssayTab(w.project_manager, w))
        return dock

    def make_citations(w):
        dock = QDockWidget("📚 Citation Manager", w)
        from gui.docks.citation_dock import CitationDock
        dock.setWidget(CitationDock(w.citation_manager, w.project_manager, w))
        return dock

    def make_ocr(w):
        dock = QDockWidget("👁️ OCR Scanner", w)
        from gui.docks.ocr_dock import OCRTab
        dock.setWidget(OCRTab(None, w))
        return dock

    def make_audio(w):
        dock = QDockWidget("🔊 Audio (TTS)", w)
        from gui.docks.tts_dock import TTSTab
        dock.setWidget(TTSTab(None, w))
        return dock

    def make_scratchpad(w):
        dock = QDockWidget("✍️ Scratchpad", w)
        editor = QTextEdit()
        editor.setPlaceholderText("Jot down quick thoughts here...\\n\\n(Stays saved in memory)")
        dock.setWidget(editor)
        return dock

    dm = dock_manager
    dm.register(DockDefinition("research", "SingleResearchDock", "Research Assistant", R, True, make_research))
    dm.register(DockDefinition("workspaces", "WorkspaceDock", "Workspaces", R, False, make_workspace))
    dm.register(DockDefinition("notes", "NotesDock", "Notes List", R, True, make_notes))
    dm.register(DockDefinition("dicts", "SingleDictionaryDock", "Dictionary", R, True, make_dict))
    dm.register(DockDefinition("essays", "EssayDock", "Essay Writer", R, False, make_essay))
    dm.register(DockDefinition("citations", "SingleCitationDock", "Citations", R, True, make_citations))
    dm.register(DockDefinition("ocrs", "SingleOCRDock", "OCR Scanner", R, True, make_ocr))
    dm.register(DockDefinition("audios", "SingleAudioDock", "Audio (TTS)", R, True, make_audio))
    dm.register(DockDefinition("scratchpads", "ScratchDock", "Scratchpad", R, False, make_scratchpad))