"""
plugins/zotero/gui/collection_tree.py

QTreeWidget showing Zotero collections and a tag filter list.
Emits signals when the user selects a collection or tag.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)


class CollectionTree(QWidget):
    """
    Left-panel widget with two sections:
      - Collections: hierarchical tree (click → filter by collection)
      - Tags: flat list (click → filter by tag)

    Signals:
      collection_selected(collection_id_or_None)
      tag_selected(tag_name_or_None)
    """

    collection_selected = Signal(object)  # int | None
    tag_selected = Signal(object)         # str | None

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        # --- Collections section ---
        lbl_col = QLabel("Collections")
        lbl_col.setObjectName("sectionLabel")
        layout.addWidget(lbl_col)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.tree.itemClicked.connect(self._on_collection_clicked)
        layout.addWidget(self.tree, 3)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(sep)

        # --- Tags section ---
        lbl_tag = QLabel("Tags")
        lbl_tag.setObjectName("sectionLabel")
        layout.addWidget(lbl_tag)

        self.tag_list = QListWidget()
        self.tag_list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.tag_list.itemClicked.connect(self._on_tag_clicked)
        layout.addWidget(self.tag_list, 2)

    # ------------------------------------------------------------------
    # Public population API
    # ------------------------------------------------------------------

    def populate_collections(self, collections: List[Dict]) -> None:
        """Build the tree from a flat list of {id, name, parent_id} dicts."""
        self.tree.clear()

        # "All Items" root node
        all_item = QTreeWidgetItem(self.tree, ["📚 All Items"])
        all_item.setData(0, Qt.ItemDataRole.UserRole, None)
        self.tree.addTopLevelItem(all_item)
        self.tree.setCurrentItem(all_item)

        # Build id → QTreeWidgetItem map
        nodes: Dict[int, QTreeWidgetItem] = {}
        # First pass: create all nodes
        for col in sorted(collections, key=lambda c: (c.get("parent_id") or 0, c["name"])):
            item = QTreeWidgetItem([f"▶ {col['name']}"])
            item.setData(0, Qt.ItemDataRole.UserRole, col["id"])
            nodes[col["id"]] = item

        # Second pass: attach to parents
        for col in collections:
            node = nodes[col["id"]]
            parent_id = col.get("parent_id")
            if parent_id and parent_id in nodes:
                nodes[parent_id].addChild(node)
            else:
                self.tree.addTopLevelItem(node)

        self.tree.expandAll()

    def populate_tags(self, tags: List[str]) -> None:
        """Populate the tag list."""
        self.tag_list.clear()
        all_tags = QListWidgetItem("All Tags")
        all_tags.setData(Qt.ItemDataRole.UserRole, None)
        self.tag_list.addItem(all_tags)
        for tag in tags:
            item = QListWidgetItem(f"🏷 {tag}")
            item.setData(Qt.ItemDataRole.UserRole, tag)
            self.tag_list.addItem(item)

    def apply_theme(self, theme: dict) -> None:
        bg = theme.get("bg_panel", "#073642")
        bg_input = theme.get("bg_input", "#00212b")
        text = theme.get("text_main", "#93a1a1")
        accent = theme.get("accent", "#268bd2")
        border = theme.get("border", "#586e75")
        muted = theme.get("text_muted", "#586e75")

        tree_style = f"""
            QTreeWidget {{
                background: {bg_input}; color: {text};
                border: 1px solid {border}; border-radius: 4px;
            }}
            QTreeWidget::item:selected {{ background: {accent}; color: #fff; }}
            QTreeWidget::item:hover {{ background: {bg}; }}
        """
        list_style = f"""
            QListWidget {{
                background: {bg_input}; color: {text};
                border: 1px solid {border}; border-radius: 4px;
            }}
            QListWidget::item:selected {{ background: {accent}; color: #fff; }}
            QListWidget::item:hover {{ background: {bg}; }}
        """
        label_style = f"color: {muted}; font-weight: bold; font-size: 11px; background: transparent;"
        sep_style = f"color: {border};"

        self.tree.setStyleSheet(tree_style)
        self.tag_list.setStyleSheet(list_style)
        for lbl in self.findChildren(QLabel):
            lbl.setStyleSheet(label_style)
        for sep in self.findChildren(QFrame):
            sep.setStyleSheet(sep_style)

    # ------------------------------------------------------------------
    # Internal slots
    # ------------------------------------------------------------------

    def _on_collection_clicked(self, item: QTreeWidgetItem, column: int):
        self.tag_list.clearSelection()
        collection_id = item.data(0, Qt.ItemDataRole.UserRole)
        self.collection_selected.emit(collection_id)

    def _on_tag_clicked(self, item: QListWidgetItem):
        self.tree.clearSelection()
        tag = item.data(Qt.ItemDataRole.UserRole)
        self.tag_selected.emit(tag)
