#!/usr/bin/env python3
"""
Tests for the optional white point / calibrated default slope
(spec/optional-white-point-default-slope.md).

When no white point is sampled, the B/W-point conversion uses a DENSITY-space
inversion with a baked SINGLE SCALAR slope, applied to the black point alone:
    out = clip(DEFAULT_DENSITY_SLOPE * max(log10(base/img), 0), 0, 1) ** (1/gamma)
This is robust to light-source changes: log10(base/img) cancels any per-channel
light scaling when the black point is re-sampled, and a scalar slope bakes in no
per-channel colour. When a white point IS supplied, the original linear
two-point math is used unchanged.

apply_bwpoint_normalization is pure numpy (no Qt), so these run headless.
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.ccr_processor import (  # noqa: E402
    apply_bwpoint_normalization,
    DEFAULT_DENSITY_SLOPE,
    DEFAULT_DENSITY_GAMMA,
)

# A calibration black/white pair (BGR), from a real run.
BASE_BGR = (11385.0, 14569.0, 7022.0)    # clear film base (high scan values)
WHITE_BGR = (760.0, 420.0, 117.0)        # dense area (low scan values)


def _img(pixels_bgr, dtype=np.uint16):
    """Build a (1, N, 3) image from a list of (B,G,R) tuples."""
    return np.array([pixels_bgr], dtype=dtype)


def _expected_default(img_bgr, base_bgr):
    """Reference implementation of the density default-slope inversion."""
    out = []
    for c in range(3):
        base = max(float(base_bgr[c]), 1.0)
        v = max(float(img_bgr[c]), 1.0)
        d = max(float(np.log10(base / v)), 0.0)
        o = np.clip(DEFAULT_DENSITY_SLOPE * d, 0.0, 1.0)
        if DEFAULT_DENSITY_GAMMA != 1.0:
            o = o ** (1.0 / DEFAULT_DENSITY_GAMMA)
        out.append(int(o * 65535.0))
    return out


def test_default_slope_is_positive_scalar():
    assert isinstance(DEFAULT_DENSITY_SLOPE, (int, float))
    assert DEFAULT_DENSITY_SLOPE > 0
    assert DEFAULT_DENSITY_GAMMA > 0


def test_default_mode_base_maps_to_black():
    """A pixel equal to the film base has density 0 → black in every channel."""
    out = apply_bwpoint_normalization(_img([tuple(map(round, BASE_BGR))]), BASE_BGR, None)
    assert out.dtype == np.uint16
    np.testing.assert_array_less(out[0, 0].astype(int), 3)


def test_default_mode_matches_density_formula():
    """Output matches out = slope * max(log10(base/img), 0), clipped + scaled."""
    px = [(3000, 2500, 900), (1500, 1200, 400), (500, 300, 120)]
    out = apply_bwpoint_normalization(_img(px), BASE_BGR, None).astype(int)
    for i, p in enumerate(px):
        exp = _expected_default(p, BASE_BGR)
        for c in range(3):
            assert abs(out[0, i, c] - exp[c]) <= 1, (i, c, out[0, i, c], exp[c])


def test_default_mode_white_cut():
    """A pixel at density 1/slope maps to ~white (the brightest cut)."""
    factor = 10.0 ** (1.0 / DEFAULT_DENSITY_SLOPE)        # base/img giving density 1/slope
    img = tuple(max(1, round(b / factor)) for b in BASE_BGR)
    out = apply_bwpoint_normalization(_img([img]), BASE_BGR, None)[0, 0].astype(int)
    for c in range(3):
        assert out[c] >= 65400, f"channel {c} expected ~white, got {out[c]}"


def test_default_mode_clamps_both_ends():
    """img above base → density < 0 → black; extremely dense img → white."""
    above_base = _img([(20000, 30000, 15000)])   # brighter than base
    very_dense = _img([(1, 1, 1)])
    assert np.all(apply_bwpoint_normalization(above_base, BASE_BGR, None)[0, 0] == 0)
    assert np.all(apply_bwpoint_normalization(very_dense, BASE_BGR, None)[0, 0] == 65535)


def test_default_mode_monotonic():
    """As img decreases (denser), the inverted value increases monotonically."""
    vals = np.linspace(200, BASE_BGR[1], 25).astype(np.uint16)
    px = [(int(BASE_BGR[0]), int(g), int(BASE_BGR[2])) for g in vals]
    out = apply_bwpoint_normalization(_img(px), BASE_BGR, None)[0, :, 1].astype(int)
    assert np.all(np.diff(out) <= 0)   # img ascends base..dark → output descends


def test_default_mode_light_source_invariant():
    """The crux of the design: a different light source scales base AND img by a
    per-channel factor k. Because the black point is re-sampled (so base scales
    too), log10(base/img) is unchanged → the density output is identical. This is
    why the default slope does NOT colour-shift across light sources."""
    base = np.array([11385.0, 14569.0, 7022.0], dtype=np.float32)
    px = np.array([3000.0, 2500.0, 900.0], dtype=np.float32)
    out1 = apply_bwpoint_normalization(np.array([[px]], dtype=np.float32),
                                       tuple(base), None).astype(int)
    k = np.array([1.7, 0.6, 2.3], dtype=np.float32)   # a very different light source
    out2 = apply_bwpoint_normalization(np.array([[px * k]], dtype=np.float32),
                                       tuple(base * k), None).astype(int)
    assert np.all(np.abs(out1 - out2) <= 1), (out1, out2)


def test_default_mode_scalar_has_no_baked_color():
    """A neutral-in-density input (equal density per channel above its own base)
    renders neutral, regardless of the per-channel base values — the scalar slope
    bakes in no colour, so balance comes only from the per-channel base divide."""
    base = (11385.0, 14569.0, 7022.0)
    # Pick img so log10(base/img) is the SAME (=0.5) in every channel.
    img = tuple(b / (10.0 ** 0.5) for b in base)
    out = apply_bwpoint_normalization(np.array([[img]], dtype=np.float32),
                                      base, None)[0, 0].astype(int)
    assert out.max() - out.min() <= 1   # equal density → neutral output


def test_whitepoint_mode_matches_closed_form():
    """Legacy LINEAR two-point path (density=False): output equals the original
    formula inverted = clip(65535*(img-white)/(base-white)). The density default
    is covered in test_density_bwpoint.py. See spec/density-bwpoint-toggle.md."""
    test_px = [(5000, 6000, 3000), (2000, 3000, 1500), (800, 700, 200)]
    out = apply_bwpoint_normalization(_img(test_px), BASE_BGR, WHITE_BGR,
                                      density=False).astype(int)
    for i, px in enumerate(test_px):
        for c in range(3):
            base = max(BASE_BGR[c], 1.0)
            white = max(WHITE_BGR[c], 1.0)
            norm = np.clip((px[c] - white) / (base - white) * 65535.0, 0, 65535)
            expected = int(np.clip(65535.0 - norm, 0, 65535))
            assert abs(out[0, i, c] - expected) <= 1, (i, c, out[0, i, c], expected)


def test_whitepoint_endpoints():
    """base -> black, white -> white in the two-point mode."""
    img = _img([tuple(map(round, BASE_BGR)), tuple(map(round, WHITE_BGR))])
    out = apply_bwpoint_normalization(img, BASE_BGR, WHITE_BGR).astype(int)
    assert np.all(out[0, 0] < 3)        # base -> ~black
    assert np.all(out[0, 1] > 65500)    # white -> ~white


def test_white_argument_optional():
    """white_point_bgr defaults to None (default-slope mode) when omitted."""
    img = _img([(1000, 3000, 2500)])
    a = apply_bwpoint_normalization(img, BASE_BGR)        # omitted
    b = apply_bwpoint_normalization(img, BASE_BGR, None)  # explicit
    np.testing.assert_array_equal(a, b)


def test_catalog_roundtrip_no_white_point():
    """A bw conversion snapshot with no white point survives JSON round-trip."""
    from core.catalog import _ci_to_json, _ci_from_json
    ci = {"mode": "bw", "bw": ((4862.5, 12124.0, 10493.0), None), "fine_rot": 0}
    j = _ci_to_json(ci)
    assert j["bw"][0] == [4862.5, 12124.0, 10493.0]
    assert j["bw"][1] is None              # white serialized as null, not crash
    back = _ci_from_json(j)
    assert back["bw"][0] == (4862.5, 12124.0, 10493.0)
    assert back["bw"][1] is None           # restored as None → default-slope mode


def test_catalog_roundtrip_with_white_point():
    """The two-point snapshot still round-trips unchanged."""
    from core.catalog import _ci_to_json, _ci_from_json
    ci = {"mode": "bw", "bw": ((1.0, 2.0, 3.0), (4.0, 5.0, 6.0)), "fine_rot": 0}
    back = _ci_from_json(_ci_to_json(ci))
    assert back["bw"] == ((1.0, 2.0, 3.0), (4.0, 5.0, 6.0))


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
