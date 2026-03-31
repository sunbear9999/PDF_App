import customtkinter as ctk
import threading
import os
from core.pdf_utils import extract_filtered_blocks
from core.tts_engine import generate_audio
from core.ocr_engine import run_ocr_on_pdf

class TTSTab(ctk.CTkFrame):
    def __init__(self, master, main_window):
        super().__init__(master, corner_radius=0, fg_color="transparent")
        self.main_window = main_window
        
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self.header = ctk.CTkLabel(self, text="PDF to Audio", font=ctk.CTkFont(size=28, weight="bold"))
        self.header.grid(row=0, column=0, padx=0, pady=(0, 10), sticky="w")

        self.params_frame = ctk.CTkScrollableFrame(self, height=70, orientation="horizontal", fg_color="#2b2b2b", corner_radius=10)
        self.params_frame.grid(row=1, column=0, padx=0, pady=5, sticky="ew")

        font_sm = ctk.CTkFont(size=13)
        self.voice_label = ctk.CTkLabel(self.params_frame, text="Voice:", font=font_sm)
        self.voice_label.pack(side="left", padx=(10, 5), pady=10)
        
        self.voice_combo = ctk.CTkComboBox(self.params_frame, values=["Voice 1", "Voice 2", "Voice 3", "Voice 4", "Voice 5"], width=120)
        self.voice_combo.pack(side="left", padx=(0, 20), pady=10)

        self.speed_slider = ctk.CTkSlider(self.params_frame, from_=0.5, to=2.0, width=120)
        self.speed_slider.set(1.0)
        self.speed_slider.pack(side="left", padx=(0, 20), pady=10)

        self.ignore_margins_var = ctk.BooleanVar(value=True)
        self.ignore_margins_checkbox = ctk.CTkCheckBox(self.params_frame, text="Ignore Headers", variable=self.ignore_margins_var, font=font_sm)
        self.ignore_margins_checkbox.pack(side="left", padx=(0, 20), pady=10)

        self.text_preview = ctk.CTkTextbox(self, wrap="word", font=ctk.CTkFont(size=15), fg_color="transparent", text_color="#e0e0e0")
        self.text_preview.grid(row=2, column=0, padx=0, pady=10, sticky="nsew")

        self.control_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.control_frame.grid(row=3, column=0, padx=0, pady=(0, 0), sticky="ew")

        self.output_name_entry = ctk.CTkEntry(self.control_frame, placeholder_text="output.wav", width=200, height=45, corner_radius=22)
        self.output_name_entry.pack(side="left", padx=(0, 15))

        self.run_tts_btn = ctk.CTkButton(
            self.control_frame, text="Generate Audio", fg_color="#cc3300", hover_color="#992600", 
            text_color="white", height=45, corner_radius=22, font=ctk.CTkFont(size=15, weight="bold"),
            command=self.start_tts_thread
        )
        self.run_tts_btn.pack(side="left")

        self.status_label = ctk.CTkLabel(self.control_frame, text="Ready", text_color="gray", font=ctk.CTkFont(size=14))
        self.status_label.pack(side="left", padx=20)

    def sync_file(self, file_path):
        base_name = os.path.splitext(os.path.basename(file_path))[0]
        self.output_name_entry.delete(0, "end")
        self.output_name_entry.insert(0, f"{base_name}.wav")
        self._extract_text_background(file_path)

    def _extract_text_background(self, file_path):
        self.status_label.configure(text="Extracting text...", text_color="#ffaa00")
        self.text_preview.delete("0.0", "end")
        thread = threading.Thread(target=self._process_extraction, args=(file_path,), daemon=True)
        thread.start()

    def _process_extraction(self, file_path):
        ignore_margins = self.ignore_margins_var.get()
        text = extract_filtered_blocks(file_path, ignore_margins=ignore_margins)
        
        if len(text.strip()) < 20 and not text.startswith("Error processing"):
            self.after(0, lambda: self.status_label.configure(text="Auto-OCR running...", text_color="#ffaa00"))
            text = run_ocr_on_pdf(file_path, mode="text")

        self.after(0, lambda: self.text_preview.insert("0.0", text))
        self.after(0, lambda: self.status_label.configure(text="Ready to Generate", text_color="#00cc66"))

    def start_tts_thread(self):
        final_text = self.text_preview.get("0.0", "end").strip()
        if not final_text: return
            
        self.run_tts_btn.configure(state="disabled")
        self.status_label.configure(text="Starting Engine...", text_color="#ffaa00")
        
        thread = threading.Thread(target=self._process_tts_logic, args=(final_text,), daemon=True)
        thread.start()

    def _process_tts_logic(self, text_to_read):
        out_name = self.output_name_entry.get().strip() or "output.wav"
        if not out_name.endswith(".wav"): out_name += ".wav"
        output_file = os.path.abspath(os.path.join(os.getcwd(), out_name))
        
        v_map = {"Voice 1":"voice1.onnx", "Voice 2":"voice2.onnx", "Voice 3":"voice3.onnx", "Voice 4":"voice4.onnx", "Voice 5":"voice5.onnx"}
        voice = v_map.get(self.voice_combo.get(), "voice1.onnx")
        
        def prog(msg): self.after(0, lambda: self.status_label.configure(text=msg, text_color="#ffaa00"))
        res = generate_audio(text_to_read, output_file, voice, self.speed_slider.get(), prog)
        self.after(0, self._finalize_tts, res, output_file)

    def _finalize_tts(self, result, output_file):
        if result is True:
            self.status_label.configure(text="Saved successfully", text_color="#00cc66")
        else:
            self.status_label.configure(text="Engine Error", text_color="#ff4444")
        self.run_tts_btn.configure(state="normal")