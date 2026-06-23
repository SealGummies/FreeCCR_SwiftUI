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
    namicolor_auto_anchors,
    _CINEON_BLACK,
    _CINEON_WHITE,
)


def _gradient_negative(h=48, w=48, scale=(1.0, 1.0, 1.0)):
    """A synthetic negative with real tonal RANGE (auto-levels needs range).
    Linear scan ramps left->right from 0.05 (clear/shadow) to 0.6 (dense/highlight);
    `scale` offsets each channel's level to fake a per-channel cast."""
    ramp = np.linspace(0.05, 0.6, w, dtype=np.float32)
    img = np.empty((h, w, 3), np.float32)
    for c in range(3):
        img[..., c] = ramp[None, :] * np.float32(scale[c])
    return np.clip(img * 65535.0, 0, 65535).astype(np.uint16)


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
        # A DARK scan region (low linear) inverts to a BRIGHT positive; a BRIGHT
        # scan region inverts to a DARK positive. Left of the ramp is the darkest
        # scan, right is the brightest.
        out = namicolor_process(_gradient_negative(), {})
        left = float(out[:, 0].mean())     # darkest scan column
        right = float(out[:, -1].mean())   # brightest scan column
        self.assertGreater(left, right)

    def test_brightness_slider_changes_output(self):
        img = _gradient_negative()
        base = namicolor_process(img, {})
        brighter = namicolor_process(img, {'brightness': 80})
        self.assertNotAlmostEqual(float(base.mean()), float(brighter.mean()), places=1)


class TestAutoLevels(unittest.TestCase):
    def test_anchors_low_below_high(self):
        p_lo, p_hi = namicolor_auto_anchors(_gradient_negative())
        self.assertTrue(np.all(p_hi > p_lo))

    def test_auto_fit_fills_display_range(self):
        # Auto-fit maps each channel's darkest->95 (display ~black) and
        # brightest->685 (display ~white), so the output spans the range.
        out = namicolor_process(_gradient_negative(), {})
        self.assertLess(float(out.min()), 6000)      # near black
        self.assertGreater(float(out.max()), 60000)  # near white

    def test_auto_neutralizes_per_channel_cast(self):
        # A per-channel level cast (orange mask): R/G/B sit in different linear
        # ranges. Per-channel auto-fit places each into the SAME Cineon range, so
        # the output channels come out balanced (cast neutralized).
        cast = namicolor_process(_gradient_negative(scale=(1.0, 0.7, 0.4)), {})
        chan_means = cast.reshape(-1, 3).mean(axis=0)
        spread = float(chan_means.max() - chan_means.min())
        self.assertLess(spread, 1500)

    def test_passed_anchors_match_self_computed(self):
        # Passing cached anchors must match letting namicolor_process compute them.
        img = _gradient_negative(scale=(1.0, 0.8, 0.5))
        anchors = namicolor_auto_anchors(img)
        a = namicolor_process(img, {}, auto_anchors=anchors)
        b = namicolor_process(img, {})
        np.testing.assert_array_equal(a, b)

    def test_sliders_refine_on_top_of_auto(self):
        # All sliders 0 = pure auto; a per-channel gain nudges that channel.
        img = _gradient_negative()
        base = namicolor_process(img, {})
        boosted = namicolor_process(img, {'ch_r_gain': 60})
        self.assertGreater(float(boosted[..., 0].mean()), float(base[..., 0].mean()))


class TestAutoLevelsCropRegion(unittest.TestCase):
    def test_anchors_measured_over_crop_region(self):
        # Auto-levels measures the "actual image area" = the crop region, so a
        # film-holder border excluded by the crop must NOT steal the white point.
        from core.ccr_image import CCRImage
        from core.ccr_processor import NAMICOLOR_AUTO
        if not NAMICOLOR_AUTO:
            self.skipTest("auto-levels disabled")
        img = CCRImage.__new__(CCRImage)
        full = _gradient_negative(h=40, w=40)
        full[:, 20:] = 200            # opaque 'holder' (dark = highest density)
        img.resized_raw = full
        img.crop_rect = None
        img.crop_angle = 0.0
        _, p_hi_full = img._namicolor_auto_anchors()
        # Crop to the left (image) half — excludes the holder.
        img.crop_rect = (0.0, 0.0, 0.5, 1.0)
        _, p_hi_crop = img._namicolor_auto_anchors()
        # The holder pushes the full-frame high anchor up; cropping it out drops it.
        self.assertTrue(all(p_hi_crop[c] < p_hi_full[c] for c in range(3)))


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
