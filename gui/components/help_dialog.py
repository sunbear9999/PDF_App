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
        self.tabs.addTab(self._build_workspace_tab(),"Workspace Help")
        self.tabs.addTab(self._build_ai_tips_tab(),"AI Tips")
        
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
        
        self._add_section(layout, "LLM Chat","Use the LLM chat tab to ask your local AI model questions about the projectʻs PDFs. Select which PDFs you wish the LLM to pull from")
        
        self._add_section(layout, "AI Annotations", 
                          "The AI can automatically highlight important sections and attach intelligent "
                          "notes directly to your document workspace. AI notes are specifically labeled as such to be properly double-checked<br><br>")
        self._add_section(layout,"Organize Selected Nodes","LLM categorizes selected workspace nodes based on user-provided criteria. Creates and attaches new categories to pre-existing notes, helping organize thoughts")
        self._add_section(layout,"Find New Connections","Searches for potential missed connections amongst selected notes, and automatically applies them. AI generated connections are explictly labeled to ensure double checking")
        self._add_section(layout,"Generate Outline","Generates a potential paper outline based on your existing nodes and connections. Helps organize abstract thoughts and connections into solid outline")
        self._add_section(layout,"Identify Weakpoints","Examines existing nodes and connections to understand argument being made. Then points out aspects of argument that donʻt have enough reliable annotations connected to them, and identifies other considerations to address")
        self._add_section(layout,"Fill out Graph","Create an outline of your claims and reasoning, and the LLM will scan the documents to find specific quotations to support your reasoning.")
        
        # Example of how to add an image:
        # self._add_section(layout, "Visual Map", "Here is how the AI maps data.", media_path="gui/assets/ai_map.png")
        
        return scroll
    def _build_workspace_tab(self):
        scroll, layout = self._create_scrollable_tab()
        self._add_section(layout,"Connect","Connect two nodes by using connect button, or selecting a note and right clicking the one you wish to connect it to")
        self._add_section(layout,"Resize","Drag the corner box to automatically scale text and box")
        self._add_section(layout,"Note Options","Hover over a note to view the full quote attached to it, change text size, change color, jump to quote in PDF, or to edit the note")
        self._add_section(layout,"Zoom","Use the buttons in the workspace to zoom, or hold shift and scroll")
        self._add_section(layout,"Select","Hold Shift and Drag to select multiple notes at once")
        self._add_section(layout,"Line Options","Write click a line to change itʻs color, size, or text")
        self._add_section(layout,"Filter by PDF","Use the filter by PDF dropdown menu to only show notes from selected PDFs. All AI features will only reference PDFs actively displayed in Workspace")
        self._add_section(layout,"Color by PDF","Use the color by PDF button to automatically make notes color coordinated with their respective PDFs")
        self._add_section(layout,"Export as Image","Export your current workspace, or selected nodes, as a png")
        self._add_section(layout,"Declutter","Select Notes and click declutter to organize notes more cleanly")
        self._add_section(layout,"Delete Node","Right click on selected node(s) to delete")
        self._add_section(layout,"Use AI tool","Use the buttons in the toolbar, or right click selected nodes to use an AI tool. Refer to the AI features tab for an explanation of each tool")
        return scroll
    def _build_other_features_tab(self):
        scroll, layout = self._create_scrollable_tab()

        self._add_section(layout,"Universal Search", "Use Control F to search across all PDFs in a project at once")
        self._add_section(layout, "Note Consolidator", "View highlights across all documents in one location. Click on a note to jump to that PDF, change highlight color, or delete the note")
        self._add_section(layout,"Workspace","Click Workspace Button in Notes tab to enter the Workspace. Move, resize, recolor, edit, and organize notes")
        self._add_section(layout,"Diagram","Connect Annotations and Nodes to map out your thoughts")
        self._add_section(layout, "Built-in OCR", 
                          "Automatically detect scanned documents. When prompted by the yellow banner, "
                          "run them through our optical character recognition engine to make the text selectable.")
                          
        self._add_section(layout, "Text to Speech", 
                          "create your own audiobook for a given PDF. Choose desired page range, voice, and speed")
        self._add_section(layout,"Custom Themes","Choose from preset themes, or create your own custom one")
        self._add_section(layout,"Projects","Create multiple projects to keep seperate work seperate")
                          
        return scroll
    
    def _build_ai_tips_tab(self):
        scroll, layout = self._create_scrollable_tab()
        self._add_section(layout,"Indexing","AI tools wonʻt work until the project is indexed. Press the index button in the LLM tab to build an index, and rebuild the index whenever a new PDF is added. Only indexed PDFs will be accesible to the AI")
        self._add_section(layout,"Use as a tool","Our tools run best when used as tools, rather than as replacements for critical thinking. AI features are more effective the more specific the prompt is. Asking it to summarize the main points will return a less helpful result than identifying a main point yourself and asking about that specifically")
        self._add_section(layout,"Batch Searching","For large projects with many PDFs, prompting the AI with only some of the PDFs selected, and then reprompting with the others selected will ensure a more accurate response")
        self._add_section(layout,"Model","Some models may be better at using our tools than others. The recommended model is Gemma4:e2b")
        self._add_section(layout,"Double Check","AI can make mistakes and carry biases. Our tools attempt to mitigate both mistakes and biases by having the AI answer only based on user provided documents, but all AI output should still be double checked. All AI generated notes are explicity labeled as such to ensure they can be double checked")
        return scroll


    def _build_ai_info_tab(self):
        scroll, layout = self._create_scrollable_tab()
        
        self._add_section(layout, "100 percent offline", "All AI features run locally. No data is shared with third parties, all documents remain on device and protected")
                          
        self._add_section(layout, "Environmentally Friendly", "Using LLMs like ChatGPT and Gemini requires large data centers that drive up power prices, cause environmental damage, and use large amounts of water. All features in this app run offline and without these data centers")
        self._add_section(layout,"Assists Human Thinking","Many AI tools are designed to replace human thinking. All our tools are designed to assist in organizing thoughts and supporting arguments.")
                          
        return scroll