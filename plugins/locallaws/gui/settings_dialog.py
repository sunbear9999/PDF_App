"""
plugins/locallaws/gui/settings_dialog.py
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
    QLineEdit, QGroupBox, QPushButton, QDialogButtonBox, 
    QScrollArea, QWidget, QCheckBox, QMessageBox, QTabWidget, QSpinBox
)

class LocalLawsSettingsDialog(QDialog):
    def __init__(self, api, law_manager, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Laws & Precedent Integrations")
        self.resize(550, 560)
        self._api = api
        self.law_manager = law_manager
        
        self.active_dbs = self._api.config.get("active_laws", [])
        self.checkbox_map = {} 
        
        # Keep track of links so we can dynamically recolor them on theme changes
        self._api_link_label = QLabel()
        self._docs_link_label = QLabel()
        
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Tabs for Download sources
        self.tabs = QTabWidget()
        
        # TAB 1: Municipal
        self.municipal_tab = QWidget()
        mun_layout = QVBoxLayout(self.municipal_tab)
        input_row = QHBoxLayout()
        self.state_input = QLineEdit()
        self.state_input.setPlaceholderText("State (e.g., CO)")
        self.city_input = QLineEdit()
        self.city_input.setPlaceholderText("City (e.g., Parker)")
        input_row.addWidget(QLabel("State:"))
        input_row.addWidget(self.state_input)
        input_row.addWidget(QLabel("City:"))
        input_row.addWidget(self.city_input)
        self.download_mun_btn = QPushButton("Download LOCUS Data")
        self.download_mun_btn.clicked.connect(self._run_municipal_ingestion)
        mun_layout.addLayout(input_row)
        mun_layout.addWidget(self.download_mun_btn)
        mun_layout.addStretch()
        self.tabs.addTab(self.municipal_tab, "Municipal Codes")
        
        # TAB 2: Case Law (CourtListener)
        self.caselaw_tab = QWidget()
        case_layout = QVBoxLayout(self.caselaw_tab)
        
        # Helpful Links Section
        links_layout = QHBoxLayout()
        self._api_link_label.setOpenExternalLinks(True)
        self._docs_link_label.setOpenExternalLinks(True)
        links_layout.addWidget(self._api_link_label)
        links_layout.addStretch()
        links_layout.addWidget(self._docs_link_label)
        case_layout.addLayout(links_layout)
        
        key_layout = QHBoxLayout()
        self.api_key_input = QLineEdit()
        self.api_key_input.setPlaceholderText("CourtListener Token")
        self.api_key_input.setText(self._api.config.get("courtlistener_token", ""))
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)
        key_layout.addWidget(QLabel("API Key:"))
        key_layout.addWidget(self.api_key_input)
        case_layout.addLayout(key_layout)

        court_row = QHBoxLayout()
        self.court_input = QLineEdit()
        self.court_input.setPlaceholderText("Court ID (e.g., scotus, ca9)")
        self.query_input = QLineEdit()
        self.query_input.setPlaceholderText("Keyword / Case Name")
        court_row.addWidget(QLabel("Court:"))
        court_row.addWidget(self.court_input)
        court_row.addWidget(QLabel("Query:"))
        court_row.addWidget(self.query_input)
        case_layout.addLayout(court_row)
        
        limit_row = QHBoxLayout()
        self.limit_input = QSpinBox()
        self.limit_input.setRange(1, 500)
        self.limit_input.setValue(25)
        limit_row.addWidget(QLabel("Max Case Limit:"))
        limit_row.addWidget(self.limit_input)
        limit_row.addStretch()
        case_layout.addLayout(limit_row)

        self.download_case_btn = QPushButton("Search & Embed Case Law")
        self.download_case_btn.clicked.connect(self._run_caselaw_ingestion)
        case_layout.addWidget(self.download_case_btn)
        case_layout.addStretch()
        self.tabs.addTab(self.caselaw_tab, "Federal/State Case Law")
        
        layout.addWidget(self.tabs)

        # Installed DBs
        self.db_group = QGroupBox("Active Workspace Routing (Check to enable RAG)")
        db_layout = QVBoxLayout()
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

        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.button_box.accepted.connect(self._save_and_accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

    def _populate_db_list(self):
        for i in reversed(range(self.scroll_layout.count())): 
            widget = self.scroll_layout.itemAt(i).widget()
            if widget: widget.deleteLater()
            
        self.checkbox_map.clear()
        dbs = self.law_manager.get_installed_dbs()
        
        if not dbs:
            self.scroll_layout.addWidget(QLabel("No local/case laws installed yet."))
            return

        for db in dbs:
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            
            icon = "🏛️" if db['type'] == "caselaw" else "📄"
            chk = QCheckBox(f"{icon} {db['label']} ({db['size_mb']} MB)")
            chk.setChecked(db['file_id'] in self.active_dbs)
            self.checkbox_map[chk] = db['file_id']
            
            rm_btn = QPushButton("Delete")
            rm_btn.setFixedWidth(80)
            rm_btn.clicked.connect(lambda _, fid=db['file_id']: self._delete_db(fid))
            
            row_layout.addWidget(chk)
            row_layout.addWidget(rm_btn)
            self.scroll_layout.addWidget(row)

    def _delete_db(self, file_id):
        confirm = QMessageBox.question(self, "Confirm Deletion", f"Permanently delete the database for {file_id}?")
        if confirm == QMessageBox.StandardButton.Yes:
            self.law_manager.remove_db(file_id)
            if file_id in self.active_dbs:
                self.active_dbs.remove(file_id)
                self._api.config.set("active_laws", self.active_dbs)
            self._populate_db_list()

    def update_theme(self, theme: dict) -> None:
        bg = theme.get("bg_panel", theme.get("background", "#202124"))
        text = theme.get("text_main", theme.get("text", "#ffffff"))
        border = theme.get("border", "#3c4043")
        input_bg = theme.get("bg_input", theme.get("input_bg", "#292a2d"))
        accent = theme.get("accent", "#1a73e8")

        # Dynamically inject the theme's accent color into the HTML payload
        self._api_link_label.setText(f'<a href="https://www.courtlistener.com/profile/api/" style="color: {accent}; text-decoration: none; font-weight: bold;">🔑 Get an API Token</a>')
        self._docs_link_label.setText(f'<a href="https://www.courtlistener.com/help/api/rest/v4/case-law/" style="color: {accent}; text-decoration: none; font-weight: bold;">📚 View Court IDs & Search Guide</a>')

        self.setStyleSheet(f"""
            QDialog, QScrollArea, QWidget, QTabWidget::pane {{ background-color: {bg}; color: {text}; border: none; }}
            QTabBar::tab {{ background: {input_bg}; color: {text}; padding: 6px 14px; border: 1px solid {border}; border-bottom: none; }}
            QTabBar::tab:selected {{ background: {accent}; color: white; }}
            QGroupBox {{ color: {accent}; font-weight: bold; border: 1px solid {border}; border-radius: 6px; margin-top: 12px; padding-top: 12px; }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}
            QCheckBox {{ color: {text}; padding: 4px; }}
            QLineEdit, QSpinBox {{ background-color: {input_bg}; color: {text}; border: 1px solid {border}; border-radius: 4px; padding: 5px; }}
            QPushButton {{ background-color: {input_bg}; color: {text}; border: 1px solid {border}; border-radius: 4px; padding: 6px 12px; }}
            QPushButton:hover {{ background-color: {accent}; border: 1px solid {accent}; }}
        """)

    def _handle_ingestion_result(self, result: dict):
        if result.get("success"):
            self._api.notify(f"Embedding complete!", level="success")
            self.active_dbs = self._api.config.get("active_laws", [])
            self._populate_db_list()
        else:
            self._api.notify(f"Error: {result.get('error')}", level="error")

    def _run_municipal_ingestion(self):
        state = self.state_input.text().strip()
        city = self.city_input.text().strip()
        if not state or not city: return
        self._api.tasks.run_background(
            self.law_manager.index_real_jurisdiction,
            state, city,
            job_name=f"Ingesting {city} Laws",
            on_done=self._handle_ingestion_result, pass_cancel_check=True,
        )

    def _run_caselaw_ingestion(self):
        token = self.api_key_input.text().strip()
        court = self.court_input.text().strip()
        query = self.query_input.text().strip()
        limit = self.limit_input.value()
        
        if not token:
            QMessageBox.warning(self, "API Key Missing", "CourtListener token is required.")
            return
        if not court: return
            
        self._api.config.set("courtlistener_token", token)
        self._api.tasks.run_background(
            self.law_manager.index_courtlistener_caselaw,
            court, query, limit, token,
            job_name=f"Ingesting Case Law ({court})",
            on_done=self._handle_ingestion_result, pass_cancel_check=True,
        )

    def _save_and_accept(self):
        token = self.api_key_input.text().strip()
        if token: self._api.config.set("courtlistener_token", token)
        active = [file_id for chk, file_id in self.checkbox_map.items() if chk.isChecked()]
        self._api.config.set("active_laws", active)
        self.accept()