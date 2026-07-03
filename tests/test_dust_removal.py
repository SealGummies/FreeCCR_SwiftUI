#!/usr/bin/env python3
"""
Tests for the Dust Removal feature.

Dust edits are stored as NORMALIZED spots on the image
({kind, pts:[[x,y],...], r}) and inpainted at render time by
ccr_processor.apply_dust_removal (cv2.inpaint, masked-only feathered composite).
The AI detector (dust_detect) only finds dust; the fill is the same cv2 path.
See spec/dust-removal.md.
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
from core import catalog, dust_detect  # noqa: E402
from core.ccr_image import CCRImage  # noqa: E402
from core.ccr_processor import (apply_dust_removal,  # noqa: E402
                                rasterize_dust_mask)


def _flat_with_speck(h=100, w=100, base=30000, speck=60000, cx=50, cy=50, r=4):
    """Flat gray image with one bright circular speck near the center."""
    img = np.full((h, w, 3), base, dtype=np.uint16)
    cv2.circle(img, (cx, cy), r, (speck, speck, speck), -1)
    return img


# --- rasterize_dust_mask ----------------------------------------------------
class TestRasterize:
    def test_centered_spot_is_a_filled_circle(self):
        spot = {"kind": "brush", "pts": [[0.5, 0.5]], "r": 0.1}
        mask = rasterize_dust_mask([spot], 100, 100)
        assert mask.dtype == np.uint8
        assert mask[50, 50] == 255          # center filled
        assert mask[50, 70] == 0            # well outside r_px=10
        area = int((mask > 0).sum())
        assert abs(area - np.pi * 100) < 60  # ~ pi*r_px^2, r_px = 10

    def test_resolution_independence(self):
        spot = {"kind": "brush", "pts": [[0.5, 0.5]], "r": 0.1}
        a1 = int((rasterize_dust_mask([spot], 100, 100) > 0).sum())
        a2 = int((rasterize_dust_mask([spot], 200, 200) > 0).sum())
        # Same normalized spot covers ~4x the pixels at 2x resolution.
        assert 3.5 < a2 / a1 < 4.6

    def test_empty_spots_is_blank(self):
        mask = rasterize_dust_mask([], 50, 50)
        assert mask.shape == (50, 50)
        assert not mask.any()


# --- apply_dust_removal -----------------------------------------------------
class TestApplyDustRemoval:
    def test_identity_returns_same_object(self):
        img = _flat_with_speck()
        assert apply_dust_removal(img, []) is img
        assert apply_dust_removal(img, None) is img

    def test_speck_removed_and_far_pixels_untouched(self):
        img = _flat_with_speck(base=30000, speck=60000, cx=50, cy=50, r=4)
        spot = {"kind": "brush", "pts": [[0.5, 0.5]], "r": 0.08}
        out = apply_dust_removal(img, [spot])
        assert out.dtype == np.uint16
        assert out is not img                       # new array (non-destructive)
        # The speck is filled toward the flat surround.
        assert abs(int(out[50, 50, 0]) - 30000) < 15000
        assert int(out[50, 50, 0]) < 55000
        # A corner far from the mask is bit-for-bit unchanged.
        assert np.array_equal(out[0:10, 0:10], img[0:10, 0:10])

    def test_input_not_mutated(self):
        img = _flat_with_speck()
        before = img.copy()
        apply_dust_removal(img, [{"kind": "brush", "pts": [[0.5, 0.5]], "r": 0.08}])
        assert np.array_equal(img, before)


# --- apply_adjustments integration (dust runs before the early-return guard) -
class TestApplyAdjustmentsIntegration:
    def test_dust_only_image_still_inpaints(self, tmp_path):
        # Build a bare CCRImage; neutralize bases so apply_adjustments takes the
        # early-return path AFTER dust removal (proving dust runs before it).
        path = str(tmp_path / "x.png")
        cv2.imwrite(path, np.zeros((10, 10, 3), np.uint8))
        img = CCRImage(path)
        img.adjustment_settings = {}
        img.contrast_base = 0
        img.temperature_base = 0
        img.brightness_base = 0
        img.color_profile = "color"
        img.dust_spots = [{"kind": "brush", "pts": [[0.5, 0.5]], "r": 0.08}]

        src = _flat_with_speck()
        out = img.apply_adjustments(src)
        # Speck healed even though every slider/base is neutral.
        assert int(out[50, 50, 0]) < 55000
        assert np.array_equal(out[0:10, 0:10], src[0:10, 0:10])

    def test_no_dust_no_change_in_neutral_pipeline(self, tmp_path):
        path = str(tmp_path / "y.png")
        cv2.imwrite(path, np.zeros((10, 10, 3), np.uint8))
        img = CCRImage(path)
        img.adjustment_settings = {}
        img.contrast_base = img.temperature_base = img.brightness_base = 0
        img.dust_spots = []
        src = _flat_with_speck()
        out = img.apply_adjustments(src)
        assert np.array_equal(out, src)


# --- dust_detect.prob_to_spots (model-free) ---------------------------------
def _bright_luma(prob):
    """Luma where high-prob regions read bright (white film dust) on a dark
    surround, so detections pass the bright-speck gate."""
    return np.clip(prob, 0.0, 1.0).astype(np.float32)


class TestProbToSpots:
    def test_threshold_direction(self):
        prob = np.zeros((100, 100), np.float32)
        prob[10:13, 10:13] = 0.5            # a small mid-confidence blob
        luma = _bright_luma(prob)
        # sensitivity 0 -> thr 0.85 -> nothing; 100 -> thr 0.25 -> detected.
        assert dust_detect.prob_to_spots(prob, luma, 0) == []
        spots = dust_detect.prob_to_spots(prob, luma, 100)
        assert len(spots) == 1
        assert spots[0]["kind"] == "auto"
        x, y = spots[0]["pts"][0]
        assert 0.08 < x < 0.16 and 0.08 < y < 0.16
        assert spots[0]["r"] > 0

    def test_size_gate_drops_large_blobs(self):
        # max_blob = MAX_BLOB(400) * max(h,w) / 2000 = 20 px for a 100x100 map.
        prob = np.zeros((100, 100), np.float32)
        prob[10:13, 10:13] = 0.9            # 9 px  -> kept
        prob[50:62, 50:62] = 0.9            # 144 px -> dropped
        spots = dust_detect.prob_to_spots(prob, _bright_luma(prob), 50)
        assert len(spots) == 1
        x, y = spots[0]["pts"][0]
        assert x < 0.3 and y < 0.3          # the small blob, not the big one

    def test_blank_prob_yields_nothing(self):
        z = np.zeros((40, 40), np.float32)
        assert dust_detect.prob_to_spots(z, z, 100) == []

    def test_drops_elongated_keeps_compact(self):
        # A thin line is real image structure (bike frame / horizon), not dust:
        # the aspect filter drops it while keeping a compact speck. Guards the
        # AI-artifact fix (see spec/dust-removal.md §5.3/§5.4).
        prob = np.zeros((100, 100), np.float32)
        prob[50, 10:22] = 0.9       # 1x12 thin line (area 12, aspect 12) -> dropped
        prob[80:83, 80:83] = 0.9    # 3x3 compact speck (area 9)          -> kept
        spots = dust_detect.prob_to_spots(prob, _bright_luma(prob), 60)
        assert len(spots) == 1
        x, y = spots[0]["pts"][0]
        assert x > 0.5 and y > 0.5  # the compact speck, not the line

    def test_radius_is_area_equivalent_not_extent(self):
        # A 6x6 compact blob -> radius ~ sqrt(36/pi) ~ 3.4 px (+pad), NOT the
        # 0.5*extent=3 of the old bounding-box sizing blown up by elongation.
        prob = np.zeros((200, 200), np.float32)
        prob[100:106, 100:106] = 0.9
        spots = dust_detect.prob_to_spots(prob, _bright_luma(prob), 60)
        assert len(spots) == 1
        r_px = spots[0]["r"] * 200
        assert 3.0 < r_px < 7.0     # tight circle, no big smudge

    def test_bright_gate_drops_dark_blob(self):
        # Film dust inverts to WHITE specks. A compact, right-sized blob that is
        # DARKER than its surround (e.g. a face on bright sky) is NOT dust and
        # must be rejected — this is what removed a person's head before.
        # 200x200 so the 6x6 blob clears the size gate (max_blob ~ 40 here) and
        # the brightness gate is what actually decides.
        prob = np.zeros((200, 200), np.float32)
        prob[40:46, 40:46] = 0.9                 # detector fires on a 6x6 region
        dark = np.full((200, 200), 0.8, np.float32)
        dark[40:46, 40:46] = 0.2                 # dark blob, bright surround
        assert dust_detect.prob_to_spots(prob, dark, 60) == []
        bright = np.full((200, 200), 0.2, np.float32)
        bright[40:46, 40:46] = 0.9               # bright blob (real dust)
        assert len(dust_detect.prob_to_spots(prob, bright, 60)) == 1


# --- Availability / graceful degradation ------------------------------------
class TestAvailability:
    def test_unavailable_when_onnxruntime_absent(self, monkeypatch):
        # Setting the module to None makes `import onnxruntime` raise ImportError.
        monkeypatch.setitem(sys.modules, "onnxruntime", None)
        assert dust_detect.is_available() is False

    def test_model_path_is_under_freeccr(self):
        assert "FreeCCR" in dust_detect.model_path()
        assert dust_detect.model_path().endswith("detector.onnx")


# --- Persistence (catalog) --------------------------------------------------
def _scan_png(tmp_path, name="neg.png", w=120, h=80, seed=3):
    rng = np.random.default_rng(seed)
    img = np.clip(rng.normal(30000, 4000, (h, w, 3)), 0, 65535).astype(np.uint16)
    path = str(tmp_path / name)
    cv2.imwrite(path, cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
    return path


class TestPersistence:
    def test_dust_spots_round_trip(self, tmp_path):
        cat = str(tmp_path / "catalog.json")
        path = _scan_png(tmp_path)
        img = CCRImage(path)
        img.dust_spots = [{"kind": "brush", "pts": [[0.25, 0.5], [0.3, 0.55]],
                           "r": 0.01},
                          {"kind": "auto", "pts": [[0.7, 0.2]], "r": 0.004}]
        catalog.update_for_images([img], path=cat)
        restored = catalog.create_images_for_path(path, path=cat)
        assert len(restored) == 1
        assert restored[0].dust_spots == img.dust_spots

    def test_old_entry_without_dust_defaults_empty(self, tmp_path):
        cat = str(tmp_path / "catalog.json")
        path = _scan_png(tmp_path)
        img = CCRImage(path)
        img.adjustment_settings = {"exposure": 5}
        state = catalog.serialize_image(img)
        del state["dust_spots"]            # simulate a pre-feature catalog entry
        restored = catalog._restore_image(path, state)
        assert restored.dust_spots == []

    def test_dust_only_image_is_not_pristine(self, tmp_path):
        path = _scan_png(tmp_path)
        img = CCRImage(path)
        img.dust_spots = [{"kind": "brush", "pts": [[0.5, 0.5]], "r": 0.01}]
        state = catalog.serialize_image(img)
        assert catalog._is_pristine(state) is False


# --- Undo -------------------------------------------------------------------
class TestUndo:
    def test_capture_and_pop_restore_dust_spots(self, tmp_path):
        path = _scan_png(tmp_path)
        img = CCRImage(path)
        img.dust_spots = [{"kind": "brush", "pts": [[0.5, 0.5]], "r": 0.01}]
        img.push_undo_state()
        # Mutate after snapshot.
        img.dust_spots.append({"kind": "auto", "pts": [[0.1, 0.1]], "r": 0.005})
        assert len(img.dust_spots) == 2
        assert img.pop_undo_state()
        assert img.dust_spots == [{"kind": "brush", "pts": [[0.5, 0.5]], "r": 0.01}]

    def test_snapshot_is_independent_deep_copy(self, tmp_path):
        path = _scan_png(tmp_path)
        img = CCRImage(path)
        img.dust_spots = [{"kind": "brush", "pts": [[0.5, 0.5]], "r": 0.01}]
        img.push_undo_state()
        # Mutate a NESTED structure in place; the snapshot must not change.
        img.dust_spots[0]["pts"].append([0.6, 0.6])
        img.pop_undo_state()
        assert img.dust_spots[0]["pts"] == [[0.5, 0.5]]


# --- DustRemovalPanel wiring (headless, with stubs) -------------------------
class _StubPreview:
    def __init__(self):
        self.dust_mode = True
        self.current_idx = None
        self.brush = None

    def set_dust_brush_size(self, r):
        self.brush = r

    def dust_undo_last(self):
        return False

    def dust_clear_all(self):
        return False


class _StubMain:
    def __init__(self):
        self.toggled = None

    def toggle_dust_removal(self, on):
        self.toggled = on


class TestPanelWiring:
    def test_panel_builds_without_onnxruntime(self):
        from widgets.dust_panel import DustRemovalPanel
        # Building the panel must never import onnxruntime / raise.
        panel = DustRemovalPanel(_StubMain(), _StubPreview())
        assert panel is not None

    def test_brush_slider_drives_canvas(self):
        from widgets.dust_panel import (DustRemovalPanel, brush_r_to_slider,
                                        slider_to_brush_r)
        prev = _StubPreview()
        panel = DustRemovalPanel(_StubMain(), prev)
        panel._on_brush_changed(brush_r_to_slider(0.030))
        # Log-step quantization: nearest step is within ~1% of the target r.
        assert abs(prev.brush - 0.030) < 0.030 * 0.015
        panel.sync_brush_size(0.05)            # canvas -> slider, no feedback loop
        assert panel.brush_slider.value() == brush_r_to_slider(0.05)
        # Mapping round-trips through the slider's integer steps.
        v = brush_r_to_slider(0.012)
        assert brush_r_to_slider(slider_to_brush_r(v)) == v

    def test_brush_reaches_fine_sizes(self):
        # The slider bottom is 0.05% of image width (~3 px radius on a 6000 px
        # scan) — finer than the old 0.2% floor; the 20% top is unchanged.
        from widgets.dust_panel import (DustRemovalPanel, BRUSH_STEPS,
                                        DUST_BRUSH_R_MIN, DUST_BRUSH_R_MAX)
        assert DUST_BRUSH_R_MIN <= 0.0005
        prev = _StubPreview()
        panel = DustRemovalPanel(_StubMain(), prev)
        assert panel.brush_slider.minimum() == 0
        assert panel.brush_slider.maximum() == BRUSH_STEPS
        panel._on_brush_changed(0)
        assert abs(prev.brush - DUST_BRUSH_R_MIN) < 1e-12
        panel._on_brush_changed(BRUSH_STEPS)
        assert abs(prev.brush - DUST_BRUSH_R_MAX) < 1e-12

    def test_done_button_exits_mode(self):
        from widgets.dust_panel import DustRemovalPanel
        main = _StubMain()
        panel = DustRemovalPanel(main, _StubPreview())
        panel._on_done()
        assert main.toggled is False

    def test_cancel_and_shutdown_are_safe_with_no_jobs(self):
        from widgets.dust_panel import DustRemovalPanel
        panel = DustRemovalPanel(_StubMain(), _StubPreview())
        panel.cancel_jobs()
        panel.shutdown()   # must not raise with no threads running

    def test_detect_all_no_targets_is_safe(self):
        from widgets.dust_panel import DustRemovalPanel, _DetectAllWorker  # noqa: F401
        from core.ccr_backend import ccr_backend
        saved = ccr_backend.images
        ccr_backend.images = []
        try:
            panel = DustRemovalPanel(_StubMain(), _StubPreview())
            assert hasattr(panel, "detect_all_btn")
            panel._on_detect_all()                 # no convertible images
            assert panel._detecting_all is False    # no batch started
            assert panel._detect_all_thread is None
        finally:
            ccr_backend.images = saved


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
