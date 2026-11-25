import os
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QTableWidget,
                               QTableWidgetItem, QPushButton, QComboBox, QLabel,
                               QHeaderView, QApplication, QMessageBox, QPlainTextEdit,
                               QMenu)
from PySide6.QtCore import Qt, QThread, Signal
from core.events.event_bus import EventBus
from core.events.domains.tool_events import CitationEvent, CitationEventPayload, CitationIntent, CitationPayload


class _CitationIndexWorker(QThread):
    """Fetch the citation index in a background thread to avoid blocking the GUI."""
    finished = Signal(object)   # CitationIndex or None

    def __init__(self, svc, doc_id: str):
        super().__init__()
        self._svc = svc
        self._doc_id = doc_id

    def run(self):
        try:
            result = self._svc.get_citation_index(self._doc_id)
        except Exception:
            result = None
        self.finished.emit(result)


class CitationDock(QWidget):
    def __init__(self, app_context=None, parent=None):
        super().__init__(parent)
        self.app_context = app_context
        self.bus = EventBus.get_instance()
        self._row_records = {}
        self._index_worker = None

        self._build_ui()

        self.bus.citation_table_data_ready.connect(self._populate_table)
        self.bus.citation_status_updated.connect(self._handle_status)
        self.bus.pdf_removed.connect(self._remove_source_row)

        self.bus.citation_action_requested.emit(CitationIntent.REFRESH_TABLE, CitationPayload())

    def _remove_source_row(self, _event, payload):
        path = getattr(payload, "path", "")
        for row in range(self.table.rowCount() - 1, -1, -1):
            item = self.table.item(row, 0)
            if item and item.data(Qt.ItemDataRole.UserRole) == path:
                self.table.removeRow(row)
        self._row_records = {
            row: self._row_records.get(old_row, {})
            for row, old_row in enumerate(sorted(self._row_records))
            if self._row_records.get(old_row, {}).get("doc_id") != path
        }

    def _build_ui(self):
        layout = QVBoxLayout(self)

        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Citation Style:"))
        self.style_combo = QComboBox()
        self.style_combo.addItems(["APA", "MLA", "Chicago"])
        toolbar.addWidget(self.style_combo)
        toolbar.addStretch()

        self.btn_refresh = QPushButton("Refresh Data")
        self.btn_refresh.clicked.connect(lambda: self.bus.citation_action_requested.emit(CitationIntent.REFRESH_TABLE, CitationPayload()))
        toolbar.addWidget(self.btn_refresh)

        self.btn_analyze = QPushButton("Citation Analysis")
        self.btn_analyze.setToolTip("Extract and analyze in-text citations for the selected document")
        self.btn_analyze.clicked.connect(self._run_citation_analysis)
        toolbar.addWidget(self.btn_analyze)

        layout.addLayout(toolbar)

        # Plugin buttons are injected here by DockManager after construction
        self._plugin_toolbar_layout = toolbar

        # Expanded columns for accuracy
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(["Use", "File Name", "Title", "Author(s)", "Year", "Journal", "Vol/Issue", "DOI/URL"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.itemChanged.connect(self._save_edits)
        self.table.itemSelectionChanged.connect(self._show_selected_details)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self.table)

        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(120)
        self.details.setPlaceholderText("Select a citation row to inspect all available metadata.")
        layout.addWidget(self.details)

        # Phase 8: Citation index diagnostics panel
        self.analysis_label = QLabel("Citation Analysis")
        self.analysis_label.setVisible(False)
        layout.addWidget(self.analysis_label)

        self.analysis_panel = QPlainTextEdit()
        self.analysis_panel.setReadOnly(True)
        self.analysis_panel.setMaximumHeight(180)
        self.analysis_panel.setPlaceholderText(
            "Click 'Citation Analysis' with a document selected to see match confidence and diagnostics."
        )
        self.analysis_panel.setVisible(False)
        layout.addWidget(self.analysis_panel)

        self.btn_generate = QPushButton("Generate Works Cited")
        self.btn_generate.clicked.connect(self._request_generation)
        layout.addWidget(self.btn_generate)

    def _populate_table(self, event: CitationEvent, payload: CitationEventPayload):
        if event != CitationEvent.TABLE_DATA_READY:
            return
        data_list = payload.data
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        self._row_records = {}

        for doc_path, data in data_list:
            row = self.table.rowCount()
            self.table.insertRow(row)
            data = data or {}
            record_id = doc_path or data.get("doc_id") or ""
            self._row_records[row] = dict(data, doc_id=record_id)

            chk_item = QTableWidgetItem()
            chk_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            chk_item.setCheckState(Qt.CheckState.Checked)
            chk_item.setData(Qt.ItemDataRole.UserRole, record_id)
            self.table.setItem(row, 0, chk_item)

            file_item = QTableWidgetItem(os.path.basename(record_id) if record_id else "")
            file_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(row, 1, file_item)

            self.table.setItem(row, 2, QTableWidgetItem(data.get("title", "")))
            self.table.setItem(row, 3, QTableWidgetItem(data.get("authors", "")))
            self.table.setItem(row, 4, QTableWidgetItem(data.get("year", "")))
            self.table.setItem(row, 5, QTableWidgetItem(data.get("journal", "")))
            self.table.setItem(row, 6, QTableWidgetItem(data.get("vol_issue", "")))
            self.table.setItem(row, 7, QTableWidgetItem(data.get("doi", "")))

        self.table.blockSignals(False)
        self._show_selected_details()

    def _save_edits(self, item):
        if item.column() < 2:
            return
        row = item.row()
        doc_path = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)

        payload = {
            "doc_id": doc_path,
            "title": self.table.item(row, 2).text(),
            "authors": self.table.item(row, 3).text(),
            "year": self.table.item(row, 4).text(),
            "journal": self.table.item(row, 5).text(),
            "vol_issue": self.table.item(row, 6).text(),
            "doi": self.table.item(row, 7).text()
        }
        existing = self._row_records.get(row, {})
        if existing:
            payload.update({
                "publisher": existing.get("publisher", ""),
                "url": existing.get("url", ""),
                "item_type": existing.get("item_type", ""),
                "fields": existing.get("fields", {}),
                "source_provider": existing.get("source_provider", ""),
                "source_item_key": existing.get("source_item_key", ""),
                "source_updated_at": existing.get("source_updated_at", ""),
            })
        self.bus.citation_action_requested.emit(CitationIntent.UPDATE_ENTRY, CitationPayload(data=payload))
        self._row_records[row] = payload

    def _request_generation(self):
        selected_docs = []
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0).checkState() == Qt.CheckState.Checked:
                selected_docs.append(self.table.item(row, 0).data(Qt.ItemDataRole.UserRole))

        if not selected_docs:
            QMessageBox.warning(self, "No Docs Selected", "Please select at least one document.")
            return

        self.bus.citation_action_requested.emit(
            CitationIntent.GENERATE_WORKS_CITED,
            CitationPayload(doc_ids=selected_docs, style=self.style_combo.currentText())
        )
    def _handle_status(self, event: CitationEvent, payload: CitationEventPayload):
        if event == CitationEvent.WORKS_CITED_GENERATED:
            works = payload.get("works", [])
            style = self.style_combo.currentText()
            clipboard_text = f"Works Cited ({style})\n\n" + "\n\n".join(works)
            QApplication.clipboard().setText(clipboard_text)
            QMessageBox.information(self, "Copied!", f"Works Cited page copied to clipboard in {style} format.")

    def _show_selected_details(self):
        row = self.table.currentRow()
        record = self._row_records.get(row, {}) if row >= 0 else {}
        if not record:
            self.details.clear()
            return
        hidden = {"doc_id", "title", "authors", "year", "journal", "doi", "vol_issue"}
        fields = record.get("fields") if isinstance(record.get("fields"), dict) else {}
        combined = dict(fields)
        for key, value in record.items():
            if key not in hidden and key != "fields" and value not in (None, "", {}, []):
                combined.setdefault(key, value)
        lines = []
        for key in sorted(combined):
            value = combined[key]
            if value in (None, "", {}, []):
                continue
            lines.append(f"{key}: {value}")
        self.details.setPlainText("\n".join(lines))

    def _on_context_menu(self, pos):
        item = self.table.itemAt(pos)
        if not item:
            return
        row = item.row()
        self.table.selectRow(row)
        record = self._row_records.get(row, {})
        doc_id = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)

        copy_action = menu.addAction("Copy Formatted Citation")
        copy_action.triggered.connect(lambda checked=False, r=record: self._copy_record_citation(r))

        action_registry = getattr(self.app_context, "action_registry", None) if self.app_context else None
        if action_registry:
            from gui.registry.context_menu_registry import ContextMenuContext
            ctx = ContextMenuContext(
                context_type="citation:item",
                selected_ids=[doc_id] if doc_id else [],
                payload={"doc_ids": [doc_id] if doc_id else [], "citation": record},
                event_bus=self.bus,
            )
            specs = list(action_registry.iter_mount("context_menu:citation:item"))
            if specs:
                menu.addSeparator()
                for spec in specs:
                    if spec.separator_before:
                        menu.addSeparator()
                    label = f"{spec.icon} {spec.label}".strip() if spec.icon else spec.label
                    action = menu.addAction(label)
                    if spec.tooltip:
                        action.setToolTip(spec.tooltip)
                    if spec.callback:
                        action.triggered.connect(lambda checked=False, cb=spec.callback, c=ctx: cb(c))

        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _copy_record_citation(self, record):
        cm = getattr(self.app_context, "citation_manager", None) if self.app_context else None
        if cm and hasattr(cm, "format_entry"):
            text = cm.format_entry(record)
        else:
            text = f"{record.get('authors', '')} ({record.get('year', '')}). {record.get('title', '')}."
        QApplication.clipboard().setText(text)
        QMessageBox.information(self, "Copied!", "Citation copied to clipboard.")

    # ------------------------------------------------------------------
    # Phase 8: Citation index diagnostics
    # ------------------------------------------------------------------

    def _run_citation_analysis(self):
        """Fetch the citation index for the selected doc and display diagnostics."""
        row = self.table.currentRow()
        if row < 0:
            self.analysis_panel.setVisible(True)
            self.analysis_label.setVisible(True)
            self.analysis_panel.setPlainText("Select a document row first.")
            return

        doc_id = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole) if self.table.item(row, 0) else None
        if not doc_id:
            return

        cas = getattr(self.app_context, "citation_app_service", None) if self.app_context else None
        if cas is None or not hasattr(cas, "get_citation_index"):
            self.analysis_panel.setVisible(True)
            self.analysis_label.setVisible(True)
            self.analysis_panel.setPlainText("Citation analysis not available (extraction service not wired).")
            return

        self.btn_analyze.setEnabled(False)
        self.btn_analyze.setText("Analyzing...")
        self.analysis_panel.setVisible(True)
        self.analysis_label.setVisible(True)
        self.analysis_panel.setPlainText("Running citation extraction...")

        self._index_worker = _CitationIndexWorker(cas, doc_id)
        self._index_worker.finished.connect(self._on_citation_index_ready)
        self._index_worker.start()

    def _on_citation_index_ready(self, index):
        self.btn_analyze.setEnabled(True)
        self.btn_analyze.setText("Citation Analysis")

        if index is None:
            self.analysis_panel.setPlainText("No citation index available for this document.")
            return

        lines = []
        bib = getattr(index, "bibliography_entries", [])
        in_text = getattr(index, "in_text_citations", [])
        matches = getattr(index, "citation_matches", [])
        unmatched_bib = getattr(index, "unmatched_bib_entry_ids", [])
        unmatched_in = getattr(index, "unmatched_in_text_ids", [])
        ambiguous = getattr(index, "ambiguous_match_ids", [])
        page_index = getattr(index, "entry_page_index", {})

        lines.append(f"Bibliography entries detected:  {len(bib)}")
        lines.append(f"In-text citations detected:     {len(in_text)}")
        lines.append(f"Matched pairs:                  {len([m for m in matches if m.confidence >= 0.3])}")
        lines.append(f"Unmatched bibliography entries: {len(unmatched_bib)}")
        lines.append(f"Unmatched in-text citations:    {len(unmatched_in)}")
        lines.append(f"Ambiguous matches:              {len(ambiguous)}")
        lines.append("")

        # Show top matched entries with confidence indicators
        if matches:
            lines.append("-- Top citation matches (confidence: H=high M=mid L=low A=ambiguous) --")
            matched = [m for m in matches if m.confidence >= 0.3]
            matched.sort(key=lambda m: -m.confidence)
            for m in matched[:10]:
                conf = m.confidence
                if conf >= 0.7:
                    badge = "H"
                elif conf >= 0.4:
                    badge = "M"
                else:
                    badge = "L"
                if m.is_ambiguous:
                    badge = "A"
                # Find bibliography entry raw text
                entry_raw = next(
                    (e.raw_text[:60] for e in bib if e.entry_id == m.bibliography_entry_id),
                    m.bibliography_entry_id[:40],
                )
                pages = page_index.get(m.bibliography_entry_id, [])
                page_str = f"pp.{','.join(str(p+1) for p in pages[:5])}" if pages else ""
                lines.append(f"  [{badge} {conf:.0%}] {entry_raw}...  {page_str}")
                if m.reason:
                    lines.append(f"          Reason: {m.reason}")

        if unmatched_in:
            lines.append("")
            lines.append(f"-- Unmatched in-text citations ({len(unmatched_in)}) --")
            for cit_id in unmatched_in[:5]:
                cit = next((c for c in in_text if c.citation_id == cit_id), None)
                if cit:
                    lines.append(f"  {cit.raw_text[:60]}  (type={cit.citation_type})")

        self.analysis_panel.setPlainText("\n".join(lines))

    def update_theme(self, theme):
        self.setStyleSheet(f"CitationDock {{ background-color: {theme['bg_main']}; color: {theme['text_main']}; }}")

        self.style_combo.setStyleSheet(f"background-color: {theme['bg_input']}; color: {theme['text_main']}; border: 1px solid {theme['border']}; padding: 4px; border-radius: 4px;")
        self.btn_refresh.setStyleSheet(f"background-color: {theme['bg_panel']}; color: {theme['text_main']}; border: 1px solid {theme['border']}; padding: 6px; border-radius: 4px;")
        self.btn_generate.setStyleSheet(f"background-color: {theme['accent']}; color: #ffffff; font-weight: bold; padding: 8px; border: none; border-radius: 4px;")
        self.details.setStyleSheet(f"background-color: {theme['bg_input']}; color: {theme['text_main']}; border: 1px solid {theme['border']}; padding: 6px;")

        # Target internal QTableWidget components to overwrite hard-coded defaults
        self.table.setStyleSheet(f"""
            QTableWidget {{
                background-color: {theme['bg_main']};
                color: {theme['text_main']};
                gridline-color: {theme['border']};
                border: 1px solid {theme['border']};
            }}
            QTableWidget::item {{
                background-color: {theme['bg_input']};
                color: {theme['text_main']};
            }}
            QTableWidget::item:selected {{
                background-color: {theme['accent']};
                color: #ffffff;
            }}
            QHeaderView::section {{
                background-color: {theme['bg_panel']};
                color: {theme['text_main']};
                border: 1px solid {theme['border']};
                border-top: none;
                border-left: none;
                padding: 4px;
                font-weight: bold;
            }}
            QTableCornerButton::section {{
                background-color: {theme['bg_panel']};
                border: 1px solid {theme['border']};
            }}
        """)
