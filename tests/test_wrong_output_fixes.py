#!/usr/bin/env python3
"""Regression tests for the wrong-output fixes.

1. Exporting via the B/W-point pipeline must not mutate the live image's
   state (_ws_windowed / contrast_base / temperature_base / exposure_base) —
   flagging a never-converted image's full-range resized_raw as windowed
   blew its next preview render to near-white.
2. The B/W-point conversion uses display semantics for fine rotation: the
   preview result is identical for any fine_rotation_angle (the canvas item
   rotates it), instead of baking a warp the display then applied AGAIN.
3. render_hires_base replays a "bw" conversion without warping, matching the
   un-rotated preview.
4. GraphicsImageView._display_transform includes the fine rotation, so
   scene→pixel sampling lands on the pixels under the cursor (the old
   coarse-only inverse drifted by ~r·sin(θ) under a fine rotation).
"""
import os
import sys

import cv2
import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])

from core.ccr_image import CCRImage  # noqa: E402
from core.ccr_processor import ccr_normalize_with_bwpoint, _ws_enabled  # noqa: E402

BLACK_POINT = (60000.0, 58000.0, 56000.0)   # clear film base (high scan values)
WHITE_POINT = (9000.0, 8500.0, 8000.0)      # dense/exposed area (low scan values)


def _make_scan(tmp_path, name="scan.png"):
    """A 16-bit-ish gradient 'negative' saved as PNG (8-bit is fine — the
    reader rescales to uint16)."""
    path = str(tmp_path / name)
    g = np.linspace(40, 220, 120, dtype=np.uint8)
    img = np.dstack([np.tile(g, (90, 1))] * 3)
    cv2.imwrite(path, img)
    return path


# --- 1. export must not mutate live state -----------------------------------

def test_export_does_not_mutate_live_state(tmp_path):
    img = CCRImage(_make_scan(tmp_path))
    assert img.converted is False
    before = (img._ws_windowed, img.contrast_base, img.temperature_base,
              getattr(img, "exposure_base", 0.0))
    out = str(tmp_path / "out.jpg")
    ccr_normalize_with_bwpoint(img, BLACK_POINT, WHITE_POINT,
                               output_path=out, jpg_out=True)
    assert os.path.exists(out)
    after = (img._ws_windowed, img.contrast_base, img.temperature_base,
             getattr(img, "exposure_base", 0.0))
    assert after == before, "export mutated live image state"
    assert img._ws_windowed is False  # full-range raw scan stays unflagged


def test_preview_conversion_still_updates_live_state(tmp_path):
    img = CCRImage(_make_scan(tmp_path))
    img.contrast_base = 40  # legacy baked S-curve value; conversion must reset
    result = ccr_normalize_with_bwpoint(img, BLACK_POINT, WHITE_POINT)
    assert result is not None
    assert img.contrast_base == 0
    assert img.temperature_base == 0
    assert img._ws_windowed == _ws_enabled()


# --- 2. fine rotation is no longer baked into the preview conversion --------

def test_bw_preview_identical_for_any_fine_rotation(tmp_path):
    path = _make_scan(tmp_path)
    img_a = CCRImage(path)
    img_a.fine_rotation_angle = 0
    img_b = CCRImage(path)
    img_b.fine_rotation_angle = 300  # 3 degrees

    out_a = ccr_normalize_with_bwpoint(img_a, BLACK_POINT, WHITE_POINT)
    out_b = ccr_normalize_with_bwpoint(img_b, BLACK_POINT, WHITE_POINT)
    assert np.array_equal(out_a, out_b), \
        "fine rotation leaked into the conversion pixels (display owns it)"


# --- 3. hi-res bw replay matches the un-rotated preview ----------------------

def test_hires_bw_replay_ignores_fine_rot(tmp_path):
    img = CCRImage(_make_scan(tmp_path))
    ci_rot = {"mode": "bw", "bw": (BLACK_POINT, WHITE_POINT),
              "fine_rot": 300, "density": False}
    ci_flat = {"mode": "bw", "bw": (BLACK_POINT, WHITE_POINT),
               "fine_rot": 0, "density": False}
    out_rot, _ = img.render_hires_base(max_long_side=256, conversion_inputs=ci_rot)
    out_flat, _ = img.render_hires_base(max_long_side=256, conversion_inputs=ci_flat)
    assert out_rot is not None
    assert np.array_equal(out_rot, out_flat)


# --- 4. sampling transform includes the fine rotation ------------------------

class _PreviewStub:
    def __init__(self, w=200, h=100, fine=0):
        from PySide6.QtGui import QPixmap
        self.current_pixmap = QPixmap(w, h)
        self.current_vertical_flip = False
        self.current_horizontal_flip = False
        self.current_rotation = 0
        self.current_fine_rotation = fine
        self.crop_mode = False
        self.dust_mode = False


class _ViewStub:
    from widgets.image_preview import GraphicsImageView as _G
    _display_transform = _G._display_transform

    def __init__(self, pw):
        self.parent_widget = pw


def test_display_transform_rotates_about_center():
    from PySide6.QtCore import QPointF
    view = _ViewStub(_PreviewStub(w=200, h=100, fine=300))  # +3 degrees
    t = view._display_transform()
    # The center is the rotation pivot — must map to itself.
    c = t.map(QPointF(100.0, 50.0))
    assert abs(c.x() - 100.0) < 1e-6 and abs(c.y() - 50.0) < 1e-6
    # A point on the +x axis from center rotates by exactly 3 degrees.
    p = t.map(QPointF(200.0, 50.0))
    theta = np.radians(3.0)
    assert abs(p.x() - (100.0 + 100.0 * np.cos(theta))) < 1e-6
    assert abs(p.y() - (50.0 + 100.0 * np.sin(theta))) < 1e-6
    # Round trip: inverse(display) really undoes it.
    inv = t.inverted()[0]
    q = inv.map(p)
    assert abs(q.x() - 200.0) < 1e-6 and abs(q.y() - 50.0) < 1e-6


def test_display_transform_suppressed_in_crop_and_dust_modes():
    from PySide6.QtCore import QPointF
    pw = _PreviewStub(w=200, h=100, fine=300)
    pw.crop_mode = True
    t = _ViewStub(pw)._display_transform()
    p = t.map(QPointF(200.0, 50.0))
    assert abs(p.x() - 200.0) < 1e-6 and abs(p.y() - 50.0) < 1e-6
    pw.crop_mode, pw.dust_mode = False, True
    t = _ViewStub(pw)._display_transform()
    p = t.map(QPointF(200.0, 50.0))
    assert abs(p.x() - 200.0) < 1e-6 and abs(p.y() - 50.0) < 1e-6


def test_display_transform_identity_without_fine_rotation():
    from PySide6.QtCore import QPointF
    t = _ViewStub(_PreviewStub(fine=0))._display_transform()
    p = t.map(QPointF(37.0, 91.0))
    assert abs(p.x() - 37.0) < 1e-6 and abs(p.y() - 91.0) < 1e-6
