# gui/managers/dock_registry.py
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDockWidget, QTextEdit
from gui.managers.dock_manager import DockDefinition


def register_default_docks(dock_manager, window_ref):
    """Registers all standard application docks."""
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
        dock.setWidget(NotesTab(parent=None, main_window=w))
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
        dock.setWidget(CitationDock(getattr(w, "app_context", None)))
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

    def make_slides(w):
        dock = QDockWidget("📊 Slideshow Maker", w)
        from gui.docks.slideshow_dock import SlideshowTab
        dock.setWidget(SlideshowTab(w.project_manager, w))
        return dock

    def make_data_dock(w):
        dock = QDockWidget("Data Dock", w)
        from gui.docks.data_dock import DataDockView
        dock.setWidget(DataDockView(w.app_context, w))
        return dock

    def make_scratchpad(w):
        dock = QDockWidget("✍️ Scratchpad", w)
        editor = QTextEdit()
        editor.setPlaceholderText("Jot down quick thoughts here...\n\n(Stays saved in memory)")
        dock.setWidget(editor)
        return dock

    dm = dock_manager
    dm.register(DockDefinition("research", "SingleResearchDock", "Research Assistant", R, True, make_research))
    dm.register(DockDefinition("slides", "SlideshowDock", "Slideshow Maker", R, False, make_slides))
    dm.register(DockDefinition("data_dock", "SingleDataDock", "Data Dock", R, True, make_data_dock))
    dm.register(DockDefinition("workspaces", "WorkspaceDock", "Workspaces", R, False, make_workspace))
    dm.register(DockDefinition("notes", "NotesDock", "Notes List", R, True, make_notes))
    dm.register(DockDefinition("dicts", "SingleDictionaryDock", "Dictionary", R, True, make_dict))
    dm.register(DockDefinition("essays", "EssayDock", "Essay Writer", R, False, make_essay))
    dm.register(DockDefinition("citations", "SingleCitationDock", "Citations", R, True, make_citations))
    dm.register(DockDefinition("ocrs", "SingleOCRDock", "OCR Scanner", R, True, make_ocr))
    dm.register(DockDefinition("audios", "SingleAudioDock", "Audio (TTS)", R, True, make_audio))
    dm.register(DockDefinition("scratchpads", "ScratchDock", "Scratchpad", R, False, make_scratchpad))


def register_plugin_docks(dock_manager, app_context) -> None:
    """Register docks contributed by plugins via DockSpec / PluginDockSpec."""
    R = Qt.DockWidgetArea.RightDockWidgetArea

    # 1. DockSpec entries from PluginExtensionRegistry (register_gui_extensions path)
    registry = getattr(app_context, "plugin_extension_registry", None)
    if registry:
        for spec in registry.get_extra_docks():
            _register_plugin_dock_spec(dock_manager, spec, app_context, R)

    # 2. PluginDockSpec entries from core.plugin_dock_specs (get_dock_spec() path)
    # app_context doesn't hold core directly; reach through bus owner if needed.
    # These are already wired via the registry path for plugins using the new API.
    # For legacy get_dock_spec() plugins, PapyrusCore.register_plugin() appends to
    # core.plugin_dock_specs — the main toolbar's "Other Tools" menu handles discovery.


def _register_plugin_dock_spec(dock_manager, spec, app_context, area) -> None:
    """Convert a DockSpec into a DockDefinition and register it."""
    from PySide6.QtCore import Qt as _Qt

    area_map = {
        "left": _Qt.DockWidgetArea.LeftDockWidgetArea,
        "right": _Qt.DockWidgetArea.RightDockWidgetArea,
        "top": _Qt.DockWidgetArea.TopDockWidgetArea,
        "bottom": _Qt.DockWidgetArea.BottomDockWidgetArea,
    }
    qt_area = area_map.get(getattr(spec, "area", "right"), area)

    label = getattr(spec, "label", spec.id)
    factory_fn = getattr(spec, "factory", None)

    def _make_dock(w, _spec=spec, _ctx=app_context, _lbl=label, _factory=factory_fn):
        from PySide6.QtWidgets import QDockWidget, QLabel
        dock = QDockWidget(_lbl, w)
        if _factory:
            try:
                widget = _factory(_ctx)
                dock.setWidget(widget)
            except Exception as exc:
                print(f"[PluginDock] Factory for '{_spec.id}' failed: {exc}")
                dock.setWidget(QLabel(f"Plugin dock error: {exc}"))
        else:
            dock.setWidget(QLabel(f"Plugin: {_lbl}"))
        return dock

    dock_manager.register(
        DockDefinition(
            spec.id,
            f"Plugin_{spec.id}",
            label,
            qt_area,
            getattr(spec, "is_singleton", True),
            _make_dock,
            plugin_id=getattr(spec, "plugin_id", ""),
        )
    )
