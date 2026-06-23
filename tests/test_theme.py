"""Tests for the neutral-dark theme system (src/ui/theme.py).

See spec/visual-redesign.md §9.1. Runs headless (offscreen Qt platform).
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtGui import QPalette, QPixmap, QColor  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])

from ui import theme  # noqa: E402


def test_apply_theme_applies_palette_and_stylesheet():
    from PySide6.QtWidgets import QStyleFactory
    theme.apply_theme(_app)
    # Qt wraps the base style in QStyleSheetStyle once a stylesheet is set, so
    # style().objectName() is unreliable; assert the observable effects instead.
    assert _app.styleSheet().strip(), "global stylesheet should be applied"
    assert _app.palette().color(QPalette.Window).name() == theme.WINDOW
    assert "Fusion" in QStyleFactory.keys(), "Fusion base style must be available"


def test_palette_matches_tokens():
    pal = theme.build_palette()
    assert pal.color(QPalette.Window).name() == theme.WINDOW
    assert pal.color(QPalette.WindowText).name() == theme.TEXT
    assert pal.color(QPalette.Base).name() == theme.SURFACE
    assert pal.color(QPalette.Highlight).name() == theme.SELECTION_BG
    assert pal.color(QPalette.HighlightedText).name() == theme.SELECTION_TEXT
    # disabled text is dimmed
    assert pal.color(QPalette.Disabled, QPalette.Text).name() == theme.TEXT_DISABLED


def test_global_qss_covers_every_control_type():
    qss = theme.global_qss()
    for selector in (
        "QPushButton", "QToolButton", "QToolBar", "QComboBox", "QLineEdit",
        "QGroupBox", "QListWidget", "QScrollBar", "QProgressBar", "QSlider",
        "QMenuBar", "QMenu", "QTabBar", "QGraphicsView", "QToolTip",
        'QLabel[caption="true"]',
    ):
        assert selector in qss, f"global QSS missing a rule for {selector}"


def test_load_tinted_icon_recolors_to_icon_token(tmp_path):
    # An opaque black source -> every opaque pixel becomes ICON after tinting.
    src = QPixmap(8, 8)
    src.fill(QColor(0, 0, 0, 255))
    png = tmp_path / "black.png"
    assert src.save(str(png))

    icon = theme.load_tinted_icon(str(png))
    assert not icon.isNull()

    img = icon.pixmap(8, 8).toImage()
    col = img.pixelColor(4, 4)
    assert (col.red(), col.green(), col.blue()) == (0xCF, 0xCF, 0xCF)


def test_histogram_backdrop_is_dark_and_coupled():
    # Numpy backdrop and the QSS container must share one dark value (spec §6.4).
    assert theme.Paint.HIST_BG == (42, 42, 42)
    assert theme.Paint.HIST_PEAK == (235, 235, 235)


def test_overlay_constants_unchanged_regression():
    # On-image overlays must keep their functional RGBA (spec §9.1 / §10.3):
    # centralized, NOT restyled. Locks "no visual change" for overlays.
    o = theme.Overlay
    assert o.REF_FRAME == (255, 0, 0, 180)
    assert o.REF_FRAME_DRAG == (255, 0, 0, 120)
    assert o.SLICE == (0, 200, 255)
    assert o.DIM == (0, 0, 0, 80)
    assert o.DIM_CROP == (0, 0, 0, 110)
    assert o.DUST_STROKE == (255, 40, 40, 150)
    assert o.DUST_CURSOR == (255, 255, 255, 220)
    assert o.CROP_BORDER == (255, 255, 255, 220)
    assert o.HANDLE_FILL == (255, 255, 255, 235)
    assert o.HANDLE_OUTLINE == (30, 30, 30, 230)
    assert o.AREA_LINE == (255, 255, 255, 220)
    assert o.AREA_FEATHER == (255, 255, 255, 120)
    assert o.BWP_WHITE == (0, 100, 255, 140)
    assert o.BWP_DENSE == (255, 140, 0, 140)
    assert o.COMP_GRID == (0, 80, 0, 80)
    assert o.CURSOR_LIGHT == (255, 255, 255)
    assert o.CURSOR_DARK == (25, 25, 25)


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
