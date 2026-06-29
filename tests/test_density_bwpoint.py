#!/usr/bin/env python3
"""
Tests for the two-point B/W Density-inversion option (spec/density-bwpoint-toggle.md).

Covers the pure maths core (`_twopoint_invert`) and the resolution-independent
replay (`apply_bwpoint_normalization`): endpoint agreement, the density log
curve vs the legacy linear curve, refactor bit-identity of the legacy path, the
`ci.get("density", False)` legacy default, and the backend reprocess that flips
loaded two-point conversions while leaving other modes alone.

The GUI reprocess + zoom hi-res replay need a Qt loop / real decode and are
covered by manual verification; the maths + wiring are unit-tested here.
"""
import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core import ccr_processor as P  # noqa: E402


# Anchors (BGR): clear/film-base is HIGH, dense/exposed is LOW.
BLACK = (4000.0, 4000.0, 4000.0)     # clear -> output black
WHITE = (400.0, 400.0, 400.0)        # dense -> output white  (10x density range)


@pytest.fixture(autouse=True)
def _force_legacy_full_range(monkeypatch):
    """These tests pin the LEGACY full-range inversion (clear→0, dense→65535). The
    windowed working space is ON by default now, so explicitly force it OFF here;
    its behaviour is covered in test_working_space.py."""
    monkeypatch.setenv("FREECCR_WORKING_SPACE", "0")


def _plane(value, h=4, w=4):
    return np.full((h, w, 3), value, dtype=np.float32)


# --------------------------------------------------------------------------- #
# Endpoint agreement: both modes map clear->0 (black), dense->65535 (white)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("density", [True, False])
def test_endpoints_clear_to_black_dense_to_white(density):
    clear = P._twopoint_invert(_plane(4000.0), BLACK, WHITE, density)
    dense = P._twopoint_invert(_plane(400.0), BLACK, WHITE, density)
    assert clear.dtype == np.uint16 and clear.shape == (4, 4, 3)
    assert np.all(clear == 0)            # clear film -> black positive
    assert np.all(dense == 65535)        # dense film -> white positive


@pytest.mark.parametrize("black,white", [
    ((52000.0, 48000.0, 39000.0), (9000.0, 9500.0, 7000.0)),   # non-power-of-10
    ((61234.0, 57777.0, 43210.0), (3137.0, 2911.0, 1777.0)),   # irrational-ish Dmax
])
def test_density_dense_endpoint_is_exactly_white_for_any_anchor(black, white):
    # Regression: the per-pixel log is float32 and Dmax is float64; the
    # clip(n,0,1) must still land the dense point at EXACTLY 65535 (no 1-LSB
    # drop to 65534) for non-power-of-10 anchors. See review nit.
    img = np.zeros((1, 1, 3), dtype=np.float32)
    img[0, 0] = white
    out = P._twopoint_invert(img, black, white, density=True)
    assert np.all(out == 65535)


@pytest.mark.parametrize("density", [True, False])
def test_pixels_brighter_than_base_clamp_to_black(density):
    # img above the clear/base value has no density -> clamps to 0.
    out = P._twopoint_invert(_plane(8000.0), BLACK, WHITE, density)
    assert np.all(out == 0)


# --------------------------------------------------------------------------- #
# The curve between the endpoints differs: density is a LOG recovery
# --------------------------------------------------------------------------- #
def test_density_midpoint_is_log_geometric_mean():
    # The geometric mean of base/dense sits at HALF the density range, so a
    # density inversion places it at ~0.5*65535. sqrt(4000*400) ~= 1264.9.
    gmean = float(np.sqrt(4000.0 * 400.0))
    out = P._twopoint_invert(_plane(gmean), BLACK, WHITE, density=True)
    assert abs(int(out[0, 0, 0]) - 32768) <= 200      # ~half scale


def test_linear_and_density_disagree_in_the_midtones():
    # Same input, same endpoints; the interior curve differs by construction.
    mid = _plane(1264.9)
    lin = P._twopoint_invert(mid, BLACK, WHITE, density=False)
    den = P._twopoint_invert(mid, BLACK, WHITE, density=True)
    # Linear arithmetic-midpoint value is far from the density log-midpoint.
    assert abs(int(den[0, 0, 0]) - int(lin[0, 0, 0])) > 5000


def test_density_is_monotonic_decreasing_in_scan_value():
    # Brighter scan (less dense) -> darker positive, for both modes.
    vals = [3000.0, 1500.0, 800.0, 500.0]
    for density in (True, False):
        outs = [int(P._twopoint_invert(_plane(v), BLACK, WHITE, density)[0, 0, 0])
                for v in vals]
        assert outs == sorted(outs)       # increasing as scan value drops


# --------------------------------------------------------------------------- #
# Per-channel independence + colour balance via the base ratio
# --------------------------------------------------------------------------- #
def test_density_per_channel_uses_its_own_anchors():
    black = (4000.0, 3000.0, 2000.0)
    white = (400.0, 300.0, 200.0)
    # Feed each channel its own dense value -> every channel hits white.
    img = np.zeros((1, 1, 3), dtype=np.float32)
    img[0, 0] = white
    out = P._twopoint_invert(img, black, white, density=True)
    assert np.all(out[0, 0] == 65535)


# --------------------------------------------------------------------------- #
# Degenerate / safety
# --------------------------------------------------------------------------- #
def test_density_degenerate_base_not_above_dense_is_black():
    # base <= dense -> no usable density range -> channel 0 (black).
    out = P._twopoint_invert(_plane(1000.0), (500.0, 500.0, 500.0),
                             (500.0, 500.0, 500.0), density=True)
    assert np.all(out == 0)


