import sys
import os

# Make onnxruntime usable for AI dust detection, then pre-load it BEFORE Qt.
# Two Windows gotchas this handles (see spec/dust-removal.md §8):
#  1) Nuitka standalone build: the bundled onnxruntime .pyd is loaded without
#     its own folder on the DLL search path, so its onnxruntime.dll dependency
#     isn't found -> "DLL initialization routine failed". Adding the capi dir
#     (next to the exe) before import fixes it. (No-op when running from source.)
#  2) onnxruntime's DLLs fail to init if first loaded AFTER Qt, so import it
#     before any PySide6 import.
# Guarded so the app still runs when onnxruntime is absent (manual dust removal
# is unaffected, and the AI panel reports the reason).
try:
    _ort_capi = os.path.join(os.path.dirname(os.path.abspath(sys.executable)),
                             "onnxruntime", "capi")
    if os.path.isdir(_ort_capi):
        os.add_dll_directory(_ort_capi)
except Exception:
    pass
try:
    import onnxruntime as _ort  # noqa: F401
    print(f"AI dust detection: onnxruntime {_ort.__version__} ready", flush=True)
    del _ort
except Exception as _e:  # noqa: BLE001
    print(f"AI dust detection: onnxruntime unavailable ({_e})", flush=True)

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