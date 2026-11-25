"""Provider-aware structured search controls for Historical Archives."""
from __future__ import annotations

from typing import Dict, Iterable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QStandardItem
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..models import ArchiveQuery, ProviderSearchSchema, SearchOption


LANGUAGES = (
    SearchOption("", "Any language"),
    SearchOption("English", "English"),
    SearchOption("Spanish", "Spanish"),
    SearchOption("French", "French"),
    SearchOption("German", "German"),
    SearchOption("Italian", "Italian"),
    SearchOption("Portuguese", "Portuguese"),
    SearchOption("Chinese", "Chinese"),
    SearchOption("Japanese", "Japanese"),
)


class CheckableComboBox(QComboBox):
    """Compact multi-select combo backed by checkable standard items."""

    selection_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.lineEdit().setReadOnly(True)
        self.view().pressed.connect(self._toggle_item)

    def add_check_item(self, label: str, value: str, checked: bool = False):
        item = QStandardItem(label)
        item.setData(value, Qt.ItemDataRole.UserRole)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
        item.setData(
            Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked,
            Qt.ItemDataRole.CheckStateRole,
        )
        self.model().appendRow(item)
        self._update_text()

    def selected_values(self):
        values = []
        for row in range(self.model().rowCount()):
            item = self.model().item(row)
            if item.checkState() == Qt.CheckState.Checked:
                values.append(item.data(Qt.ItemDataRole.UserRole))
        return values

    def set_selected_values(self, values: Iterable[str]):
        wanted = set(values)
        for row in range(self.model().rowCount()):
            item = self.model().item(row)
            state = (
                Qt.CheckState.Checked
                if item.data(Qt.ItemDataRole.UserRole) in wanted
                else Qt.CheckState.Unchecked
            )
            item.setCheckState(state)
        self._update_text()
        self.selection_changed.emit()

    def _toggle_item(self, index):
        item = self.model().itemFromIndex(index)
        state = (
            Qt.CheckState.Unchecked
            if item.checkState() == Qt.CheckState.Checked
            else Qt.CheckState.Checked
        )
        item.setCheckState(state)
        self._update_text()
        self.selection_changed.emit()

    def _update_text(self):
        labels = []
        for row in range(self.model().rowCount()):
            item = self.model().item(row)
            if item.checkState() == Qt.CheckState.Checked:
                labels.append(item.text())
        self.lineEdit().setText(", ".join(labels) if labels else "Select archives…")


