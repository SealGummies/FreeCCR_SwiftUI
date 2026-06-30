#!/usr/bin/env python3
"""Tests for flat white balance in the working space
(spec/working-space-white-balance.md).

White balance is a flat per-channel gain applied in the scene-linear working
domain BEFORE the window clamp, so a warm/cool shift lands in recoverable
headroom instead of clipping, and cooling can pull a channel's highlights back
from headroom. Pure numpy core, runs headless.
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.ccr_processor import (  # noqa: E402
    _white_balance_gains,
    _apply_working_space_recovery,
    compute_neutral_temp_tint,
    encode_window,
    adjust_image,
    _WB_TEMP_STRENGTH, _WB_TINT_STRENGTH,
)


@pytest.fixture
def ws_on(monkeypatch):
    monkeypatch.setenv("FREECCR_WORKING_SPACE", "1")


def _px(d_rgb):
    """A 1-pixel windowed base from display values d (R, G, B), overshoot kept."""
    return encode_window(np.array([[list(d_rgb)]], dtype=np.float32))


# --- the flat gain mapping -----------------------------------------------

def test_neutral_is_identity():
    assert _white_balance_gains(0.0, 0.0) == (1.0, 1.0, 1.0)


def test_temperature_gains_match_design():
    # temp s = (slider/100)*0.40 → R*(1+s), B*(1-s)
    assert _white_balance_gains(50.0, 0.0) == pytest.approx((1.2, 1.0, 0.8))
    assert _white_balance_gains(100.0, 0.0) == pytest.approx((1.4, 1.0, 0.6))
    assert _white_balance_gains(-100.0, 0.0) == pytest.approx((0.6, 1.0, 1.4))


def test_tint_pulls_green_against_rb():
    gr, gg, gb = _white_balance_gains(0.0, 50.0)
    assert gg < 1.0 < gr and gr == pytest.approx(gb)   # G down, R/B up symmetrically


def test_balance_factor_scales_tint():
    _, gg1, _ = _white_balance_gains(0.0, 40.0, tint_balance_factor=1.0)
    _, gg2, _ = _white_balance_gains(0.0, 40.0, tint_balance_factor=0.5)
    assert (1.0 - gg2) == pytest.approx((1.0 - gg1) * 0.5)


# --- WB is headroom-safe (the whole point) -------------------------------

def test_cooling_recovers_a_headroom_highlight(ws_on):
    """An R highlight sitting in headroom (d>1) clips to white with no WB, but a
    cooling shift (which scales R down) pulls it back into the window — recovery
    that is impossible when WB runs after the clamp."""
    base = _px((1.30, 0.50, 0.50))           # R in headroom, G/B mid

    flat = _apply_working_space_recovery(base, 0.0)[0, 0, 0]
    assert int(flat) == 65535                # R clips to white with no WB

    cooled = _apply_working_space_recovery(base, 0.0, 0.0, kelvin_shift=-100.0)
    r = int(cooled[0, 0, 0])
    assert r < 65535                          # recovered below white
    assert abs(r - int(0.78 * 65535)) < 400   # ≈ 1.30 * 0.60 (cooling R gain)


def test_warming_overrange_is_recoverable_via_white_point(ws_on):
    """Warming pushes R over white, but because WB is pre-clamp the White Point
    recovery can pull it back — the over-range was kept, not clipped away."""
    base = _px((1.30, 0.50, 0.50))
    blown = _apply_working_space_recovery(base, 0.0, 0.0, kelvin_shift=100.0)
    assert int(blown[0, 0, 0]) == 65535       # warmed R is over white
    rec = _apply_working_space_recovery(base, 0.0, -60.0, kelvin_shift=100.0)
    assert int(rec[0, 0, 0]) < 65535          # White Point pulls the warmed R back


def test_wb_neutral_byte_identical(ws_on):
    base = _px((0.4, 0.6, 0.8))
    np.testing.assert_array_equal(
        _apply_working_space_recovery(base, 0.0, 0.0, 0.0, 0.0),
        _apply_working_space_recovery(base, 0.0))


# --- adjust_image consumes WB once via the pre-stage ---------------------

def test_adjust_image_wb_consumed_once(ws_on):
    """adjust_image(ws_windowed) with only WB equals the recovery helper directly
    — WB is consumed by the pre-stage and not re-applied by the look chain."""
    base = _px((1.10, 0.50, 0.30))
    out = adjust_image(base, kelvin_shift=-80.0, tint_shift=20.0, ws_windowed=True)
    exp = _apply_working_space_recovery(base, 0.0, 0.0,
                                        kelvin_shift=-80.0, tint_shift=20.0)
    np.testing.assert_array_equal(out, exp)


def test_adjust_image_nonws_applies_flat_gain():
    """Non-windowed path: WB is a flat per-channel multiply (no tone weighting)."""
    img = np.full((1, 1, 3), 20000, np.uint16)   # neutral gray
    out = adjust_image(img, kelvin_shift=50.0, ws_windowed=False)[0, 0]
    gr, gg, gb = _white_balance_gains(50.0, 0.0)
    assert abs(int(out[0]) - int(20000 * gr)) <= 2     # R *1.2
    assert abs(int(out[2]) - int(20000 * gb)) <= 2     # B *0.8


# --- neutral picker inverts the flat model exactly -----------------------

def test_neutral_picker_round_trip():
    """compute_neutral_temp_tint returns sliders that neutralize a sampled cast
    under the flat WB (R==G==B), within slider-rounding tolerance."""
    for rgb in [(12000, 10000, 8000), (9000, 10000, 11000), (15000, 14000, 12000)]:
        ts, tn = compute_neutral_temp_tint(*rgb)
        gr, gg, gb = _white_balance_gains(ts, tn)
        r, g, b = rgb[0] * gr, rgb[1] * gg, rgb[2] * gb
        spread = max(r, g, b) - min(r, g, b)
        assert spread < 0.02 * max(r, g, b)   # neutral to within 2%


def test_strength_constants():
    assert _WB_TEMP_STRENGTH == 0.40
    assert _WB_TINT_STRENGTH == 0.26


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
