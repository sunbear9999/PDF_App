import customtkinter as ctk
from gui.main_window import MainWindow

def main():
    # Set the general theme and color palette for Linux Mint
    ctk.set_appearance_mode("System")  # Follows your Mint dark/light mode
    ctk.set_default_color_theme("blue") 

    # Initialize and run the app
    app = MainWindow()
    app.mainloop()

if __name__ == "__main__":
    main()