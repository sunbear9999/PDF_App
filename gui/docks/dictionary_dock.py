# gui/docks/dictionary_dock.py
import os
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
                             QPushButton, QComboBox, QLineEdit, QCheckBox, 
                             QFileDialog, QMessageBox, QScrollArea, QFrame,
                             QDialog, QTextEdit)
from PySide6.QtCore import Qt, QThread, Signal

class SearchWorker(QThread):
    results_ready = Signal(list)

    def __init__(self, dict_manager, query, dict_id, ignore_diacritics, parent=None):
        super().__init__(parent)
        self.dict_manager = dict_manager
        self.query = query
        self.dict_id = dict_id
        self.ignore_diacritics = ignore_diacritics

    def run(self):
        # Only perform the instant exact/fuzzy SQLite search
        results = self.dict_manager.exact_search(self.query, self.dict_id, self.ignore_diacritics)
        self.results_ready.emit(results)

class AddWordDialog(QDialog):
    def __init__(self, theme=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Custom Word")
        self.setMinimumWidth(400)
        
        if theme:
            self.setStyleSheet(f"background-color: {theme['bg_main']}; color: {theme['text_main']};")
            input_style = f"background-color: {theme['bg_input']}; color: {theme['text_main']}; border: 1px solid {theme['border']}; padding: 6px; border-radius: 4px;"
            btn_style = f"background-color: {theme['bg_panel']}; color: {theme['text_main']}; border: 1px solid {theme['border']}; padding: 6px; border-radius: 4px; font-weight: bold;"
            accent_style = f"background-color: {theme['accent']}; color: #ffffff; border: none; padding: 6px; border-radius: 4px; font-weight: bold;"
        else:
            input_style = ""
            btn_style = ""
            accent_style = ""

        layout = QVBoxLayout(self)
        
        layout.addWidget(QLabel("Word:"))
        self.word_input = QLineEdit()
        self.word_input.setStyleSheet(input_style)
        layout.addWidget(self.word_input)
        
        layout.addWidget(QLabel("Definition:"))
        self.def_input = QTextEdit()
        self.def_input.setStyleSheet(input_style)
        layout.addWidget(self.def_input)
        
        btn_layout = QHBoxLayout()
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setStyleSheet(btn_style)
        self.btn_cancel.clicked.connect(self.reject)
        
        self.btn_save = QPushButton("Save Word")
        self.btn_save.setStyleSheet(accent_style)
        self.btn_save.clicked.connect(self.accept)
        
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_cancel)
        btn_layout.addWidget(self.btn_save)
        layout.addLayout(btn_layout)

    def get_data(self):
        return self.word_input.text().strip(), self.def_input.toPlainText().strip()


# --- NATIVE EXPANDABLE BUBBLE WIDGET ---
class DefinitionBubble(QFrame):
    def __init__(self, word, definitions, source="", theme=None):
        super().__init__()
        self.definitions = definitions
        self.is_expanded = False 
        self.theme = theme

        bg_color = theme['bg_panel'] if theme else "#2b2b2b"
        border_color = theme['border'] if theme else "#444"
        text_color = theme['text_main'] if theme else "#fff"
        self.muted_color = theme['text_muted'] if theme else "#888"
        
        self.setStyleSheet(f"""
            DefinitionBubble {{
                background-color: {bg_color};
                border: 1px solid {border_color};
                border-radius: 8px;
                margin-bottom: 8px;
            }}
        """)
        
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(12, 12, 12, 12)

        # Header
        header_layout = QHBoxLayout()
        word_lbl = QLabel(f"<h2 style='color:{text_color}; margin:0;'>{word.upper()}</h2>")
        header_layout.addWidget(word_lbl)
        header_layout.addStretch()
        self.layout.addLayout(header_layout)

        # Source
        src_lbl = QLabel(f"<i style='color:{self.muted_color}; font-size: 11px;'>Source: {source}</i>")
        self.layout.addWidget(src_lbl)

        # Definitions Container
        self.def_container = QWidget()
        self.def_layout = QVBoxLayout(self.def_container)
        self.def_layout.setContentsMargins(0, 10, 0, 0)
        self.layout.addWidget(self.def_container)

        # Toggle Button
        self.btn_toggle = QPushButton()
        self.btn_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_style = f"background-color: transparent; color: {theme['accent'] if theme else '#55aaff'}; font-weight: bold; border: none; text-align: left; padding-left: 0px;"
        self.btn_toggle.setStyleSheet(btn_style)
        self.btn_toggle.clicked.connect(self.toggle_expand)
        self.layout.addWidget(self.btn_toggle)

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
            if self.theme: lbl.setStyleSheet(f"color: {self.theme['text_main']}; line-height: 1.4; margin-bottom: 4px;")
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


