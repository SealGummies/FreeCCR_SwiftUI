"""Unit tests for the experimental NamiColor conversion pipeline.

Pure-math, no Qt. Covers the Cineon film-log decode, the NamiColor channel
transform (inversion + density), the Adobe->Rec.2020 matrix neutrality, the
full namicolor_process frame pipeline, and slider monotonicity.
See spec/namicolor-conversion.md.
"""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.ccr_processor import (  # noqa: E402
    M_ADOBE2REC2020,
    cineon_film_log_to_linear,
    namicolor_channel_transform,
    namicolor_process,
    _CINEON_BLACK,
    _CINEON_WHITE,
)


class TestCineonDecode(unittest.TestCase):
    def test_anchors(self):
        # 685/1023 -> ~1.0 (90% white), 95/1023 -> ~0.0 (Dmin).
        white = float(cineon_film_log_to_linear(np.float32(_CINEON_WHITE / 1023.0)))
        black = float(cineon_film_log_to_linear(np.float32(_CINEON_BLACK / 1023.0)))
        self.assertAlmostEqual(white, 1.0, places=4)
        self.assertAlmostEqual(black, 0.0, places=3)

    def test_monotonic(self):
        cv = np.linspace(0.0, 1.0, 64).astype(np.float32)
        lin = cineon_film_log_to_linear(cv)
        self.assertTrue(np.all(np.diff(lin) > 0))

    def test_superwhite(self):
        # Code values above the white point exceed scene-linear 1.0.
        self.assertGreater(float(cineon_film_log_to_linear(np.float32(800.0 / 1023.0))), 1.0)


class TestNamiColorChannelTransform(unittest.TestCase):
    def _neutral(self):
        return {}  # all sliders default 0 -> NamiColor neutral

    def test_inversion_is_decreasing(self):
        # NamiColor inverts: a brighter scan pixel (scene shadow on a negative)
        # must yield a LOWER positive code value. So the transform is
        # monotonically DECREASING in the linear input.
        x = np.linspace(0.02, 1.0, 50).astype(np.float32)
        rgb = np.stack([x, x, x], axis=-1)  # (50, 3)
        out = namicolor_channel_transform(rgb, self._neutral())
        for c in range(3):
            self.assertTrue(np.all(np.diff(out[:, c]) < 0),
                            f"channel {c} not strictly decreasing")

    def test_finite(self):
        rgb = np.array([[1e-6, 0.5, 1.0]], dtype=np.float32).reshape(1, 3)
        out = namicolor_channel_transform(rgb, self._neutral())
        self.assertTrue(np.all(np.isfinite(out)))

    def test_r_gain_brightens_red(self):
        # Raising R Gain shrinks the denominator -> larger red code value.
        rgb = np.full((8, 3), 0.3, dtype=np.float32)
        base = namicolor_channel_transform(rgb, {})
        boosted = namicolor_channel_transform(rgb, {'ch_r_gain': 80})
        self.assertTrue(np.all(boosted[:, 0] > base[:, 0]))
        # Green/blue untouched.
        np.testing.assert_allclose(boosted[:, 1], base[:, 1], rtol=1e-5)

    def test_master_shift_lifts_all_channels(self):
        rgb = np.full((8, 3), 0.3, dtype=np.float32)
        base = namicolor_channel_transform(rgb, {})
        lifted = namicolor_channel_transform(rgb, {'ch_master_shift': 60})
        self.assertTrue(np.all(lifted > base))


class TestAdobeMatrix(unittest.TestCase):
    def test_neutral_grey_stays_neutral(self):
        grey = np.full((4, 3), 0.5, dtype=np.float32)
        out = grey @ M_ADOBE2REC2020.T
        spread = float(out.max(axis=1).mean() - out.min(axis=1).mean())
        self.assertLess(spread, 0.02)  # primaries differ but grey stays ~neutral


class TestNamiColorProcess(unittest.TestCase):
    def test_valid_uint16_output(self):
        rng = np.random.default_rng(0)
        img = (rng.random((32, 32, 3)) * 4000 + 200).astype(np.uint16)
        out = namicolor_process(img, {})
        self.assertEqual(out.dtype, np.uint16)
        self.assertEqual(out.shape, img.shape)
        self.assertTrue(np.all(out <= 65535))

    def test_inversion_dark_negative_to_bright_positive(self):
        # A DARK scan region (low linear = dense film = scene highlight on the
        # negative... actually low transmittance = scene highlight) inverts to a
        # BRIGHT positive; a BRIGHT scan region inverts to a DARK positive.
        dark = np.full((8, 8, 3), 300, dtype=np.uint16)    # low linear
        bright = np.full((8, 8, 3), 40000, dtype=np.uint16)  # high linear
        out_dark = namicolor_process(dark, {})
        out_bright = namicolor_process(bright, {})
        self.assertGreater(float(out_dark.mean()), float(out_bright.mean()))

    def test_neutral_input_stays_near_neutral(self):
        # ~0.3 linear: a representative negative base level that lands mid-range
        # under the neutral NamiColor transform (won't clip to Cineon superwhite).
        img = np.full((8, 8, 3), 20000, dtype=np.uint16)
        out = namicolor_process(img, {})
        ch = out.reshape(-1, 3).mean(axis=0)
        spread = float(ch.max() - ch.min())
        # A neutral grey negative should not develop a huge color cast.
        self.assertLess(spread, 6000)

    def test_brightness_slider_changes_output(self):
        img = np.full((8, 8, 3), 20000, dtype=np.uint16)  # mid-range, non-clipping
        base = namicolor_process(img, {})
        brighter = namicolor_process(img, {'brightness': 80})
        self.assertNotAlmostEqual(float(base.mean()), float(brighter.mean()), places=1)


class TestMonochromeGuard(unittest.TestCase):
    def test_namicolor_skips_monochrome(self):
        # A monochrome scan must NOT route through NamiColor (its Adobe->Rec.2020
        # matrix + per-channel log would tint a grayscale image).
        from core.ccr_image import CCRImage
        from core.ccr_processor import NAMICOLOR_EXPERIMENT
        img = CCRImage.__new__(CCRImage)
        img.is_monochrome = False
        self.assertEqual(img._namicolor_active(), NAMICOLOR_EXPERIMENT)
        img.is_monochrome = True
        self.assertFalse(img._namicolor_active())


if __name__ == '__main__':
    unittest.main()
