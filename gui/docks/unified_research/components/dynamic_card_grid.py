# gui/docks/unified_research/components/dynamic_card_grid.py
import json
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QCursor, QDesktopServices

class UniversalCardWidget(QFrame):
    def __init__(self, data_dict, theme=None, parent=None):
        super().__init__(parent)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(12, 12, 12, 12)
        
        # 1. Main Content Rendering
        # Assume the first key is the title, the rest are details
        keys = list(data_dict.keys())
        if not keys: return
        
        title_key = keys[0]
        lbl_title = QLabel(f"<b>{data_dict.get(title_key, 'Item')}</b>")
        lbl_title.setWordWrap(True)
        lbl_title.setStyleSheet("font-size: 14px;")
        self.layout.addWidget(lbl_title)
        
        for key in keys[1:]:
            if key == "actions": continue # Handled below
            val = str(data_dict[key])
            lbl = QLabel(f"<i><b>{key.title()}:</b> {val}</i>")
            lbl.setWordWrap(True)
            self.layout.addWidget(lbl)

        # 2. Dynamic Actions (Buttons)
        # Allows blueprints to say {"actions": [{"label": "Search Google", "url": "https://google.com/search?q={title}"}]}
        actions = data_dict.get("actions", [])
        if actions:
            btn_layout = QHBoxLayout()
            for act in actions:
                btn = QPushButton(act.get("label", "Action"))
                btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
                
                # Dynamic binding based on action type
                url = act.get("url")
                if url:
                    # Safely inject the card's data into the URL template
                    formatted_url = url
                    for k, v in data_dict.items():
                        if k != "actions": formatted_url = formatted_url.replace(f"{{{k}}}", str(v))
                    btn.clicked.connect(lambda _, u=formatted_url: QDesktopServices.openUrl(QUrl(u)))
                
                if theme:
                    btn.setStyleSheet(f"background-color: {theme.get('bg_panel', '#333')}; border: 1px solid {theme.get('border', '#444')}; padding: 4px; border-radius: 4px;")
                btn_layout.addWidget(btn)
                
            btn_layout.addStretch()
            self.layout.addLayout(btn_layout)

        if theme:
            self.update_theme(theme)

    def update_theme(self, theme):
        bg_color = theme.get('bg_input', '#2b2b2b')
        border_color = theme.get('border', '#444')
        self.setStyleSheet(f"""
            UniversalCardWidget {{ background-color: {bg_color}; border: 1px solid {border_color}; border-radius: 6px; margin-bottom: 8px; }}
            QLabel {{ color: {theme.get('text_main', '#fff')}; }}
        """)

class DynamicCardGridWidget(QWidget):
    def __init__(self, json_data, theme=None, parent=None):
        super().__init__(parent)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        
        if isinstance(json_data, str):
            try: json_data = json.loads(json_data)
            except: json_data = []
            
        if isinstance(json_data, dict):
            for val in json_data.values():
                if isinstance(val, list): json_data = val; break
            if isinstance(json_data, dict): json_data = [json_data]
            
        for item in json_data:
            if isinstance(item, dict):
                self.layout.addWidget(UniversalCardWidget(item, theme))