import json
import re
from PySide6.QtWidgets import QWidget, QVBoxLayout, QPushButton, QLabel, QFrame, QTextEdit
from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor

class InteractiveListButton(QPushButton):
    """A beautiful button styled like a tag that triggers RAG searches when clicked."""
    def __init__(self, text, theme, parent=None):
        super().__init__(text, parent)
        self.setStyleSheet(f"""
            QPushButton {{
                background-color: {theme.get('bg_panel', '#333')}; color: {theme.get('accent', '#b366ff')};
                border: 1px solid {theme.get('border', '#444')}; border-radius: 6px; padding: 6px 10px; 
                text-align: left; font-weight: bold;
            }}
            QPushButton:hover {{ background-color: {theme.get('accent', '#b366ff')}; color: white; }}
        """)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

class UniversalOutlineWidget(QWidget):
    """Dynamically renders JSON dictionaries into an interactive GUI layout."""
    def __init__(self, title, json_data, theme, annot_manager=None, is_expanded=True, parent=None):
        super().__init__(parent)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 8)
        self.layout.setSpacing(0)
        
        raw_text_fallback = ""
        parsed_dict = None
        
        # --- BULLETPROOF JSON PARSING ---
        if isinstance(json_data, str):
            raw_text_fallback = json_data.strip()
            
            # Clean common markdown artifacts
            clean_str = re.sub(r'^```json', '', raw_text_fallback, flags=re.MULTILINE)
            clean_str = re.sub(r'^```', '', clean_str, flags=re.MULTILINE).strip()
            
            # Attempt 1: Standard Parse with strict=False (handles unescaped newlines/tabs)
            try:
                parsed_dict = json.loads(clean_str, strict=False)
            except json.JSONDecodeError:
                # Attempt 2: Strip trailing commas
                clean_str = re.sub(r',\s*\}', '}', clean_str)
                clean_str = re.sub(r',\s*\]', ']', clean_str)
                try:
                    parsed_dict = json.loads(clean_str, strict=False)
                except json.JSONDecodeError:
                    # Attempt 3: Force close brackets (Repairs Truncation)
                    try:
                        parsed_dict = json.loads(clean_str + "}", strict=False)
                    except:
                        try:
                            parsed_dict = json.loads(clean_str + "]}", strict=False)
                        except:
                            parsed_dict = None # Give up, fallback to raw text view
        else:
            parsed_dict = json_data

        # The Toggle Button
        self.btn_toggle = QPushButton(f"▼ {title}" if is_expanded else f"▶ {title}")
        self.btn_toggle.setCheckable(True)
        self.btn_toggle.setChecked(not is_expanded)
        self.btn_toggle.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_toggle.setStyleSheet(f"""
            QPushButton {{
                background-color: {theme.get('bg_panel', '#333')}; color: {theme.get('accent', '#b366ff')};
                border: 1px solid {theme.get('border', '#444')}; border-radius: 6px;
                padding: 8px; font-weight: bold; text-align: left; font-size: 14px;
            }}
            QPushButton:hover {{ background-color: {theme.get('bg_input', '#2b2b2b')}; }}
        """)
        self.btn_toggle.clicked.connect(self._toggle)
        self.layout.addWidget(self.btn_toggle)
        
        # The Content Frame
        self.content_frame = QFrame()
        self.content_frame.setStyleSheet(f"QFrame {{ border-left: 2px solid {theme.get('border', '#444')}; margin-left: 8px; padding-left: 8px; }}")
        cf_layout = QVBoxLayout(self.content_frame)
        cf_layout.setContentsMargins(0, 8, 0, 8)
        self.content_frame.setVisible(is_expanded)
        self.layout.addWidget(self.content_frame)

        # --- RENDER LOGIC ---
        if parsed_dict and isinstance(parsed_dict, dict):
            for key, value in parsed_dict.items():
                if not value: continue
                lbl_title = QLabel(f"<b>{key.replace('_', ' ').title()}</b>")
                lbl_title.setStyleSheet(f"color: {theme.get('text_main', '#fff')}; margin-top: 4px;")
                cf_layout.addWidget(lbl_title)

                if isinstance(value, str):
                    lbl_text = QLabel(value)
                    lbl_text.setWordWrap(True)
                    lbl_text.setStyleSheet(f"color: {theme.get('text_muted', '#aaa')};")
                    cf_layout.addWidget(lbl_text)
                    
                elif isinstance(value, list) and all(isinstance(i, str) for i in value):
                    for term in value:
                        btn = InteractiveListButton(f"🔍 {term}", theme)
                        if annot_manager: btn.clicked.connect(lambda _, t=term: annot_manager.trigger_similar_context(t))
                        cf_layout.addWidget(btn)
                    
                elif isinstance(value, list) and all(isinstance(i, dict) for i in value):
                    for item_dict in value:
                        keys = list(item_dict.keys())
                        if not keys: continue
                        main_val = item_dict[keys[0]]
                        btn = InteractiveListButton(f"📌 {main_val}", theme)
                        if annot_manager: btn.clicked.connect(lambda _, t=main_val: annot_manager.trigger_similar_context(str(t)))
                        cf_layout.addWidget(btn)
                        
                        for sub_key in keys[1:]:
                            sub_lbl = QLabel(f"<i>{item_dict[sub_key]}</i>")
                            sub_lbl.setWordWrap(True)
                            sub_lbl.setStyleSheet(f"color: {theme.get('text_muted', '#aaa')}; padding-left: 16px; margin-bottom: 4px; border-left: 2px solid {theme.get('border', '#444')};")
                            cf_layout.addWidget(sub_lbl)
        else:
            # FALLBACK: Display the raw text beautifully if JSON is totally broken
            err_lbl = QLabel("⚠️ <b>AI Output Formatting Error. Displaying raw data:</b>")
            err_lbl.setStyleSheet(f"color: {theme.get('warning', '#ffaa00')}; margin-bottom: 4px;")
            cf_layout.addWidget(err_lbl)
            
            txt_browser = QTextEdit()
            txt_browser.setPlainText(raw_text_fallback)
            txt_browser.setReadOnly(True)
            txt_browser.setStyleSheet(f"background: rgba(0,0,0,0.1); color: {theme.get('text_muted', '#aaa')}; border: 1px solid {theme.get('border', '#444')}; border-radius: 4px; padding: 4px;")
            
            # Auto-resize height based on content length
            lines = raw_text_fallback.count('\n') + (len(raw_text_fallback) // 80)
            txt_browser.setMinimumHeight(min(400, max(60, lines * 20)))
            cf_layout.addWidget(txt_browser)

    def _toggle(self):
        is_collapsed = self.btn_toggle.isChecked()
        self.content_frame.setVisible(not is_collapsed)
        self.btn_toggle.setText(self.btn_toggle.text().replace("▼" if is_collapsed else "▶", "▶" if is_collapsed else "▼"))