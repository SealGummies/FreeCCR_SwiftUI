#!/usr/bin/env python3
"""
Tests for the percentage-zoom math (dropdown): 100% must mean one source
(actual-size) pixel per screen pixel, independent of the preview downscale.
Exercises a headless ImagePreview with an explicitly sized viewport.
"""

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout  # noqa: E402
from PySide6.QtGui import QPixmap, QColor  # noqa: E402
from PySide6.QtWidgets import QGraphicsPixmapItem  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])

from core.ccr_backend import ccr_backend  # noqa: E402
from core.ccr_image import CCRImage  # noqa: E402
from widgets.image_preview import ImagePreview  # noqa: E402


class _StubImage:
    def __init__(self, source_long, source_short):
        self.original_full_size = (source_short, source_long)  # (h, w)
        self.converted = False


class _StubPanel:
    def set_sliders_enabled(self, *a):
        pass


class _Host(QWidget):
    """Provides the parent().parent().sliders_panel chain ImagePreview expects."""
    def __init__(self):
        super().__init__()
        self.sliders_panel = _StubPanel()
        self.mid = QWidget(self)


# Keep hosts alive for the process so child widgets aren't GC'd mid-test
_HOSTS = []


def _setup_preview(source_w=8660, source_h=5773, preview_w=1080, preview_h=720,
                   view_w=1300, view_h=900):
    """Build an ImagePreview with a known preview pixmap + source size and a
    sized viewport, without going through the heavy update_preview path."""
    ccr_backend.images = []  # clear any stubs left by a prior test
    host = _Host()
    _HOSTS.append(host)
    # Lay out host -> mid -> ImagePreview so the inner QGraphicsView gets a
    # real viewport size once the top-level window is shown.
    host_layout = QVBoxLayout(host)
    host_layout.setContentsMargins(0, 0, 0, 0)
    mid_layout = QVBoxLayout(host.mid)
    mid_layout.setContentsMargins(0, 0, 0, 0)
    host_layout.addWidget(host.mid)
    ip = ImagePreview(host.mid)
    mid_layout.addWidget(ip)
    host.resize(view_w, view_h)
    host.show()
    _app.processEvents()

    ccr_backend.images = [_StubImage(source_w, source_h)]
    ip.current_idx = 0
    ip._current_image_ref = ccr_backend.images[0]

    pm = QPixmap(preview_w, preview_h)
    pm.fill(QColor(128, 128, 128))
    ip.current_pixmap = pm
    ip.pixmap_item = QGraphicsPixmapItem(pm)
    ip.scene.addItem(ip.pixmap_item)
    # These tests cover zoom geometry only — don't spawn hi-res worker threads.
    ip._maybe_request_hires = lambda: None
    ip._zoom = 1.0
    ip._fit_view_to_content()
    _app.processEvents()
    return ip


def _viewport_ok(ip):
    vp = ip.view.viewport().rect()
    return vp.width() > 50 and vp.height() > 50


class TestPercentRatio:
    def test_ratio_is_preview_over_source(self):
        ip = _setup_preview(source_w=8660, preview_w=1080)
        assert ip._preview_to_source_ratio() == pytest.approx(1080.0 / 8660.0)

    def test_ratio_one_for_small_source(self):
        # Source smaller than 1080 -> preview is full size -> ratio 1.0
        ip = _setup_preview(source_w=800, source_h=600, preview_w=800, preview_h=600)
        assert ip._preview_to_source_ratio() == pytest.approx(1.0)

    def test_ratio_none_without_source_size(self):
        ip = _setup_preview()
        ccr_backend.images[0].original_full_size = None
        assert ip._preview_to_source_ratio() is None


class TestZoomToPercent:
    def test_100_percent_is_one_source_pixel_per_screen_pixel(self):
        ip = _setup_preview()
        if not _viewport_ok(ip):
            pytest.skip("offscreen viewport not sized")
        ip.zoom_to_percent(1.0)
        # view px per source px == _view_scale * (preview/source) == 1.0
        assert ip._current_percent() == pytest.approx(1.0, abs=0.01)

    def test_50_and_200_percent(self):
        ip = _setup_preview()
        if not _viewport_ok(ip):
            pytest.skip("offscreen viewport not sized")
        ip.zoom_to_percent(2.0)
        assert ip._current_percent() == pytest.approx(2.0, abs=0.02)
        ip.zoom_to_percent(0.5)
        # 50% of an 8660px source is ~4330 view px wide — still larger than the
        # ~1300px viewport, so it does not fit and the exact percent holds.
        assert ip._current_percent() == pytest.approx(0.5, abs=0.02)

    def test_subfit_percentage_snaps_to_fit_and_centers(self):
        # A tiny source: fit scales it UP, so even 100% is below fit -> whole
        # image fits -> snap to fit (zoom == 1.0).
        ip = _setup_preview(source_w=400, source_h=300, preview_w=400, preview_h=300)
        if not _viewport_ok(ip):
            pytest.skip("offscreen viewport not sized")
        ip.zoom_to_percent(0.25)
        assert ip._zoom == pytest.approx(1.0)

    def test_fit_resets_zoom(self):
        ip = _setup_preview()
        if not _viewport_ok(ip):
            pytest.skip("offscreen viewport not sized")
        ip.zoom_to_percent(2.0)
        assert ip._zoom > 1.0
        ip.zoom_to_fit()
        assert ip._zoom == pytest.approx(1.0)

    def test_combo_reflects_zoom(self):
        ip = _setup_preview()
        if not _viewport_ok(ip):
            pytest.skip("offscreen viewport not sized")
        ip.zoom_to_fit()
        assert ip.zoom_combo.currentText() == "Fit"
        ip.zoom_to_percent(1.0)
        assert ip.zoom_combo.currentText() == "100%"


class TestWheelZoomMax:
    def test_max_zoom_allows_reaching_200_percent(self):
        ip = _setup_preview()
        if not _viewport_ok(ip):
            pytest.skip("offscreen viewport not sized")
        # Wheel up many times; should be able to exceed 100% of actual
        for _ in range(40):
            ip.zoom_by(1.25)
        assert ip._current_percent() >= 2.0 - 0.05


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
