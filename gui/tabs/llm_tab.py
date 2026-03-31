import customtkinter as ctk
import threading
import os
from core.pdf_utils import extract_filtered_blocks
from core.llm_manager import LocalLLMManager

class LLMTab(ctk.CTkFrame):
    def __init__(self, master, main_window):
        super().__init__(master, corner_radius=0, fg_color="transparent")
        self.main_window = main_window
        self.llm_manager = LocalLLMManager()
        
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self.header = ctk.CTkLabel(self, text="Chat with Document", font=ctk.CTkFont(size=28, weight="bold"))
        self.header.grid(row=0, column=0, padx=0, pady=(0, 10), sticky="w")

        self.top_frame = ctk.CTkFrame(self, fg_color="#2b2b2b", corner_radius=10, height=50)
        self.top_frame.grid(row=1, column=0, padx=0, pady=5, sticky="ew")
        
        self.index_status_label = ctk.CTkLabel(self.top_frame, text="Waiting for document...", text_color="gray", font=ctk.CTkFont(size=13))
        self.index_status_label.pack(side="left", padx=15, pady=10)

        avail_models = self.llm_manager.get_available_models()
        self.model_combo = ctk.CTkComboBox(self.top_frame, values=avail_models, width=160)
        if avail_models: self.model_combo.set(avail_models[0])
        self.model_combo.pack(side="right", padx=15, pady=10)

        self.chat_history = ctk.CTkTextbox(self, wrap="word", state="disabled", font=ctk.CTkFont(size=15), fg_color="transparent", text_color="#e0e0e0")
        self.chat_history.grid(row=2, column=0, padx=0, pady=10, sticky="nsew")

        self.input_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.input_frame.grid(row=3, column=0, padx=0, pady=0, sticky="ew")
        self.input_frame.grid_columnconfigure(0, weight=1)

        self.chat_input = ctk.CTkEntry(self.input_frame, placeholder_text="Ask a question...", height=50, corner_radius=25, font=ctk.CTkFont(size=15))
        self.chat_input.grid(row=0, column=0, sticky="ew", padx=(0, 15))
        self.chat_input.bind("<Return>", lambda e: self.start_query_thread())

        self.send_btn = ctk.CTkButton(
            self.input_frame, text="Send", width=90, height=50, corner_radius=25, 
            font=ctk.CTkFont(size=15, weight="bold"), command=self.start_query_thread
        )
        self.send_btn.grid(row=0, column=1)

    def append_to_chat(self, sender, message, end="\n\n"):
        self.chat_history.configure(state="normal")
        if sender: self.chat_history.insert("end", f"{sender}\n")
        self.chat_history.insert("end", f"{message}{end}")
        self.chat_history.configure(state="disabled")
        self.chat_history.see("end")

    def sync_file(self, file_path):
        """Auto-triggers when a new PDF opens in the main window."""
        self.index_status_label.configure(text="Indexing in background...", text_color="#ffaa00")
        thread = threading.Thread(target=self._process_indexing, args=(file_path,), daemon=True)
        thread.start()

    def _process_indexing(self, file_path):
        text = extract_filtered_blocks(file_path, ignore_margins=True)
        if len(text.strip()) < 20:
            self.after(0, lambda: self.index_status_label.configure(text="No readable text for AI.", text_color="#ff4444"))
            return

        def prog(msg): self.after(0, lambda: self.index_status_label.configure(text=msg, text_color="#ffaa00"))
        try:
            self.llm_manager.index_document(text, progress_callback=prog)
            self.after(0, lambda: self.index_status_label.configure(text=f"AI Ready", text_color="#00cc66"))
        except Exception as e:
            self.after(0, lambda: self.index_status_label.configure(text="Indexing failed", text_color="#ff4444"))

    def start_query_thread(self):
        user_text = self.chat_input.get().strip()
        if not user_text: return
        model = self.model_combo.get()
        
        self.append_to_chat("You", user_text)
        self.chat_input.delete(0, "end")
        self.chat_history.configure(state="normal")
        self.chat_history.insert("end", f"{model}\n")
        self.chat_history.configure(state="disabled")
        self.send_btn.configure(state="disabled")

        thread = threading.Thread(target=self._process_query, args=(user_text, model), daemon=True)
        thread.start()

    def _process_query(self, user_text, selected_model):
        def stream_cb(token): self.after(0, lambda: self.append_to_chat(None, token, end=""))
        try:
            self.llm_manager.query(user_text, selected_model, callback=stream_cb)
            self.after(0, lambda: self.append_to_chat(None, "", end="\n\n"))
        except Exception as e:
            pass
        self.after(0, lambda: self.send_btn.configure(state="normal"))