import os
import sys
import subprocess
import platform
import urllib.request
import urllib.error
import ssl
import time
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import shutil
import tempfile
import ctypes  # Added to check and request Admin privileges

# Data mappings for the UI
MODELS = {
    "Meta Llama 3 (8B) - Fast & Capable (Req: ~8GB RAM)": "llama3",
    "Qwen 2.5 (7B) - Excellent Reasoning (Req: ~8GB RAM)": "qwen2.5:7b",
    "Mistral (7B) - Highly Efficient (Req: ~8GB RAM)": "mistral",
    "Llama 3 (70B) - Massive, High Accuracy (Req: 64GB+ RAM)": "llama3:70b",
    "Gemma4:e2b (reccomended) Highly efficent (Req: ~8gb RAM)": "gemma4:e2b",
    "Skip Model Download": "skip"
}

TTS_VOICES = {
    "Lessac (Medium Quality, Faster)": ["en_US-lessac-medium"],
    "LibriTTS (High Quality, Slower)": ["en_US-libritts-high"],
    "Both Voices": ["en_US-lessac-medium", "en_US-libritts-high"],
    "Skip TTS Download": []
}

class InstallerGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Papyrus Research - System Installer")
        self.geometry("650x550")
        self.resizable(False, False)
        
        # UI Styling
        self.configure(padx=20, pady=20)
        style = ttk.Style()
        style.theme_use('clam')
        
        # Header
        ttk.Label(self, text="Papyrus Research Setup", font=("Helvetica", 16, "bold")).pack(anchor="w", pady=(0, 10))
        ttk.Label(self, text="This wizard will install all necessary AI models, background services (like Ollama and Tesseract OCR), and create system shortcuts.", wraplength=600).pack(anchor="w", pady=(0, 20))

        # Model Selection
        ttk.Label(self, text="Select Primary Chat Model:", font=("Helvetica", 10, "bold")).pack(anchor="w")
        self.model_var = tk.StringVar(value=list(MODELS.keys())[0])
        self.model_dropdown = ttk.Combobox(self, textvariable=self.model_var, values=list(MODELS.keys()), state="readonly", width=70)
        self.model_dropdown.pack(anchor="w", pady=(5, 15))

        # TTS Selection
        ttk.Label(self, text="Select Text-to-Speech (TTS) Voices:", font=("Helvetica", 10, "bold")).pack(anchor="w")
        self.tts_var = tk.StringVar(value=list(TTS_VOICES.keys())[0])
        self.tts_dropdown = ttk.Combobox(self, textvariable=self.tts_var, values=list(TTS_VOICES.keys()), state="readonly", width=70)
        self.tts_dropdown.pack(anchor="w", pady=(5, 20))

        # Install Button
        self.install_btn = ttk.Button(self, text="Start Installation", command=self.start_installation)
        self.install_btn.pack(pady=10, fill="x")

        # Console Output
        ttk.Label(self, text="Installation Log:", font=("Helvetica", 10, "bold")).pack(anchor="w")
        self.log_area = scrolledtext.ScrolledText(self, height=12, width=75, state="disabled", bg="#1e1e1e", fg="#00ff00", font=("Consolas", 9))
        self.log_area.pack(fill="both", expand=True)

    def log(self, message):
        """Appends a message to the console text box safely."""
        self.log_area.config(state="normal")
        self.log_area.insert(tk.END, message + "\n")
        self.log_area.see(tk.END)
        self.log_area.config(state="disabled")
        self.update()

    def run_hidden_command(self, cmd, timeout=None):
        """Runs a command silently and waits for completion."""
        try:
            shell = True if platform.system() == "Windows" else False
            result = subprocess.run(cmd, capture_output=True, text=True, shell=shell, timeout=timeout)
            if result.returncode != 0:
                self.log(f"Warning/Error: {result.stderr.strip()}")
            return result.returncode == 0
        except Exception as e:
            self.log(f"Command execution error: {e}")
            return False

    def get_ollama_cmd(self):
        """Finds the Ollama executable, even if it's not in the system PATH."""
        cmd = shutil.which("ollama")
        if cmd:
            return cmd
            
        if platform.system() == "Darwin":
            mac_paths = [
                "/usr/local/bin/ollama",
                "/Applications/Ollama.app/Contents/Resources/ollama",
                os.path.expanduser("~/Applications/Ollama.app/Contents/Resources/ollama")
            ]
            for p in mac_paths:
                if os.path.exists(p):
                    return p
                    
        if platform.system() == "Windows":
            win_path = os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe")
            if os.path.exists(win_path):
                return win_path
                
        return None

    def is_ollama_installed(self):
        return self.get_ollama_cmd() is not None

    def is_tesseract_installed(self):
        """Checks if Tesseract OCR is installed on the system."""
        if shutil.which("tesseract"):
            return True
        if platform.system() == "Windows":
            paths = [r"C:\Program Files\Tesseract-OCR\tesseract.exe", r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"]
            for path in paths:
                if os.path.exists(path):
                    return True
        return False

    def get_project_dir(self):
        """Get the base directory, supporting PyInstaller bundled executables."""
        if getattr(sys, 'frozen', False):
            return os.path.dirname(sys.executable)
        return os.path.abspath(os.path.dirname(__file__))

    def start_installation(self):
        self.install_btn.config(state="disabled", text="Installing... Please wait")
        self.model_dropdown.config(state="disabled")
        self.tts_dropdown.config(state="disabled")
        
        threading.Thread(target=self.install_process, daemon=True).start()

    def install_process(self):
        self.log("=== Starting Installation ===")
        project_dir = self.get_project_dir()
        system = platform.system()

        # 1. Tesseract OCR Installation
        self.log("\n🔍 Checking Tesseract OCR Installation...")
        if self.is_tesseract_installed():
            self.log("✅ Tesseract OCR is already installed.")
        else:
            self.log("📥 Installing Tesseract OCR... Please wait.")
            if system == "Windows":
                tess_url = "https://digi.bib.uni-mannheim.de/tesseract/tesseract-ocr-w64-setup-v5.3.0.20221214.exe"
                tess_installer = os.path.join(tempfile.gettempdir(), "TesseractSetup.exe")
                try:
                    req = urllib.request.Request(tess_url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
                    context = ssl._create_unverified_context()
                    with urllib.request.urlopen(req, context=context, timeout=60) as response, open(tess_installer, 'wb') as out_file:
                        shutil.copyfileobj(response, out_file)
                    
                    self.log("🚀 Launching Tesseract Installer. Please click 'Next' and leave default settings...")
                    subprocess.run([tess_installer], check=True)
                    os.remove(tess_installer)
                    self.log("✅ Tesseract installed successfully!")
                except Exception as e:
                    self.log(f"❌ Failed to install Tesseract: {e}")
            elif system == "Linux":
                self.log("⚠️ Tesseract is missing. Please open a terminal and run: sudo apt install tesseract-ocr")
            elif system == "Darwin":
                self.log("⚠️ Tesseract is missing. Please open a terminal and run: brew install tesseract")

        # 2. Ollama Installation
        self.log("\n🦙 Checking Ollama Installation...")
        if self.is_ollama_installed():
            self.log("✅ Ollama is already installed on your system.")
        else:
            self.log("📥 Downloading Ollama... Please wait.")
            if system == "Windows":
                installer_path = os.path.join(tempfile.gettempdir(), "OllamaSetup.exe")
                try:
                    urllib.request.urlretrieve("https://ollama.com/download/OllamaSetup.exe", installer_path)
                    self.log("🚀 Launching Ollama Installer. Please complete the setup wizard...")
                    subprocess.run([installer_path], check=True)
                    self.log("🧹 Cleaning up installer...")
                    os.remove(installer_path)
                    self.log("✅ Ollama installed! Note: Background service may take a moment to start.")
                    time.sleep(3)
                except Exception as e:
                    self.log(f"❌ Failed to install Ollama: {e}")
            elif system == "Linux":
                self.log("📥 Installing Ollama for Linux...")
                try:
                    os.system("curl -fsSL https://ollama.com/install.sh | sh")
                    self.log("✅ Ollama installed successfully!")
                except Exception as e:
                    self.log(f"❌ Failed to install Ollama: {e}")
            elif system == "Darwin":
                self.log("⚠️ Auto-install for Mac is limited. Opening browser to download Ollama...")
                import webbrowser
                webbrowser.open("https://ollama.com/download/mac")
                self.log("👉 Please install Ollama from the downloaded zip file, run it, and then restart this installer.")
                self.after(0, self._finish_ui_update)
                return
            else:
                self.log(f"⚠️ Auto-install not supported on {system}. Install manually from ollama.com.")

        # 3. Download Models
        ollama_cmd = self.get_ollama_cmd()
        if ollama_cmd:
            self.log("\n🧠 Downloading embedding model (nomic-embed-text)... This may take a minute.")
            self.run_hidden_command([ollama_cmd, 'pull', 'nomic-embed-text'])
            self.log("✅ Embedding model ready.")

            model_choice_text = self.model_var.get()
            model_id = MODELS[model_choice_text]
            
            if model_id != "skip":
                self.log(f"\n🧠 Downloading chat model ({model_id})...")
                self.log("⏳ This is a large file (4GB+). It may take 5-15 minutes depending on your internet speed. Please wait...")
                success = self.run_hidden_command([ollama_cmd, 'pull', model_id])
                if success:
                    self.log(f"✅ {model_id} downloaded successfully!")
                else:
                    self.log(f"❌ Failed to download {model_id}. You can retry later.")
            else:
                self.log("\n⏭️ Skipping chat model download.")
        else:
            self.log("\n⚠️ Ollama is not installed/running. Skipping model downloads.")

        # 4. Download TTS Voices
        voices_to_download = TTS_VOICES[self.tts_var.get()]
        if voices_to_download:
            self.log("\n🗣️ Downloading TTS Voices...")
            voices_dir = os.path.join(project_dir, "models")
            os.makedirs(voices_dir, exist_ok=True)
            
            for v_name in voices_to_download:
                self.log(f"📥 Downloading {v_name}...")
                onnx_url = f"https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/{v_name.split('-')[1]}/{v_name.split('-')[2]}/{v_name}.onnx?download=true"
                json_url = f"https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/{v_name.split('-')[1]}/{v_name.split('-')[2]}/{v_name}.onnx.json?download=true"
                
                try:
                    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
                    context = ssl._create_unverified_context()
                    
                    req_onnx = urllib.request.Request(onnx_url, headers=headers)
                    with urllib.request.urlopen(req_onnx, context=context, timeout=30) as response, open(os.path.join(voices_dir, f"{v_name}.onnx"), 'wb') as out_file:
                        shutil.copyfileobj(response, out_file)
                        
                    req_json = urllib.request.Request(json_url, headers=headers)
                    with urllib.request.urlopen(req_json, context=context, timeout=30) as response, open(os.path.join(voices_dir, f"{v_name}.onnx.json"), 'wb') as out_file:
                        shutil.copyfileobj(response, out_file)
                        
                    self.log(f"✅ {v_name} installed!")
                except Exception as e:
                    self.log(f"❌ Failed to download {v_name}. HuggingFace might be busy: {e}")
        else:
            self.log("\n⏭️ Skipping TTS voice download.")

        # 5. Create System Shortcuts
        self.log("\n🖥️ Creating System Shortcuts...")
        
        app_exe_name = "main.exe" if system == "Windows" else "main"
        app_exe_path = os.path.join(project_dir, app_exe_name)
        
        if not os.path.exists(app_exe_path):
            if system == "Darwin" and os.path.exists(os.path.join(project_dir, "Papyrus Research.app")):
                app_exe_path = os.path.join(project_dir, "Papyrus Research.app")
            else:
                self.log(f"⚠️ Could not find the compiled application '{app_exe_name}' in the directory. Skipping shortcut creation.")
                app_exe_path = None

        if app_exe_path:
            if system == "Windows":
                try:
                    appdata = os.environ.get('APPDATA', '')
                    desktop = os.environ.get('USERPROFILE', '') + "\\Desktop"
                    start_menu = os.path.join(appdata, "Microsoft\\Windows\\Start Menu\\Programs")
                    
                    shortcut_paths = [
                        os.path.join(start_menu, "Papyrus Research.lnk"),
                        os.path.join(desktop, "Papyrus Research.lnk")
                    ]
                    
                    for spath in shortcut_paths:
                        # SAFELY WRITE THE SCRIPT TO THE TEMP FOLDER
                        vbs_path = os.path.join(tempfile.gettempdir(), "create_shortcut.vbs")
                        vbs_content = f"""
Set oWS = WScript.CreateObject("WScript.Shell")
sLinkFile = "{spath}"
Set oLink = oWS.CreateShortcut(sLinkFile)
oLink.TargetPath = "{app_exe_path}"
oLink.WorkingDirectory = "{project_dir}"
oLink.Description = "Ethical, Offline AI-Powered PDF Research Assistant"
oLink.WindowStyle = 1
oLink.Save
                        """
                        with open(vbs_path, "w") as f:
                            f.write(vbs_content)
                        subprocess.run(['cscript', '//nologo', vbs_path], check=True, creationflags=subprocess.CREATE_NO_WINDOW)
                        os.remove(vbs_path)
                    
                    self.log("✅ Created shortcuts on Desktop and Start Menu.")
                except Exception as e:
                    self.log(f"❌ Failed to create Windows shortcuts: {e}")

            elif system == "Linux":
                try:
                    desktop_dir = os.path.expanduser("~/.local/share/applications")
                    os.makedirs(desktop_dir, exist_ok=True)
                    desktop_file_path = os.path.join(desktop_dir, "papyrus_research.desktop")
                    
                    desktop_content = f"""[Desktop Entry]
Name=Papyrus
Comment=Ethical, Offline AI-Powered PDF Research Assistant
Exec="{app_exe_path}"
Path={project_dir}
Icon=accessories-text-editor
Terminal=false
Type=Application
Categories=Office;Utility;
"""
                    with open(desktop_file_path, "w") as f:
                        f.write(desktop_content)
                    os.chmod(desktop_file_path, 0o755)
                    self.log("✅ Created application entry in app launcher (~/.local/share/applications).")
                except Exception as e:
                    self.log(f"❌ Failed to create Linux shortcut: {e}")
                    
            elif system == "Darwin":
                try:
                    desktop_dir = os.path.expanduser("~/Desktop")
                    symlink_path = os.path.join(desktop_dir, "Papyrus Research")
                    if not os.path.exists(symlink_path):
                        os.symlink(app_exe_path, symlink_path)
                    self.log("✅ Created a shortcut on your Desktop.")
                except Exception as e:
                    self.log(f"❌ Failed to create Mac shortcut: {e}")

        # Finish up
        self.log("\n🎉 Installation Complete!")
        self.log("You can now launch 'Papyrus Research' from your shortcuts, or close this window.")
        
        self.after(0, self._finish_ui_update)

    def _finish_ui_update(self):
        self.install_btn.config(state="normal", text="Finish & Close", command=self.destroy)

if __name__ == "__main__":
    # AUTO-ELEVATION CHECK: If running on Windows, force Admin rights
    if platform.system() == "Windows":
        try:
            is_admin = ctypes.windll.shell32.IsUserAnAdmin()
        except:
            is_admin = False
            
        if not is_admin:
            # If not Admin, relaunch the executable triggering the UAC Yes/No Prompt
            ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, "", None, 1)
            sys.exit()

    app = InstallerGUI()
    app.mainloop()