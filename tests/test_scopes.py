#!/usr/bin/env python3
"""Tests for the Scopes panel: core/scopes.py math (parade / vectorscope /
scaling) and a headless smoke of widgets/scopes_panel.py + the ImagePreview
capture helper. See spec/color-scopes-panel.md."""
import os
import sys

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])

from core import scopes  # noqa: E402
from widgets.scopes_panel import ScopesPanel  # noqa: E402


# --- rgb_to_cbcr ------------------------------------------------------------

def test_cbcr_gray_is_center():
    for v in (0, 64, 128, 255):
        cb, cr = scopes.rgb_to_cbcr(np.array([v, v, v], dtype=np.uint8))
        assert abs(float(cb)) < 1e-3 and abs(float(cr)) < 1e-3


def test_cbcr_primary_directions():
    cb, cr = scopes.rgb_to_cbcr(np.array([255, 0, 0], np.float32))
    assert cb < 0 and cr > 0          # red: up-left
    cb, cr = scopes.rgb_to_cbcr(np.array([0, 0, 255], np.float32))
    assert cb > 0 and cr < 0          # blue: right, slightly down
    cb, cr = scopes.rgb_to_cbcr(np.array([0, 255, 0], np.float32))
    assert cb < 0 and cr < 0          # green: down-left


def test_cbcr_complements_are_opposite():
    a = scopes.rgb_to_cbcr(np.array([255, 255, 0], np.float32))   # yellow
    b = scopes.rgb_to_cbcr(np.array([0, 0, 255], np.float32))     # blue
    assert abs(a[0] + b[0]) < 1e-3 and abs(a[1] + b[1]) < 1e-3


# --- compute_parade ---------------------------------------------------------

def _frame(w=4, h=3, value=(10, 20, 30)):
    rgb = np.zeros((h, w, 3), np.uint8)
    rgb[:] = value
    return rgb, np.ones((h, w), bool)


def test_parade_shape_and_totals():
    rgb, mask = _frame()
    counts = scopes.compute_parade(rgb, mask)
    assert counts.shape == (3, 256, 4)
    for c in range(3):
        assert counts[c].sum() == mask.sum()


def test_parade_bins_land_per_column():
    rgb, mask = _frame(w=3, h=2)
    rgb[:, 1] = (100, 150, 200)       # column 1 differs
    counts = scopes.compute_parade(rgb, mask)
    assert counts[0, 10, 0] == 2 and counts[0, 100, 1] == 2
    assert counts[1, 150, 1] == 2 and counts[2, 200, 1] == 2
    assert counts[0, 100, 0] == 0     # value stays in its own column


def test_parade_respects_mask():
    rgb, mask = _frame(w=2, h=2)
    mask[:, 1] = False
    counts = scopes.compute_parade(rgb, mask)
    assert counts[:, :, 1].sum() == 0
    assert counts[0].sum() == 2


def test_parade_empty_mask():
    rgb, mask = _frame()
    counts = scopes.compute_parade(rgb, ~mask)
    assert counts.sum() == 0


# --- compute_vectorscope ----------------------------------------------------

def test_vectorscope_gray_hits_center():
    rgb, mask = _frame(value=(128, 128, 128))
    counts = scopes.compute_vectorscope(rgb, mask, size=128)
    assert counts.shape == (128, 128)
    # Exactly neutral chroma sits on the bin boundary between 63 and 64 —
    # float32 weight rounding may land it either side, so check the 2×2 core.
    assert counts[63:65, 63:65].sum() == mask.sum()
    assert counts.sum() == mask.sum()


def test_vectorscope_red_lands_up_left():
    rgb, mask = _frame(value=(255, 0, 0))
    counts = scopes.compute_vectorscope(rgb, mask, size=128)
    ys, xs = np.nonzero(counts)
    assert ys[0] < 64 and xs[0] < 64  # +Cr is up (small row), -Cb is left


