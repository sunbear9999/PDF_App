# gui/docks/dictionary_dock.py
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QPushButton, QComboBox, QLineEdit, QCheckBox,
                             QFileDialog, QMessageBox, QScrollArea, QFrame,
                             QDialog, QTextEdit)
from PySide6.QtCore import Qt
from core.events.event_bus import EventBus
from gui.components.base import BaseCard, BaseDialog
from core.events.domains.tool_events import DictionaryEvent, DictionaryEventPayload, DictionaryIntent, DictionaryPayload

class AddWordDialog(BaseDialog):
    def __init__(self, theme=None, parent=None):
        super().__init__("Add Custom Word", theme=theme, parent=parent)
        self.setMinimumWidth(400)
        self.update_theme(self.theme)

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Word:"))
        self.word_input = QLineEdit()
        layout.addWidget(self.word_input)

        layout.addWidget(QLabel("Definition:"))
        self.def_input = QTextEdit()
        layout.addWidget(self.def_input)

        btn_layout = QHBoxLayout()
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)

        self.btn_save = QPushButton("Save Word")
        self.btn_save.clicked.connect(self.accept)

        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_cancel)
        btn_layout.addWidget(self.btn_save)
        layout.addLayout(btn_layout)
        self.btn_save.setStyleSheet(self.button_style(accent=True))

    def get_data(self):
        return self.word_input.text().strip(), self.def_input.toPlainText().strip()


# --- NATIVE EXPANDABLE BUBBLE WIDGET ---
class DefinitionBubble(BaseCard):
    def __init__(self, word, definitions, source="", theme=None):
        super().__init__(theme=theme)
        self.definitions = definitions
        self.is_expanded = False

        # Header
        header_layout = QHBoxLayout()
        word_lbl = QLabel(f"<h2 style='margin:0;'>{word.upper()}</h2>")
        header_layout.addWidget(word_lbl)
        header_layout.addStretch()
        self.body_layout.addLayout(header_layout)

        # Source
        self.src_lbl = self.add_body_text(f"<i>Source: {source}</i>", muted=True)

        # Definitions Container
        self.def_container = QWidget()
        self.def_layout = QVBoxLayout(self.def_container)
        self.def_layout.setContentsMargins(0, 10, 0, 0)
        self.body_layout.addWidget(self.def_container)

        # Toggle Button
        self.btn_toggle = QPushButton()
        self.btn_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_toggle.clicked.connect(self.toggle_expand)
        self.body_layout.addWidget(self.btn_toggle)

        self.update_ui()

    def update_ui(self):
        while self.def_layout.count():
            child = self.def_layout.takeAt(0)
            if child.widget(): child.widget().deleteLater()

        display_count = len(self.definitions) if self.is_expanded else 0

        for i in range(display_count):
            lbl = QLabel(f"• {self.definitions[i]}")
            lbl.setWordWrap(True)
            lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            lbl.setStyleSheet(f"color: {self.theme['text_main']}; line-height: 1.4; margin-bottom: 4px;")
            self.def_layout.addWidget(lbl)

        if self.is_expanded:
            self.btn_toggle.show()
            self.btn_toggle.setText("Collapse ▴")
        elif len(self.definitions) > 0:
            self.btn_toggle.show()
            self.btn_toggle.setText(f"Show {len(self.definitions)} definitions ▾")
        else:
            self.btn_toggle.hide()

    def toggle_expand(self):
        self.is_expanded = not self.is_expanded
        self.update_ui()

    def update_theme(self, theme):
        super().update_theme(theme)
        self.btn_toggle.setStyleSheet(self.button_style(transparent=True) + f" color: {self.theme.get('accent')}; text-align: left;")


