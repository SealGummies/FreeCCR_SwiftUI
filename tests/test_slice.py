#!/usr/bin/env python3
"""
Tests for slice mode's model layer: source_region reading, single-decode
child construction, backend grid slicing, region composition for nested
slices, and cut cleaning.
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
from core.ccr_backend import ccr_backend, CCRBackend  # noqa: E402
from core.ccr_image import CCRImage  # noqa: E402


def _coordinate_png(tmp_path, w=600, h=400, name="scan.png"):
    """16-bit PNG whose channel 0 encodes x and channel 1 encodes y, so any
    region read can be verified pixel-exactly."""
    img = np.zeros((h, w, 3), dtype=np.uint16)
    img[..., 0] = (np.arange(w, dtype=np.uint16) * 100)[None, :]
    img[..., 1] = (np.arange(h, dtype=np.uint16) * 150)[:, None]
    path = str(tmp_path / name)
    cv2.imwrite(path, cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
    return path, img


class TestSourceRegion:
    def test_read_image_crops_to_region(self, tmp_path):
        path, img = _coordinate_png(tmp_path)
        obj = CCRImage(path, source_region=(0.25, 0.5, 0.75, 1.0))
        # Region of a 600x400 image: x 150..450, y 200..400
        expected = img[200:400, 150:450]
        np.testing.assert_array_equal(obj.resized_raw, expected)
        assert obj.original_full_size == (200, 300)

    def test_no_region_reads_whole_file(self, tmp_path):
        path, img = _coordinate_png(tmp_path)
        obj = CCRImage(path)
        np.testing.assert_array_equal(obj.resized_raw, img)
        assert obj.original_full_size == (400, 600)

    def test_preloaded_constructor_skips_file_read(self, tmp_path):
        path, img = _coordinate_png(tmp_path)
        crop = img[0:200, 0:300]
        obj = CCRImage(path, source_region=(0.0, 0.0, 0.5, 0.5),
                       preloaded_img=crop, preloaded_full_size=(200, 300),
                       display_name="scan_s1.png")
        np.testing.assert_array_equal(obj.resized_raw, crop)
        assert obj.original_full_size == (200, 300)
        assert obj.display_name == "scan_s1.png"
        # Must own its pixels, not view the shared parent decode
        assert obj.resized_raw.base is None

    def test_reload_respects_region(self, tmp_path):
        path, img = _coordinate_png(tmp_path)
        obj = CCRImage(path, source_region=(0.5, 0.0, 1.0, 0.5))
        obj.reload_image()
        np.testing.assert_array_equal(obj.resized_raw, img[0:200, 300:600])


class TestCleanSliceCuts:
    def test_basic(self):
        assert CCRBackend._clean_slice_cuts([0.5]) == [0.0, 0.5, 1.0]

    def test_sorted_and_deduped(self):
        out = CCRBackend._clean_slice_cuts([0.7, 0.3, 0.302, 0.7005])
        assert out == [0.0, 0.3, 0.7, 1.0]

    def test_edge_cuts_dropped(self):
        assert CCRBackend._clean_slice_cuts([0.001, 0.999]) == [0.0, 1.0]

    def test_empty(self):
        assert CCRBackend._clean_slice_cuts([]) == [0.0, 1.0]


class TestSliceImage:
    def _load(self, tmp_path, **png_kwargs):
        path, img = _coordinate_png(tmp_path, **png_kwargs)
        ccr_backend.images = [CCRImage(path)]
        ccr_backend.file_paths = [path]
        return path, img

    def test_vertical_cuts_make_three_children(self, tmp_path):
        path, img = self._load(tmp_path)
        n = ccr_backend.slice_image_by_index(0, [1.0 / 3.0, 2.0 / 3.0], [])
        assert n == 3
        assert ccr_backend.get_image_count() == 3
        regions = [im.source_region for im in ccr_backend.images]
        assert regions[0] == pytest.approx((0.0, 0.0, 1.0 / 3.0, 1.0))
        assert regions[1] == pytest.approx((1.0 / 3.0, 0.0, 2.0 / 3.0, 1.0))
        assert regions[2] == pytest.approx((2.0 / 3.0, 0.0, 1.0, 1.0))
        # Children carry the correct pixels (reading order, left to right)
        np.testing.assert_array_equal(ccr_backend.images[1].resized_raw,
                                      img[:, 200:400])
        # Distinct display names
        names = [im.display_name for im in ccr_backend.images]
        assert names == ["scan_s1.png", "scan_s2.png", "scan_s3.png"]
        # All un-converted, fresh state
        assert all(not im.converted for im in ccr_backend.images)

    def test_grid_two_by_two(self, tmp_path):
        self._load(tmp_path)
        n = ccr_backend.slice_image_by_index(0, [0.5], [0.5])
        assert n == 4
        # Reading order: TL, TR, BL, BR
        regions = [im.source_region for im in ccr_backend.images]
        assert regions[0] == pytest.approx((0.0, 0.0, 0.5, 0.5))
        assert regions[1] == pytest.approx((0.5, 0.0, 1.0, 0.5))
        assert regions[2] == pytest.approx((0.0, 0.5, 0.5, 1.0))
        assert regions[3] == pytest.approx((0.5, 0.5, 1.0, 1.0))

    def test_nested_slice_composes_into_original_coords(self, tmp_path):
        path, img = self._load(tmp_path)
        ccr_backend.slice_image_by_index(0, [0.5], [])     # two halves
        # Slice the RIGHT half again at its own middle
        n = ccr_backend.slice_image_by_index(1, [0.5], [])
        assert n == 2
        regions = [im.source_region for im in ccr_backend.images]
        assert regions[1] == pytest.approx((0.5, 0.0, 0.75, 1.0))
        assert regions[2] == pytest.approx((0.75, 0.0, 1.0, 1.0))
        # The nested child's pixels come from the right place in the ORIGINAL
        np.testing.assert_array_equal(ccr_backend.images[2].resized_raw,
                                      img[:, 450:600])

    def test_no_effective_cuts_is_noop(self, tmp_path):
        self._load(tmp_path)
        n = ccr_backend.slice_image_by_index(0, [0.001], [])
        assert n == 0
        assert ccr_backend.get_image_count() == 1

    def test_replaces_parent_in_place(self, tmp_path):
        path1, _ = _coordinate_png(tmp_path, name="a.png")
        path2, _ = _coordinate_png(tmp_path, name="b.png")
        ccr_backend.images = [CCRImage(path1), CCRImage(path2)]
        ccr_backend.file_paths = [path1, path2]
        n = ccr_backend.slice_image_by_index(0, [0.5], [])
        assert n == 2
        assert ccr_backend.get_image_count() == 3
        # b.png stays last; slices of a.png take positions 0 and 1
        assert os.path.basename(ccr_backend.images[2].file_path) == "b.png"
        assert ccr_backend.file_paths == [im.file_path for im in ccr_backend.images]

    def test_full_res_export_read_uses_region(self, tmp_path):
        """The export path (full-res read) must return only the slice."""
        path, img = self._load(tmp_path)
        ccr_backend.slice_image_by_index(0, [0.5], [])
        child = ccr_backend.images[1]
        full = child.read_image(child.file_path, preview=False)
        np.testing.assert_array_equal(full, img[:, 300:600])


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
