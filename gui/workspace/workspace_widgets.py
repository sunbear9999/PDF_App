# gui/components/workspace_widgets.py
import uuid as _uuid

from PySide6.QtWidgets import (QMenu, QPushButton, QFrame, QHBoxLayout, QLabel,
                               QComboBox, QDialog, QVBoxLayout, QListWidget, QListWidgetItem,
                               QTabWidget, QWidget, QMessageBox, QScrollArea, QSizePolicy)
from PySide6.QtGui import QPainterPath, QPainterPathStroker, QStandardItemModel, QStandardItem, QCursor
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QGraphicsLineItem
from core.utils.workspace_utils import format_highlight_item_label

class GhostLineItem(QGraphicsLineItem):
    def __init__(self, x1, y1, x2, y2, source_id, target_id, sim_score, workspace_view):
        super().__init__(x1, y1, x2, y2)
        self.source_id = source_id
        self.target_id = target_id
        self.sim_score = sim_score
        self.workspace_view = workspace_view
        self.setAcceptedMouseButtons(Qt.MouseButton.RightButton)

    def shape(self):
        p = QPainterPath()
        p.moveTo(self.line().p1())
        p.lineTo(self.line().p2())
        stroker = QPainterPathStroker()
        stroker.setWidth(12)
        return stroker.createStroke(p)

    def contextMenuEvent(self, event):
        menu = QMenu()
        pct = int(self.sim_score * 100)
        convert_action = menu.addAction(f"🔗 Convert to Edge  (similarity {pct}%)")
        action = menu.exec(event.screenPos())
        if action == convert_action:
            self.workspace_view._convert_ghost_to_edge(self.source_id, self.target_id, self.sim_score)
        event.accept()

class CollapsingButton(QPushButton):
    _COLLAPSED_WIDTH = 36
    def __init__(self, icon_text, full_text, parent=None):
        super().__init__(icon_text, parent)
        self._icon_text = icon_text
        self._full_text = full_text
        self.setFixedWidth(self._COLLAPSED_WIDTH)

    def _sync_toolbar(self):
        w = self.parentWidget()
        while w is not None:
            if isinstance(w, QFrame) and w.objectName() == "WorkspaceToolbar":
                w.adjustSize()
                w.move(15, 15)
                return
            w = w.parentWidget()

    def enterEvent(self, event):
        self.setText(self._full_text)
        self.setFixedWidth(self.sizeHint().width() + 8)
        self._sync_toolbar()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.setText(self._icon_text)
        self.setFixedWidth(self._COLLAPSED_WIDTH)
        self._sync_toolbar()
        super().leaveEvent(event)

class CollapsingSection(QFrame):
    def __init__(self, icon_text, content_widget, parent=None):
        super().__init__(parent)
        self._content = content_widget
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(4)
        self._icon_label = QLabel(icon_text)
        self._icon_label.setObjectName("CollapsingIcon")
        layout.addWidget(self._icon_label)
        layout.addWidget(content_widget)
        content_widget.hide()

    def _sync_toolbar(self):
        w = self.parentWidget()
        while w is not None:
            if isinstance(w, QFrame) and w.objectName() == "WorkspaceToolbar":
                w.adjustSize()
                w.move(15, 15)
                return
            w = w.parentWidget()

    def enterEvent(self, event):
        self._content.show()
        self.adjustSize()
        self._sync_toolbar()
        super().enterEvent(event)

    def leaveEvent(self, event):
        local_pos = self.mapFromGlobal(QCursor.pos())
        if not self.rect().contains(local_pos):
            self._content.hide()
            self.adjustSize()
            self._sync_toolbar()
        super().leaveEvent(event)

