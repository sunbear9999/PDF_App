# gui/managers/workspace_builder.py
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDockWidget, QWidget, QHBoxLayout, QLabel, QPushButton


class WorkspaceBuilder:
    """
    Assembles the fixed-position, non-spawnable docks of the main workspace:
      - Source Viewer dock (center-left, closable/reopenable)
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
    # Source Viewer
    # ----------------------------------------------------------------

    def _build_pdf_dock(self) -> None:
        w = self._w
        pdf_dock = QDockWidget("📄 Source Viewer", w)
        pdf_dock.setObjectName("PDFViewerDock")
        pdf_dock.setWidget(w.viewer)
        pdf_dock.setMinimumSize(160, 200)

        # Reclaimable source pane: users can close it for more workspace room and reopen it from toolbar/shortcuts.
        pdf_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
            | QDockWidget.DockWidgetFeature.DockWidgetClosable
        )
        pdf_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
            | Qt.DockWidgetArea.TopDockWidgetArea
            | Qt.DockWidgetArea.BottomDockWidgetArea
        )

        # Custom title bar with viewer controls + close button
        title_bar = QWidget()
        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(6, 0, 4, 0)
        title_layout.setSpacing(2)

        lbl = QLabel("📄 Source Viewer")
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

        # Explicit close button since we override the system title bar
        btn_close = QPushButton("✕")
        btn_close.setFixedSize(22, 22)
        btn_close.setToolTip("Close Source Viewer")
        btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_close.setStyleSheet(
            "QPushButton { background: transparent; border: none; padding: 0; font-size: 13px; }"
            "QPushButton:hover { background: rgba(200,60,60,0.7); border-radius: 4px; color: white; }"
        )
        btn_close.clicked.connect(pdf_dock.close)
        title_layout.addWidget(btn_close)

        pdf_dock.setTitleBarWidget(title_bar)

        w.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, pdf_dock)
        w.pdf_dock = pdf_dock

    # ----------------------------------------------------------------
    # Document Explorer
    # ----------------------------------------------------------------

    def _build_doc_explorer_dock(self) -> None:
        w = self._w
        from gui.components.document_explorer import DocumentExplorer
        doc_explorer = DocumentExplorer(w.app_context)
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
