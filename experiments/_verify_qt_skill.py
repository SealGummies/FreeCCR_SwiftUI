"""Throwaway verification for the qt-testing skill. Renders real FreeCCR widgets."""
import sys

SKILL = r"H:\Projects\FreeCCR\.claude\skills\qt-testing"
sys.path.insert(0, SKILL)
from scripts.qt_capture import capture_widget, init_qt, setup_freeccr_path, OUTPUT_DIR

setup_freeccr_path()
app = init_qt(offscreen=True)

from core.ccr_backend import ccr_backend  # noqa: F401  (ensures singleton)

from widgets.sliders_panel import SlidersPanel
panel = SlidersPanel(None)
panel.resize(360, 700)
p1 = capture_widget(panel, "verify_sliders_panel")
print("SLIDERS_PNG", p1)

from widgets.curve_editor import CurveEditor
ce = CurveEditor(None)
ce.resize(320, 360)
p2 = capture_widget(ce, "verify_curve_editor")
print("CURVE_PNG", p2)

from ui.main_window import MainWindow
win = MainWindow()
win.resize(1280, 800)
p3 = capture_widget(win, "verify_main_window")
win.close()
print("MAINWINDOW_PNG", p3)

print("OUTPUT_DIR", OUTPUT_DIR)
print("OK")
