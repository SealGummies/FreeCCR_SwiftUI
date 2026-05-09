import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon

def main():
    from ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon("./icons/haloimagery.png"))  # Set app icon for taskbar
    print("Starting CCR Client...")
    window = MainWindow()
    print("MainWindow created, setting up UI...")

    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        print("Exception occurred:", e)
        traceback.print_exc()