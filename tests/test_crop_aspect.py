#!/usr/bin/env python3
"""
Unit tests for the pure (Qt-free) crop aspect-ratio + straighten helpers in
core.crop_aspect. See spec/crop-panel.md §5/§8.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core import crop_aspect as c  # noqa: E402

TOL = 1e-9


def _ratio(wh):
    return wh[0] / wh[1]


class TestEnforceRatioSize:
    def test_wide_drag_covers_and_holds_ratio(self):
        # adx dominates: width kept, height derived.
        bw, bh = c.enforce_ratio_size(100, 10, 1.5)
        assert bw == 100
        assert _ratio((bw, bh)) == pytest.approx(1.5)
        assert bh >= 10                       # covers the pointer's y extent

    def test_tall_drag_covers_and_holds_ratio(self):
        bw, bh = c.enforce_ratio_size(10, 100, 1.5)
        assert bh == 100
        assert _ratio((bw, bh)) == pytest.approx(1.5)
        assert bw >= 10

    def test_square_ratio(self):
        assert c.enforce_ratio_size(40, 80, 1.0) == (80, 80)

    def test_uses_absolute_values(self):
        assert c.enforce_ratio_size(-100, -10, 1.5) == c.enforce_ratio_size(100, 10, 1.5)

    def test_none_ratio_passes_through(self):
        assert c.enforce_ratio_size(30, 40, None) == (30, 40)
        assert c.enforce_ratio_size(30, 40, 0) == (30, 40)


class TestFitRatioWithin:
    def test_width_bound(self):
        # 300x300, want 1.5 -> width is the binding side.
        bw, bh = c.fit_ratio_within(300, 300, 1.5)
        assert (bw, bh) == pytest.approx((300, 200))
        assert _ratio((bw, bh)) == pytest.approx(1.5)

    def test_height_bound(self):
        bw, bh = c.fit_ratio_within(300, 300, 0.5)
        assert (bw, bh) == pytest.approx((150, 300))
        assert _ratio((bw, bh)) == pytest.approx(0.5)

    def test_never_exceeds_bounds(self):
        for r in (0.3, 1.0, 1.777, 4.0):
            bw, bh = c.fit_ratio_within(123, 456, r)
            assert bw <= 123 + TOL and bh <= 456 + TOL
            assert _ratio((bw, bh)) == pytest.approx(r)

    def test_degenerate_passes_through(self):
        assert c.fit_ratio_within(100, 50, None) == (100, 50)
        assert c.fit_ratio_within(0, 50, 1.5) == (0, 50)


class TestEffectiveBoxRatio:
    def test_unchanged_for_0_and_180(self):
        assert c.effective_box_ratio(1.5, 0) == pytest.approx(1.5)
        assert c.effective_box_ratio(1.5, 180) == pytest.approx(1.5)

    def test_inverted_for_90_and_270(self):
        assert c.effective_box_ratio(1.5, 90) == pytest.approx(1 / 1.5)
        assert c.effective_box_ratio(1.5, 270) == pytest.approx(1 / 1.5)

    def test_none_passes_through(self):
        assert c.effective_box_ratio(None, 90) is None


class TestOrientedRatio:
    def test_landscape_unchanged(self):
        assert c.oriented_ratio(1.5, True) == pytest.approx(1.5)

    def test_portrait_inverted(self):
        assert c.oriented_ratio(1.5, False) == pytest.approx(1 / 1.5)

    def test_none_passes_through(self):
        assert c.oriented_ratio(None, False) is None


class TestReshapeToRatio:
    def test_fits_within_existing_box(self):
        # Current box 200x100 (ratio 2.0); reshape to 1.5 must fit inside it.
        res = c.reshape_to_ratio((150, 150), (200, 100), (400, 300), 1.5)
        cx, cy, bw, bh = res
        assert (cx, cy) == (150, 150)            # center preserved
        assert bw <= 200 + TOL and bh <= 100 + TOL
        assert _ratio((bw, bh)) == pytest.approx(1.5)

    def test_seeds_centered_in_image_when_no_box(self):
        res = c.reshape_to_ratio((0, 0), None, (400, 300), 1.5)
        cx, cy, bw, bh = res
        assert (cx, cy) == (200, 150)            # image center
        assert bw <= 400 + TOL and bh <= 300 + TOL
        assert _ratio((bw, bh)) == pytest.approx(1.5)

    def test_free_returns_none(self):
        assert c.reshape_to_ratio((0, 0), None, (400, 300), None) is None

    def test_degenerate_image_returns_none(self):
        assert c.reshape_to_ratio((0, 0), None, (0, 300), 1.5) is None


class TestFoldedCropAngle:
    def test_zero_fine_is_noop(self):
        assert c.folded_crop_angle(0.0, 0) == 0.0
        assert c.folded_crop_angle(3.5, 0) == 3.5

    def test_sign_and_scale(self):
        # fine is hundredths of a degree; +200 -> +2.0 deg content CW -> crop -2.0
        assert c.folded_crop_angle(0.0, 200) == pytest.approx(-2.0)
        assert c.folded_crop_angle(0.0, -150) == pytest.approx(1.5)

    def test_combines_with_existing_crop_angle(self):
        assert c.folded_crop_angle(1.0, -150) == pytest.approx(2.5)

    def test_handles_none(self):
        assert c.folded_crop_angle(None, None) == 0.0


class TestPresets:
    def test_keys_unique_and_ratios_sane(self):
        keys = [k for _l, k, _r in c.ASPECT_PRESETS]
        assert len(keys) == len(set(keys))
        for _label, _key, ratio in c.ASPECT_PRESETS:
            if isinstance(ratio, (int, float)):
                assert ratio > 0

    def test_expected_keys_present(self):
        keys = {k for _l, k, _r in c.ASPECT_PRESETS}
        assert {"free", "original", "1:1", "3:2", "16:9", "custom"} <= keys

    def test_preset_for_key(self):
        assert c.preset_for_key("3:2")[2] == pytest.approx(1.5)
        assert c.preset_for_key("nope") is None

    def test_orientation_fixed_keys(self):
        assert {"free", "original", "1:1", "custom"} <= c.ORIENTATION_FIXED_KEYS


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
