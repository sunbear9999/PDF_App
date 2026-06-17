# gui/managers/workspace_builder.py
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDockWidget, QWidget, QHBoxLayout, QLabel, QPushButton


class WorkspaceBuilder:
    """
    Assembles the fixed-position, non-spawnable docks of the main workspace:
      - PDF Viewer dock (center-left, non-closable)
      - Document Explorer dock (left panel, non-closable)

    Called once from MainWindow.__init__ after dock_manager is configured.
    The viewer is already constructed on main_window; we just wrap it here.
    """

    def __init__(self, main_window) -> None:
        self._w = main_window

    def build(self) -> None:
        self._build_pdf_dock()
        self._build_doc_explorer_dock()
        # Arrange doc explorer left of PDF viewer (horizontal split)
        w = self._w
        w.splitDockWidget(
            w.doc_explorer_dock, w.pdf_dock, Qt.Orientation.Horizontal
        )

    # ----------------------------------------------------------------
    # PDF Viewer
    # ----------------------------------------------------------------

    def _build_pdf_dock(self) -> None:
        w = self._w
        pdf_dock = QDockWidget("📄 PDF Viewer", w)
        pdf_dock.setObjectName("PDFViewerDock")
        pdf_dock.setWidget(w.viewer)
        pdf_dock.setMinimumSize(400, 400)

        # Non-closable, non-floatable — always visible centre pane
        pdf_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
        )
        pdf_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
            | Qt.DockWidgetArea.TopDockWidgetArea
            | Qt.DockWidgetArea.BottomDockWidgetArea
        )

        # Custom title bar with viewer controls
        title_bar = QWidget()
        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(10, 0, 10, 0)

        lbl = QLabel("📄 PDF Viewer")
        lbl.setStyleSheet("font-weight: bold; background: transparent;")
        title_layout.addWidget(lbl)
        title_layout.addStretch()

        btn_style = (
            "QPushButton { background: transparent; border: none; padding: 4px 8px; }"
            "QPushButton:hover { background: rgba(128,128,128,0.3); border-radius: 4px; }"
        )
        viewer = w.viewer
        for label, slot in [
            ("➖", viewer.zoom_out),
            ("Fit Width", viewer.zoom_reset),
            ("➕", viewer.zoom_in),
            ("🎯 Focus", viewer.sharpen_focus),
            ("🔃 Rotate", viewer.rotate_view),
        ]:
            btn = QPushButton(label)
            btn.setStyleSheet(btn_style)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(slot)
            title_layout.addWidget(btn)

        title_layout.addStretch()
        pdf_dock.setTitleBarWidget(title_bar)

        w.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, pdf_dock)
        w.pdf_dock = pdf_dock

    # ----------------------------------------------------------------
    # Document Explorer
    # ----------------------------------------------------------------

    def _build_doc_explorer_dock(self) -> None:
        w = self._w
        from gui.components.document_explorer import DocumentExplorer
        doc_explorer = DocumentExplorer(w)
        w.doc_explorer = doc_explorer

        explorer_dock = QDockWidget("📂 Documents", w)
        explorer_dock.setObjectName("DocExplorerDock")
        explorer_dock.setWidget(doc_explorer)
        explorer_dock.setMinimumSize(200, 200)

        # Non-closable sidebar
        explorer_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        explorer_dock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)

        w.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, explorer_dock)
        w.doc_explorer_dock = explorer_dock
