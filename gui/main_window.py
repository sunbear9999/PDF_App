import customtkinter as ctk
import subprocess
import os
import fitz
from tkinter import filedialog
from gui.components.pdf_viewer import PDFViewer
from gui.tabs.ocr_tab import OCRTab
from gui.tabs.tts_tab import TTSTab
from gui.tabs.llm_tab import LLMTab
from gui.tabs.notes_tab import NotesTab

class MainWindow(ctk.CTk):
    def __init__(self):
        super().__init__()

        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("blue")

        self.title("PDF Workspace")
        self.geometry("1400x900")
        self.minsize(1000, 700)

        self.current_file_path = None

        self.bind("<Control-f>", lambda e: self.viewer.toggle_search())
        self.bind("<Control-F>", lambda e: self.viewer.toggle_search())

        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1) # PDF Viewer
        self.grid_columnconfigure(1, weight=0) # Tools Panel (Hidden by default)

        self._build_top_menu()
        self._build_ocr_banner()
        self._build_workspace()

    def _build_top_menu(self):
        self.top_menu = ctk.CTkFrame(self, height=50, fg_color="#1a1a1a", corner_radius=0)
        self.top_menu.grid(row=0, column=0, columnspan=2, sticky="ew")
        self.top_menu.grid_propagate(False)

        btn_kwargs = {"fg_color": "transparent", "hover_color": "#333333", "font": ctk.CTkFont(size=14, weight="bold")}
        
        self.btn_open = ctk.CTkButton(self.top_menu, text="📂 Open PDF", width=100, command=self.open_file, **btn_kwargs)
        self.btn_open.pack(side="left", padx=10, pady=10)

        self.view_seg = ctk.CTkSegmentedButton(self.top_menu, values=["Read", "Overview"], command=self.toggle_view)
        self.view_seg.set("Read")
        self.view_seg.pack(side="left", padx=30, pady=10)

        self.hint_label = ctk.CTkLabel(self.top_menu, text="(Hold Shift + Drag to Highlight)", text_color="gray", font=ctk.CTkFont(size=12))
        self.hint_label.pack(side="left", padx=5, pady=10)

        self.btn_zoom_out = ctk.CTkButton(self.top_menu, text="➖", width=40, command=lambda: self.viewer.zoom_out(), **btn_kwargs)
        self.btn_zoom_out.pack(side="left", padx=(10, 2), pady=10)
        
        self.btn_zoom_reset = ctk.CTkButton(self.top_menu, text="Fit Width", width=80, command=lambda: self.viewer.zoom_reset(), **btn_kwargs)
        self.btn_zoom_reset.pack(side="left", padx=2, pady=10)
        
        self.btn_zoom_in = ctk.CTkButton(self.top_menu, text="➕", width=40, command=lambda: self.viewer.zoom_in(), **btn_kwargs)
        self.btn_zoom_in.pack(side="left", padx=2, pady=10)

        self.tool_seg = ctk.CTkSegmentedButton(
            self.top_menu, 
            values=["Notes", "OCR", "Audio (TTS)", "LLM Chat", "Close Tool"], 
            command=self.toggle_tool_panel,
            selected_color="#0055ff",
            selected_hover_color="#0044cc"
        )
        self.tool_seg.pack(side="right", padx=20, pady=10)

    def _build_ocr_banner(self):
        self.ocr_banner = ctk.CTkFrame(self, height=40, fg_color="#cc8800", corner_radius=0)
        self.ocr_banner.grid_propagate(False)
        
        lbl = ctk.CTkLabel(self.ocr_banner, text="⚠️ This document appears to be scanned and lacks selectable text. Would you like to run OCR?", text_color="white", font=ctk.CTkFont(weight="bold"))
        lbl.pack(side="left", padx=20, pady=5)
        
        btn_run = ctk.CTkButton(self.ocr_banner, text="Run OCR Now", fg_color="white", text_color="black", hover_color="#e6e6e6", width=100, command=self._trigger_auto_ocr)
        btn_run.pack(side="right", padx=10, pady=5)
        
        btn_dismiss = ctk.CTkButton(self.ocr_banner, text="Dismiss", fg_color="transparent", text_color="white", hover_color="#b37700", width=80, command=lambda: self.ocr_banner.grid_forget())
        btn_dismiss.pack(side="right", padx=5, pady=5)

    def _build_workspace(self):
        self.viewer = PDFViewer(self)
        self.viewer.grid(row=2, column=0, sticky="nsew", padx=10, pady=10)

        self.tool_panel = ctk.CTkFrame(self, fg_color="#222222", corner_radius=10)
        self.tool_panel.grid(row=2, column=1, sticky="nsew", padx=(0, 10), pady=10)
        self.tool_panel.grid_rowconfigure(0, weight=1)
        self.tool_panel.grid_columnconfigure(0, weight=1)

        # Pass the Main Window (self) into all tools so they share a unified file state
        self.tools = {
            "Notes": NotesTab(self.tool_panel, self.viewer),
            "OCR": OCRTab(self.tool_panel, self),
            "Audio (TTS)": TTSTab(self.tool_panel, self),
            "LLM Chat": LLMTab(self.tool_panel, self)
        }
        self.tool_panel.grid_remove()
        self.viewer.on_note_added = self.tools["Notes"].refresh_notes

    def _get_native_file_picker(self):
        try:
            result = subprocess.run(
                ['zenity', '--file-selection', '--title=Open PDF', '--file-filter=PDF Files | *.pdf'],
                capture_output=True, text=True, check=True
            )
            return result.stdout.strip()
        except FileNotFoundError:
            return filedialog.askopenfilename(title="Open PDF", filetypes=[("PDF Files", "*.pdf")])
        except subprocess.CalledProcessError:
            return None

    def open_file(self):
        file_path = self._get_native_file_picker()
        if file_path:
            self.current_file_path = file_path
            self.title(f"PDF Workspace - {os.path.basename(file_path)}")
            
            if self.viewer.load_document(file_path):
                self._check_needs_ocr()
                self._sync_tools_with_file(file_path)

    def _check_needs_ocr(self):
        self.ocr_banner.grid_forget()
        if not self.viewer.doc: return
        pages_to_check = min(3, len(self.viewer.doc))
        total_text = ""
        for i in range(pages_to_check):
            total_text += self.viewer.doc.load_page(i).get_text()
        if len(total_text.strip()) < 50:
            self.ocr_banner.grid(row=1, column=0, columnspan=2, sticky="ew")

    def _trigger_auto_ocr(self):
        self.ocr_banner.grid_forget()
        self.tool_seg.set("OCR")
        self.toggle_tool_panel("OCR")
        self.tools["OCR"].rb_new.select()
        self.tools["OCR"].start_ocr_thread()

    def _sync_tools_with_file(self, file_path):
        """Silently updates all tools in the background when a new file is opened."""
        self.tools["Notes"].refresh_notes()
        self.tools["OCR"].sync_file(file_path)
        self.tools["Audio (TTS)"].sync_file(file_path)
        self.tools["LLM Chat"].sync_file(file_path)

    def toggle_view(self, mode):
        if mode == "Read":
            self.viewer.set_view_mode("read")
            self.btn_zoom_in.configure(state="normal")
            self.btn_zoom_out.configure(state="normal")
            self.btn_zoom_reset.configure(state="normal")
        else:
            self.viewer.set_view_mode("overview")
            self.btn_zoom_in.configure(state="disabled")
            self.btn_zoom_out.configure(state="disabled")
            self.btn_zoom_reset.configure(state="disabled")

    def toggle_tool_panel(self, tool_name):
        for tool in self.tools.values():
            tool.grid_forget()

        if tool_name == "Close Tool":
            self.tool_panel.grid_remove()
            # CRITICAL FIX: Restore grid weights so Viewer snaps back to full width
            self.grid_columnconfigure(1, weight=0) 
            self.grid_columnconfigure(0, weight=1) 
        else:
            # CRITICAL FIX: Reserve space for the tool panel
            self.grid_columnconfigure(1, weight=1) 
            self.grid_columnconfigure(0, weight=3) 
            self.tool_panel.grid() 
            if tool_name in self.tools:
                self.tools[tool_name].grid(row=0, column=0, sticky="nsew", padx=10, pady=10)