def test_density_handles_zero_scan_without_nan():
    out = P._twopoint_invert(_plane(0.0), BLACK, WHITE, density=True)
    assert np.isfinite(out).all()
    assert np.all(out == 65535)           # floored, maximal density -> white


# --------------------------------------------------------------------------- #
# Refactor safety: density=False is bit-identical to the legacy two-point math
# --------------------------------------------------------------------------- #
def test_legacy_linear_is_bit_identical_to_reference_formula():
    rng = np.random.default_rng(3)
    img = rng.uniform(300, 4200, size=(6, 5, 3)).astype(np.float32)
    out = P._twopoint_invert(img, BLACK, WHITE, density=False)

    # Reference: the exact prior per-channel linear stretch + 65535-x invert.
    ref = np.empty_like(img)
    for c in range(3):
        p_hi = max(float(BLACK[c]), 1.0)
        p_lo = max(float(WHITE[c]), 1.0)
        denom = p_hi - p_lo
        n = np.clip((img[..., c] - p_lo) / denom * 65535.0, 0, 65535)
        ref[..., c] = n
    ref = (65535.0 - ref).clip(0, 65535).astype(np.uint16)
    assert np.array_equal(out, ref)


def test_apply_bwpoint_normalization_threads_density_flag():
    img = _plane(1264.9).astype(np.uint16)
    den = P.apply_bwpoint_normalization(img, BLACK, WHITE, density=True)
    lin = P.apply_bwpoint_normalization(img, BLACK, WHITE, density=False)
    assert abs(int(den[0, 0, 0]) - int(lin[0, 0, 0])) > 5000
    # default is the legacy linear path (density is opt-in / OFF)
    dflt = P.apply_bwpoint_normalization(img, BLACK, WHITE)
    assert np.array_equal(dflt, lin)


def test_black_point_only_ignores_density_flag():
    # white is None -> default-slope mode; density flag must not matter.
    img = _plane(1500.0).astype(np.uint16)
    a = P.apply_bwpoint_normalization(img, BLACK, None, density=True)
    b = P.apply_bwpoint_normalization(img, BLACK, None, density=False)
    assert np.array_equal(a, b)


# --------------------------------------------------------------------------- #
# Backend: the toggle reprocess flips two-point bw images, spares other modes
# --------------------------------------------------------------------------- #
class _StubImg:
    def __init__(self, ci):
        self.conversion_inputs = ci
        self.converted = ci is not None
        self.source_ops = []
        self.is_duplicate = False
        self.tint_balance_factor = 1.0
        self.file_path = "x.arw"
        self.resized_raw = np.zeros((2, 2, 3), dtype=np.uint16)

    def reload_image(self):           # no real decode in the unit test
        pass

    def update_thumbnail_and_preview(self):
        pass


def test_reprocess_flips_only_twopoint_bw(monkeypatch):
    import core.ccr_backend as B
    from core.ccr_backend import ccr_backend

    recorded = []

    def fake_bw(im, b, w, water_mark=True, density=False, **kw):
        recorded.append((id(im), density))
        return im.resized_raw

    monkeypatch.setattr(B, "ccr_normalize_with_bwpoint", fake_bw)

    two = _StubImg({"mode": "bw", "bw": ((4000, 4000, 4000), (400, 400, 400)),
                    "fine_rot": 0, "density": False})           # legacy linear
    ref = _StubImg({"mode": "ref", "ref": (1, 2, 3, 4), "fine_rot": 0})
    blackonly = _StubImg({"mode": "bw", "bw": ((4000, 4000, 4000), None),
                          "fine_rot": 0})

    saved_imgs = ccr_backend.images
    saved_mode = ccr_backend.density_bwpoint
    try:
        ccr_backend.images = [two, ref, blackonly]
        ccr_backend.density_bwpoint = True
        ccr_backend.reprocess_all_for_density_bwpoint_change()
    finally:
        ccr_backend.images = saved_imgs
        ccr_backend.density_bwpoint = saved_mode

    # Only the two-point bw image was re-converted, with the NEW density=True.
    assert recorded == [(id(two), True)]
    assert two.conversion_inputs["density"] is True
    # Other modes untouched.
    assert ref.conversion_inputs == {"mode": "ref", "ref": (1, 2, 3, 4), "fine_rot": 0}
    assert "density" not in blackonly.conversion_inputs


def test_reprocess_can_turn_density_off(monkeypatch):
    import core.ccr_backend as B
    from core.ccr_backend import ccr_backend

    recorded = []
    monkeypatch.setattr(B, "ccr_normalize_with_bwpoint",
                        lambda im, b, w, water_mark=True, density=False, **kw:
                        (recorded.append(density) or im.resized_raw))

    img = _StubImg({"mode": "bw", "bw": ((4000, 4000, 4000), (400, 400, 400)),
                    "fine_rot": 0, "density": True})
    saved = ccr_backend.images
    saved_mode = ccr_backend.density_bwpoint
    try:
        ccr_backend.images = [img]
        ccr_backend.density_bwpoint = False
        ccr_backend.reprocess_all_for_density_bwpoint_change()
    finally:
        ccr_backend.images = saved
        ccr_backend.density_bwpoint = saved_mode
    assert recorded == [False]
    assert img.conversion_inputs["density"] is False
