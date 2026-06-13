#!/usr/bin/env python3
"""
Tests for the Color Profile feature (Color / Black & White).

Black & White maps the adjusted RGB result to a single luminance channel
(Rec.601 weights) replicated across R/G/B, so it renders and exports as a
neutral grayscale image while keeping the 3-channel shape downstream expects.
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.ccr_image import CCRImage  # noqa: E402


def _stub_image(color_profile="color", adjustment_settings=None):
    """CCRImage without the heavy file-loading __init__."""
    img = CCRImage.__new__(CCRImage)
    img.adjustment_settings = adjustment_settings or {}
    img.color_profile = color_profile
    img.contrast_base = 0
    img.temperature_base = 0
    img.brightness_base = 0
    img.tint_balance_factor = 1.0
    return img


def _color_image():
    img = np.zeros((2, 3, 3), dtype=np.uint16)
    img[0, 0] = (60000, 30000, 10000)   # saturated orange
    img[0, 1] = (10000, 50000, 20000)   # saturated green
    img[0, 2] = (30000, 30000, 30000)   # neutral gray
    img[1, 0] = (0, 0, 0)               # black
    img[1, 1] = (65535, 65535, 65535)   # white
    img[1, 2] = (200, 100, 50)          # dark saturated
    return img


def _is_neutral(img):
    """True when every pixel has R == G == B (i.e. truly grayscale)."""
    return np.array_equal(img[..., 0], img[..., 1]) and np.array_equal(img[..., 1], img[..., 2])


class TestColorProfile:
    def test_color_profile_is_identity_no_adjustments(self):
        img = _color_image()
        out = _stub_image("color").apply_adjustments(img)
        # No adjustments + Color profile = unchanged input (same object).
        assert out is img

    def test_bw_no_adjustments_produces_grayscale(self):
        img = _color_image()
        out = _stub_image("bw").apply_adjustments(img)
        assert out.shape == img.shape
        assert out.dtype == img.dtype
        assert _is_neutral(out)

    def test_bw_does_not_mutate_input(self):
        img = _color_image()
        before = img.copy()
        _stub_image("bw").apply_adjustments(img)
        np.testing.assert_array_equal(img, before)

    def test_bw_luminance_matches_rec601_weights(self):
        img = _color_image()
        out = _stub_image("bw").apply_adjustments(img)
        expected = (img[..., 0] * 0.299 + img[..., 1] * 0.587
                    + img[..., 2] * 0.114)
        # cv2's RGB2GRAY rounds; allow a 1-LSB tolerance.
        np.testing.assert_allclose(out[..., 0].astype(np.int64),
                                   expected, atol=1)

    def test_bw_preserves_neutral_extremes(self):
        img = _color_image()
        out = _stub_image("bw").apply_adjustments(img)
        np.testing.assert_array_equal(out[1, 0], (0, 0, 0))            # black
        np.testing.assert_array_equal(out[1, 1], (65535, 65535, 65535))  # white

    def test_bw_with_adjustments_is_grayscale(self):
        img = _color_image()
        out = _stub_image("bw", {"saturation": 50, "contrast": 20}).apply_adjustments(img)
        assert out.shape == img.shape
        assert out.dtype == np.uint16
        assert _is_neutral(out)

    def test_color_with_adjustments_keeps_color(self):
        img = _color_image()
        out = _stub_image("color", {"saturation": 50}).apply_adjustments(img)
        # A saturated image boosted in Color mode must NOT collapse to gray.
        assert not _is_neutral(out)


class TestColorProfilePersistence:
    def test_undo_round_trips_color_profile(self):
        img = _stub_image("color")
        img.crop_rect = None
        img.crop_angle = 0.0
        img.rotation_angle = 0
        img.fine_rotation_angle = 0
        img.horizontal_mirrored = False
        img.vertical_mirrored = False
        img.undo_stack = []

        img.push_undo_state()
        img.color_profile = "bw"
        assert img.pop_undo_state() is True
        assert img.color_profile == "color"

    def test_catalog_serialize_round_trip(self):
        from core.catalog import serialize_image
        img = _stub_image("bw")
        img.display_name = None
        img.is_duplicate = False
        img.slice_group = None
        img.slice_parent = None
        img.source_ops = []
        img.converted = False
        img.conversion_inputs = None
        img.crop_rect = None
        img.crop_angle = 0.0
        img.rotation_angle = 0
        img.fine_rotation_angle = 0
        img.horizontal_mirrored = False
        img.vertical_mirrored = False
        img.reference_frame = None
        state = serialize_image(img)
        assert state["color_profile"] == "bw"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
