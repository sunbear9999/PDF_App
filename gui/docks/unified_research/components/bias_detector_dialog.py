import os
import json
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QPushButton,
                             QLabel, QLineEdit, QTextEdit, QComboBox, QTabWidget,
                             QMessageBox, QListWidget, QListWidgetItem, QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem, QGroupBox, QSplitter, QWidget, QAbstractItemView)
from PySide6.QtCore import Qt, QThread, Signal
from core.engine.bias_evaluator import BiasEvaluator
from core.events.event_bus import EventBus

class BiasDetectorDialog(QDialog):
    def __init__(self, app_context, theme, parent=None):
        super().__init__(parent)
        self._ctx = app_context
        self.pm = app_context.project_manager
        self.llm_manager = app_context.llm_manager
        self.theme = theme
        self.bias_api = BiasEvaluator(self.llm_manager, self.pm)
        self.setWindowTitle("⚖️ LIBRA Bias Lab")
        self.resize(1000, 700)
        self.datasets = {}
        self.current_filename = None
        app_context.bus.ui_render_requested.connect(self._on_bus_render)
        self._build_ui()
        self._load_datasets()
        self._apply_theme()

        self.ui_router = getattr(app_context, "ui_router", None)
        if self.ui_router:
            self.ui_router.register_target("bias_lab", self)

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)

        # --- TAB 1: DASHBOARD & METRICS ---
        self.dashboard_tab = QWidget()
        dash_layout = QVBoxLayout(self.dashboard_tab)
        
        # Controls
        ctrl_layout = QHBoxLayout()
        ctrl_layout.addWidget(QLabel("Dataset:"))
        self.combo_dataset = QComboBox()
        ctrl_layout.addWidget(self.combo_dataset)
        ctrl_layout.addWidget(QLabel("Model:"))
        self.combo_model = QComboBox()
        self.combo_model.addItems(self.llm_manager.get_available_models())
        ctrl_layout.addWidget(self.combo_model)
        
        self.btn_run = QPushButton("▶ Run Assessment")
        self.btn_run.clicked.connect(self._run_assessment)
        ctrl_layout.addWidget(self.btn_run)
        dash_layout.addLayout(ctrl_layout)
        
        self.lbl_status = QLabel("Ready.")
        dash_layout.addWidget(self.lbl_status)

        # Metrics Panel
        metrics_box = QGroupBox("Evaluation Metrics")
        metrics_layout = QGridLayout(metrics_box)
        
        self.lbl_eicat = QLabel("EiCAT: --")
        self.lbl_eicat.setStyleSheet("font-size: 24px; font-weight: bold;")
        self.lbl_jsd = QLabel("Semantic Divergence: --")
        self.lbl_bbs = QLabel("Knowledge Boundary (BBS): --")
        self.lbl_lms = QLabel("Language Score (LMS): --")
        
        metrics_layout.addWidget(self.lbl_eicat, 0, 0, 2, 1)
        metrics_layout.addWidget(self.lbl_bbs, 0, 1)
        metrics_layout.addWidget(self.lbl_jsd, 1, 1)
        metrics_layout.addWidget(self.lbl_lms, 0, 2)
        dash_layout.addWidget(metrics_box)

        # Collapsible Raw Output Tree
        dash_layout.addWidget(QLabel("<b>Raw Evaluation Outputs</b> (Collapsible)"))
        self.tree_results = QTreeWidget()
        self.tree_results.setHeaderLabels(["Triplet / Output Segment", "Generated Text"])
        self.tree_results.setColumnWidth(0, 200)
        dash_layout.addWidget(self.tree_results)
        
        self.tabs.addTab(self.dashboard_tab, "📊 Dashboard")

        # --- TAB 2: DATASET EDITOR ---
        self.editor_tab = QWidget()
        editor_layout = QHBoxLayout(self.editor_tab)
        
        # Left Panel: List of files
        left_panel = QVBoxLayout()
        self.list_datasets = QListWidget()
        self.list_datasets.itemClicked.connect(self._on_dataset_selected)
        left_panel.addWidget(QLabel("<b>Saved Datasets</b>"))
        left_panel.addWidget(self.list_datasets)

        btn_refresh = QPushButton("🔄 Refresh Files")
        btn_refresh.clicked.connect(self._load_datasets)
        left_panel.addWidget(btn_refresh)
        
        btn_new = QPushButton("➕ New Dataset")
        btn_new.clicked.connect(self._create_new_dataset)
        left_panel.addWidget(btn_new)
        editor_layout.addLayout(left_panel, 1)
        
        # Right Panel: Visual JSON Editor
        right_panel = QVBoxLayout()
        
        right_panel.addWidget(QLabel("<b>Dataset Name:</b>"))
        self.edit_name = QLineEdit()
        right_panel.addWidget(self.edit_name)
        
        # Terms Table
        right_panel.addWidget(QLabel("<b>Regional/Target Terms & Definitions:</b>"))
        self.table_terms = QTableWidget(0, 2)
        self.table_terms.setHorizontalHeaderLabels(["Target Term", "Official Definition"])
        self.table_terms.horizontalHeader().setStretchLastSection(True)
        right_panel.addWidget(self.table_terms)
        
        term_btns = QHBoxLayout()
        btn_add_term = QPushButton("Add Term")
        btn_add_term.clicked.connect(lambda: self.table_terms.insertRow(self.table_terms.rowCount()))
        btn_del_term = QPushButton("Remove Term")
        btn_del_term.clicked.connect(lambda: self.table_terms.removeRow(self.table_terms.currentRow()))
        term_btns.addWidget(btn_add_term)
        term_btns.addWidget(btn_del_term)
        term_btns.addStretch()
        right_panel.addLayout(term_btns)

        # Triplets List
        right_panel.addWidget(QLabel("<b>Evaluation Triplets:</b>"))
        self.list_triplets = QListWidget()
        self.list_triplets.setFixedHeight(100)
        self.list_triplets.itemClicked.connect(self._load_triplet_form)
        right_panel.addWidget(self.list_triplets)
        
        trip_btns = QHBoxLayout()
        btn_add_trip = QPushButton("Add Triplet")
        btn_add_trip.clicked.connect(self._add_triplet)
        btn_del_trip = QPushButton("Remove Triplet")
        btn_del_trip.clicked.connect(self._delete_triplet)
        trip_btns.addWidget(btn_add_trip)
        trip_btns.addWidget(btn_del_trip)
        trip_btns.addStretch()
        right_panel.addLayout(trip_btns)

        # Triplet Form
        trip_form = QGroupBox("Edit Selected Triplet")
        form_layout = QGridLayout(trip_form)
        self.edit_trip_id = QLineEdit()
        self.edit_trip_term = QLineEdit()
        self.edit_trip_s = QLineEdit()
        self.edit_trip_sp = QLineEdit()
        self.edit_trip_su = QLineEdit()
        
        form_layout.addWidget(QLabel("ID:"), 0, 0)
        form_layout.addWidget(self.edit_trip_id, 0, 1)
        form_layout.addWidget(QLabel("Target Term:"), 0, 2)
        form_layout.addWidget(self.edit_trip_term, 0, 3)
        form_layout.addWidget(QLabel("Baseline (S):"), 1, 0)
        form_layout.addWidget(self.edit_trip_s, 1, 1, 1, 3)
        form_layout.addWidget(QLabel("Counterfactual (Sp):"), 2, 0)
        form_layout.addWidget(self.edit_trip_sp, 2, 1, 1, 3)
        form_layout.addWidget(QLabel("Control (Su):"), 3, 0)
        form_layout.addWidget(self.edit_trip_su, 3, 1, 1, 3)
        
        btn_apply_trip = QPushButton("Apply to Triplet")
        btn_apply_trip.clicked.connect(self._apply_triplet_form)
        form_layout.addWidget(btn_apply_trip, 4, 3)
        right_panel.addWidget(trip_form)

        # Save Set
        save_layout = QHBoxLayout()
        save_layout.addStretch()
        btn_save = QPushButton("💾 Save Dataset")
        btn_save.setFixedSize(150, 40)
        btn_save.clicked.connect(self._save_dataset)
        save_layout.addWidget(btn_save)
        right_panel.addLayout(save_layout)
        
        editor_layout.addLayout(right_panel, 2)
        self.tabs.addTab(self.editor_tab, "✏️ Dataset Editor")

    def _load_datasets(self):
        self.datasets.clear()
        self.list_datasets.clear()
        self.combo_dataset.clear()
        
        for file in os.listdir(self.bias_api.datasets_dir):
            if file.endswith(".json"):
                try:
                    data = self.bias_api.load_bias_dataset(file)
                    self.datasets[file] = data
                    self.list_datasets.addItem(file)
                    self.combo_dataset.addItem(data.get("dataset_name", file), file)
                except Exception:
                    pass

    # --- EXECUTION LOGIC ---
    def _on_bus_render(self, target_id, payload):
        if target_id == "bias_lab":
            self.receive_ai_payload(payload)

    def _run_assessment(self):
        from core.engine.master_runner import MasterActionRunner
        from core.engine.default_blueprints import DefaultBlueprints

        filename = self.combo_dataset.currentData()
        model = self.combo_model.currentText()
        if not filename or not model: return
        
        self.btn_run.setEnabled(False)
        self.tree_results.clear()
        
        # Let the user know it's working, as local models take time
        self.lbl_status.setText("Processing models and evaluating triplets... (This may take a minute)")
        
        blueprint = DefaultBlueprints.get_bias_detector_blueprint()
        
        state = {
            "dataset_filename": filename,
            "target_model": model,
            "selected_model": model, 
            "target_id": "bias_lab"
        }
        
        self.runner = MasterActionRunner(
            blueprint=blueprint,
            initial_state=state,
            llm_manager=self.llm_manager,
            project_manager=self.pm,
            process_registry=getattr(self._ctx, 'process_registry', None),
            prompt_manager=self._ctx.prompt_manager
        )
        
        # 4. Use the cleanly fetched router
        if self.ui_router:
            self.ui_router.attach_runner(self.runner)
        self.runner.action_complete.connect(self._on_runner_complete)
        self.runner.error.connect(self._on_runner_error)
            
        registry = getattr(self._ctx, 'process_registry', None)
        if registry and hasattr(registry, 'enqueue_runner'):
            registry.enqueue_runner(self.runner, "LIBRA Bias Assessment", is_express=False)
        else:
            self.runner.start()

    # 3. ADD THE PAYLOAD RECEIVER (The UI Router will call this)
    def receive_ai_payload(self, payload: dict):
        p_type = payload.get("type")
        
        if p_type == "status":
            self.lbl_status.setText(payload.get("text", ""))
            
        elif p_type == "error":
            self.btn_run.setEnabled(True)
            self.lbl_status.setText(f"❌ Error: {payload.get('message')}")
            
        elif p_type == "bias_metrics":
            self._render_final_metrics(payload.get("metrics", {}))

    def _on_runner_complete(self, final_state: dict):
        metrics = final_state.get("final_bias_metrics") if isinstance(final_state, dict) else None
        if isinstance(metrics, str):
            try:
                metrics = json.loads(metrics)
            except Exception:
                metrics = None
        if isinstance(metrics, dict):
            self._render_final_metrics(metrics)
        elif self.lbl_status.text().startswith("Processing"):
            self.btn_run.setEnabled(True)
            self.lbl_status.setText("Assessment completed, but no bias metrics were produced.")

    def _on_runner_error(self, message: str):
        self.btn_run.setEnabled(True)
        self.lbl_status.setText(f"❌ Error: {message}")

    # 4. RENAME YOUR COMPLETION METHOD
    def _render_final_metrics(self, metrics: dict):
        self.btn_run.setEnabled(True)
        self.lbl_status.setText("✅ Assessment Complete.")
        
        # Update Big Metrics
        self.lbl_eicat.setText(f"EiCAT: {metrics.get('EiCAT', 0)}/100")
        self.lbl_bbs.setText(f"Knowledge Boundary (BBS): {metrics.get('bbs', 0)}%")
        self.lbl_jsd.setText(f"Semantic Divergence: {metrics.get('Semantic_Divergence', 0)}%")
        self.lbl_lms.setText(f"Language Score (LMS): {metrics.get('lms', 0)}%")
        
        color = self.theme.get('error', '#ff4444') if metrics.get('is_highly_biased') else self.theme.get('success', '#00cc66')
        self.lbl_eicat.setStyleSheet(f"font-size: 24px; font-weight: bold; color: {color};")
        
        # Populate Tree
        for r in metrics.get("test_cases", []):
            parent_item = QTreeWidgetItem(self.tree_results, [f"[{r.get('id')}] {r.get('target_term')}", ""])
            base_item = QTreeWidgetItem(parent_item, ["Baseline", r.get("baseline_output", "")])
            cf_item = QTreeWidgetItem(parent_item, ["Counterfactual", r.get("counterfactual_output", "")])
            
            for item in [base_item, cf_item]:
                self.tree_results.setItemWidget(item, 1, self._make_wordwrap_label(item.text(1)))
    def _make_wordwrap_label(self, text):
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        return lbl

    # --- EDITOR LOGIC ---
    def _create_new_dataset(self):
        self.current_filename = None
        self.edit_name.setText("New Dataset")
        self.table_terms.setRowCount(0)
        self.list_triplets.clear()
        self._clear_triplet_form()

    def _on_dataset_selected(self, item):
        filename = item.text()
        self.current_filename = filename
        data = self.datasets.get(filename, {})
        
        self.edit_name.setText(data.get("dataset_name", filename))
        
        self.table_terms.setRowCount(0)
        for term, dfn in data.get("regional_terms", {}).items():
            r = self.table_terms.rowCount()
            self.table_terms.insertRow(r)
            self.table_terms.setItem(r, 0, QTableWidgetItem(term))
            self.table_terms.setItem(r, 1, QTableWidgetItem(dfn))
            
        self.list_triplets.clear()
        for t in data.get("triplets", []):
            item = QListWidgetItem(f"[{t.get('id', 'N/A')}] {t.get('target_term', '')}")
            item.setData(Qt.ItemDataRole.UserRole, t)
            self.list_triplets.addItem(item)
            
        self._clear_triplet_form()

    def _clear_triplet_form(self):
        self.edit_trip_id.clear()
        self.edit_trip_term.clear()
        self.edit_trip_s.clear()
        self.edit_trip_sp.clear()
        self.edit_trip_su.clear()

    def _load_triplet_form(self, item):
        data = item.data(Qt.ItemDataRole.UserRole)
        self.edit_trip_id.setText(data.get("id", ""))
        self.edit_trip_term.setText(data.get("target_term", ""))
        self.edit_trip_s.setText(data.get("S", ""))
        self.edit_trip_sp.setText(data.get("Sp", ""))
        self.edit_trip_su.setText(data.get("Su", ""))

    def _apply_triplet_form(self):
        item = self.list_triplets.currentItem()
        if not item: return
        data = {
            "id": self.edit_trip_id.text(),
            "target_term": self.edit_trip_term.text(),
            "S": self.edit_trip_s.text(),
            "Sp": self.edit_trip_sp.text(),
            "Su": self.edit_trip_su.text()
        }
        item.setData(Qt.ItemDataRole.UserRole, data)
        item.setText(f"[{data['id']}] {data['target_term']}")

    def _add_triplet(self):
        item = QListWidgetItem("[new] TargetTerm")
        item.setData(Qt.ItemDataRole.UserRole, {"id": "new", "target_term": "TargetTerm", "S": "", "Sp": "", "Su": ""})
        self.list_triplets.addItem(item)
        self.list_triplets.setCurrentItem(item)
        self._load_triplet_form(item)

    def _delete_triplet(self):
        row = self.list_triplets.currentRow()
        if row >= 0:
            self.list_triplets.takeItem(row)
            self._clear_triplet_form()

    def _save_dataset(self):
        name = self.edit_name.text().strip()
        if not name: return QMessageBox.warning(self, "Error", "Dataset needs a name.")
        
        filename = self.current_filename or f"{name.replace(' ', '_').lower()}.json"
        
        regional_terms = {}
        for r in range(self.table_terms.rowCount()):
            t_item = self.table_terms.item(r, 0)
            d_item = self.table_terms.item(r, 1)
            if t_item and d_item and t_item.text().strip():
                regional_terms[t_item.text().strip()] = d_item.text().strip()
                
        triplets = []
        for i in range(self.list_triplets.count()):
            triplets.append(self.list_triplets.item(i).data(Qt.ItemDataRole.UserRole))
            
        data = {
            "dataset_name": name,
            "regional_terms": regional_terms,
            "triplets": triplets
        }
        
        filepath = os.path.join(self.bias_api.datasets_dir, filename)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
            
        QMessageBox.information(self, "Saved", f"Successfully saved {filename}")
        self._load_datasets()

    def _apply_theme(self):
        if not self.theme: return
        self.setStyleSheet(f"background-color: {self.theme.get('bg_main', '#1e1e1e')}; color: {self.theme.get('text_main', '#fff')};")
        style = f"background-color: {self.theme.get('bg_input', '#2b2b2b')}; border: 1px solid {self.theme.get('border', '#444')}; border-radius: 4px;"
        
        for w in [self.list_datasets, self.list_triplets, self.table_terms, self.tree_results, 
                  self.combo_dataset, self.combo_model, self.edit_name, self.edit_trip_id,
                  self.edit_trip_term, self.edit_trip_s, self.edit_trip_sp, self.edit_trip_su]:
            w.setStyleSheet(style)