def test_vectorscope_blue_lands_right():
    rgb, mask = _frame(value=(0, 0, 255))
    counts = scopes.compute_vectorscope(rgb, mask, size=128)
    ys, xs = np.nonzero(counts)
    assert xs[0] > 64 and ys[0] > 64


def test_vectorscope_respects_mask():
    rgb, mask = _frame(value=(0, 255, 0))
    mask[0, :] = False
    counts = scopes.compute_vectorscope(rgb, mask)
    assert counts.sum() == mask.sum()


# --- scale_counts -----------------------------------------------------------

def test_scale_counts_range_and_zero():
    assert scopes.scale_counts(np.zeros((4, 4))).sum() == 0
    out = scopes.scale_counts(np.array([0.0, 1.0, 5.0, 1e6]))
    assert out.min() >= 0.0 and out.max() <= 1.0


def test_scale_counts_dominant_pile_does_not_crush_traces():
    counts = np.ones(100, np.float32)
    counts[0] = 1e6                    # a huge flat-area pile
    out = scopes.scale_counts(counts)
    # The percentile reference keeps the 1-count trace visible, the pile clips.
    assert out[1] > 0.5 and out[0] == 1.0


# --- vectorscope LUT / targets ----------------------------------------------

def test_vector_color_lut_center_is_neutral():
    lut = scopes.vector_color_lut(128)
    assert lut.shape == (128, 128, 3)
    assert lut.min() >= 0.0 and lut.max() <= 1.0
    center = lut[64, 64]
    assert np.allclose(center, center[0], atol=0.02)   # gray-ish
    # Top-left region (toward red): R dominates.
    r, g, b = lut[16, 32]
    assert r > g and r > b


def test_skin_tone_direction_between_red_and_yellow():
    ucb, ucr = scopes.skin_tone_direction()
    assert abs((ucb * ucb + ucr * ucr) ** 0.5 - 1.0) < 1e-5   # unit vector
    import math
    skin = math.atan2(ucr, ucb)
    r_cb, r_cr = scopes.rgb_to_cbcr(np.array([255, 0, 0], np.float32))
    y_cb, y_cr = scopes.rgb_to_cbcr(np.array([255, 255, 0], np.float32))
    red = math.atan2(float(r_cr), float(r_cb))
    yellow = math.atan2(float(y_cr), float(y_cb))
    # The classic skin line sits between the R and Yl targets (10:30-11
    # o'clock), i.e. between their angles in the upper-left quadrant.
    assert red < skin < yellow


def test_vector_targets():
    targets = dict((n, (cb, cr)) for n, cb, cr in scopes.vector_targets())
    assert set(targets) == {"R", "Mg", "B", "Cy", "G", "Yl"}
    assert targets["R"][0] < 0 and targets["R"][1] > 0
    # Complementary pairs are point-symmetric about the center.
    for a, b in (("R", "Cy"), ("G", "Mg"), ("B", "Yl")):
        assert abs(targets[a][0] + targets[b][0]) < 1e-3
        assert abs(targets[a][1] + targets[b][1]) < 1e-3


# --- widget smoke -----------------------------------------------------------

def _panel():
    p = ScopesPanel()
    p.resize(700, 220)
    return p


def test_panel_set_frame_and_grab():
    p = _panel()
    if not p.is_expanded():
        p._toggle_btn.click()
    rng = np.random.default_rng(7)
    rgb = rng.integers(0, 256, (90, 160, 3), dtype=np.uint8)
    mask = np.ones((90, 160), bool)
    mask[:, :20] = False               # a letterbox strip
    p.set_frame(rgb, mask)
    assert p.parade._img is not None and p.vectorscope._img is not None
    assert not p.grab().isNull()
    p._toggle_btn.click()              # restore default collapsed state
    assert not p.is_expanded()


def test_panel_probe_and_clear():
    p = _panel()
    p.set_probe(255, 128, 3, 0.5)
    assert "255" in p._readout.text() and "128" in p._readout.text()
    assert p.parade._probe == (255, 128, 3, 0.5)
    p.clear_probe()
    assert p.parade._probe is None and "---" in p._readout.text()
    rgb = np.zeros((4, 4, 3), np.uint8)
    p.set_frame(rgb, np.ones((4, 4), bool))
    p.clear()
    assert p.parade._img is None and p.vectorscope._img is None


