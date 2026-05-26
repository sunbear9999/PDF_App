# gui/docks/unified_research/components/dynamic_data_table.py
import json
from PySide6.QtWidgets import QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QHeaderView
from PySide6.QtCore import Qt

class DynamicDataTableWidget(QWidget):
    def __init__(self, json_data, theme=None, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)

        # Ensure we are working with a list of dictionaries
        if isinstance(json_data, str):
            try: json_data = json.loads(json_data)
            except: json_data = []
            
        if isinstance(json_data, dict):
            # If it's a dict holding a list (e.g., {"cases": [...]}), extract the list
            for val in json_data.values():
                if isinstance(val, list):
                    json_data = val
                    break
            if isinstance(json_data, dict): json_data = [json_data]
            
        if not json_data or not isinstance(json_data[0], dict):
            self.layout.addWidget(QTableWidget()) # Empty fallback
            return

        # Extract headers dynamically from the first object
        headers = list(json_data[0].keys())
        
        self.table = QTableWidget(len(json_data), len(headers))
        self.table.setHorizontalHeaderLabels([h.replace('_', ' ').title() for h in headers])
        
        # Populate data
        for row_idx, row_data in enumerate(json_data):
            for col_idx, key in enumerate(headers):
                val = str(row_data.get(key, ""))
                item = QTableWidgetItem(val)
                item.setFlags(item.flags() ^ Qt.ItemFlag.ItemIsEditable) # Read-only
                self.table.setItem(row_idx, col_idx, item)

        # Auto-resize columns
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        
        self.layout.addWidget(self.table)
        
        if theme:
            self.update_theme(theme)

    def update_theme(self, theme):
        bg_color = theme.get('bg_input', '#2b2b2b')
        text_color = theme.get('text_main', '#ffffff')
        border_color = theme.get('border', '#444')
        
        self.table.setStyleSheet(f"""
            QTableWidget {{
                background-color: {bg_color}; color: {text_color};
                gridline-color: {border_color}; border: 1px solid {border_color};
                border-radius: 4px;
            }}
            QHeaderView::section {{
                background-color: {theme.get('bg_panel', '#333')}; color: {text_color};
                padding: 4px; border: 1px solid {border_color}; font-weight: bold;
            }}
        """)