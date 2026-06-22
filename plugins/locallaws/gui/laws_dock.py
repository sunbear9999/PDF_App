"""
plugins/locallaws/gui/laws_dock.py
"""
import os
import pandas as pd
from PySide6.QtCore import Qt
from PySide6.QtGui import QPdfWriter, QTextDocument, QPageSize
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QComboBox, QListWidget, 
    QTextEdit, QPushButton, QSplitter, QLabel, QFileDialog, QListWidgetItem
)

class LocalLawsDock(QWidget):
    def __init__(self, api, law_manager, app_context=None, parent=None):
        super().__init__(parent)
        self.api = api
        self.law_manager = law_manager
        self.app_context = app_context
        self.current_df = None
        self.active_city = ""
        
        self._build_ui()
        self.refresh_db_list()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # 1. Database Selector
        layout.addWidget(QLabel("Select Jurisdiction:"))
        self.db_selector = QComboBox()
        self.db_selector.currentIndexChanged.connect(self._load_selected_db)
        layout.addWidget(self.db_selector)

        # 2. Vertical Splitter for scaling
        splitter = QSplitter(Qt.Orientation.Vertical)

        # Top Half: List of Laws
        self.law_list = QListWidget()
        self.law_list.itemSelectionChanged.connect(self._display_law)
        splitter.addWidget(self.law_list)

        # Bottom Half: Text Viewer & Export Button
        viewer_widget = QWidget()
        v_layout = QVBoxLayout(viewer_widget)
        v_layout.setContentsMargins(0, 0, 0, 0)
        
        self.text_viewer = QTextEdit()
        self.text_viewer.setReadOnly(True)
        self.text_viewer.setPlaceholderText("Select an ordinance to view its text...")
        
        self.export_btn = QPushButton("Save as PDF to Project")
        self.export_btn.clicked.connect(self._export_pdf)
        self.export_btn.setEnabled(False)
        
        v_layout.addWidget(self.text_viewer)
        v_layout.addWidget(self.export_btn)
        
        splitter.addWidget(viewer_widget)
        
        # Give the list and text viewer a 40/60 height ratio
        splitter.setSizes([400, 600])
        layout.addWidget(splitter)

    def refresh_db_list(self):
        """Pulls the available Parquet files from the law manager."""
        self.db_selector.blockSignals(True)
        self.db_selector.clear()
        
        dbs = self.law_manager.get_installed_dbs()
        if not dbs:
            self.db_selector.addItem("No jurisdictions installed yet...")
        else:
            self.db_selector.addItem("--- Select a Database ---", None)
            for db in dbs:
                self.db_selector.addItem(f"{db['city']}, {db['state']}", db)
                
        self.db_selector.blockSignals(False)

    def _load_selected_db(self):
        """Reads the selected Parquet file and populates the list widget."""
        db_data = self.db_selector.currentData()
        self.law_list.clear()
        self.text_viewer.clear()
        self.export_btn.setEnabled(False)
        
        if not db_data: return
        
        file_path = os.path.join(self.law_manager.download_dir, f"{db_data['city'].lower()}_{db_data['state'].lower()}.parquet")
        
        if not os.path.exists(file_path):
            self.api.notify("Database file missing.", level="error")
            return
            
        try:
            self.current_df = pd.read_parquet(file_path)
            self.active_city = db_data['city']
            
            # Dynamically detect columns (handling dataset variations safely)
            cols = [str(c).lower() for c in self.current_df.columns]
            title_col = next((c for c in self.current_df.columns if 'title' in str(c).lower()), None)
            chap_col = next((c for c in self.current_df.columns if 'chapter' in str(c).lower()), None)
            sec_col = next((c for c in self.current_df.columns if 'section' in str(c).lower()), None)
            text_col = next((c for c in self.current_df.columns if 'text' in str(c).lower() or 'content' in str(c).lower()), None)
            
            if not text_col:
                self.api.notify("Could not detect text column in this database.", level="error")
                return

            for _, row in self.current_df.iterrows():
                title = row[title_col] if title_col else 'N/A'
                chapter = row[chap_col] if chap_col else 'N/A'
                section = row[sec_col] if sec_col else 'N/A'
                text = str(row[text_col]).strip()
                
                label = f"Title {title}, Chapter {chapter}, Section {section}"
                
                item = QListWidgetItem(label)
                # Store the data payload directly inside the UI item
                item.setData(Qt.ItemDataRole.UserRole, {
                    "label": label,
                    "title": title,
                    "chapter": chapter,
                    "section": section,
                    "text": text
                })
                self.law_list.addItem(item)
                
        except Exception as e:
            self.api.notify(f"Error reading Parquet: {e}", level="error")

    def _display_law(self):
        """Shows the text when a user clicks a row."""
        item = self.law_list.currentItem()
        if not item:
            self.export_btn.setEnabled(False)
            return
            
        law_data = item.data(Qt.ItemDataRole.UserRole)
        
        # Format the text viewer with clean HTML
        html = f"""
        <h3 style="margin-bottom: 2px;">{self.active_city} Municipal Code</h3>
        <p style="color: gray; margin-top: 0px;"><b>{law_data['label']}</b></p>
        <hr>
        <p style="font-size: 14px;">{law_data['text'].replace(chr(10), '<br>')}</p>
        """
        self.text_viewer.setHtml(html)
        self.export_btn.setEnabled(True)

    def _export_pdf(self):
        """Converts the text to a native PDF and saves it to the project workspace."""
        item = self.law_list.currentItem()
        if not item: return
        
        law_data = item.data(Qt.ItemDataRole.UserRole)
        default_filename = f"{self.active_city}_Title_{law_data['title']}_Sec_{law_data['section']}.pdf"
        
        # Open a file dialog. If the user is in a project, they can save it directly into their documents folder
        save_path, _ = QFileDialog.getSaveFileName(
            self, "Save Law as PDF", default_filename, "PDF Documents (*.pdf)"
        )
        
        if not save_path: return
        
        try:
            # 1. Setup the PySide6 PDF Engine
            writer = QPdfWriter(save_path)
            writer.setPageSize(QPageSize(QPageSize.PageSizeId.Letter))
            writer.setResolution(300)
            
            # 2. Build the Document formatting
            doc = QTextDocument()
            html_payload = f"""
            <h1 style="text-align: center;">{self.active_city.title()} Municipal Code</h1>
            <h2 style="text-align: center; color: #444;">Title {law_data['title']}, Chapter {law_data['chapter']}, Section {law_data['section']}</h2>
            <hr>
            <p style="font-size: 12pt; line-height: 1.5;">{law_data['text'].replace(chr(10), '<br><br>')}</p>
            """
            doc.setHtml(html_payload)
            
            # 3. Print directly to the file
            doc.print_(writer)
            
            self.api.notify(f"Successfully saved {os.path.basename(save_path)} to project!", level="success")
        except Exception as e:
            self.api.notify(f"Failed to generate PDF: {str(e)}", level="error")

    def update_theme(self, theme: dict) -> None:
        """Applies Papyrus styling to the dock."""
        bg = theme.get("background", "#202124")
        text = theme.get("text", "#ffffff")
        border = theme.get("border", "#3c4043")
        input_bg = theme.get("input_bg", "#292a2d")
        accent = theme.get("accent", "#1a73e8")

        self.setStyleSheet(f"""
            QWidget {{ background-color: {bg}; color: {text}; }}
            QComboBox, QListWidget, QTextEdit {{ 
                background-color: {input_bg}; color: {text}; 
                border: 1px solid {border}; border-radius: 4px; padding: 4px;
            }}
            QListWidget::item:selected {{ background-color: {accent}; color: #ffffff; }}
            QPushButton {{ 
                background-color: {input_bg}; color: {text}; 
                border: 1px solid {border}; border-radius: 4px; padding: 6px; 
            }}
            QPushButton:hover:!disabled {{ background-color: {accent}; color: #ffffff; border: 1px solid {accent}; }}
            QPushButton:disabled {{ color: gray; border: 1px solid {border}; }}
            QSplitter::handle {{ background-color: {border}; margin: 2px 0px; }}
        """)