#!/usr/bin/env python3
"""
Tests for the zoom hi-res detail pipeline: the resolution-independent
conversion helpers must reproduce the real conversion pipelines' output
(color-match guarantee), and the load-path changes must keep values stable.
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.ccr_processor import (  # noqa: E402
    ccr_normalize_with_reference, ccr_normalize_with_bwpoint,
    compute_reference_norm_params, apply_reference_normalization,
    apply_bwpoint_normalization,
)
from core.ccr_image import CCRImage  # noqa: E402


def _conversion_stub(img, ref_rect, fine_rot=0):
    """CCRImage carrying just the attributes the conversion pipelines read."""
    s = CCRImage.__new__(CCRImage)
    s.resized_raw = img
    s.reference_frame = ref_rect
    s.fine_rotation_angle = fine_rot
    s.rotation_angle = 0
    s.horizontal_mirrored = False
    s.vertical_mirrored = False
    s.contrast_base = 0
    s.temperature_base = 0
    s.brightness_base = -8
    s.adjustment_settings = {}
    s.conversion_inputs = None
    s.source_ops = []
    return s


def _scan_like_image(h=200, w=300, seed=42):
    """Synthetic negative-scan-like data: smooth gradients + noise."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    base = (20000 + 25000 * (xx / w) + 10000 * (yy / h))
    img = np.stack([base * 1.2, base, base * 0.7], axis=-1)
    img += rng.normal(0, 1500, img.shape)
    return np.clip(img, 1000, 64000).astype(np.uint16)


class TestHiResColorMatch:
    """The split-out helpers must equal the real pipelines at the same size —
    this is the color-match guarantee the zoom detail view relies on."""

    def test_reference_helpers_match_pipeline(self):
        img = _scan_like_image()
        ref = (20, 20, 280, 180)
        expected = ccr_normalize_with_reference(_conversion_stub(img.copy(), ref))
        base_density, gamma_ch, levels = compute_reference_norm_params(img, ref, 0)
        got = apply_reference_normalization(img, base_density, gamma_ch, levels)
        np.testing.assert_allclose(got.astype(np.int64),
                                   expected.astype(np.int64), atol=2)

    def test_reference_helpers_match_pipeline_with_fine_rotation(self):
        img = _scan_like_image(seed=7)
        ref = (40, 30, 260, 170)
        fine_rot = 250  # 2.5 degrees
        expected = ccr_normalize_with_reference(_conversion_stub(img.copy(), ref, fine_rot))
        base_density, gamma_ch, levels = compute_reference_norm_params(img, ref, fine_rot)
        got = apply_reference_normalization(img, base_density, gamma_ch, levels)
        np.testing.assert_allclose(got.astype(np.int64),
                                   expected.astype(np.int64), atol=2)

    def test_bwpoint_helpers_match_pipeline(self):
        img = _scan_like_image(seed=3)
        black_point = (52000.0, 48000.0, 39000.0)  # film base (bright scan)
        white_point = (9000.0, 9500.0, 7000.0)     # dense/exposed area
        expected = ccr_normalize_with_bwpoint(
            _conversion_stub(img.copy(), None), black_point, white_point)
        got = apply_bwpoint_normalization(img, black_point, white_point)
        np.testing.assert_allclose(got.astype(np.int64),
                                   expected.astype(np.int64), atol=2)

    def test_hires_scales_consistently(self):
        """Converting a 2x-resolution version and downsampling should land
        close to the 1x conversion (no resolution-dependent constants)."""
        import cv2
        img2x = _scan_like_image(h=400, w=600, seed=11)
        img1x = cv2.resize(img2x, (300, 200), interpolation=cv2.INTER_AREA)
        ref = (20, 20, 280, 180)
        base_density, gamma_ch, levels = compute_reference_norm_params(img1x, ref, 0)
        out1x = apply_reference_normalization(img1x, base_density, gamma_ch, levels)
        out2x = apply_reference_normalization(img2x, base_density, gamma_ch, levels)
        out2x_down = cv2.resize(out2x, (300, 200), interpolation=cv2.INTER_AREA)
        # Interpolation through the nonlinear look costs a little accuracy;
        # the images must still be visually identical (small mean error).
        diff = np.abs(out2x_down.astype(np.int64) - out1x.astype(np.int64))
        assert diff.mean() < 600   # < ~1% of full scale on average


class TestRenderHiresBaseRouting:
    def test_unconverted_returns_decode_shape(self, tmp_path):
        import cv2
        # 16-bit PNG stand-in for a scan file
        path = str(tmp_path / "scan.png")
        img = _scan_like_image(h=240, w=360)
        cv2.imwrite(path, cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
        stub = _conversion_stub(None, None)
        stub.file_path = path
        stub.converted = False
        out = stub.render_hires_base()
        assert out is not None and out.shape == (240, 360, 3)

    def test_converted_without_conversion_snapshot_returns_none(self, tmp_path):
        import cv2
        path = str(tmp_path / "scan.png")
        cv2.imwrite(path, cv2.cvtColor(_scan_like_image(h=120, w=160),
                                       cv2.COLOR_RGB2BGR))
        stub = _conversion_stub(None, None)
        stub.file_path = path
        stub.converted = True
        stub.conversion_inputs = None
        assert stub.render_hires_base() is None

    def test_bwpoint_replay_includes_fine_rotation(self, tmp_path):
        """The bwpoint pipeline bakes the fine rotation into the preview
        pixels before normalization; the hi-res replay must do the same so
        the detail layer lines up with the preview content."""
        import cv2
        img = _scan_like_image(h=240, w=360, seed=5)
        path = str(tmp_path / "scan.png")
        cv2.imwrite(path, cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
        black_point = (52000.0, 48000.0, 39000.0)
        white_point = (9000.0, 9500.0, 7000.0)
        fine_rot = 250  # 2.5 degrees
        expected = ccr_normalize_with_bwpoint(
            _conversion_stub(img.copy(), None, fine_rot), black_point, white_point)
        stub = _conversion_stub(None, None, fine_rot)
        stub.file_path = path
        stub.converted = True
        stub.conversion_inputs = {"mode": "bw",
                                  "bw": (black_point, white_point),
                                  "fine_rot": fine_rot}
        got = stub.render_hires_base()
        np.testing.assert_allclose(got.astype(np.int64),
                                   expected.astype(np.int64), atol=2)

    def test_ref_replay_uses_snapshot_not_live_state(self, tmp_path):
        """Changing the live reference frame after conversion must not change
        the replay — it renders from the convert-time snapshot."""
        import cv2
        img = _scan_like_image(h=200, w=300, seed=9)
        path = str(tmp_path / "scan.png")
        cv2.imwrite(path, cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
        ref = (20, 20, 280, 180)
        stub = _conversion_stub(None, ref)
        stub.file_path = path
        stub.converted = True
        stub.conversion_inputs = {"mode": "ref", "ref": ref, "fine_rot": 0}
        baseline = stub.render_hires_base()
        stub.reference_frame = (100, 100, 150, 150)   # live edit, no re-convert
        stub.fine_rotation_angle = 900
        again = stub.render_hires_base()
        np.testing.assert_array_equal(baseline, again)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
