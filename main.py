# main.py
import sys
import traceback
from PyQt6.QtWidgets import QApplication, QMessageBox
from PyQt6.QtGui import QIcon
from PyQt6.QtCore import QSettings, QTimer

from gui.main_window import MainWindow
from gui.theme import ThemeManager

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