import customtkinter as ctk
import threading
import os
from core.ocr_engine import run_ocr_on_pdf

class OCRTab(ctk.CTkFrame):
    def __init__(self, master, main_window):
        super().__init__(master, corner_radius=0, fg_color="transparent")
        self.main_window = main_window
        
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self.header = ctk.CTkLabel(self, text="OCR Engine", font=ctk.CTkFont(size=28, weight="bold"))
        self.header.grid(row=0, column=0, padx=0, pady=(0, 10), sticky="w")

        self.top_frame = ctk.CTkFrame(self, fg_color="#2b2b2b", corner_radius=10)
        self.top_frame.grid(row=1, column=0, padx=0, pady=5, sticky="ew")
        
        self.output_mode = ctk.StringVar(value="text")
        self.rb_text = ctk.CTkRadioButton(self.top_frame, text="Extract Text", variable=self.output_mode, value="text")
        self.rb_text.pack(side="left", padx=(20, 20), pady=15)
        
        self.rb_new = ctk.CTkRadioButton(self.top_frame, text="Save New PDF", variable=self.output_mode, value="save_new")
        self.rb_new.pack(side="left", padx=(0, 20), pady=15)

        self.rb_replace = ctk.CTkRadioButton(self.top_frame, text="Replace Original", variable=self.output_mode, value="replace")
        self.rb_replace.pack(side="left", pady=15)

        self.text_area = ctk.CTkTextbox(self, wrap="word", font=ctk.CTkFont(size=15), fg_color="transparent", text_color="#e0e0e0")
        self.text_area.grid(row=2, column=0, padx=0, pady=10, sticky="nsew")
        
        self.control_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.control_frame.grid(row=3, column=0, padx=0, pady=(0, 0), sticky="ew")

        self.run_ocr_btn = ctk.CTkButton(
            self.control_frame, text="Run OCR", fg_color="#00cc66", hover_color="#00994c", 
            text_color="white", height=45, corner_radius=22, font=ctk.CTkFont(size=15, weight="bold"),
            command=self.start_ocr_thread
        )
        self.run_ocr_btn.pack(side="left")

        self.status_label = ctk.CTkLabel(self.control_frame, text="Ready", text_color="gray", font=ctk.CTkFont(size=14))
        self.status_label.pack(side="left", padx=20)

    def sync_file(self, file_path):
        """Called automatically by main window when a new file opens."""
        self.text_area.delete("0.0", "end")
        self.status_label.configure(text=f"Target: {os.path.basename(file_path)}")

    def update_progress(self, current_page, total_pages):
        self.after(0, lambda: self.status_label.configure(text=f"Processing Page {current_page}/{total_pages}...", text_color="#ffaa00"))

    def start_ocr_thread(self):
        current_file = self.main_window.current_file_path
        if not current_file:
            self.status_label.configure(text="No document loaded in viewer.", text_color="#ff4444")
            return
            
        self.run_ocr_btn.configure(state="disabled")
        self.text_area.delete("0.0", "end")
        
        mode = self.output_mode.get()
        thread = threading.Thread(target=self._process_ocr_logic, args=(current_file, mode), daemon=True)
        thread.start()

    def _process_ocr_logic(self, file_path, ui_mode):
        save_path = None
        engine_mode = "text"

        if ui_mode == "save_new":
            engine_mode = "pdf"
            base, ext = os.path.splitext(file_path)
            save_path = f"{base}_ocr{ext}"
        elif ui_mode == "replace":
            engine_mode = "pdf"
            save_path = file_path
            
        result_text = run_ocr_on_pdf(file_path, mode=engine_mode, save_path=save_path, progress_callback=self.update_progress)
        self.after(0, self._finalize_ocr, result_text, ui_mode, save_path)

    def _finalize_ocr(self, text, ui_mode, save_path):
        if text.startswith("OCR Engine Error"):
            self.text_area.insert("0.0", text)
            self.status_label.configure(text="Failed", text_color="#ff4444")
        else:
            self.text_area.insert("0.0", text)
            msg = "OCR Complete!"
            if ui_mode != "text":
                msg += f" Saved to {os.path.basename(save_path)}"
                # If they replaced the original, reload the viewer!
                if ui_mode == "replace":
                    self.main_window.viewer.load_document(save_path)
            self.status_label.configure(text=msg, text_color="#00cc66")
            
        self.run_ocr_btn.configure(state="normal")