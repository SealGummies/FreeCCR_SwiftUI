#!/usr/bin/env python3
"""
Tests for the persistent edit catalog: serialize/restore round trips
(adjustments, crop, orientation, slices), conversion replay, and stale-file
invalidation.
"""

import os
import sys
import time

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])

import cv2  # noqa: E402
from core import catalog  # noqa: E402
from core.ccr_backend import ccr_backend  # noqa: E402
from core.ccr_image import CCRImage  # noqa: E402


def _scan_png(tmp_path, name="negative.png", w=600, h=400, seed=21):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    base = 20000 + 25000 * (xx / w) + 10000 * (yy / h)
    img = np.stack([base * 1.2, base, base * 0.7], axis=-1)
    img += rng.normal(0, 1500, img.shape)
    img = np.clip(img, 1000, 64000).astype(np.uint16)
    path = str(tmp_path / name)
    cv2.imwrite(path, cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
    return path, img


class TestCatalogRoundTrip:
    def test_plain_state_round_trip(self, tmp_path):
        cat = str(tmp_path / "catalog.json")
        path, _ = _scan_png(tmp_path)
        img = CCRImage(path)
        img.adjustment_settings = {"temperature": 12, "saturation": -5}
        img.crop_rect = (0.1, 0.2, 0.8, 0.9)
        img.crop_angle = 3.5
        img.rotation_angle = 90
        img.fine_rotation_angle = 250
        img.horizontal_mirrored = True
        img.reference_frame = (10, 10, 590, 390)
        catalog.update_for_images([img], path=cat)

        restored = catalog.create_images_for_path(path, path=cat)
        assert len(restored) == 1
        r = restored[0]
        assert r.adjustment_settings == {"temperature": 12, "saturation": -5}
        assert r.crop_rect == (0.1, 0.2, 0.8, 0.9)
        assert r.crop_angle == 3.5
        assert r.rotation_angle == 90
        assert r.fine_rotation_angle == 250
        assert r.horizontal_mirrored is True
        assert r.reference_frame == (10, 10, 590, 390)
        assert not r.converted

    def test_conversion_replay_round_trip(self, tmp_path):
        cat = str(tmp_path / "catalog.json")
        path, _ = _scan_png(tmp_path)
        img = CCRImage(path)
        img.reference_frame = (20, 20, 580, 380)
        ccr_backend.images = [img]
        ccr_backend.convert_negative_by_index(0)
        assert img.converted
        original_converted = img.resized_raw.copy()
        catalog.update_for_images([img], path=cat)

        restored = catalog.create_images_for_path(path, path=cat)[0]
        assert restored.converted
        assert restored.conversion_inputs == img.conversion_inputs
        np.testing.assert_allclose(restored.resized_raw.astype(np.int64),
                                   original_converted.astype(np.int64), atol=2)

    def test_slices_round_trip(self, tmp_path):
        cat = str(tmp_path / "catalog.json")
        path, _ = _scan_png(tmp_path)
        parent = CCRImage(path)
        parent.reference_frame = (20, 20, 580, 380)
        ccr_backend.images = [parent]
        ccr_backend.file_paths = [path]
        ccr_backend.convert_negative_by_index(0)
        ccr_backend.slice_image_by_index(0, [0.5], [])
        ccr_backend.images[0].adjustment_settings = {"contrast": 20}
        originals = [im.resized_raw.copy() for im in ccr_backend.images]
        catalog.update_for_images(ccr_backend.images, path=cat)

        restored = catalog.create_images_for_path(path, path=cat)
        assert len(restored) == 2
        assert [r.display_name for r in restored] == \
               ["negative_s1.png", "negative_s2.png"]
        assert restored[0].source_ops == ccr_backend.images[0].source_ops
        assert restored[0].adjustment_settings == {"contrast": 20}
        for r, orig in zip(restored, originals):
            assert r.converted
            assert r.conversion_inputs["mode"] == "ref_params"
            np.testing.assert_allclose(r.resized_raw.astype(np.int64),
                                       orig.astype(np.int64), atol=2)

    def test_bw_conversion_replay(self, tmp_path):
        cat = str(tmp_path / "catalog.json")
        path, _ = _scan_png(tmp_path)
        img = CCRImage(path)
        from core.ccr_processor import ccr_normalize_with_bwpoint
        black_point = (52000.0, 48000.0, 39000.0)
        white_point = (9000.0, 9500.0, 7000.0)
        processed = ccr_normalize_with_bwpoint(img, black_point, white_point)
        img.resized_raw = processed
        img.converted = True
        img.conversion_inputs = {"mode": "bw", "bw": (black_point, white_point),
                                 "fine_rot": 0}
        original = img.resized_raw.copy()
        catalog.update_for_images([img], path=cat)

        restored = catalog.create_images_for_path(path, path=cat)[0]
        assert restored.converted
        assert restored.conversion_inputs["mode"] == "bw"
        # bases written by the bw pipeline must match what was stored
        assert restored.contrast_base == img.contrast_base
        assert restored.temperature_base == img.temperature_base
        np.testing.assert_allclose(restored.resized_raw.astype(np.int64),
                                   original.astype(np.int64), atol=2)

    def test_tint_factor_round_trip(self, tmp_path):
        cat = str(tmp_path / "catalog.json")
        path, _ = _scan_png(tmp_path)
        img = CCRImage(path)
        img.tint_balance_factor = 1.1337
        catalog.update_for_images([img], path=cat)
        restored = catalog.create_images_for_path(path, path=cat)[0]
        assert restored.tint_balance_factor == pytest.approx(1.1337)


class TestCatalogInvalidation:
    def test_modified_file_ignored(self, tmp_path):
        cat = str(tmp_path / "catalog.json")
        path, _ = _scan_png(tmp_path)
        img = CCRImage(path)
        img.adjustment_settings = {"temperature": 50}
        catalog.update_for_images([img], path=cat)
        # Rewrite the file with different content (size and mtime change)
        time.sleep(0.05)
        _scan_png(tmp_path, w=500, h=300, seed=99)
        assert catalog.entries_for_path(path, path=cat) is None
        restored = catalog.create_images_for_path(path, path=cat)
        assert len(restored) == 1
        assert restored[0].adjustment_settings == {}

    def test_unknown_file_plain_load(self, tmp_path):
        cat = str(tmp_path / "catalog.json")
        path, _ = _scan_png(tmp_path)
        restored = catalog.create_images_for_path(path, path=cat)
        assert len(restored) == 1
        assert restored[0].adjustment_settings == {}

    def test_unloaded_entries_are_kept(self, tmp_path):
        cat = str(tmp_path / "catalog.json")
        path_a, _ = _scan_png(tmp_path, name="a.png")
        path_b, _ = _scan_png(tmp_path, name="b.png")
        img_a = CCRImage(path_a)
        img_a.adjustment_settings = {"tint": 9}
        catalog.update_for_images([img_a], path=cat)
        # A later session with only b loaded must not erase a's entry
        img_b = CCRImage(path_b)
        catalog.update_for_images([img_b], path=cat)
        assert catalog.entries_for_path(path_a, path=cat) is not None

    def test_corrupt_catalog_is_tolerated(self, tmp_path):
        cat = str(tmp_path / "catalog.json")
        with open(cat, "w", encoding="utf-8") as f:
            f.write("{not json!!")
        path, _ = _scan_png(tmp_path)
        restored = catalog.create_images_for_path(path, path=cat)
        assert len(restored) == 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
