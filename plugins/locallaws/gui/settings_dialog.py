"""
plugins/locallaws/gui/settings_dialog.py
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
    QLineEdit, QGroupBox, QPushButton, QDialogButtonBox, 
    QScrollArea, QWidget, QCheckBox, QMessageBox
)

class LocalLawsSettingsDialog(QDialog):
    def __init__(self, api, law_manager, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Local Laws Database Manager")
        self.resize(550, 480)
        self._api = api
        self.law_manager = law_manager
        
        # Load the saved list of active DB ids (e.g. ["Parker_CO", "Denver_CO"])
        self.active_dbs = self._api.config.get("active_laws", [])
        self.checkbox_map = {} # Maps CheckBox to file_id
        
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # 1. Download / Ingestion
        self.download_group = QGroupBox("Download New Jurisdiction")
        download_layout = QVBoxLayout()
        input_row = QHBoxLayout()
        self.state_input = QLineEdit()
        self.state_input.setPlaceholderText("State (e.g., CO)")
        self.city_input = QLineEdit()
        self.city_input.setPlaceholderText("City (e.g., Parker)")
        input_row.addWidget(QLabel("State:"))
        input_row.addWidget(self.state_input)
        input_row.addWidget(QLabel("City:"))
        input_row.addWidget(self.city_input)
        self.download_btn = QPushButton("Download & Embed")
        self.download_btn.clicked.connect(self._run_ingestion)
        download_layout.addLayout(input_row)
        download_layout.addWidget(self.download_btn)
        self.download_group.setLayout(download_layout)
        layout.addWidget(self.download_group)

        # 2. Installed Databases Manager
        self.db_group = QGroupBox("Installed Databases (Select to include in AI searches)")
        db_layout = QVBoxLayout()
        
        # Scroll area for when you have lots of cities installed
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_widget = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_widget)
        self.scroll_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        
        self._populate_db_list()
        
        self.scroll_area.setWidget(self.scroll_widget)
        db_layout.addWidget(self.scroll_area)
        self.db_group.setLayout(db_layout)
        layout.addWidget(self.db_group)

        # Buttons
        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.button_box.accepted.connect(self._save_and_accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

    def _populate_db_list(self):
        """Clears and rebuilds the installed databases list."""
        for i in reversed(range(self.scroll_layout.count())): 
            widget = self.scroll_layout.itemAt(i).widget()
            if widget: widget.deleteLater()
            
        self.checkbox_map.clear()
        dbs = self.law_manager.get_installed_dbs()
        
        if not dbs:
            self.scroll_layout.addWidget(QLabel("No local laws installed yet. Download a jurisdiction above."))
            return

        for db in dbs:
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            
            # The Toggle Checkbox
            chk = QCheckBox(f"{db['city']}, {db['state']} ({db['size_mb']} MB)")
            chk.setChecked(db['file_id'] in self.active_dbs)
            self.checkbox_map[chk] = db['file_id']
            
            # The Remove Button
            rm_btn = QPushButton("Delete")
            rm_btn.setFixedWidth(80)
            # PySide6 lambda capturing requires default argument binding
            rm_btn.clicked.connect(lambda _, c=db['city'], s=db['state']: self._delete_db(c, s))
            
            row_layout.addWidget(chk)
            row_layout.addWidget(rm_btn)
            self.scroll_layout.addWidget(row)

    def _delete_db(self, city, state):
        confirm = QMessageBox.question(self, "Confirm Deletion", f"Permanently delete the database for {city}, {state}?")
        if confirm == QMessageBox.StandardButton.Yes:
            self.law_manager.remove_db(city, state)
            
            # Remove it from active list if it was checked
            file_id = f"{city.title()}_{state.upper()}"
            if file_id in self.active_dbs:
                self.active_dbs.remove(file_id)
                self._api.config.set("active_laws", self.active_dbs)
                
            self._populate_db_list()

    def update_theme(self, theme: dict) -> None:
        bg = theme.get("background", "#202124")
        text = theme.get("text", "#ffffff")
        border = theme.get("border", "#3c4043")
        input_bg = theme.get("input_bg", "#292a2d")
        accent = theme.get("accent", "#1a73e8")

        self.setStyleSheet(f"""
            QDialog, QScrollArea, QWidget {{ background-color: {bg}; color: {text}; border: none; }}
            QGroupBox {{ color: {accent}; font-weight: bold; border: 1px solid {border}; border-radius: 6px; margin-top: 12px; padding-top: 12px; }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}
            QCheckBox {{ color: {text}; padding: 4px; }}
            QLineEdit {{ background-color: {input_bg}; color: {text}; border: 1px solid {border}; border-radius: 4px; padding: 5px; }}
            QPushButton {{ background-color: {input_bg}; color: {text}; border: 1px solid {border}; border-radius: 4px; padding: 6px 12px; }}
            QPushButton:hover {{ background-color: {accent}; border: 1px solid {accent}; }}
        """)

    def _handle_ingestion_result(self, result: dict):
        if result.get("success"):
            self._api.notify(f"{result.get('city')} laws embedded!", level="success")
            # Automatically check the new db to be active and refresh the UI
            self.active_dbs = self._api.config.get("active_laws", [])
            self._populate_db_list()
        else:
            self._api.notify(f"Error: {result.get('error')}", level="error")

    def _run_ingestion(self):
        state = self.state_input.text().strip()
        city = self.city_input.text().strip()
        if not state or not city: return
        self._api.notify(f"Querying Hugging Face...", level="info")
        self._api.tasks.run_background(
            self.law_manager.index_real_jurisdiction,
            state, city,
            job_name=f"Ingesting {city} Laws",
            on_done=self._handle_ingestion_result
        )

    def _save_and_accept(self):
        # Gather all checked databases from the map
        active = [file_id for chk, file_id in self.checkbox_map.items() if chk.isChecked()]
        self._api.config.set("active_laws", active)
        self.accept()