# gui/components/help_dialog.py
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, 
                             QWidget, QLabel, QPushButton, QCheckBox, QScrollArea, QFrame)
from PyQt6.QtCore import Qt, QSettings
from PyQt6.QtGui import QPixmap, QFont, QMovie
from gui.theme import ThemeManager

class HelpDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Welcome to PDF Workspace - Help & Features")
        self.resize(850, 650)
        self.setModal(True) # Overlays the main window
        
        self.settings = QSettings("PDFMultitool", "Workspace")
        self.theme_manager = ThemeManager()
        
        main_layout = QVBoxLayout(self)
        
        # 1. Setup Tabs
        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)
        
        # Add the specific tabs
        self.tabs.addTab(self._build_ai_features_tab(), "🤖 AI Features")
        self.tabs.addTab(self._build_other_features_tab(), "🛠️ Other Features")
        self.tabs.addTab(self._build_ai_info_tab(), "ℹ️ AI Info")
        
        # 2. Setup Bottom Bar (Checkbox and Close Button)
        bottom_layout = QHBoxLayout()
        
        self.startup_checkbox = QCheckBox("Show this help window on startup")
        # Default to True if the setting doesn't exist yet
        show_on_startup = self.settings.value("show_help_on_startup", True, type=bool)
        self.startup_checkbox.setChecked(show_on_startup)
        self.startup_checkbox.stateChanged.connect(self._save_startup_preference)
        
        close_btn = QPushButton("Close")
        close_btn.setFixedWidth(120)
        close_btn.clicked.connect(self.accept)
        
        bottom_layout.addWidget(self.startup_checkbox)
        bottom_layout.addStretch()
        bottom_layout.addWidget(close_btn)
        
        main_layout.addLayout(bottom_layout)
        self.apply_theme()
        
    def _save_startup_preference(self):
        self.settings.setValue("show_help_on_startup", self.startup_checkbox.isChecked())
        
    def apply_theme(self):
        """Applies the current theme to the dialog so it matches the main app."""
        theme = self.theme_manager.get_theme()
        self.setStyleSheet(f"background-color: {theme['bg_main']}; color: {theme['text_main']};")

    # ---------------------------------------------------------
    # UTILITY METHODS (Makes editing tabs very easy)
    # ---------------------------------------------------------
    def _create_scrollable_tab(self):
        """Creates a standardized scrolling container for a tab."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setSpacing(25)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        
        scroll.setWidget(container)
        return scroll, layout

    def _add_section(self, layout, title, description, media_path=None):
        """
        Easily add a new section to any tab.
        - title: The header text
        - description: The body text (Supports HTML/Rich Text for bolding, bullet points, etc.)
        - media_path: Optional path to an image (.png, .jpg) or animated .gif to embed
        """
        section_widget = QFrame()
        section_layout = QVBoxLayout(section_widget)
        section_layout.setContentsMargins(0, 0, 0, 10)
        
        # 1. Title
        lbl_title = QLabel(title)
        font = lbl_title.font()
        font.setPointSize(16)
        font.setBold(True)
        lbl_title.setFont(font)
        section_layout.addWidget(lbl_title)
        
        # 2. Description
        lbl_desc = QLabel(description)
        lbl_desc.setWordWrap(True)
        lbl_desc.setTextFormat(Qt.TextFormat.RichText) # Allows HTML like <b> or <br>
        lbl_desc.setStyleSheet("font-size: 14px; line-height: 1.5;")
        section_layout.addWidget(lbl_desc)
        
        # 3. Media (Photo / Graphic)
        if media_path:
            lbl_media = QLabel()
            if media_path.lower().endswith('.gif'):
                movie = QMovie(media_path)
                if movie.isValid():
                    # Scale the GIF if it is too large
                    movie.jumpToFrame(0)
                    size = movie.currentImage().size()
                    if not size.isEmpty() and (size.width() > 700 or size.height() > 400):
                        size.scale(700, 400, Qt.AspectRatioMode.KeepAspectRatio)
                        movie.setScaledSize(size)
                    
                    lbl_media.setMovie(movie)
                    movie.start()
                    
                    # CRITICAL: Attach the movie object to the label so it doesn't get garbage collected
                    lbl_media.movie_obj = movie 
                    section_layout.addWidget(lbl_media)
                else:
                    lbl_media.setText(f"<i>[Animation not found/invalid: {media_path}]</i>")
                    section_layout.addWidget(lbl_media)
            else:
                # Handle static images as normal
                pixmap = QPixmap(media_path)
                if not pixmap.isNull():
                    pixmap = pixmap.scaled(700, 400, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                    lbl_media.setPixmap(pixmap)
                    section_layout.addWidget(lbl_media)
                else:
                    lbl_media.setText(f"<i>[Image not found: {media_path}]</i>")
                    section_layout.addWidget(lbl_media)
        
        # 4. Bottom Divider Line
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setFrameShadow(QFrame.Shadow.Sunken)
        theme = self.theme_manager.get_theme()
        divider.setStyleSheet(f"background-color: {theme['border']};") 
        
        layout.addWidget(section_widget)
        layout.addWidget(divider)

    # ---------------------------------------------------------
    # TAB CONTENT DEFINITIONS (Edit these to add your actual info!)
    # ---------------------------------------------------------
    def _build_ai_features_tab(self):
        scroll, layout = self._create_scrollable_tab()
        
        self._add_section(layout, "Chat with your PDF", 
                          "Use the <b>LLM Chat</b> tool to ask questions about your currently active PDF. "
                          "The AI uses advanced retrieval to find exactly what you need.","gui/components/examples/highlight.gif")
        
        self._add_section(layout, "AI Annotations", 
                          "The AI can automatically highlight important sections and attach intelligent "
                          "notes directly to your document workspace.<br><br>"
                          "<i>Tip: Try asking the AI to 'Summarize the main points and highlight them'.</i>")
        
        # Example of how to add an image:
        # self._add_section(layout, "Visual Map", "Here is how the AI maps data.", media_path="gui/assets/ai_map.png")
        
        return scroll

    def _build_other_features_tab(self):
        scroll, layout = self._create_scrollable_tab()
        
        self._add_section(layout, "Built-in OCR", 
                          "Automatically detect scanned documents. When prompted by the yellow banner, "
                          "run them through our optical character recognition engine to make the text selectable.")
                          
        self._add_section(layout, "Workspace Notes", 
                          "Visualize your highlights and notes in an interactive graph layout in the Notes tab. "
                          "Clicking on a note will automatically scroll you to its location in the document.")
                          
        return scroll

    def _build_ai_info_tab(self):
        scroll, layout = self._create_scrollable_tab()
        
        self._add_section(layout, "Privacy & Data Handling", 
                          "Our application prioritizes your privacy. Documents are processed and indexed locally. "
                          "If connecting to a remote LLM API, only the specifically relevant text snippets are sent.")
                          
        self._add_section(layout, "Model Configuration", 
                          "You can connect to Local AI models via systems like Ollama for completely offline functionality, "
                          "or use cloud APIs for higher performance reasoning.")
                          
        return scroll