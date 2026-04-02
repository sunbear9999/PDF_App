# main.py
import sys
import traceback
from PyQt6.QtWidgets import QApplication
from gui.main_window import MainWindow

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
    
    # Apply a global stylesheet to ensure consistent theming across all dialogs/menus
    app.setStyleSheet("""
        QMenu { background-color: #2b2b2b; color: white; border: 1px solid #444; }
        QMenu::item:selected { background-color: #0078D7; }
        QMessageBox { background-color: #1e1e1e; color: white; }
        QMessageBox QLabel { color: white; }
        QMessageBox QPushButton { background-color: #333; color: white; padding: 5px 15px; border-radius: 4px; }
        QMessageBox QPushButton:hover { background-color: #444; }
        QInputDialog { background-color: #1e1e1e; color: white; }
        QInputDialog QLineEdit { background-color: #333; color: white; border: 1px solid #555; padding: 4px; }
    """)
    
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()