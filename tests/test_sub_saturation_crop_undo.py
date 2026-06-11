#!/usr/bin/env python3
"""
Tests for the subtracted (film-density) saturation slider, the non-destructive
crop helper, and the per-image undo stack.
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.ccr_processor import adjust_image, adjust_image_opencl, apply_crop_to_image  # noqa: E402
from core.ccr_image import CCRImage  # noqa: E402


def _test_image():
    """Small image with neutral, saturated, black, and white pixels."""
    img = np.zeros((2, 3, 3), dtype=np.uint16)
    img[0, 0] = (30000, 30000, 30000)   # neutral gray
    img[0, 1] = (60000, 30000, 10000)   # saturated orange
    img[0, 2] = (10000, 50000, 20000)   # saturated green
    img[1, 0] = (0, 0, 0)               # black
    img[1, 1] = (65535, 65535, 65535)   # white
    img[1, 2] = (200, 100, 50)          # dark saturated
    return img


class TestSubtractedSaturation:
    def test_zero_is_identity(self):
        img = _test_image()
        out = adjust_image(img, sub_saturation=0)
        assert np.array_equal(out, img)

    def test_neutral_pixels_unchanged(self):
        img = _test_image()
        for amount in (-100, -50, 25, 100):
            out = adjust_image(img, sub_saturation=amount)
            # gray, black, and white pixels must stay neutral and unmoved
            for pos in ((0, 0), (1, 0), (1, 1)):
                np.testing.assert_allclose(
                    out[pos].astype(np.int64), img[pos].astype(np.int64), atol=2)

    def test_positive_darkens_non_dominant_channels(self):
        img = _test_image()
        out = adjust_image(img, sub_saturation=50).astype(np.int64)
        src = img.astype(np.int64)
        # Dominant channel is pinned; the others are darkened (denser color)
        assert abs(out[0, 1, 0] - src[0, 1, 0]) <= 2          # R (max) pinned
        assert out[0, 1, 1] < src[0, 1, 1] - 100              # G darkened
        assert out[0, 1, 2] < src[0, 1, 2] - 100              # B darkened
        assert abs(out[0, 2, 1] - src[0, 2, 1]) <= 2          # G (max) pinned
        assert out[0, 2, 0] < src[0, 2, 0] - 100
        assert out[0, 2, 2] < src[0, 2, 2] - 100
        # Total light is reduced: saturation comes from absorption
        assert out[0, 1].sum() < src[0, 1].sum()

    def test_negative_desaturates_toward_dominant(self):
        img = _test_image()
        out = adjust_image(img, sub_saturation=-50).astype(np.int64)
        src = img.astype(np.int64)
        assert abs(out[0, 1, 0] - src[0, 1, 0]) <= 2          # R (max) pinned
        assert out[0, 1, 1] > src[0, 1, 1] + 100              # G lifted toward R
        assert out[0, 1, 2] > src[0, 1, 2] + 100              # B lifted toward R

    def test_opencl_matches_cpu(self):
        img = _test_image()
        cpu = adjust_image(img, sub_saturation=40)
        gpu = adjust_image_opencl(img, sub_saturation=40)
        np.testing.assert_allclose(cpu.astype(np.int64), gpu.astype(np.int64), atol=2)

    def test_combines_with_regular_saturation(self):
        img = _test_image()
        out = adjust_image(img, saturation=30, sub_saturation=30)
        assert out.shape == img.shape
        assert out.dtype == np.uint16


class TestApplyCrop:
    def test_none_returns_input(self):
        img = _test_image()
        assert apply_crop_to_image(img, None) is img

    def test_basic_crop_dimensions(self):
        img = np.zeros((100, 200, 3), dtype=np.uint16)
        out = apply_crop_to_image(img, (0.25, 0.10, 0.75, 0.90))
        assert out.shape == (80, 100, 3)

    def test_full_rect_is_identity_shape(self):
        img = np.zeros((50, 60, 3), dtype=np.uint16)
        out = apply_crop_to_image(img, (0.0, 0.0, 1.0, 1.0))
        assert out.shape == img.shape

    def test_degenerate_rect_returns_input(self):
        img = np.zeros((50, 60, 3), dtype=np.uint16)
        out = apply_crop_to_image(img, (0.5, 0.5, 0.5, 0.5))
        assert out.shape == img.shape

    def test_out_of_range_rect_is_clamped(self):
        img = np.zeros((50, 60, 3), dtype=np.uint16)
        out = apply_crop_to_image(img, (-0.5, -0.5, 1.5, 1.5))
        assert out.shape == img.shape

    def test_crop_selects_expected_region(self):
        img = np.arange(10 * 10 * 3, dtype=np.uint16).reshape(10, 10, 3)
        out = apply_crop_to_image(img, (0.2, 0.3, 0.7, 0.8))
        np.testing.assert_array_equal(out, img[3:8, 2:7])


def _stub_image():
    """CCRImage without the heavy file-loading __init__."""
    img = CCRImage.__new__(CCRImage)
    img.adjustment_settings = {}
    img.crop_rect = None
    img.rotation_angle = 0
    img.fine_rotation_angle = 0
    img.horizontal_mirrored = False
    img.vertical_mirrored = False
    img.undo_stack = []
    return img


class TestUndoStack:
    def test_pop_empty_returns_false(self):
        img = _stub_image()
        assert img.pop_undo_state() is False

    def test_push_then_pop_restores_state(self):
        img = _stub_image()
        img.adjustment_settings = {"temperature": 10}
        img.push_undo_state()
        img.adjustment_settings = {"temperature": 50}
        img.crop_rect = (0.1, 0.1, 0.9, 0.9)
        img.rotation_angle = 90
        assert img.pop_undo_state() is True
        assert img.adjustment_settings == {"temperature": 10}
        assert img.crop_rect is None
        assert img.rotation_angle == 0

    def test_multiple_undo_steps(self):
        img = _stub_image()
        for temp in (10, 20, 30):
            img.push_undo_state()
            img.adjustment_settings = {"temperature": temp}
        assert img.pop_undo_state()
        assert img.adjustment_settings == {"temperature": 20}
        assert img.pop_undo_state()
        assert img.adjustment_settings == {"temperature": 10}
        assert img.pop_undo_state()
        assert img.adjustment_settings == {}
        assert img.pop_undo_state() is False

    def test_consecutive_identical_snapshots_collapse(self):
        img = _stub_image()
        img.push_undo_state()
        img.push_undo_state()
        img.push_undo_state()
        assert len(img.undo_stack) == 1

    def test_stack_is_capped(self):
        img = _stub_image()
        for i in range(CCRImage.UNDO_STACK_LIMIT + 25):
            img.adjustment_settings = {"temperature": i}
            img.push_undo_state()
        assert len(img.undo_stack) == CCRImage.UNDO_STACK_LIMIT

    def test_snapshot_is_isolated_from_later_mutation(self):
        img = _stub_image()
        img.adjustment_settings = {"temperature": 5}
        img.push_undo_state()
        # Mutating the live dict must not corrupt the stored snapshot
        img.adjustment_settings["temperature"] = 99
        assert img.pop_undo_state()
        assert img.adjustment_settings == {"temperature": 5}


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
