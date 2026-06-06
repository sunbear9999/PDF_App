# gui/docks/unified_research/components/dynamic_card_grid.py
import json
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QCursor, QDesktopServices
from gui.components.base import BaseCard


class UniversalCardWidget(BaseCard):
    def __init__(self, data_dict, theme=None, parent=None):
        super().__init__(theme=theme, parent=parent)
        self.data_dict = data_dict

        # 1. Main Content Rendering
        # Assume the first key is the title, the rest are details
        keys = list(data_dict.keys())
        if not keys: return

        title_key = keys[0]
        lbl_title = self.add_title(f"<b>{data_dict.get(title_key, 'Item')}</b>")
        lbl_title.setStyleSheet("font-size: 14px;")

        for key in keys[1:]:
            if key == "actions": continue # Handled below
            val = str(data_dict[key])
            self.add_body_text(f"<i><b>{key.title()}:</b> {val}</i>")

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

                btn_layout.addWidget(btn)
                btn.setStyleSheet(self.button_style())

            btn_layout.addStretch()
            self.body_layout.addLayout(btn_layout)

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
