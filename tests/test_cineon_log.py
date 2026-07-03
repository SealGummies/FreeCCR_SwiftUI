#!/usr/bin/env python3
"""Tests for the Cineon film log → Rec.709 (γ 2.2) display conversion — the
optional FINAL pipeline stage behind the Channel Levels checkbox (settings
key "cineon_log"). See spec/cineon-display-transform.md."""
import os
import sys

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])

from core.ccr_processor import (apply_cineon_to_rec709,  # noqa: E402
                                _cineon_rec709_lut16,
                                CINEON_BLACK_CODE, CINEON_WHITE_CODE)


def _codes():
    """10-bit code value of every 16-bit LUT index."""
    return np.linspace(0.0, 1023.0, 65536)


# --- LUT math ---------------------------------------------------------------

def test_lut_black_and_white_anchors():
    lut = _cineon_rec709_lut16()
    codes = _codes()
    assert (lut[codes <= CINEON_BLACK_CODE] == 0).all()      # Dmin and below → black
    assert (lut[codes >= CINEON_WHITE_CODE] == 65535).all()  # 90% white and above → clip


def test_lut_monotonic_and_range():
    lut = _cineon_rec709_lut16().astype(np.int64)
    assert (np.diff(lut) >= 0).all()
    assert lut[0] == 0 and lut[-1] == 65535


def test_lut_matches_closed_form_midscale():
    lut = _cineon_rec709_lut16()
    for code in (200.0, 390.0, 500.0, 600.0):
        idx = int(round(code * 65535.0 / 1023.0))
        c = idx * 1023.0 / 65535.0            # exact code at that index
        off = 10.0 ** ((95.0 - 685.0) * 0.002 / 0.6)
        lin = (10.0 ** ((c - 685.0) * 0.002 / 0.6) - off) / (1.0 - off)
        expected = round((max(lin, 0.0) ** (1 / 2.2)) * 65535.0)
        assert abs(int(lut[idx]) - expected) <= 1


# --- apply function ----------------------------------------------------------

def test_apply_preserves_shape_dtype_and_neutrality():
    img = np.linspace(0, 65535, 2 * 3 * 3, dtype=np.uint16).reshape(2, 3, 3)
    out = apply_cineon_to_rec709(img)
    assert out.shape == img.shape and out.dtype == np.uint16
    # A neutral pixel (R=G=B) stays neutral — the LUT is per-channel identical.
    gray = np.full((2, 2, 3), 30000, dtype=np.uint16)
    out_g = apply_cineon_to_rec709(gray)
    assert (out_g[..., 0] == out_g[..., 1]).all()
    assert (out_g[..., 1] == out_g[..., 2]).all()


# --- pipeline: final stage after all other adjustments ------------------------

def _bare_image_obj():
    """A CCRImage shell with just the attributes apply_adjustments reads when
    every stage is overridden via parameters (no file/decoding needed)."""
    from core.ccr_image import CCRImage
    obj = CCRImage.__new__(CCRImage)
    obj.tint_balance_factor = 1.0
    obj.converted = False          # Auto Gain path off (conversion-only)
    return obj


def _apply(obj, img, settings):
    return obj.apply_adjustments(
        img, settings=settings, contrast_base=0, temperature_base=0,
        brightness_base=0, exposure_base=0, ws_windowed=False,
        color_profile="color", areas_override=[])


def test_pipeline_applies_cineon_as_final_stage():
    rng = np.random.default_rng(3)
    img = rng.integers(0, 65536, (8, 8, 3), dtype=np.uint16)
    obj = _bare_image_obj()
    # Same heavy path with and without the flag (both dicts are non-empty);
    # the flagged result must be exactly the post-adjustment result pushed
    # through the LUT — i.e. a pure final stage, nothing interleaved.
    base = _apply(obj, img, {"saturation": 0})
    out = _apply(obj, img, {"saturation": 0, "cineon_log": True})
    assert np.array_equal(out, apply_cineon_to_rec709(base))
    # With a real adjustment active the property still holds.
    base2 = _apply(obj, img, {"contrast": 30})
    out2 = _apply(obj, img, {"contrast": 30, "cineon_log": True})
    assert np.array_equal(out2, apply_cineon_to_rec709(base2))
    # Absent/falsy flag → no transform.
    assert np.array_equal(base, _apply(obj, img, {"saturation": 0,
                                                  "cineon_log": 0}))


# --- panel wiring -------------------------------------------------------------

def test_sync_group_carries_cineon_key():
    from widgets.sliders_panel import SYNC_GROUPS
    channels = dict((gid, keys) for gid, _l, keys in SYNC_GROUPS)["channels"]
    assert "cineon_log" in channels


def test_panel_has_checkbox_default_unchecked():
    from widgets.sliders_panel import SlidersPanel
    panel = SlidersPanel(None)
    assert not panel.cineon_checkbox.isChecked()
    # _attach_cineon is a no-op with no image selected.
    adj = {}
    panel._attach_cineon(adj)
    assert "cineon_log" not in adj
