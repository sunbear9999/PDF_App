import os
import sys
import subprocess
import urllib.request
import ssl
import tempfile
import shutil
import time
import configparser
import threading
import tkinter as tk

def run_silent(cmd):
    """Executes a command completely hidden from the user."""
    subprocess.run(cmd, check=True, creationflags=subprocess.CREATE_NO_WINDOW)

def get_ollama_cmd():
    cmd = shutil.which("ollama")
    if cmd: return cmd
    win_path = os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe")
    if os.path.exists(win_path): return win_path
    return None

class SleekStatusUI(tk.Tk):
    """A frameless, modern, dark-mode status splash screen."""
    def __init__(self):
        super().__init__()
        # Remove standard Windows title bar and borders for a sleek look
        self.overrideredirect(True)
        self.geometry("450x120")
        
        # Dark mode styling with a subtle blue border
        self.configure(bg="#1e1e1e", highlightbackground="#007acc", highlightcolor="#007acc", highlightthickness=2)
        
        # Center the window
        self.eval('tk::PlaceWindow . center')
        self.attributes('-topmost', True)
        
        # Clean Typography
        self.status_label = tk.Label(self, text="Initializing AI Engine...", font=("Segoe UI", 12, "bold"), bg="#1e1e1e", fg="#ffffff")
        self.status_label.pack(pady=(30, 5))
        
        self.detail_label = tk.Label(self, text="Preparing installation...", font=("Segoe UI", 9), bg="#1e1e1e", fg="#aaaaaa")
        self.detail_label.pack()

    def update_status(self, main_text, sub_text=""):
        """Thread-safe way to update UI text."""
        self.status_label.config(text=main_text)
        self.detail_label.config(text=sub_text)

    def close_ui(self):
        self.destroy()

