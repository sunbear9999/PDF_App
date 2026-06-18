from __future__ import annotations

import webbrowser
from typing import Any, Dict, List

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from ..zotero_sync_adapter import (
    DEFAULT_ZOTERO_LOCAL_API_BASE_URL,
    PYZOTERO_DOCS_URL,
    ZOTERO_API_KEYS_URL,
    PyZoteroClient,
    default_zotero_settings,
)


class ZoteroSettingsDialog(QDialog):
    """Plugin-owned settings dialog for Zotero/PyZotero behavior."""

    def __init__(self, api, db, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Zotero Settings")
        self.resize(640, 680)
        self._api = api
        self._db = db
        self._collections: List[Dict[str, Any]] = []
        self._probe_result = None
        self._build_ui()
        self._load_values()
        self._load_saved_collection()
        self._set_initial_status()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        form = QFormLayout()
        self.auto_add = QCheckBox("Automatically add newly imported project PDFs to Zotero")
        form.addRow("Auto add:", self.auto_add)

        self.local_api_base_url = QLineEdit(DEFAULT_ZOTERO_LOCAL_API_BASE_URL)
        form.addRow("Local API URL:", self.local_api_base_url)

        self.library_type = QComboBox()
        self.library_type.addItem("Personal library", "user")
        self.library_type.addItem("Group library", "group")
        form.addRow("Library type:", self.library_type)

        self.library_id = QLineEdit()
        self.library_id.setPlaceholderText("Your Zotero user ID or group ID")
        form.addRow("Library ID:", self.library_id)

        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("Zotero API key with write access")
        form.addRow("API key:", self.api_key)

        self.collection_mode = QComboBox()
        self.collection_mode.addItem("Collection named after project", "project_named")
        self.collection_mode.addItem("Existing Zotero collection", "existing_collection")
        self.collection_mode.currentIndexChanged.connect(self._update_collection_enabled)
        form.addRow("Collection:", self.collection_mode)

        self.collection_select = QComboBox()
        form.addRow("Existing collection:", self.collection_select)
        layout.addLayout(form)

        self.instructions_label = QLabel(
            "For local Zotero metadata access, open Zotero Settings > Advanced and enable "
            "'Allow other applications on this computer to communicate with Zotero'. "
            "PyZotero's local mode is read-only, so automatic PDF add also requires a Zotero "
            "API key with write access for the selected library."
        )
        self.instructions_label.setWordWrap(True)
        self.instructions_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.instructions_label)

        from PySide6.QtWidgets import QFrame
        sync_box = QFrame()
        sync_box.setObjectName("zoteroSyncBox")
        sync_box.setStyleSheet(
            "QFrame#zoteroSyncBox { border: 1px solid #666; border-radius: 6px; "
            "background: rgba(100,160,255,0.08); padding: 4px; }"
        )
        sync_layout = QVBoxLayout(sync_box)
        sync_layout.setContentsMargins(10, 8, 10, 8)
        sync_layout.setSpacing(4)
        sync_title = QLabel("⚠️  Desktop Sync Required for New Items")
        sync_title.setStyleSheet("font-weight: bold;")
        sync_layout.addWidget(sync_title)
        sync_note = QLabel(
            "Items added via the Zotero web interface or this app's API integration are stored in "
            "Zotero's cloud. They only appear in this app after Zotero desktop syncs from the cloud "
            "to its local database.\n\n"
            "To enable automatic background sync in Zotero desktop:\n"
            "  1. Open Zotero  →  Edit  →  Preferences  →  Sync\n"
            "  2. Sign in to your Zotero account\n"
            "  3. Check 'Sync automatically'\n"
            "  4. Optionally check 'Sync full-text content'\n\n"
            "After syncing, press 🔄 in the Research tab or Sync Zotero to see new items."
        )
        sync_note.setWordWrap(True)
        sync_note.setObjectName("zoteroMuted")
        sync_note.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        sync_layout.addWidget(sync_note)
        layout.addWidget(sync_box)

        action_row = QHBoxLayout()
        self.probe_btn = QPushButton("Check PyZotero")
        self.probe_btn.clicked.connect(self._probe)
        self.keys_btn = QPushButton("Open API Key Page")
        self.keys_btn.clicked.connect(lambda: webbrowser.open(ZOTERO_API_KEYS_URL))
        self.docs_btn = QPushButton("Open PyZotero Docs")
        self.docs_btn.clicked.connect(lambda: webbrowser.open(PYZOTERO_DOCS_URL))
        action_row.addWidget(self.probe_btn)
        action_row.addWidget(self.keys_btn)
        action_row.addWidget(self.docs_btn)
        action_row.addStretch()
        layout.addLayout(action_row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _load_values(self):
        config = self._api.config
        defaults = default_zotero_settings()
        self.auto_add.setChecked(bool(config.get("auto_add_pdfs_to_zotero", defaults["auto_add_pdfs_to_zotero"])))
        self.local_api_base_url.setText(str(config.get("pyzotero_local_api_base_url", defaults["pyzotero_local_api_base_url"])))
        self.library_id.setText(str(config.get("pyzotero_library_id", defaults["pyzotero_library_id"])))
        self.api_key.setText(str(config.get("pyzotero_api_key", defaults["pyzotero_api_key"])))

        library_type = str(config.get("pyzotero_library_type", defaults["pyzotero_library_type"]))
        library_index = self.library_type.findData(library_type)
        self.library_type.setCurrentIndex(library_index if library_index >= 0 else 0)

        mode = str(config.get("target_collection_mode", defaults["target_collection_mode"]))
        index = self.collection_mode.findData(mode)
        self.collection_mode.setCurrentIndex(index if index >= 0 else 0)

    def _load_saved_collection(self):
        self.collection_select.clear()
        self._collections = []
        saved_key = self._api.config.get("target_collection_key", "")
        saved_name = self._api.config.get("target_collection_name", "")
        if saved_key or saved_name:
            self.collection_select.addItem(saved_name or saved_key, saved_key)
        else:
            self.collection_select.addItem("Check Zotero to load local collections", "")
        self._update_collection_enabled()

    def _update_collection_enabled(self):
        self.collection_select.setEnabled(self.collection_mode.currentData() == "existing_collection")

    def _set_initial_status(self):
        db_status = "available" if self._db and self._db.is_available() else "not found"
        self.status_label.setText(
            f"Zotero library database: {db_status}. Saved settings loaded. "
            "Use Check PyZotero to test local access and refresh collections."
        )

    def _refresh_local_api_collections(self, client: PyZoteroClient):
        collections = client.list_local_collections()
        self._set_collection_items(collections)

    def _refresh_sqlite_collections(self):
        try:
            collections = self._db.get_collections() if self._db and self._db.is_available() else []
        except Exception:
            collections = []
        self._set_collection_items(collections)

    def _set_collection_items(self, collections: List[Dict[str, Any]]):
        self._collections = collections
        self.collection_select.clear()
        if not collections:
            self.collection_select.addItem("No local Zotero collections found", "")
        for collection in collections:
            self.collection_select.addItem(
                collection.get("name", "(Untitled Collection)"),
                str(collection.get("key") or collection.get("id") or ""),
            )
        saved_key = self._api.config.get("target_collection_key", "")
        if saved_key:
            index = self.collection_select.findData(saved_key)
            if index >= 0:
                self.collection_select.setCurrentIndex(index)
        self._update_collection_enabled()

    def _probe(self):
        client = PyZoteroClient(
            local_api_base_url=self.local_api_base_url.text().strip() or DEFAULT_ZOTERO_LOCAL_API_BASE_URL,
            library_id=self.library_id.text().strip(),
            library_type=self.library_type.currentData() or "user",
            api_key=self.api_key.text().strip(),
        )
        self._probe_result = client.probe()
        db_status = "available" if self._db and self._db.is_available() else "not found"
        local_status = "reachable" if self._probe_result.local_api_available else "not reachable"
        parts = [
            f"Zotero library database: {db_status}.",
            f"Local Zotero API: {local_status}.",
            self._probe_result.message,
        ]
        if self._probe_result.local_api_available and not self._probe_result.write_capable:
            parts.append("Project-scoped metadata matching/copy stays available; automatic PDF add remains disabled.")
        if self._probe_result.local_api_available:
            try:
                self._refresh_local_api_collections(client)
            except Exception as exc:
                parts.append(f"Local API collections could not be loaded: {exc}")
        elif self._db and self._db.is_available():
            self._refresh_sqlite_collections()
            parts.append("If Zotero desktop is open and lists are blank, close Zotero and refresh.")
        self.status_label.setText(" ".join(parts))

    def update_theme(self, theme: dict):
        bg = theme.get("bg_panel", theme.get("bg_main", "#1e1e1e"))
        input_bg = theme.get("bg_input", "#2b2b2b")
        text = theme.get("text_main", "#ffffff")
        muted = theme.get("text_muted", "#aaaaaa")
        border = theme.get("border", "#444444")
        accent = theme.get("accent", "#4a8cff")
        self.setStyleSheet(f"""
            QDialog {{ background-color: {bg}; color: {text}; }}
            QLabel {{ color: {text}; }}
            QLabel#zoteroMuted {{ color: {muted}; }}
            QLineEdit, QComboBox {{
                background-color: {input_bg};
                color: {text};
                border: 1px solid {border};
                border-radius: 4px;
                padding: 5px;
            }}
            QCheckBox {{ color: {text}; }}
            QPushButton {{
                background-color: {input_bg};
                color: {text};
                border: 1px solid {border};
                border-radius: 4px;
                padding: 6px 10px;
            }}
            QPushButton:hover {{ background-color: {accent}; color: #ffffff; }}
            QDialogButtonBox QPushButton {{
                background-color: {accent};
                color: #ffffff;
                border: none;
            }}
        """)

    def _save_and_accept(self):
        config = self._api.config
        config.set("auto_add_pdfs_to_zotero", self.auto_add.isChecked())
        config.set("pyzotero_local_api_base_url", self.local_api_base_url.text().strip() or DEFAULT_ZOTERO_LOCAL_API_BASE_URL)
        config.set("pyzotero_library_type", self.library_type.currentData() or "user")
        config.set("pyzotero_library_id", self.library_id.text().strip())
        config.set("pyzotero_api_key", self.api_key.text().strip())
        config.set("target_collection_mode", self.collection_mode.currentData() or "project_named")
        config.set("target_collection_key", self.collection_select.currentData() or "")
        config.set("target_collection_name", self.collection_select.currentText() if self.collection_select.currentData() else "")
        self.accept()