class DictionaryTab(QWidget):
    # Removed llm_manager from init requirements
    def __init__(self, dictionary_manager, main_window=None):
        super().__init__()
        self.dict_manager = dictionary_manager
        self.main_window = main_window
        self.theme = None

        self._build_ui()
        self._refresh_dictionary_list()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # --- 1. Top Controls ---
        top_layout = QHBoxLayout()
        self.dict_combo = QComboBox()
        self.dict_combo.addItem("All Dictionaries", "ALL")
        
        # Replaced AI Sync with Add Word
        self.btn_add = QPushButton("➕ Add Word")
        self.btn_add.setToolTip("Add a custom word to the currently selected dictionary.")
        self.btn_add.clicked.connect(self._add_custom_word)
        
        self.btn_import = QPushButton("➕ Import")
        self.btn_import.clicked.connect(self._import_dictionary)
        
        top_layout.addWidget(self.dict_combo, 1)
        top_layout.addWidget(self.btn_add)
        top_layout.addWidget(self.btn_import)
        layout.addLayout(top_layout)

        # --- 2. Search Bar ---
        search_layout = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Enter word to define...")
        self.search_input.returnPressed.connect(self._trigger_search)
        self.btn_search = QPushButton("Search")
        self.btn_search.clicked.connect(self._trigger_search)
        search_layout.addWidget(self.search_input, 1)
        search_layout.addWidget(self.btn_search)
        layout.addLayout(search_layout)

        # --- 3. Options ---
        options_layout = QVBoxLayout()
        options_layout.setSpacing(2)
        
        self.chk_diacritics = QCheckBox("Ignore Accents & Capitalization (Fuzzy Match)")
        self.chk_diacritics.setChecked(True)
        options_layout.addWidget(self.chk_diacritics)
        layout.addLayout(options_layout)

        # --- 4. Progress / Status Bar ---
        self.lbl_status = QLabel("")
        self.lbl_status.hide()
        layout.addWidget(self.lbl_status)

        # --- 5. NATIVE SCROLL AREA ---
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        
        self.scroll_content = QWidget()
        self.output_layout = QVBoxLayout(self.scroll_content)
        self.output_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        
        self.scroll_area.setWidget(self.scroll_content)
        layout.addWidget(self.scroll_area, 1)

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

    def _refresh_dictionary_list(self):
        current_id = self.dict_combo.currentData()
        self.dict_combo.blockSignals(True)
        self.dict_combo.clear()
        self.dict_combo.addItem("All Dictionaries", "ALL")
        
        dicts = self.dict_manager.get_available_dictionaries()
        for d in dicts:
            self.dict_combo.addItem(d["name"], d["id"])
            
        idx = self.dict_combo.findData(current_id)
        if idx >= 0:
            self.dict_combo.setCurrentIndex(idx)
        self.dict_combo.blockSignals(False)

    def _add_custom_word(self):
        dict_id = self.dict_combo.currentData()
        if dict_id == "ALL":
            QMessageBox.warning(self, "Invalid Selection", "Please select a specific dictionary from the dropdown to add a word to, not 'All Dictionaries'.")
            return
            
        dialog = AddWordDialog(theme=self.theme, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            word, definition = dialog.get_data()
            if word and definition:
                success = self.dict_manager.add_custom_entry(dict_id, word, definition)
                if success:
                    self.lbl_status.setText(f"🟢 Successfully added '{word}'!")
                    self.lbl_status.setStyleSheet("color: #00cc66; font-weight: bold;")
                    self.lbl_status.show()
                    # Optionally search for it immediately to show the user
                    self.public_search(word)
                else:
                    QMessageBox.warning(self, "Error", "Failed to add the word to the database.")
            else:
                QMessageBox.warning(self, "Missing Data", "Both a word and a definition are required.")

    def _import_dictionary(self):
        filepath, _ = QFileDialog.getOpenFileName(
            self, 
            "Import Dictionary", 
            "", 
            "Dictionary Files (*.json *.csv *.xdxf *.ifo)"
        )
        if not filepath: return
        
        ext = filepath.split('.')[-1].lower()
        success = False
        
        self._clear_output()
        self.lbl_status.setText("Importing dictionary to local database...")
        self.lbl_status.show()
        
        if ext == 'json':
            success = self.dict_manager.import_json(filepath)
        elif ext == 'csv':
            success = self.dict_manager.import_csv(filepath)
        elif ext == 'xdxf':
            success = self.dict_manager.import_xdxf(filepath)
        elif ext == 'ifo':
            success = self.dict_manager.import_stardict(filepath)
            
        if success:
            self._refresh_dictionary_list()
            self.lbl_status.setText("🟢 Import successful!")
            self.lbl_status.setStyleSheet("color: #00cc66; font-weight: bold;")
        else:
            QMessageBox.warning(self, "Import Failed", "Could not parse the dictionary file. If importing StarDict, ensure the .idx and .dict files are in the exact same folder as the .ifo file.")

    def public_search(self, query):
        self.search_input.setText(query)
        self._trigger_search()

    def _clear_output(self):
        while self.output_layout.count():
            child = self.output_layout.takeAt(0)
            if child.widget(): child.widget().deleteLater()

    def _trigger_search(self):
        query = self.search_input.text().strip()
        if not query: return
        
        self.btn_search.setEnabled(False)
        self._clear_output()
        
        lbl_searching = QLabel("<i>Searching...</i>")
        if self.theme: lbl_searching.setStyleSheet(f"color: {self.theme['text_muted']};")
        self.output_layout.addWidget(lbl_searching)
        
        dict_id = self.dict_combo.currentData()
        ignore_diacritics = self.chk_diacritics.isChecked()

        self.search_worker = SearchWorker(
            self.dict_manager, query, dict_id, ignore_diacritics, parent=self
        )
        self.search_worker.results_ready.connect(self._display_results)
        self.search_worker.start()

    def _display_results(self, results):
        self.btn_search.setEnabled(True)
        self._clear_output()
        
        if not results:
            lbl = QLabel(f"No definition found for <b>'{self.search_input.text()}'</b>.")
            if self.theme: lbl.setStyleSheet(f"color: {self.theme['text_muted']};")
            self.output_layout.addWidget(lbl)
            return
            
        grouped_results = {}
        for res in results:
            word = res['word'].upper()
            if word not in grouped_results:
                grouped_results[word] = {
                    "sources": set(),
                    "definitions": []
                }
            grouped_results[word]["sources"].add(res['dictionary'])
            
            raw_def = res['definition']
            bullets = [b.strip() for b in raw_def.replace("<br>", "\n").split("\n") if b.strip()]
            grouped_results[word]["definitions"].extend(bullets)

        for word, data in grouped_results.items():
            sources_str = ", ".join(data["sources"])
            
            bubble = DefinitionBubble(
                word=word, 
                definitions=data["definitions"], 
                source=sources_str, 
                theme=self.theme
            )
            self.output_layout.addWidget(bubble)