class CheckableComboBox(QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.view().clicked.connect(self.handle_item_clicked)
        self._changed = False

    def handle_item_clicked(self, index):
        item = self.model().itemFromIndex(index)
        if not item: return

        clicked_data = item.data(Qt.ItemDataRole.UserRole)
        current_state = item.checkState()
        all_item = self.model().item(0)

        self.model().blockSignals(True)

        if clicked_data == "ALL":
            item.setCheckState(Qt.CheckState.Checked)
            for i in range(1, self.model().rowCount()):
                self.model().item(i).setCheckState(Qt.CheckState.Unchecked)
        else:
            if current_state == Qt.CheckState.Checked:
                if all_item and all_item.checkState() == Qt.CheckState.Checked:
                    all_item.setCheckState(Qt.CheckState.Unchecked)
                    for i in range(1, self.model().rowCount()):
                        other_item = self.model().item(i)
                        if other_item != item:
                            other_item.setCheckState(Qt.CheckState.Unchecked)
            else:
                any_checked = any(self.model().item(i).checkState() == Qt.CheckState.Checked for i in range(1, self.model().rowCount()))
                if not any_checked and all_item:
                    all_item.setCheckState(Qt.CheckState.Checked)

        self._changed = True
        self.model().blockSignals(False)
        self._sync_current_index()

        top_left = self.model().index(0, 0)
        bottom_right = self.model().index(self.model().rowCount() - 1, 0)
        self.model().dataChanged.emit(top_left, bottom_right)

    def hidePopup(self):
        if not self._changed:
            super().hidePopup()
        self._changed = False

    def addItem(self, text, userData=None, checked=False):
        if not isinstance(self.model(), QStandardItemModel):
            self.setModel(QStandardItemModel(self))

        item = QStandardItem(text)
        item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
        item.setData(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked, Qt.ItemDataRole.CheckStateRole)
        item.setData(userData, Qt.ItemDataRole.UserRole)
        self.model().appendRow(item)
        if self.currentIndex() < 0:
            self.setCurrentIndex(0)
        self._sync_current_index()

    def _sync_current_index(self):
        model = self.model()
        if isinstance(model, QStandardItemModel):
            for i in range(model.rowCount()):
                item = model.item(i)
                if item and item.checkState() == Qt.CheckState.Checked:
                    self.setCurrentIndex(i)
                    return
            if model.rowCount() > 0:
                self.setCurrentIndex(0)

    def get_checked_items(self):
        checked = []
        model = self.model()
        if isinstance(model, QStandardItemModel):
            for i in range(model.rowCount()):
                item = model.item(i)
                if item and item.checkState() == Qt.CheckState.Checked:
                    checked.append(item.data(Qt.ItemDataRole.UserRole))
        return checked

    def clear(self):
        model = self.model()
        if isinstance(model, QStandardItemModel):
            model.clear()
            model.setColumnCount(1)
            self.setCurrentIndex(-1)
        else:
            super().clear()

class EntityRefsDialog(QDialog):
    """Paginated dialog showing every project-wide occurrence of a workspace entity.

    Opens immediately in a "Scanning…" loading state; call populate() when the
    background worker finishes.  Each card shows the in-context quote, document
    name, page number, an Open button and an Add-to-Workspace button.
    """

    _PAGE_SIZE = 5

    def __init__(self, entity_name: str, entity_node_id: str, bus, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"References  ·  {entity_name[:60]}")
        self.setMinimumSize(560, 500)
        self.resize(600, 560)
        self.setWindowModality(Qt.WindowModality.NonModal)

        self._entity_name = entity_name
        self._entity_node_id = entity_node_id
        self._bus = bus
        self._theme: dict = {}
        self._current_page = 0
        self._occurrences: list = []
        self._total_pages = 1
        self._populated = False

        # ── layout ──────────────────────────────────────────────────────────
        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.setContentsMargins(16, 14, 16, 14)

        self._header = QLabel(f"<b>{entity_name}</b>  —  scanning project…")
        self._header.setWordWrap(True)
        self._header.setStyleSheet("font-size: 13px;")
        root.addWidget(self._header)

        self._content_widget = QWidget()
        self._content_layout = QVBoxLayout(self._content_widget)
        self._content_layout.setSpacing(8)
        self._content_layout.setContentsMargins(0, 0, 4, 0)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setWidget(self._content_widget)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        root.addWidget(self._scroll, 1)

        nav = QHBoxLayout()
        nav.setSpacing(8)
        self._btn_prev = QPushButton("← Prev")
        self._btn_prev.setFixedWidth(84)
        self._lbl_page = QLabel("Scanning…")
        self._lbl_page.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._btn_next = QPushButton("Next →")
        self._btn_next.setFixedWidth(84)
        self._btn_prev.clicked.connect(self._go_prev)
        self._btn_next.clicked.connect(self._go_next)
        nav.addWidget(self._btn_prev)
        nav.addWidget(self._lbl_page, 1)
        nav.addWidget(self._btn_next)
        root.addLayout(nav)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        root.addWidget(close_btn)

        self._btn_prev.setEnabled(False)
        self._btn_next.setEnabled(False)
        self._show_loading_placeholder()

    # ── public API ──────────────────────────────────────────────────────────

    def populate(self, references: list):
        """Fill the dialog with scan results; safe to call from main thread only."""
        self._occurrences = []
        for ref in references:
            occs = ref.get("occurrences") or [
                {"page_num": 0, "context": c}
                for c in ref.get("contexts", [])[:self._PAGE_SIZE]
            ]
            doc_name = ref.get("doc_name", "")
            doc_path = ref.get("doc_path", "")
            for occ in occs:
                self._occurrences.append({
                    "doc_name": doc_name,
                    "doc_path": doc_path,
                    "page_num": occ.get("page_num", 0),
                    "context": occ.get("context", "").strip(),
                })

        n_docs = len(references)
        n_occs = len(self._occurrences)
        self._total_pages = max(1, (n_occs + self._PAGE_SIZE - 1) // self._PAGE_SIZE)
        self._current_page = 0
        self._populated = True

        self._header.setText(
            f"<b>{self._entity_name}</b> — found in <b>{n_docs}</b> "
            f"document{'s' if n_docs != 1 else ''}, "
            f"<b>{n_occs}</b> occurrence{'s' if n_occs != 1 else ''}"
        )
        self._render_page()

    # ── internal helpers ─────────────────────────────────────────────────────

    def _show_loading_placeholder(self):
        self._clear_content()
        lbl = QLabel("Scanning project for references…")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet("color: #888; font-size: 12px; padding: 24px;")
        self._content_layout.addWidget(lbl)
        self._content_layout.addStretch()

    def _clear_content(self):
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    # ── pagination ──────────────────────────────────────────────────────────

    def _go_prev(self):
        self._current_page -= 1
        self._render_page()
        self._scroll.verticalScrollBar().setValue(0)

    def _go_next(self):
        self._current_page += 1
        self._render_page()
        self._scroll.verticalScrollBar().setValue(0)

    def _render_page(self):
        self._clear_content()

        self._current_page = max(0, min(self._current_page, self._total_pages - 1))
        start = self._current_page * self._PAGE_SIZE
        page_occs = self._occurrences[start:start + self._PAGE_SIZE]

        if not self._occurrences:
            lbl = QLabel("No mentions found in any project document.")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet("color: #888; font-size: 12px; padding: 24px;")
            self._content_layout.addWidget(lbl)
        else:
            for occ in page_occs:
                self._content_layout.addWidget(self._build_card(occ))

        self._content_layout.addStretch()
        self._lbl_page.setText(f"Page {self._current_page + 1} of {self._total_pages}")
        self._btn_prev.setEnabled(self._current_page > 0)
        self._btn_next.setEnabled(self._current_page < self._total_pages - 1)

    # ── card builder ────────────────────────────────────────────────────────

    def _build_card(self, occ: dict) -> QFrame:
        t = self._theme
        bg     = t.get("bg_input",  "#2d2d2d")
        border = t.get("border",    "#444")
        text   = t.get("text_main", "#eeeeee")
        dim    = t.get("text_dim",  "#888888")
        accent = t.get("accent",    "#5ba3f5")
        add_bg = t.get("bg_panel",  "#383838")

        card = QFrame()
        card.setFrameShape(QFrame.Shape.NoFrame)
        card.setStyleSheet(
            f"QFrame {{ background:{bg}; border:1px solid {border}; border-radius:10px; }}"
        )
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        # Quote text
        ctx = occ.get("context", "")
        if ctx:
            quote_lbl = QLabel(f"“{ctx[:400]}”")
            quote_lbl.setWordWrap(True)
            quote_lbl.setStyleSheet(
                f"color:{text}; font-style:italic; font-size:12px; "
                f"background:transparent; border:none;"
            )
            layout.addWidget(quote_lbl)

        # Footer row: doc · page | Open | Add to Workspace
        footer = QHBoxLayout()
        footer.setSpacing(6)

        doc_name = occ.get("doc_name", "Unknown")
        pg = occ.get("page_num", 0) + 1
        info_lbl = QLabel(f"📄  {doc_name}  ·  p. {pg}")
        info_lbl.setStyleSheet(
            f"color:{dim}; font-size:10px; background:transparent; border:none;"
        )
        footer.addWidget(info_lbl)
        footer.addStretch()

        doc_path = occ.get("doc_path", "")
        page_num = occ.get("page_num", 0)
        context_text = ctx

        # Open button
        btn_open = QPushButton("Open")
        btn_open.setFixedSize(58, 24)
        btn_open.setStyleSheet(
            f"QPushButton {{ background:{accent}; color:#111; border:none; "
            f"border-radius:5px; font-size:11px; font-weight:600; }}"
            f"QPushButton:hover {{ background:{accent}bb; }}"
        )

        def _open(checked=False, _p=doc_path, _pg=page_num):
            if _p:
                from core.events.domains.document_events import DocumentIntent, DocumentPayload
                self._bus.document_action_requested.emit(
                    DocumentIntent.OPEN, DocumentPayload(path=_p, source_type="pdf")
                )
                QTimer.singleShot(120, lambda: self._bus.document_action_requested.emit(
                    DocumentIntent.JUMP_TO_LOCATION, DocumentPayload(path=_p, page_num=_pg)
                ))

        btn_open.clicked.connect(_open)
        footer.addWidget(btn_open)

        # Add to Workspace button
        btn_add = QPushButton("+ Add")
        btn_add.setFixedSize(58, 24)
        btn_add.setStyleSheet(
            f"QPushButton {{ background:{add_bg}; color:{accent}; border:1px solid {accent}; "
            f"border-radius:5px; font-size:11px; }}"
            f"QPushButton:hover {{ background:{accent}22; }}"
        )

        def _add_quote(checked=False, _btn=btn_add,
                       _p=doc_path, _pg=page_num, _ctx=context_text,
                       _doc=doc_name):
            from core.events.domains.workspace_events import WorkspaceIntent, WorkspacePayload
            quote_id = str(_uuid.uuid4())
            entity_dict = {
                "id": quote_id,
                "type": "entity.evidence",
                "text": _ctx[:300] if _ctx else f"p.{_pg + 1} – {_doc}",
                "pdf_path": _p,
                "page_num": _pg,
            }
            relation = {
                "source": quote_id,
                "target": self._entity_node_id,
                "type": "relation.supports",
                "label": "supports",
            }
            self._bus.workspace_action_requested.emit(
                WorkspaceIntent.IMPORT_GRAPH,
                WorkspacePayload(extra={"graph": {
                    "entities": [entity_dict],
                    "relations": [relation],
                }}),
            )
            _btn.setText("✓")
            _btn.setEnabled(False)

        btn_add.clicked.connect(_add_quote)
        footer.addWidget(btn_add)

        layout.addLayout(footer)
        return card

    # ── theming ─────────────────────────────────────────────────────────────

    def apply_theme(self, theme: dict):
        self._theme = theme
        bg   = theme.get("bg_panel", "#1e1e1e")
        text = theme.get("text_main", "#eeeeee")
        dim  = theme.get("text_dim",  "#888888")
        nav_btn = (
            f"QPushButton {{ background:{theme.get('bg_input','#333')}; "
            f"color:{text}; border:1px solid {theme.get('border','#555')}; "
            f"border-radius:5px; padding:2px 8px; }}"
            f"QPushButton:hover {{ background:{theme.get('accent','#5ba3f5')}33; }}"
            f"QPushButton:disabled {{ color:{dim}; }}"
        )
        self.setStyleSheet(
            f"QDialog {{ background:{bg}; color:{text}; }}"
            f"QScrollArea {{ background:transparent; border:none; }}"
            f"QLabel {{ color:{text}; }}"
        )
        self._btn_prev.setStyleSheet(nav_btn)
        self._btn_next.setStyleSheet(nav_btn)
        if self._populated:
            self._render_page()
        # If not yet populated, leave the loading placeholder visible
