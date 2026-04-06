# main.py
import sys
import traceback
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QIcon
from PyQt6.QtCore import QSettings

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
        # We silently catch it, log it to the console, and PREVENT the app from crashing.
        print(f"🛡️ [Global Error Handler] Caught deleted C++ object access. Ignoring safely: {error_msg}")
        return
        
    # If it's a different kind of error (like a syntax error or a missing file), 
    # we still want the app to print the normal traceback so we can fix it.
    print("\n--- UNHANDLED EXCEPTION ---", file=sys.stderr)
    traceback.print_exception(exc_type, exc_value, exc_traceback)
    print("---------------------------\n", file=sys.stderr)
    
    # Pass it back to the default system handler (will crash the app for severe bugs)
    sys.__excepthook__(exc_type, exc_value, exc_traceback)

def main():
    # 1. Attach the universal error handler BEFORE starting the app
    sys.excepthook = global_exception_handler
    
    # 2. Start the application
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon("icon.png"))
    
    # 3. Initialize and apply Theme Manager
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