class ProviderSearchForm(QWidget):
    """Build query controls from the schemas declared by selected providers."""

    changed = Signal()

    def __init__(self, providers: Dict[str, object], max_results: int, parent=None):
        super().__init__(parent)
        self._providers = providers
        self._schemas = {
            key: getattr(provider, "search_schema", ProviderSearchSchema())
            for key, provider in providers.items()
        }
        self._rows = {}
        self._build_ui(max_results)
        self._update_schema()

    def _build_ui(self, max_results):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        archive_row = QFormLayout()
        self.archives = CheckableComboBox()
        for provider_id, provider in self._providers.items():
            self.archives.add_check_item(
                provider.label,
                provider_id,
                checked=provider_id in {"loc", "internet_archive"},
            )
        self.archives.selection_changed.connect(self._providers_changed)
        archive_row.addRow("Archives to search", self.archives)
        root.addLayout(archive_row)
        self.provider_help = QLabel()
        self.provider_help.setWordWrap(True)
        self.provider_help.setProperty("muted", True)
        root.addWidget(self.provider_help)

        self.advanced_toggle = QPushButton("Show advanced search ▸")
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.toggled.connect(self._toggle_advanced)
        root.addWidget(self.advanced_toggle)
        self.advanced = QScrollArea()
        self.advanced.setWidgetResizable(True)
        self.advanced.setMaximumHeight(360)
        advanced_content = QWidget()
        self.form = QFormLayout(advanced_content)
        self._build_filter_fields(max_results)
        self._build_text_fields()
        self.advanced.setWidget(advanced_content)
        self.advanced.setVisible(False)
        root.addWidget(self.advanced)
        self.intent = QLabel()
        self.intent.setWordWrap(True)
        self.intent.setObjectName("intentLabel")
        root.addWidget(self.intent)

    def _build_text_fields(self):
        specs = (
            ("exact_phrase", "Exact phrase", "Words that must occur together"),
            ("all_words", "All words (AND)", "Every word must match"),
            ("any_words", "Any words (OR)", "At least one word must match"),
            ("excluded_words", "Exclude (NOT)", "Words that must not match"),
            ("title", "Title", "Search within title where supported"),
            ("creator", "Creator / contributor", "Author, creator, or contributor"),
            ("subject", "Subject", "Catalog subject or topic"),
        )
        for key, label, placeholder in specs:
            field = QLineEdit()
            field.setPlaceholderText(placeholder)
            field.textChanged.connect(self.changed)
            setattr(self, key, field)
            self._add_row(key, label, field)

    def _build_filter_fields(self, max_results):
        self.date_from, self.date_to = QLineEdit(), QLineEdit()
        self.date_from.setPlaceholderText("YYYY or YYYY-MM-DD")
        self.date_to.setPlaceholderText("YYYY or YYYY-MM-DD")
        dates = QHBoxLayout()
        dates.addWidget(self.date_from)
        dates.addWidget(QLabel("to"))
        dates.addWidget(self.date_to)
        self.location = self._editable_combo("Type or choose a place")
        self.publication = self._editable_combo("Publication or collection")
        self.language = QComboBox()
        self._replace_options(self.language, LANGUAGES)
        self.result_type = QComboBox()
        self.sort = QComboBox()
        self.max_results = QSpinBox()
        self.max_results.setRange(5, 100)
        self.max_results.setValue(max_results)
        self._add_row("result_type", "Material type", self.result_type)
        self._add_row("location", "Place", self.location)
        self._add_row("date", "Date range", dates)
        self._add_row("publication", "Publication / collection", self.publication)
        self._add_row("language", "Language", self.language)
        self._add_row("sort", "Sort results", self.sort)
        self._add_row("max_results", "Maximum per archive", self.max_results)
        for widget in (
            self.date_from, self.date_to, self.location, self.publication,
            self.language, self.result_type, self.sort, self.max_results,
        ):
            self._connect_change(widget)

    def _add_row(self, key, label, field):
        self.form.addRow(label, field)
        label_widget = self.form.labelForField(field)
        self._rows[key] = (label_widget, field)

    def _connect_change(self, widget):
        signal = (
            widget.textChanged if isinstance(widget, QLineEdit)
            else widget.currentTextChanged if isinstance(widget, QComboBox)
            else widget.valueChanged
        )
        signal.connect(self.changed)

    def _providers_changed(self):
        self._update_schema()
        self.changed.emit()

    def _update_schema(self):
        provider_ids = self.selected_provider_ids()
        schemas = [self._schemas[key] for key in provider_ids]
        supported = {field for schema in schemas for field in schema.fields}
        for key, (label, field) in self._rows.items():
            visible = key == "max_results" or key in supported
            label.setVisible(visible)
            self._set_field_visible(field, visible)
            if visible and key != "max_results":
                names = [
                    self._providers[pid].label
                    for pid in provider_ids if key in self._schemas[pid].fields
                ]
                tooltip = f"Supported by: {', '.join(names)}"
                label.setToolTip(tooltip)
                self._set_tooltip(field, tooltip)
        self._merge_schema_options(schemas)
        self._set_provider_help(provider_ids, schemas)

    def _merge_schema_options(self, schemas):
        result_types = self._unique_options(
            option for schema in schemas for option in schema.result_types
        )
        sorts = self._unique_options(
            option for schema in schemas for option in schema.sort_options
        )
        self._replace_options(self.result_type, result_types, "newspaper")
        self._replace_options(self.sort, sorts, "relevance")
        self._merge_editable_options(
            self.location,
            (value for schema in schemas for value in schema.location_options),
        )
        self._merge_editable_options(
            self.publication,
            (value for schema in schemas for value in schema.collection_options),
        )

    def _set_provider_help(self, provider_ids, schemas):
        if not provider_ids:
            text = "Select at least one archive."
        elif len(provider_ids) == 1 and schemas[0].accepts_manifest_url:
            text = schemas[0].query_help
        else:
            text = " ".join(schema.query_help for schema in schemas if schema.query_help)
        self.provider_help.setText(text)

    def selected_provider_ids(self):
        return [
            value for value in self.archives.selected_values()
            if value in self._providers
        ]

    def build_query(self, raw_query: str) -> ArchiveQuery:
        return ArchiveQuery(
            original_query=raw_query.strip() or "*",
            raw_query=raw_query.strip(),
            exact_phrase=self.exact_phrase.text().strip(),
            all_words=self.all_words.text().strip(),
            any_words=self.any_words.text().strip(),
            excluded_words=self.excluded_words.text().strip(),
            title=self.title.text().strip(),
            creator=self.creator.text().strip(),
            subject=self.subject.text().strip(),
            date_from=self.date_from.text().strip(),
            date_to=self.date_to.text().strip(),
            location=self.location.currentText().strip(),
            publication=self.publication.currentText().strip(),
            language=self.language.currentData() or "",
            result_type=self.result_type.currentData() or "all",
            sort=self.sort.currentData() or "relevance",
            max_results=self.max_results.value(),
            provider_ids=self.selected_provider_ids(),
        )

    def load_query(self, query: ArchiveQuery):
        for key in (
            "exact_phrase", "all_words", "any_words", "excluded_words",
            "title", "creator", "subject", "date_from", "date_to",
        ):
            getattr(self, key).setText(getattr(query, key, ""))
        self.location.setEditText(query.location)
        self.publication.setEditText(query.publication)
        self._select_data(self.language, query.language)
        self._select_data(self.result_type, query.result_type)
        self._select_data(self.sort, query.sort)
        self.max_results.setValue(query.max_results)
        if query.provider_ids:
            self.archives.set_selected_values(query.provider_ids)

    def set_interpretation(self, inferred=(), error=""):
        if error:
            self.intent.setText(f"Query issue: {error}")
            self.intent.setProperty("state", "error")
        elif inferred:
            self.intent.setText("Recognized automatically — " + " · ".join(inferred))
            self.intent.setProperty("state", "inferred")
        else:
            self.intent.setText(
                "Boolean syntax supported: \"exact phrase\", AND, OR, NOT, and parentheses."
            )
            self.intent.setProperty("state", "ready")
        self.intent.style().unpolish(self.intent)
        self.intent.style().polish(self.intent)

    def add_result_suggestions(self, results):
        self._merge_editable_options(
            self.location, (place for result in results for place in result.places)
        )
        self._merge_editable_options(
            self.publication,
            (result.publication or result.collection for result in results),
        )

    def _toggle_advanced(self, visible):
        self.advanced.setVisible(visible)
        self.advanced_toggle.setText(
            "Hide advanced search ▾" if visible else "Show advanced search ▸"
        )

    @staticmethod
    def _editable_combo(placeholder):
        combo = QComboBox()
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        combo.lineEdit().setPlaceholderText(placeholder)
        return combo

    @staticmethod
    def _unique_options(options):
        output, seen = [], set()
        for option in options:
            if option.value not in seen:
                seen.add(option.value)
                output.append(option)
        return output

    @staticmethod
    def _replace_options(combo, options, preferred=""):
        current = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        for option in options:
            combo.addItem(option.label, option.value)
        target = preferred if preferred and current is None else current
        index = combo.findData(target)
        combo.setCurrentIndex(max(0, index))
        combo.blockSignals(False)

    @staticmethod
    def _merge_editable_options(combo, values):
        current = combo.currentText()
        existing = {combo.itemText(index).casefold() for index in range(combo.count())}
        for value in values:
            cleaned = str(value or "").strip()
            if cleaned and cleaned.casefold() not in existing:
                combo.addItem(cleaned, cleaned)
                existing.add(cleaned.casefold())
        combo.setEditText(current)

    @staticmethod
    def _select_data(combo, value):
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    @staticmethod
    def _set_field_visible(field, visible):
        if hasattr(field, "setVisible"):
            field.setVisible(visible)
        else:
            for index in range(field.count()):
                widget = field.itemAt(index).widget()
                if widget:
                    widget.setVisible(visible)

    @staticmethod
    def _set_tooltip(field, tooltip):
        if hasattr(field, "setToolTip"):
            field.setToolTip(tooltip)
        else:
            for index in range(field.count()):
                widget = field.itemAt(index).widget()
                if widget:
                    widget.setToolTip(tooltip)
