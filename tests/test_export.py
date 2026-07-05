#!/usr/bin/env python3
"""
Tests for the export pipeline, focused on the downsized-export optimization
(`_load_export_source`): decode/resize to the export target up front for
downsized, un-cropped exports, while keeping output dimensions correct for
full-size and cropped exports.
"""
import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])

import cv2  # noqa: E402
import tifffile  # noqa: E402
from core.ccr_backend import ccr_backend  # noqa: E402
from core.ccr_image import CCRImage  # noqa: E402
from core import ccr_processor  # noqa: E402


def _scan_png(tmp_path, name="negative.png", w=2400, h=1600, seed=7):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    base = 20000 + 25000 * (xx / w) + 10000 * (yy / h)
    img = np.stack([base * 1.2, base, base * 0.7], axis=-1)
    img += rng.normal(0, 1500, img.shape)
    img = np.clip(img, 1000, 64000).astype(np.uint16)
    path = str(tmp_path / name)
    cv2.imwrite(path, cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
    return path, img


def _converted(tmp_path, **kw):
    path, _ = _scan_png(tmp_path, **kw)
    img = CCRImage(path)
    img.reference_frame = (40, 40, 2360, 1560)
    ccr_backend.images = [img]
    ccr_backend.file_paths = [path]
    ccr_backend.convert_negative_by_index(0)
    return img, path


def _long_side(tiff_path):
    arr = tifffile.imread(tiff_path)
    h, w = arr.shape[:2]
    return max(h, w), arr


class TestExportDimensions:
    def test_full_export_keeps_full_resolution(self, tmp_path):
        img, _ = _converted(tmp_path)
        out = str(tmp_path / "full.tiff")
        assert ccr_backend.export_image_by_index(0, out, max_long_side=None)
        long_side, arr = _long_side(out)
        assert long_side == 2400          # full source long side
        assert arr.dtype == np.uint16
        assert int(arr.max()) > 0          # not a black frame

    def test_downsized_export_hits_target(self, tmp_path):
        img, _ = _converted(tmp_path)
        out = str(tmp_path / "small.tiff")
        assert ccr_backend.export_image_by_index(0, out, max_long_side=800)
        long_side, arr = _long_side(out)
        assert long_side == 800
        assert int(arr.max()) > 0

    def test_cropped_downsized_export_hits_target(self, tmp_path):
        # A crop changes which region maps to max_long_side; the cropped output's
        # long side must still equal the requested target (late-resize path).
        img, _ = _converted(tmp_path)
        img.crop_rect = (0.25, 0.25, 0.75, 0.75)
        out = str(tmp_path / "cropsmall.tiff")
        assert ccr_backend.export_image_by_index(0, out, max_long_side=600)
        long_side, _ = _long_side(out)
        assert long_side == 600

    def test_jpeg_export(self, tmp_path):
        img, _ = _converted(tmp_path)
        out = str(tmp_path / "small.jpg")
        assert ccr_backend.export_image_by_index(0, out, jpg_output=True,
                                                 max_long_side=800)
        arr = cv2.imread(out)
        assert arr is not None and max(arr.shape[:2]) == 800


class TestRefExportSnapshotReplay:
    """Issue #94: the reference-frame export must replay the frame BAKED at
    convert time (conversion_inputs["ref"]), not the live reference_frame —
    a stray right-click clears the live frame while the converted preview
    stays intact, and every export then failed."""

    def test_export_survives_cleared_reference_frame(self, tmp_path):
        img, _ = _converted(tmp_path)
        img.reference_frame = None      # right-click without a drag clears it
        out = str(tmp_path / "cleared.tiff")
        assert ccr_backend.export_image_by_index(0, out, max_long_side=1080)
        long_side, arr = _long_side(out)
        assert long_side == 1080
        assert int(arr.max()) > 0

    def test_full_export_survives_cleared_reference_frame(self, tmp_path):
        img, _ = _converted(tmp_path)
        img.reference_frame = None
        out = str(tmp_path / "cleared_full.tiff")
        assert ccr_backend.export_image_by_index(0, out, max_long_side=None)
        long_side, _ = _long_side(out)
        assert long_side == 2400

    def test_cleared_frame_with_black_point_still_uses_ref_recipe(self, tmp_path):
        # A sampled global black point must NOT hijack a reference-converted
        # image's export onto the B/W-point pipeline when the live frame is
        # gone — the snapshot branch matches first.
        img, _ = _converted(tmp_path)
        baseline = str(tmp_path / "ref_baseline.tiff")
        assert ccr_backend.export_image_by_index(0, baseline, max_long_side=800)
        img.reference_frame = None
        ccr_backend.black_point_bgr = (40000.0, 40000.0, 40000.0)
        try:
            out = str(tmp_path / "cleared_bp.tiff")
            assert ccr_backend.export_image_by_index(0, out, max_long_side=800)
            _, base_arr = _long_side(baseline)
            _, arr = _long_side(out)
            assert np.array_equal(base_arr, arr)
        finally:
            ccr_backend.black_point_bgr = None

    def test_export_uses_convert_time_frame_not_redrawn_one(self, tmp_path):
        # Redrawing the frame AFTER converting must not change the export —
        # the file must match the conversion the preview shows.
        img, _ = _converted(tmp_path)
        baseline = str(tmp_path / "frame_baseline.tiff")
        assert ccr_backend.export_image_by_index(0, baseline, max_long_side=800)
        img.reference_frame = (10, 10, 200, 150)   # new frame, not converted yet
        out = str(tmp_path / "frame_redrawn.tiff")
        assert ccr_backend.export_image_by_index(0, out, max_long_side=800)
        _, base_arr = _long_side(baseline)
        _, arr = _long_side(out)
        assert np.array_equal(base_arr, arr)

    def test_export_items_reports_real_failure_reason(self, tmp_path):
        # The failures list must carry the actual exception text — the bundled
        # app has no visible console, so this is the only diagnostic channel.
        img, _ = _converted(tmp_path)
        img.conversion_inputs = None    # un-snapshotted legacy state
        img.reference_frame = None      # → live-frame replay must raise
        result = ccr_backend.export_items([(0, str(tmp_path / "fail.tiff"))])
        assert result["failed"] == 1
        assert result["failures"], "failure reason missing"
        idx, msg = result["failures"][0]
        assert idx == 0
        assert "reference_frame is None" in msg


class TestLoadExportSource:
    def test_processing_path_uses_resized_raw(self, tmp_path):
        img, _ = _converted(tmp_path)
        # output_path None => in-app processing => returns the live resized_raw.
        src = ccr_processor._load_export_source(img, None, None)
        assert src is img.resized_raw

    def test_full_export_decodes_full(self, tmp_path):
        img, path = _converted(tmp_path)
        src = ccr_processor._load_export_source(img, "out.tiff", None)
        assert max(src.shape[:2]) == 2400

    def test_downsized_no_crop_decodes_small(self, tmp_path):
        img, _ = _converted(tmp_path)
        src = ccr_processor._load_export_source(img, "out.tiff", 800)
        assert max(src.shape[:2]) == 800     # resized to target up front

    def test_downsized_with_crop_decodes_full(self, tmp_path):
        img, _ = _converted(tmp_path)
        img.crop_rect = (0.1, 0.1, 0.9, 0.9)
        src = ccr_processor._load_export_source(img, "out.tiff", 800)
        # A crop forces full decode (late resize applies the target post-crop).
        assert max(src.shape[:2]) == 2400


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
