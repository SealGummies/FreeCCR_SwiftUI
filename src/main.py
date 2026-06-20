import sys
import os

# Pre-load onnxruntime (if installed) BEFORE Qt. On Windows, onnxruntime's
# native DLLs fail their init routine when loaded AFTER Qt ("DLL initialization
# routine failed"), which would silently disable AI dust detection in this Qt
# app. Importing it first lets its DLLs load cleanly; Qt then loads fine on top.
# Guarded so the app still runs when onnxruntime isn't installed (manual dust
# removal is unaffected). See spec/dust-removal.md §8.
try:
    import onnxruntime  # noqa: F401
except Exception:
    pass

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