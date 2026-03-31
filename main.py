import sys
from PyQt6.QtWidgets import QApplication
from gui.main_window import MainWindow

def main():
    # Initialize the PyQt6 Application
    app = QApplication(sys.argv)
    
    # Set global application style (optional but makes popups look better)
    app.setStyle("Fusion")
    
    # Instantiate and show the main window
    window = MainWindow()
    window.show()
    
    # Execute the application event loop
    sys.exit(app.exec())

if __name__ == "__main__":
    main()