import os
import sys
import subprocess
import platform
import urllib.request
import time
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox

# Data mappings for the UI
MODELS = {
    "Meta Llama 3 (8B) - Fast & Capable (Req: ~8GB RAM)": "llama3",
    "Qwen 2.5 (7B) - Excellent Reasoning (Req: ~8GB RAM)": "qwen2.5:7b",
    "Mistral (7B) - Highly Efficient (Req: ~8GB RAM)": "mistral",
    "Llama 3 (70B) - Massive, High Accuracy (Req: 64GB+ RAM)": "llama3:70b",
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
        self.title("PDF AI Workspace - System Installer")
        self.geometry("650x550")
        self.resizable(False, False)
        
        # UI Styling
        self.configure(padx=20, pady=20)
        style = ttk.Style()
        style.theme_use('clam')
        
        # Header
        ttk.Label(self, text="PDF AI Workspace Setup", font=("Helvetica", 16, "bold")).pack(anchor="w", pady=(0, 10))
        ttk.Label(self, text="This wizard will install all necessary dependencies, AI models, and create system shortcuts.", wraplength=600).pack(anchor="w", pady=(0, 20))

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
            # Use shell=True for windows pip commands to resolve correctly if paths are weird
            shell = True if platform.system() == "Windows" else False
            result = subprocess.run(cmd, capture_output=True, text=True, shell=shell, timeout=timeout)
            if result.returncode != 0:
                self.log(f"Warning/Error: {result.stderr.strip()}")
            return result.returncode == 0
        except Exception as e:
            self.log(f"Command execution error: {e}")
            return False

    def is_ollama_installed(self):
        try:
            subprocess.run(['ollama', '--version'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except FileNotFoundError:
            return False

    def get_project_dir(self):
        """Get the base directory, supporting PyInstaller bundled executables."""
        if getattr(sys, 'frozen', False):
            return os.path.dirname(sys.executable)
        return os.path.abspath(os.path.dirname(__file__))

    def get_python_cmd(self):
        """Get the system python command (sys.executable might be the PyInstaller exe)."""
        if getattr(sys, 'frozen', False):
            return "python" if platform.system() == "Windows" else "python3"
        return sys.executable

    def start_installation(self):
        self.install_btn.config(state="disabled", text="Installing... Please wait")
        self.model_dropdown.config(state="disabled")
        self.tts_dropdown.config(state="disabled")
        
        # Run installation in a background thread to keep UI responsive
        threading.Thread(target=self.install_process, daemon=True).start()

    def install_process(self):
        self.log("=== Starting Installation ===")
        project_dir = self.get_project_dir()
        python_cmd = self.get_python_cmd()

        # 1. Install Dependencies
        self.log("\n📦 Installing Python dependencies...")
        req_file = os.path.join(project_dir, "requirements.txt")
        if os.path.exists(req_file):
            success = self.run_hidden_command([python_cmd, "-m", "pip", "install", "-r", req_file])
            if success:
                self.log("✅ Python dependencies installed successfully.")
            else:
                self.log("⚠️ Failed to install some dependencies. Ensure Python/pip is installed.")
        else:
            self.log(f"⚠️ requirements.txt not found at {req_file}. Skipping.")

        # 2. Ollama Installation
        self.log("\n🦙 Checking Ollama Installation...")
        if self.is_ollama_installed():
            self.log("✅ Ollama is already installed on your system.")
        else:
            self.log("📥 Downloading Ollama... Please wait.")
            system = platform.system()
            if system == "Windows":
                installer_path = os.path.join(project_dir, "OllamaSetup.exe")
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
            else:
                self.log(f"⚠️ Auto-install not supported on {system}. Install manually from ollama.com.")

        # 3. Download Models
        if self.is_ollama_installed():
            self.log("\n🧠 Downloading embedding model (nomic-embed-text)... This may take a minute.")
            self.run_hidden_command(['ollama', 'pull', 'nomic-embed-text'])
            self.log("✅ Embedding model ready.")

            model_choice_text = self.model_var.get()
            model_id = MODELS[model_choice_text]
            
            if model_id != "skip":
                self.log(f"\n🧠 Downloading chat model ({model_id})...")
                self.log("⏳ This is a large file (4GB+). It may take 5-15 minutes depending on your internet speed. Please wait...")
                success = self.run_hidden_command(['ollama', 'pull', model_id])
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
            voices_dir = os.path.join(project_dir, "voices")
            os.makedirs(voices_dir, exist_ok=True)
            
            for v_name in voices_to_download:
                self.log(f"📥 Downloading {v_name}...")
                onnx_url = f"https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/{v_name.split('-')[1]}/{v_name.split('-')[2]}/{v_name}.onnx?download=true"
                json_url = f"https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/{v_name.split('-')[1]}/{v_name.split('-')[2]}/{v_name}.onnx.json?download=true"
                try:
                    urllib.request.urlretrieve(onnx_url, os.path.join(voices_dir, f"{v_name}.onnx"))
                    urllib.request.urlretrieve(json_url, os.path.join(voices_dir, f"{v_name}.onnx.json"))
                    self.log(f"✅ {v_name} installed!")
                except Exception as e:
                    self.log(f"❌ Failed to download {v_name}: {e}")
        else:
            self.log("\n⏭️ Skipping TTS voice download.")

        # 5. Create System Shortcuts
        self.log("\n🖥️ Creating System Shortcuts...")
        main_script = os.path.join(project_dir, "main.py")
        
        if not os.path.exists(main_script):
            self.log("⚠️ Could not find main.py in project directory. Skipping shortcut creation.")
        else:
            system = platform.system()
            if system == "Windows":
                try:
                    # Prefer pythonw.exe to launch app without terminal
                    exec_python = python_cmd
                    if exec_python.endswith("python.exe"):
                        pythonw = exec_python.replace("python.exe", "pythonw.exe")
                        if os.path.exists(pythonw): exec_python = pythonw

                    appdata = os.environ.get('APPDATA', '')
                    desktop = os.environ.get('USERPROFILE', '') + "\\Desktop"
                    start_menu = os.path.join(appdata, "Microsoft\\Windows\\Start Menu\\Programs")
                    
                    shortcut_paths = [
                        os.path.join(start_menu, "PDF AI Workspace.lnk"),
                        os.path.join(desktop, "PDF AI Workspace.lnk")
                    ]
                    
                    for spath in shortcut_paths:
                        vbs_path = os.path.join(project_dir, "create_shortcut.vbs")
                        # Fixed: Using Chr(34) to safely wrap paths with spaces in quotes for VBScript
                        vbs_content = f"""
Set oWS = WScript.CreateObject("WScript.Shell")
sLinkFile = "{spath}"
Set oLink = oWS.CreateShortcut(sLinkFile)
oLink.TargetPath = "{exec_python}"
oLink.Arguments = Chr(34) & "{main_script}" & Chr(34)
oLink.WorkingDirectory = "{project_dir}"
oLink.Description = "AI-Powered PDF Workspace"
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
                    desktop_file_path = os.path.join(desktop_dir, "pdf_ai_workspace.desktop")
                    
                    # Fixed: Added quotes around {python_cmd} and {main_script} to handle spaces in paths
                    desktop_content = f"""[Desktop Entry]
Name=PDF AI Workspace
Comment=AI-Powered PDF Workspace
Exec="{python_cmd}" "{main_script}"
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

        # Finish up
        self.log("\n🎉 Installation Complete!")
        self.log("You can now launch 'PDF AI Workspace' from your shortcuts, or close this window.")
        
        # Update UI back to normal thread safely
        self.after(0, self._finish_ui_update)

    def _finish_ui_update(self):
        self.install_btn.config(state="normal", text="Finish & Close", command=self.destroy)

if __name__ == "__main__":
    app = InstallerGUI()
    app.mainloop()