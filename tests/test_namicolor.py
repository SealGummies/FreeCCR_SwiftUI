"""Unit tests for the NamiColor B/W-point conversion (no Qt).

Covers the Cineon decode, the invert + auto-levels transform, the Adobe->Rec.2020
matrix, the full namicolor_process frame pipeline, and the new B/W-point -> anchor
mapping (black point -> 95/1023, white point -> 685/1023, percentile fallback).
See spec/namicolor-bwpoint-conversion.md.
"""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.ccr_processor import (  # noqa: E402
    M_ADOBE2REC2020,
    NAMICOLOR_CONVERSION,
    cineon_film_log_to_linear,
    namicolor_channel_transform,
    namicolor_process,
    namicolor_anchors,
    _CINEON_BLACK,
    _CINEON_WHITE,
)


def _gradient_negative(h=48, w=48, scale=(1.0, 1.0, 1.0)):
    """A synthetic negative with real tonal RANGE. Linear scan ramps left->right
    from 0.05 (clear/shadow) to 0.6 (dense/highlight); `scale` offsets each
    channel to fake a per-channel cast."""
    ramp = np.linspace(0.05, 0.6, w, dtype=np.float32)
    img = np.empty((h, w, 3), np.float32)
    for c in range(3):
        img[..., c] = ramp[None, :] * np.float32(scale[c])
    return np.clip(img * 65535.0, 0, 65535).astype(np.uint16)


class TestCineonDecode(unittest.TestCase):
    def test_anchors(self):
        white = float(cineon_film_log_to_linear(np.float32(_CINEON_WHITE / 1023.0)))
        black = float(cineon_film_log_to_linear(np.float32(_CINEON_BLACK / 1023.0)))
        self.assertAlmostEqual(white, 1.0, places=4)
        self.assertAlmostEqual(black, 0.0, places=3)

    def test_monotonic(self):
        cv = np.linspace(0.0, 1.0, 64).astype(np.float32)
        self.assertTrue(np.all(np.diff(cineon_film_log_to_linear(cv)) > 0))


class TestAdobeMatrix(unittest.TestCase):
    def test_neutral_grey_stays_neutral(self):
        grey = np.full((4, 3), 0.5, dtype=np.float32)
        out = grey @ M_ADOBE2REC2020.T
        self.assertLess(float(out.max(axis=1).mean() - out.min(axis=1).mean()), 0.02)


class TestProcess(unittest.TestCase):
    def test_valid_uint16(self):
        rng = np.random.default_rng(0)
        img = (rng.random((32, 32, 3)) * 4000 + 200).astype(np.uint16)
        out = namicolor_process(img, {}, namicolor_anchors(img))
        self.assertEqual(out.dtype, np.uint16)
        self.assertEqual(out.shape, img.shape)

    def test_inversion(self):
        img = _gradient_negative()
        out = namicolor_process(img, {}, namicolor_anchors(img))
        # darkest scan column -> bright positive; brightest scan -> dark positive.
        self.assertGreater(float(out[:, 0].mean()), float(out[:, -1].mean()))

    def test_auto_fit_fills_range(self):
        img = _gradient_negative()
        out = namicolor_process(img, {}, namicolor_anchors(img))
        self.assertLess(float(out.min()), 6000)
        self.assertGreater(float(out.max()), 60000)

    def test_auto_neutralizes_cast(self):
        img = _gradient_negative(scale=(1.0, 0.7, 0.4))
        out = namicolor_process(img, {}, namicolor_anchors(img))
        m = out.reshape(-1, 3).mean(axis=0)
        self.assertLess(float(m.max() - m.min()), 1500)

    def test_slider_refines(self):
        img = _gradient_negative()
        a = namicolor_anchors(img)
        base = namicolor_process(img, {}, a)
        boosted = namicolor_process(img, {'ch_r_gain': 60}, a)
        self.assertGreater(float(boosted[..., 0].mean()), float(base[..., 0].mean()))


class TestBWPointAnchors(unittest.TestCase):
    def test_unset_falls_back_to_percentile(self):
        img = _gradient_negative()
        p_lo, p_hi = namicolor_anchors(img, None, None)
        self.assertTrue(np.all(p_hi > p_lo))

    def test_black_point_sets_low_anchor(self):
        img = _gradient_negative()
        base_lo, base_hi = namicolor_anchors(img, None, None)
        # A clear-base (HIGH scan value) black point -> a LOWER density anchor.
        bp = (60000, 60000, 60000)
        lo, hi = namicolor_anchors(img, bp, None)
        self.assertFalse(np.allclose(lo, base_lo))   # black point overrode p_lo
        np.testing.assert_allclose(hi, base_hi, rtol=1e-5)  # white still percentile

    def test_white_point_sets_high_anchor(self):
        img = _gradient_negative()
        base_lo, base_hi = namicolor_anchors(img, None, None)
        wp = (3000, 3000, 3000)   # dense area, low scan value -> high density
        lo, hi = namicolor_anchors(img, None, wp)
        np.testing.assert_allclose(lo, base_lo, rtol=1e-5)
        self.assertFalse(np.allclose(hi, base_hi))

    def test_point_channel_order_no_rb_swap(self):
        # The sampled point is RGB-order (resized_raw is RGB). A point bright in R
        # and dark in B must give a LOWER density anchor for R than for B — guards
        # against an R/B reversal in _point_density.
        from core.ccr_processor import _point_density
        d = _point_density((60000, 20000, 4000))   # (R high, G mid, B low)
        self.assertLess(float(d[0]), float(d[2]))

    def test_high_anchor_kept_above_low(self):
        # A mis-sampled pair (black darker than white) must not invert the channel.
        img = _gradient_negative()
        lo, hi = namicolor_anchors(img, (3000, 3000, 3000), (60000, 60000, 60000))
        self.assertTrue(np.all(hi > lo))

    def test_points_map_to_cineon_anchors(self):
        # A pixel equal to the BLACK point converts to ~Cineon black (display dark);
        # a pixel equal to the WHITE point to ~Cineon white (display bright).
        bp = (52000, 52000, 52000)   # clear base (high) -> 95/1023
        wp = (4000, 4000, 4000)      # dense (low)        -> 685/1023
        img = np.zeros((4, 4, 3), np.uint16)
        img[:] = 20000
        img[0, 0] = bp               # a black-point pixel
        img[0, 1] = wp               # a white-point pixel
        out = namicolor_process(img, {}, namicolor_anchors(img, bp, wp))
        self.assertLess(int(out[0, 0].mean()), 4000)      # black point -> dark
        self.assertGreater(int(out[0, 1].mean()), 60000)  # white point -> bright


class TestMonochromeGuard(unittest.TestCase):
    def test_namicolor_skips_monochrome(self):
        from core.ccr_image import CCRImage
        img = CCRImage.__new__(CCRImage)
        img.is_monochrome = True
        self.assertFalse(img._namicolor_active())
        img.is_monochrome = False
        self.assertEqual(img._namicolor_active(), bool(NAMICOLOR_CONVERSION))


if __name__ == '__main__':
    unittest.main()