def install_logic(ui):
    """This runs in the background thread, so it can take as long as it wants without freezing the UI."""
    project_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
    config_file = os.path.join(project_dir, "ai_config.ini")
    
    config = configparser.ConfigParser()
    if os.path.exists(config_file):
        config.read(config_file)
    else:
        ui.after(0, ui.close_ui)
        return 
        
    install_ocr = config.getboolean('Setup', 'OCR', fallback=False)
    install_ollama = config.getboolean('Setup', 'Ollama', fallback=False)
    model_choice = config.get('Setup', 'Model', fallback='skip')
    voice_choice = config.get('Setup', 'Voice', fallback='skip')

    if not install_ocr and not install_ollama and model_choice == "skip" and voice_choice == "skip":
        try: os.remove(config_file)
        except: pass
        ui.after(0, ui.close_ui)
        return

    # 1. Tesseract OCR
    if install_ocr and not shutil.which("tesseract") and not os.path.exists(r"C:\Program Files\Tesseract-OCR\tesseract.exe"):
        ui.after(0, lambda: ui.update_status("Installing Tesseract OCR...", "Downloading components (approx 40MB)"))
        tess_url = "https://digi.bib.uni-mannheim.de/tesseract/tesseract-ocr-w64-setup-v5.3.0.20221214.exe"
        tess_exe = os.path.join(tempfile.gettempdir(), "TessSetup.exe")
        
        req = urllib.request.Request(tess_url, headers={'User-Agent': 'Mozilla/5.0'})
        context = ssl._create_unverified_context()
        with urllib.request.urlopen(req, context=context) as response, open(tess_exe, 'wb') as out_file:
            shutil.copyfileobj(response, out_file)
            
        ui.after(0, lambda: ui.update_status("Installing Tesseract OCR...", "Running setup silently..."))
        run_silent([tess_exe, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/S"])
        try: os.remove(tess_exe)
        except: pass

    # 2. Ollama
    if install_ollama and not get_ollama_cmd():
        ui.after(0, lambda: ui.update_status("Installing Ollama AI Engine...", "Downloading framework..."))
        ollama_url = "https://ollama.com/download/OllamaSetup.exe"
        ollama_exe = os.path.join(tempfile.gettempdir(), "OllamaSetup.exe")
        
        req = urllib.request.Request(ollama_url, headers={'User-Agent': 'Mozilla/5.0'})
        context = ssl._create_unverified_context()
        with urllib.request.urlopen(req, context=context) as response, open(ollama_exe, 'wb') as out_file:
            shutil.copyfileobj(response, out_file)
        
        ui.after(0, lambda: ui.update_status("Installing Ollama AI Engine...", "Configuring background service..."))
        run_silent([ollama_exe, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"])
        try: os.remove(ollama_exe)
        except: pass
        
        ui.after(0, lambda: ui.update_status("Installing Ollama AI Engine...", "Suppressing welcome screen..."))
        subprocess.run(["taskkill", "/F", "/IM", "ollama app.exe"], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        subprocess.run(["taskkill", "/F", "/IM", "ollama.exe"], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        time.sleep(2)

    # 3. Pull Models
    ollama_cmd = get_ollama_cmd()
    if ollama_cmd:
        if install_ollama or model_choice != "skip":
            ui.after(0, lambda: ui.update_status("Starting AI Service...", "Preparing for model downloads..."))
            server_proc = subprocess.Popen([ollama_cmd, "serve"], creationflags=subprocess.CREATE_NO_WINDOW)
            time.sleep(4) 
            
            ui.after(0, lambda: ui.update_status("Downloading Core Embedding Model...", "nomic-embed-text (approx 270MB)"))
            run_silent([ollama_cmd, "pull", "nomic-embed-text"])
        
        if model_choice != "skip":
            ui.after(0, lambda: ui.update_status(f"Downloading Chat Model ({model_choice})...", "This is a large file (4GB+) and will take a while."))
            run_silent([ollama_cmd, "pull", model_choice])
            
        if install_ollama or model_choice != "skip":
            server_proc.terminate()
            subprocess.run(["taskkill", "/F", "/IM", "ollama.exe"], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)

    # 4. Download TTS Voices
    voices_to_download = []
    if voice_choice == "lessac" or voice_choice == "both":
        voices_to_download.append("en_US-lessac-medium")
    if voice_choice == "libritts" or voice_choice == "both":
        voices_to_download.append("en_US-libritts-high")
        
    if voices_to_download:
        voices_dir = os.path.join(project_dir, "voices")
        os.makedirs(voices_dir, exist_ok=True)
        
        for v_name in voices_to_download:
            ui.after(0, lambda: ui.update_status("Downloading Text-to-Speech Voices...", f"Fetching {v_name}"))
            onnx_url = f"https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/{v_name.split('-')[1]}/{v_name.split('-')[2]}/{v_name}.onnx?download=true"
            json_url = f"https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/{v_name.split('-')[1]}/{v_name.split('-')[2]}/{v_name}.onnx.json?download=true"
            headers = {'User-Agent': 'Mozilla/5.0'}
            context = ssl._create_unverified_context()

            req_onnx = urllib.request.Request(onnx_url, headers=headers)
            with urllib.request.urlopen(req_onnx, context=context) as response, open(os.path.join(voices_dir, f"{v_name}.onnx"), 'wb') as out_file:
                shutil.copyfileobj(response, out_file)

            req_json = urllib.request.Request(json_url, headers=headers)
            with urllib.request.urlopen(req_json, context=context) as response, open(os.path.join(voices_dir, f"{v_name}.onnx.json"), 'wb') as out_file:
                shutil.copyfileobj(response, out_file)

    try: os.remove(config_file)
    except: pass
    
    # Safely tell the UI thread to close itself
    ui.after(0, ui.close_ui)

if __name__ == "__main__":
    # Start the UI on the main thread
    app_ui = SleekStatusUI()
    
    # Start the heavy lifting on a background thread so the UI never freezes
    worker_thread = threading.Thread(target=install_logic, args=(app_ui,), daemon=True)
    worker_thread.start()
    
    # Keep the UI running smoothly
    app_ui.mainloop()