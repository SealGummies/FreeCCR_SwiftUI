#!/usr/bin/env python3
"""Tests for the Gamma slider (center-point tone curve).

Gamma is a single control point on the composite ("rgb") tone curve that moves
diagonally, perpendicular to the identity line, from the center (127.5, 127.5):
toward the top-left to brighten (+) or the bottom-right to darken (-). It reuses
the Curves editor's monotone-cubic interpolation via ``apply_curves``, so the
result matches dragging that midpoint by hand. Endpoints stay pinned. See
spec/gamma-slider.md.
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.ccr_processor import (  # noqa: E402
    apply_curves, apply_gamma_curve, gamma_curve_points, _GAMMA_MAX_OFFSET,
)


def _solid(value):
    """A small solid 16-bit RGB image at the given level."""
    return np.full((4, 4, 3), value, dtype=np.uint16)


def _to16(v255):
    return int(round(v255 / 255.0 * 65535))


# --- control-point geometry -------------------------------------------------

def test_points_identity_at_zero():
    assert gamma_curve_points(0) == [[0.0, 0.0], [127.5, 127.5], [255.0, 255.0]]


@pytest.mark.parametrize("g", [-100, -37, 20, 50, 100])
def test_points_move_perpendicular_through_center(g):
    """The center point stays on the anti-diagonal (cx + cy == 255) — pure
    perpendicular movement — with the endpoints pinned."""
    pts = gamma_curve_points(g)
    assert pts[0] == [0.0, 0.0] and pts[2] == [255.0, 255.0]
    cx, cy = pts[1]
    assert cx + cy == pytest.approx(255.0)
    off = g / 100.0 * _GAMMA_MAX_OFFSET
    assert cx == pytest.approx(127.5 - off)
    assert cy == pytest.approx(127.5 + off)


def test_positive_moves_top_left_negative_bottom_right():
    """+ lifts the point up-left (cy>cx, above diagonal); - pushes it down-right."""
    up = gamma_curve_points(60)[1]
    assert up[1] > up[0]            # above the diagonal -> brighten
    down = gamma_curve_points(-60)[1]
    assert down[1] < down[0]        # below the diagonal -> darken


def test_sign_symmetry_mirrors_point():
    """+g and -g are mirror images across the diagonal (cx/cy swap)."""
    a = gamma_curve_points(45)[1]
    b = gamma_curve_points(-45)[1]
    assert a[0] == pytest.approx(b[1])
    assert a[1] == pytest.approx(b[0])


# --- rendered effect --------------------------------------------------------

def test_gamma_zero_is_identity():
    img = np.linspace(0, 65535, 4 * 4 * 3).reshape(4, 4, 3).astype(np.uint16)
    assert np.array_equal(apply_gamma_curve(img, 0), img)


def test_gamma_matches_apply_curves():
    """apply_gamma_curve is exactly apply_curves over the synthesized points —
    i.e. it is the same path as the editor, no bespoke math."""
    img = np.linspace(0, 65535, 8 * 8 * 3).reshape(8, 8, 3).astype(np.uint16)
    direct = apply_curves(img, {"rgb": gamma_curve_points(55)})
    assert np.array_equal(apply_gamma_curve(img, 55), direct)


def test_positive_lifts_midtones_negative_lowers():
    mid = _to16(127.5)
    assert int(apply_gamma_curve(_solid(mid), 60)[0, 0, 0]) > mid    # brighter
    assert int(apply_gamma_curve(_solid(mid), -60)[0, 0, 0]) < mid   # darker


def test_control_point_maps_to_its_target():
    """A value at the control point's input maps ~onto its output (the curve
    passes through the node), within LUT quantization."""
    g = 50.0
    cx, cy = gamma_curve_points(g)[1]
    out = int(apply_gamma_curve(_solid(_to16(cx)), g)[0, 0, 0])
    assert abs(out - _to16(cy)) <= 400


@pytest.mark.parametrize("g", [100.0, -100.0, 50.0, -37.0])
def test_endpoints_pinned(g):
    """Pure black and pure white are invariant (the effect stays in the window)."""
    assert int(apply_gamma_curve(_solid(0), g)[0, 0, 0]) == 0
    assert int(apply_gamma_curve(_solid(65535), g)[0, 0, 0]) == 65535


# --- luminance (hue-preserving) mode ---------------------------------------
# apply_gamma_curve(..., luminance=True) applies the SAME tone curve to luminance
# and scales RGB together, so chromaticity (hue + HSV saturation) is preserved
# except where a channel clips. See spec/gamma-luminance-mode.md.

_COLOR = np.array([[[20000, 12000, 6000]]], dtype=np.uint16)   # non-clipping warm


def test_luminance_zero_is_identity():
    img = np.linspace(0, 65535, 4 * 4 * 3).reshape(4, 4, 3).astype(np.uint16)
    assert np.array_equal(apply_gamma_curve(img, 0, luminance=True), img)


@pytest.mark.parametrize("g", [60, -60, 37, -100])
@pytest.mark.parametrize("v255", [0, 1, 40, 128, 200, 255])
def test_luminance_neutral_matches_per_channel(v255, g):
    """Grays render bit-for-bit identically in both modes — toggling the mode
    only changes non-neutral colours."""
    gray = _solid(_to16(v255))
    assert np.array_equal(apply_gamma_curve(gray, g, luminance=True),
                          apply_gamma_curve(gray, g))


@pytest.mark.parametrize("g", [60, -60])
def test_luminance_preserves_channel_ratios(g):
    """A non-clipping colour keeps its R:G:B ratios (uniform scale) — the point
    of the feature."""
    inp = _COLOR[0, 0].astype(float)
    out = apply_gamma_curve(_COLOR, g, luminance=True)[0, 0].astype(float)
    assert np.allclose(out / out.max(), inp / inp.max(), atol=0.01)


def test_per_channel_shifts_channel_ratios():
    """The default per-channel path DOES distort the ratios (this is the hue
    shift the luminance mode avoids)."""
    inp = _COLOR[0, 0].astype(float)
    out = apply_gamma_curve(_COLOR, 60)[0, 0].astype(float)
    assert not np.allclose(out / out.max(), inp / inp.max(), atol=0.01)


def test_luminance_clips_bright_saturated_highlight():
    """The hue guarantee holds only pre-clip: a bright saturated colour with a
    strong +gamma scales its top channel past white, which clips."""
    px = np.array([[[60000, 20000, 10000]]], dtype=np.uint16)
    out = apply_gamma_curve(px, 100, luminance=True)[0, 0]
    assert int(out[0]) == 65535
