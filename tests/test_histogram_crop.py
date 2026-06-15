#!/usr/bin/env python3
"""
Tests for two crop-related behaviours:

1. The preview histogram reflects the *cropped* region — `update_thumbnail_and_preview`
   computes it over the kept pixels (via `apply_crop_to_image`), not the full frame.
2. The Reset button clears the crop together with the sliders/curves, but only
   when the Whole Image layer is active (the crop is image-global, so resetting
   an individual area leaves it alone).
"""

import os
import sys

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import cv2  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])

from core.ccr_backend import ccr_backend  # noqa: E402
from core.ccr_image import CCRImage  # noqa: E402
from widgets.sliders_panel import SlidersPanel  # noqa: E402


def _ccr_image(tmp_path, name="scan.png"):
    """Construct a CCRImage from a tiny uniform on-disk scan so __init__
    succeeds; callers overwrite resized_raw with deterministic content."""
    path = str(tmp_path / name)
    base = np.full((40, 60, 3), 20000, np.uint16)
    cv2.imwrite(path, cv2.cvtColor(base, cv2.COLOR_RGB2BGR))
    return CCRImage(path)


def _half_image(h=40, w=60):
    """Left half pure red, right half pure blue: the per-channel histograms of
    the two halves are maximally different, so a crop must change the result."""
    a = np.zeros((h, w, 3), np.uint16)
    a[:, : w // 2] = (60000, 0, 0)      # red
    a[:, w // 2:] = (0, 0, 60000)       # blue
    return a


def _hist_bytes(img):
    qimg = img.histogram_image.toImage().convertToFormat(QImage.Format_RGB888)
    return bytes(qimg.constBits())


class TestHistogramReflectsCrop:
    def test_crop_changes_histogram(self, tmp_path):
        img = _ccr_image(tmp_path)
        img.converted = True            # skip the preview-only auto-brightness
        img.adjustment_settings = {}
        img.resized_raw = _half_image()

        img.crop_rect = None
        img.update_thumbnail_and_preview()
        full = _hist_bytes(img)

        img.crop_rect = (0.0, 0.0, 0.5, 1.0)   # left (red) half only
        img.update_thumbnail_and_preview()
        left = _hist_bytes(img)

        img.crop_rect = (0.5, 0.0, 1.0, 1.0)   # right (blue) half only
        img.update_thumbnail_and_preview()
        right = _hist_bytes(img)

        assert full != left
        assert full != right
        assert left != right

    def test_cropped_histogram_matches_native_region(self, tmp_path):
        """Cropping to the right half must yield the same histogram as an image
        whose pixels ARE only that right half — i.e. the histogram is computed
        over the kept region, not a re-weighting of the whole frame."""
        full = _half_image()

        img = _ccr_image(tmp_path, "a.png")
        img.converted = True
        img.adjustment_settings = {}
        img.resized_raw = full.copy()
        img.crop_rect = (0.5, 0.0, 1.0, 1.0)
        img.update_thumbnail_and_preview()
        cropped = _hist_bytes(img)

        ref = _ccr_image(tmp_path, "b.png")
        ref.converted = True
        ref.adjustment_settings = {}
        ref.resized_raw = full[:, full.shape[1] // 2:].copy()   # native right half
        ref.crop_rect = None
        ref.update_thumbnail_and_preview()
        native = _hist_bytes(ref)

        assert cropped == native


# --------------------------------------------------------------------------
# Reset clears the crop
# --------------------------------------------------------------------------
class _ImagePreviewStub:
    def __init__(self):
        self.updated = []

    def update_preview(self, idx):
        self.updated.append(idx)


class _Host(QWidget):
    """parent().parent() target on_reset_clicked reaches for image_preview."""
    def __init__(self):
        super().__init__()
        self.image_preview = _ImagePreviewStub()
        self.mid = QWidget(self)


_HOSTS = []


def _panel():
    host = _Host()
    _HOSTS.append(host)               # keep alive for the test's lifetime
    # The panel's Qt parent is set by being added to a layout (its constructor
    # ignores the parent arg), so on_reset_clicked's parent().parent() resolves
    # to the host that carries image_preview.
    mid_layout = QVBoxLayout(host.mid)
    panel = SlidersPanel(host.mid)
    mid_layout.addWidget(panel)
    return panel


class TestResetClearsCrop:
    def test_reset_clears_crop_on_whole_image(self, tmp_path):
        img = _ccr_image(tmp_path)
        img.crop_rect = (0.1, 0.1, 0.9, 0.9)
        img.crop_angle = 5.0
        img.active_area_id = None             # Whole Image layer active
        ccr_backend.images = [img]
        ccr_backend.file_paths = [img.file_path]

        panel = _panel()
        panel.current_idx = 0
        panel.on_reset_clicked()

        assert img.crop_rect is None
        assert img.crop_angle == 0.0

    def test_reset_of_area_leaves_crop(self, tmp_path):
        img = _ccr_image(tmp_path)
        img.crop_rect = (0.1, 0.1, 0.9, 0.9)
        img.crop_angle = 5.0
        img.area_layers = [{"id": "a1", "kind": "circle", "enabled": True,
                            "feather": 0.25, "angle": 0.0,
                            "geometry": {"cx": 0.5, "cy": 0.5, "rx": 0.25, "ry": 0.25},
                            "settings": {"exposure": 40}}]
        img.active_area_id = "a1"             # an area layer is active
        ccr_backend.images = [img]
        ccr_backend.file_paths = [img.file_path]

        panel = _panel()
        panel.current_idx = 0
        panel.on_reset_clicked()

        # Crop is image-global — resetting an area must not touch it.
        assert img.crop_rect == (0.1, 0.1, 0.9, 0.9)
        assert img.crop_angle == 5.0

    def test_reset_crop_is_undoable(self, tmp_path):
        img = _ccr_image(tmp_path)
        img.crop_rect = (0.2, 0.2, 0.8, 0.8)
        img.crop_angle = 0.0
        img.active_area_id = None
        ccr_backend.images = [img]
        ccr_backend.file_paths = [img.file_path]

        panel = _panel()
        panel.current_idx = 0
        panel.on_reset_clicked()
        assert img.crop_rect is None

        # The crop clear shares the single undo snapshot Reset pushes.
        assert img.pop_undo_state()
        assert img.crop_rect == (0.2, 0.2, 0.8, 0.8)


# --------------------------------------------------------------------------
# Other crop-setting paths must keep the crop-aware histogram in step
# --------------------------------------------------------------------------
class TestDuplicateCropHistogram:
    def test_duplicate_of_crop_only_image_has_cropped_histogram(self, tmp_path):
        """Duplicating an image whose only edit is a crop must give the copy a
        histogram over the cropped region — the copy's crop is assigned after
        its constructor already built the full-image histogram."""
        full = _half_image()
        img = _ccr_image(tmp_path)
        img.converted = True
        img.adjustment_settings = {}
        img.resized_raw = full.copy()
        img.crop_rect = (0.5, 0.0, 1.0, 1.0)
        img.update_thumbnail_and_preview()
        src_cropped = _hist_bytes(img)

        ccr_backend.images = [img]
        ccr_backend.file_paths = [img.file_path]
        assert ccr_backend.duplicate_images_by_indices([0]) == 1
        dup = ccr_backend.images[1]

        assert dup.crop_rect == (0.5, 0.0, 1.0, 1.0)
        assert _hist_bytes(dup) == src_cropped


class TestSyncToAllCropHistogram:
    def test_crop_only_sync_updates_target_histogram(self, tmp_path):
        """Syncing only the crop group to all images must regenerate each
        target's crop-aware histogram, not just copy the crop value."""
        full = _half_image()

        src = _ccr_image(tmp_path, "src.png")
        src.converted = True
        src.adjustment_settings = {}
        src.resized_raw = full.copy()
        src.crop_rect = (0.5, 0.0, 1.0, 1.0)
        src.update_thumbnail_and_preview()
        src_cropped = _hist_bytes(src)

        tgt = _ccr_image(tmp_path, "tgt.png")
        tgt.converted = True
        tgt.adjustment_settings = {}
        tgt.resized_raw = full.copy()
        tgt.crop_rect = None
        tgt.update_thumbnail_and_preview()
        tgt_full = _hist_bytes(tgt)
        assert tgt_full != src_cropped        # precondition: differ before sync

        ccr_backend.images = [src, tgt]
        ccr_backend.file_paths = [src.file_path, tgt.file_path]

        panel = _panel()
        panel.current_idx = 0                 # src is the sync source
        panel._sync_group_selection = {"crop": True}
        panel._perform_sync_to_all()

        assert tgt.crop_rect == (0.5, 0.0, 1.0, 1.0)
        assert _hist_bytes(tgt) == src_cropped
