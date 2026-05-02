# main.py
import os
import sys
import traceback
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtGui import QIcon
from PySide6.QtCore import QSettings, QTimer, Qt
from gui.theme import ThemeManager
from gui.main_window import MainWindow

if getattr(sys, 'frozen', False):
    root_dir = sys._MEIPASS
else:
    root_dir = os.path.abspath(os.path.dirname(__file__))


def _configure_qtwebengine_dictionaries(base_dir):
    """Point Qt to bundled dictionaries if present; otherwise leave unset
    so Qt's WebEngine spellcheck quietly disables itself."""
    dict_path = os.path.join(base_dir, "qtwebengine_dictionaries")
    if os.path.isdir(dict_path):
        os.environ["QTWEBENGINE_DICTIONARIES_PATH"] = dict_path


_configure_qtwebengine_dictionaries(root_dir)

if getattr(sys, 'frozen', False) and len(sys.argv) > 1 and sys.argv[1] == "--run-pdf-worker":
    # 1. Modify sys.argv so pdf_worker parses the right arguments
    sys.argv = [sys.argv[0]] + sys.argv[2:] 
    
    # 2. Find the raw script inside the PyInstaller temporary bundle
    base_path = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    worker_path = os.path.join(base_path, 'core', 'pdf_worker.py')
    
    # 3. Read the text and execute it directly in memory, bypassing PyArmor completely
    try:
        with open(worker_path, 'r', encoding='utf-8') as f:
            worker_code = f.read()
            
        # Passing __name__: __main__ tricks the script into running its main() function
        exec(worker_code, {'__name__': '__main__'})
        
    except Exception as e:
        print(f"Failed to run pdf_worker: {e}")
        sys.exit(1)
        
    # Exit immediately
    sys.exit(0)

def global_exception_handler(exc_type, exc_value, exc_traceback):
    """
    A universal safety net for the application. 
    Intercepts unhandled exceptions before they crash the app.
    """
    error_msg = str(exc_value)
    
    # Check if this is the notorious PyQt C++ deletion error
    if issubclass(exc_type, RuntimeError) and "wrapped C/C++ object" in error_msg:
        print(f"🛡️ [Global Error Handler] Caught deleted C++ object access. Ignoring safely: {error_msg}")
        return
        
    print("\n--- UNHANDLED EXCEPTION ---", file=sys.stderr)
    traceback.print_exception(exc_type, exc_value, exc_traceback)
    print("---------------------------\n", file=sys.stderr)
    
    # Attempt to show a critical error dialog instead of just crashing silently
    try:
        app = QApplication.instance()
        if app:
            msg = QMessageBox()
            msg.setIcon(QMessageBox.Icon.Critical)
            msg.setWindowTitle("Critical Error")
            msg.setText("An unexpected error occurred. The application will try to continue.")
            msg.setDetailedText("".join(traceback.format_exception(exc_type, exc_value, exc_traceback)))
            msg.exec()
    except Exception:
        pass # If the UI is completely dead, just pass to system handler

    sys.__excepthook__(exc_type, exc_value, exc_traceback)

def main():
    sys.excepthook = global_exception_handler
    
    # ==========================================
    # CRITICAL RENDER FIX 
    # Forces Qt to share the OpenGL context across all docks
    # ==========================================
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon("icon.png"))
    
    theme_manager = ThemeManager()
    settings = QSettings("PDFMultitool", "Workspace")
    saved_theme = settings.value("theme", "Dark (Default)")
    theme_manager.set_theme(saved_theme)
    theme_manager.apply_global_style(app)
    
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()