def test_scope_widgets_accept_none():
    p = _panel()
    p.parade.set_data(None)
    p.vectorscope.set_data(None)
    assert not p.parade.grab().isNull()
    assert not p.vectorscope.grab().isNull()


def test_parade_dots_are_dilated():
    # A single lit bin must bleed into its 4 neighbors (soft dilation) so
    # isolated waveform samples stay visible at screen scale.
    p = _panel()
    counts = np.zeros((3, 256, 9), np.float32)
    counts[0, 128, 4] = 1.0
    p.parade.set_data(counts)
    buf = p.parade._img_buf            # (256, 3*9, 3), red cell = cols 0..8
    row = 255 - 128                    # value 128, rows flipped (255 at top)
    center = int(buf[row, 4, 0])
    assert center > 0
    for r, c in ((row - 1, 4), (row + 1, 4), (row, 3), (row, 5)):
        neighbor = int(buf[r, c, 0])
        assert 0 < neighbor < center   # lit, but dimmer than the sample


def test_body_height_resize_clamps_and_keeps_vectorscope_square():
    p = _panel()
    p.set_body_height(300)
    assert p.body_height() == 300
    assert p.vectorscope.minimumWidth() == 300
    p.set_body_height(10_000)      # clamp to max
    assert p.body_height() == 420
    p.set_body_height(-50)         # clamp to min
    assert p.body_height() == 140
    p.set_body_height(180)         # restore the default


# --- ImagePreview capture helper ---------------------------------------------

def test_capture_display_image():
    from PySide6.QtWidgets import (QGraphicsPixmapItem, QWidget, QVBoxLayout)
    from PySide6.QtGui import QPixmap, QColor
    from widgets.image_preview import ImagePreview

    # ImagePreview expects a two-level parent chain (MainWindow layout).
    host = QWidget()
    host_lay = QVBoxLayout(host)
    mid = QWidget(host)
    host_lay.addWidget(mid)
    mid_lay = QVBoxLayout(mid)
    ip = ImagePreview(mid)
    mid_lay.addWidget(ip)
    host.resize(700, 600)
    host.show()                        # offscreen: activates the layout so the
    _app.processEvents()               # view gets its real viewport geometry

    assert ip._capture_display_image() is None   # no image → None

    pm = QPixmap(120, 80)
    pm.fill(QColor(200, 50, 25))
    ip.current_pixmap = pm
    ip.pixmap_item = QGraphicsPixmapItem(pm)
    ip.scene.addItem(ip.pixmap_item)
    ip._fit_view_to_content()
    frame = ip._capture_display_image()
    assert frame is not None
    rgb, mask = frame
    assert rgb.shape[2] == 3 and mask.shape == rgb.shape[:2]
    # The whole (un-rotated) image is covered — no letterbox in the capture.
    assert mask.all()
    # Uncropped, unrotated: the capture keeps the image aspect (120:80).
    assert abs(rgb.shape[1] / rgb.shape[0] - 1.5) < 0.05
    inside = rgb[mask]
    # The solid color survives the round-trip (smooth scaling tolerance).
    assert abs(int(np.median(inside[:, 0])) - 200) <= 2
    assert abs(int(np.median(inside[:, 1])) - 50) <= 2
    assert abs(int(np.median(inside[:, 2])) - 25) <= 2

    # Zoom/pan must NOT change the scopes: an aggressive zoom-in + pan leaves
    # the capture bit-identical (it never looks through the view transform).
    ip.view.scale(4.0, 4.0)
    ip.view.translate(37.0, -21.0)
    _app.processEvents()
    rgb2, mask2 = ip._capture_display_image()
    assert rgb2.shape == rgb.shape
    assert np.array_equal(rgb2, rgb) and np.array_equal(mask2, mask)
    host.hide()
