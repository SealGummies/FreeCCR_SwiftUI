import sys
import os
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon

def main():
    from ui.main_window import MainWindow

    app = QApplication(sys.argv)
    _icon_path = os.path.join(os.path.dirname(__file__), 'icons', 'freeccr_logo.png')
    app.setWindowIcon(QIcon(_icon_path))
    print("Starting FreeCCR...")
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