class DictionaryTab(QWidget):
    def __init__(self, dictionary_manager=None, parent=None):
        super().__init__(parent)
        self.bus = EventBus.get_instance()
        self.theme = None
        self.dictionary_manager = dictionary_manager

        self._build_ui()

        self.bus.dictionary_results_ready.connect(self._display_results)
        self.bus.dictionary_status_updated.connect(self._handle_status)

        self.bus.dictionary_action_requested.emit(DictionaryIntent.FETCH_DICTS, DictionaryPayload())

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        top_layout = QHBoxLayout()
        self.dict_combo = QComboBox()
        self.dict_combo.addItem("All Dictionaries", "ALL")

        self.btn_add = QPushButton("➕ Add Word")
        self.btn_add.clicked.connect(self._trigger_add_word)

        self.btn_import = QPushButton("➕ Import")
        self.btn_import.clicked.connect(self._trigger_import)

        top_layout.addWidget(self.dict_combo, 1)
        top_layout.addWidget(self.btn_add)
        top_layout.addWidget(self.btn_import)
        layout.addLayout(top_layout)

        search_layout = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Enter word to define...")
        self.search_input.returnPressed.connect(self._request_search)
        self.btn_search = QPushButton("Search")
        self.btn_search.clicked.connect(self._request_search)
        search_layout.addWidget(self.search_input, 1)
        search_layout.addWidget(self.btn_search)
        layout.addLayout(search_layout)

        options_layout = QVBoxLayout()
        self.chk_diacritics = QCheckBox("Ignore Accents & Capitalization (Fuzzy Match)")
        self.chk_diacritics.setChecked(True)
        options_layout.addWidget(self.chk_diacritics)
        layout.addLayout(options_layout)

        self.lbl_status = QLabel("")
        self.lbl_status.hide()
        layout.addWidget(self.lbl_status)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_content = QWidget()
        self.output_layout = QVBoxLayout(self.scroll_content)
        self.output_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll_area.setWidget(self.scroll_content)
        layout.addWidget(self.scroll_area, 1)

    def _trigger_add_word(self):
        dict_id = self.dict_combo.currentData()
        if dict_id == "ALL":
            QMessageBox.warning(self, "Invalid Selection", "Please select a specific dictionary.")
            return

        dialog = AddWordDialog(theme=self.theme, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            word, definition = dialog.get_data()
            if word and definition:
                self.bus.dictionary_action_requested.emit(
                    DictionaryIntent.ADD_WORD,
                    DictionaryPayload(dict_id=dict_id, word=word, definition=definition)
                )
            else:
                QMessageBox.warning(self, "Missing Data", "Both a word and a definition are required.")

    def _trigger_import(self):
        filepath, _ = QFileDialog.getOpenFileName(self, "Import Dictionary", "", "Dictionary Files (*.json *.csv *.xdxf *.ifo)")
        if not filepath: return

        self._clear_output()
        self.lbl_status.setText("Importing dictionary to local database...")
        self.lbl_status.show()

        self.bus.dictionary_action_requested.emit(
            DictionaryIntent.IMPORT,
            DictionaryPayload(path=filepath, ext=filepath.split('.')[-1].lower())
        )
    def public_search(self, query):
        self.search_input.setText(query)
        self._request_search()

    def _clear_output(self):
        while self.output_layout.count():
            child = self.output_layout.takeAt(0)
            if child.widget(): child.widget().deleteLater()

    def _request_search(self):
        query = self.search_input.text().strip()
        if not query: return

        self.btn_search.setEnabled(False)
        self._clear_output()
        self.output_layout.addWidget(QLabel("<i>Searching...</i>"))

        self.bus.dictionary_action_requested.emit(
            DictionaryIntent.SEARCH,
            DictionaryPayload(
                query=query,
                dict_id=self.dict_combo.currentData(),
                fuzzy=self.chk_diacritics.isChecked()
            )
        )
    def _handle_status(self, event: DictionaryEvent, payload: DictionaryEventPayload):
        status = event
        if status == DictionaryEvent.DICTS_LOADED:
            current_id = self.dict_combo.currentData()
            self.dict_combo.blockSignals(True)
            self.dict_combo.clear()
            self.dict_combo.addItem("All Dictionaries", "ALL")
            for d in payload.get("data", []):
                self.dict_combo.addItem(d["name"], d["id"])
            idx = self.dict_combo.findData(current_id)
            if idx >= 0: self.dict_combo.setCurrentIndex(idx)
            self.dict_combo.blockSignals(False)

        elif status == DictionaryEvent.WORD_ADDED:
            self.lbl_status.setText(f"🟢 Successfully added '{payload.get('word')}'!")
            self.lbl_status.setStyleSheet("color: #00cc66; font-weight: bold;")
            self.lbl_status.show()
            self.public_search(payload.get("word"))

        elif status == DictionaryEvent.IMPORT_SUCCESS:
            self.lbl_status.setText("🟢 Import successful!")
            self.lbl_status.setStyleSheet("color: #00cc66; font-weight: bold;")

        elif status == DictionaryEvent.PUBLIC_SEARCH:
            query = payload.get("query", "")
            self.search_input.setText(query)
            self.btn_search.setEnabled(False)
            self._clear_output()
            self.output_layout.addWidget(QLabel("<i>Searching...</i>"))

        elif status == DictionaryEvent.ERROR:
            QMessageBox.warning(self, "Error", payload.get("msg", "An error occurred."))

    def _display_results(self, event: DictionaryEvent, payload: DictionaryEventPayload):
        if event != DictionaryEvent.RESULTS_READY:
            return
        results = payload.results
        self.btn_search.setEnabled(True)
        self._clear_output()

        if not results:
            self.output_layout.addWidget(QLabel(f"No definition found for <b>'{self.search_input.text()}'</b>."))
            return

        for data in results:
            bubble = DefinitionBubble(
                word=data.get("word", ""),
                definitions=data.get("definitions", []),
                source=data.get("source") or ", ".join(data.get("sources", [])),
                theme=self.theme,
            )
            self.output_layout.addWidget(bubble)

    def update_theme(self, theme):
        self.theme = theme
        self.setStyleSheet(f"background-color: {theme['bg_main']}; color: {theme['text_main']};")

        style_input = f"background-color: {theme['bg_input']}; color: {theme['text_main']}; border: 1px solid {theme['border']}; padding: 4px; border-radius: 4px;"
        style_btn = f"background-color: {theme['bg_panel']}; color: {theme['text_main']}; border: 1px solid {theme['border']}; padding: 6px; border-radius: 4px; font-weight: bold;"
        style_btn_accent = f"background-color: {theme['accent']}; color: #ffffff; border: none; padding: 6px; border-radius: 4px; font-weight: bold;"

        self.dict_combo.setStyleSheet(style_input)
        self.search_input.setStyleSheet(style_input)

        self.scroll_area.setStyleSheet("background: transparent;")
        self.scroll_content.setStyleSheet("background: transparent;")

        self.btn_import.setStyleSheet(style_btn)
        self.btn_add.setStyleSheet(style_btn)
        self.btn_search.setStyleSheet(style_btn_accent)
        self.lbl_status.setStyleSheet(f"color: {theme['warning']}; font-weight: bold;")

        for i in range(self.output_layout.count()):
            widget = self.output_layout.itemAt(i).widget()
            if isinstance(widget, DefinitionBubble):
                widget.theme = theme
                widget.update_ui()
