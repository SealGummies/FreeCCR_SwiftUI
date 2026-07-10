"""Tests for the macOS sRGB window-colorspace shim (src/ui/theme.py).

The objc call itself can only execute on a real Mac under the cocoa platform;
these tests cover the platform guards, the app-level filter dispatch, and
installer idempotence. Runs headless (offscreen Qt platform).
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402
from PySide6.QtCore import QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QWidget  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])

from ui import theme  # noqa: E402


@pytest.mark.skipif(sys.platform == "darwin",
                    reason="off-macOS guard; the objc path needs cocoa, not offscreen")
def test_apply_is_noop_off_macos():
    assert theme.apply_macos_srgb_colorspace(QWidget()) is False


@pytest.mark.skipif(sys.platform == "darwin",
                    reason="off-macOS guard; the objc path needs cocoa, not offscreen")
def test_install_is_noop_off_macos():
    theme.install_macos_srgb_filter(_app)
    assert theme._macos_srgb_filter is None


def test_filter_tags_top_level_windows_only(monkeypatch):
    calls = []
    monkeypatch.setattr(theme, "apply_macos_srgb_colorspace",
                        lambda w: calls.append(w) or True)
    f = theme._MacSRGBColorSpaceFilter()
    top = QWidget()
    child = QLabel(top)
    show = QEvent(QEvent.Type.Show)
    assert f.eventFilter(top, show) is False, "filter must never consume events"
    assert f.eventFilter(child, show) is False
    assert f.eventFilter(top, QEvent(QEvent.Type.Paint)) is False
    assert calls == [top], "only the shown top-level window gets tagged"


def test_filter_reapplies_on_every_show(monkeypatch):
    # No seen-set: a platform window Qt re-created in between needs re-tagging.
    calls = []
    monkeypatch.setattr(theme, "apply_macos_srgb_colorspace",
                        lambda w: calls.append(w) or True)
    f = theme._MacSRGBColorSpaceFilter()
    top = QWidget()
    f.eventFilter(top, QEvent(QEvent.Type.Show))
    f.eventFilter(top, QEvent(QEvent.Type.Show))
    assert calls == [top, top]


def test_install_on_darwin_installs_once(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(theme, "_macos_srgb_filter", None)
    try:
        theme.install_macos_srgb_filter(_app)
        first = theme._macos_srgb_filter
        assert first is not None
        theme.install_macos_srgb_filter(_app)
        assert theme._macos_srgb_filter is first, "second install must be a no-op"
    finally:
        if theme._macos_srgb_filter is not None:
            _app.removeEventFilter(theme._macos_srgb_filter)
            theme._macos_srgb_filter = None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
