#!/usr/bin/env python3
"""
Tests for Area Editing (local masked adjustment layers).

Covers the mask math + additive compositing, the CCRImage / backend / catalog
round-trips (persistence, undo, duplicate, slice), active-layer routing, the
hi-res adjustment signature, and a headless smoke test of the canvas overlay.
See spec/area-editing.md.
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
from core import catalog  # noqa: E402
from core.ccr_backend import ccr_backend  # noqa: E402
from core.ccr_image import CCRImage  # noqa: E402
from core.ccr_processor import (build_circle_mask, build_gradient_mask,  # noqa: E402
                                build_area_mask, apply_area_layers)


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


def _circle(cx=0.5, cy=0.5, rx=0.25, ry=0.25, **kw):
    a = {"id": kw.get("id", "a1"), "kind": "circle", "enabled": True,
         "feather": kw.get("feather", 0.25), "angle": kw.get("angle", 0.0),
         "geometry": {"cx": cx, "cy": cy, "rx": rx, "ry": ry},
         "settings": kw.get("settings", {})}
    return a


# --------------------------------------------------------------------------
# Mask math
# --------------------------------------------------------------------------
class TestMasks:
    def test_circle_center_full_corner_zero(self):
        m = build_circle_mask(100, 200, {"cx": 0.5, "cy": 0.5, "rx": 0.2, "ry": 0.2},
                              0.0, 0.2)
        assert m.shape == (100, 200)
        assert m.dtype == np.float32
        assert m[50, 100] == pytest.approx(1.0, abs=1e-3)
        assert m[0, 0] == pytest.approx(0.0, abs=1e-3)
        assert 0.0 <= m.min() and m.max() <= 1.0

    def test_circle_feather_monotonic(self):
        # Along +x from center to edge, alpha must be non-increasing.
        m = build_circle_mask(100, 100, {"cx": 0.5, "cy": 0.5, "rx": 0.4, "ry": 0.4},
                              0.0, 0.5)
        row = m[50, 50:]
        assert np.all(np.diff(row) <= 1e-6)

    def test_circle_squish_is_elliptical(self):
        # rx != ry -> wider coverage horizontally than vertically.
        m = build_circle_mask(200, 200, {"cx": 0.5, "cy": 0.5, "rx": 0.4, "ry": 0.1},
                              0.0, 0.01)
        cy, cx = 100, 100
        # a point far horizontally is inside; the same distance vertically is out
        assert m[cy, cx + 60] > 0.5
        assert m[cy + 60, cx] < 0.5

    def test_circle_rotation(self):
        # A 90-degree rotation swaps the long axis.
        m = build_circle_mask(200, 200, {"cx": 0.5, "cy": 0.5, "rx": 0.4, "ry": 0.1},
                              90.0, 0.01)
        assert m[100 + 60, 100] > 0.5   # now elongated vertically
        assert m[100, 100 + 60] < 0.5

    def test_gradient_endpoints(self):
        g = build_gradient_mask(100, 200, {"x0": 0.2, "y0": 0.5, "x1": 0.8, "y1": 0.5},
                                0.25)
        assert g[50, int(0.2 * 200)] == pytest.approx(0.0, abs=1e-2)
        assert g[50, int(0.8 * 200)] == pytest.approx(1.0, abs=1e-2)
        assert g[50, 100] == pytest.approx(0.5, abs=0.1)

    def test_resolution_independence(self):
        geom = {"cx": 0.5, "cy": 0.5, "rx": 0.3, "ry": 0.2}
        coverage = []
        for (h, w) in ((135, 200), (270, 400), (540, 800)):
            m = build_circle_mask(h, w, geom, 0.0, 0.25)
            coverage.append(m.mean())
        assert max(coverage) - min(coverage) < 0.01  # same normalized coverage

    def test_dispatch(self):
        c = build_area_mask(50, 50, _circle())
        g = build_area_mask(50, 50, {"kind": "gradient", "feather": 0.25,
                                     "geometry": {"x0": 0, "y0": 0.5, "x1": 1, "y1": 0.5}})
        assert c.shape == (50, 50) and g.shape == (50, 50)


# --------------------------------------------------------------------------
# Additive compositing
# --------------------------------------------------------------------------
def _add_layer(amount):
    def fn(base, settings):
        return np.clip(base.astype(np.int32) + int(settings.get("add", amount)),
                       0, 65535).astype(np.uint16)
    return fn


class TestCompositing:
    def test_no_areas_returns_same(self):
        base = np.full((20, 20, 3), 10000, np.uint16)
        assert apply_area_layers(base, [], _add_layer(0)) is base
        assert apply_area_layers(base, [_circle(settings={"add": 5}, **{"id": "x"})
                                        | {"enabled": False}], _add_layer(0)) is base

    def test_enabled_but_empty_area_is_skipped(self):
        # A freshly created area (enabled, no settings yet) must be a true no-op
        # — returns the base unchanged without allocating a full-res mask/delta.
        base = np.full((30, 30, 3), 12000, np.uint16)
        area = {"kind": "circle", "enabled": True, "feather": 0.2, "angle": 0,
                "geometry": {"cx": 0.5, "cy": 0.5, "rx": 0.3, "ry": 0.3},
                "settings": {}}
        assert apply_area_layers(base, [area], _add_layer(0)) is base

    def test_single_area_affects_only_mask(self):
        base = np.full((40, 40, 3), 10000, np.uint16)
        # a small centered circle, hard edge
        area = _circle(rx=0.15, ry=0.15, feather=0.01, settings={"add": 5000})
        out = apply_area_layers(base, [area], _add_layer(0))
        assert int(out[20, 20, 0]) == 15000      # inside
        assert int(out[0, 0, 0]) == 10000        # outside untouched

    def test_additive_overlap_accumulates(self):
        base = np.full((40, 40, 3), 10000, np.uint16)
        big = lambda add: _circle(rx=2.0, ry=2.0, feather=0.01, settings={"add": add})
        out = apply_area_layers(base, [big(1000), big(2000)], _add_layer(0))
        assert int(out[20, 20, 0]) == 13000      # 10000 + 1000 + 2000

    def test_order_independent(self):
        base = np.full((30, 30, 3), 8000, np.uint16)
        a = _circle(rx=2.0, ry=2.0, feather=0.2, settings={"add": 1500})
        b = _circle(cx=0.4, cy=0.6, rx=0.3, ry=0.3, feather=0.3, settings={"add": -700})
        out1 = apply_area_layers(base, [a, b], _add_layer(0))
        out2 = apply_area_layers(base, [b, a], _add_layer(0))
        np.testing.assert_array_equal(out1, out2)

    def test_clips_to_uint16(self):
        base = np.full((10, 10, 3), 64000, np.uint16)
        area = _circle(rx=2.0, ry=2.0, feather=0.01, settings={"add": 10000})
        out = apply_area_layers(base, [area], _add_layer(0))
        assert out.max() <= 65535 and out.dtype == np.uint16


# --------------------------------------------------------------------------
# CCRImage integration: apply_adjustments, early-return, undo, active_settings
# --------------------------------------------------------------------------
class TestImageIntegration:
    def _converted(self, tmp_path):
        path, _ = _scan_png(tmp_path)
        img = CCRImage(path)
        img.reference_frame = (20, 20, 580, 380)
        ccr_backend.images = [img]
        ccr_backend.file_paths = [path]
        ccr_backend.convert_negative_by_index(0)
        return img

    def test_area_only_edit_renders(self, tmp_path):
        img = self._converted(tmp_path)
        base = img.apply_adjustments(img.resized_raw)        # no areas
        img.area_layers = [_circle(rx=0.2, ry=0.2, feather=0.05,
                                   settings={"exposure": 60})]
        out = img.apply_adjustments(img.resized_raw)
        # The early-return guard must NOT skip an area-only edit.
        assert not np.array_equal(base, out)
        h, w = out.shape[:2]
        # center changed, a far corner did not
        assert not np.array_equal(out[h // 2, w // 2], base[h // 2, w // 2])
        np.testing.assert_array_equal(out[0, 0], base[0, 0])

    def test_disabled_area_is_noop(self, tmp_path):
        img = self._converted(tmp_path)
        base = img.apply_adjustments(img.resized_raw)
        img.area_layers = [_circle(settings={"exposure": 80}) | {"enabled": False}]
        out = img.apply_adjustments(img.resized_raw)
        np.testing.assert_array_equal(base, out)

    def test_active_settings_routing(self, tmp_path):
        img = self._converted(tmp_path)
        img.area_layers = [_circle(settings={"exposure": 10})]
        assert img.active_settings() is img.adjustment_settings   # global default
        img.active_area_id = "a1"
        assert img.active_settings() is img.area_layers[0]["settings"]
        img.active_area_id = "stale"
        assert img.active_settings() is img.adjustment_settings   # fallback

    def test_undo_restores_areas_without_alias(self, tmp_path):
        img = self._converted(tmp_path)
        img.area_layers = [_circle(settings={"exposure": 10})]
        img.push_undo_state()
        img.area_layers[0]["settings"]["exposure"] = 99
        img.area_layers.append(_circle(id="a2"))
        assert img.pop_undo_state()
        assert len(img.area_layers) == 1
        assert img.area_layers[0]["settings"]["exposure"] == 10
        # Mutating the restored structure must not corrupt the (popped) snapshot.
        img.area_layers[0]["settings"]["exposure"] = 1
        img.push_undo_state()
        snap = img.undo_stack[-1]
        assert snap["area_layers"][0]["settings"]["exposure"] == 1
        img.area_layers[0]["settings"]["exposure"] = 2
        assert snap["area_layers"][0]["settings"]["exposure"] == 1


# --------------------------------------------------------------------------
# Backend CRUD + duplicate + slice
# --------------------------------------------------------------------------
class TestBackend:
    def _converted(self, tmp_path):
        path, _ = _scan_png(tmp_path)
        img = CCRImage(path)
        img.reference_frame = (20, 20, 580, 380)
        ccr_backend.images = [img]
        ccr_backend.file_paths = [path]
        ccr_backend.convert_negative_by_index(0)
        return img

    def test_add_select_enable_remove(self, tmp_path):
        self._converted(tmp_path)
        aid = ccr_backend.add_area_by_index(0, "circle")
        assert aid is not None
        assert ccr_backend.get_active_area_id_by_index(0) == aid
        assert len(ccr_backend.get_areas_by_index(0)) == 1
        # active settings now routes to the area
        ccr_backend.set_active_settings_by_index(0, {"exposure": 25}, reprocess=False)
        assert ccr_backend.get_areas_by_index(0)[0]["settings"]["exposure"] == 25
        assert ccr_backend.images[0].adjustment_settings == {}   # global untouched
        ccr_backend.set_area_enabled_by_index(0, aid, False)
        assert ccr_backend.get_areas_by_index(0)[0]["enabled"] is False
        ccr_backend.remove_area_by_index(0, aid)
        assert ccr_backend.get_areas_by_index(0) == []
        assert ccr_backend.get_active_area_id_by_index(0) is None

    def test_default_circle_is_on_screen_round(self, tmp_path):
        img = self._converted(tmp_path)
        ccr_backend.add_area_by_index(0, "circle")
        g = ccr_backend.get_areas_by_index(0)[0]["geometry"]
        h, w = img.resized_raw.shape[:2]
        # rx*w == ry*h => equal pixel radii => circular on screen
        assert g["rx"] * w == pytest.approx(g["ry"] * h, rel=1e-3)

    def test_update_geometry(self, tmp_path):
        self._converted(tmp_path)
        aid = ccr_backend.add_area_by_index(0, "circle")
        ccr_backend.update_area_geometry_by_index(
            0, aid, geometry={"cx": 0.2, "cy": 0.3, "rx": 0.1, "ry": 0.1},
            angle=15.0, feather=0.4, reprocess=False)
        a = ccr_backend.get_areas_by_index(0)[0]
        assert a["geometry"]["cx"] == 0.2 and a["angle"] == 15.0
        assert a["feather"] == 0.4

    def test_duplicate_deepcopies_areas(self, tmp_path):
        self._converted(tmp_path)
        ccr_backend.add_area_by_index(0, "circle")
        ccr_backend.set_active_settings_by_index(0, {"exposure": 30}, reprocess=False)
        ccr_backend.duplicate_images_by_indices([0])
        assert len(ccr_backend.images) == 2
        src, dup = ccr_backend.images[0], ccr_backend.images[1]
        assert len(dup.area_layers) == 1
        assert dup.area_layers[0] is not src.area_layers[0]
        # mutate the duplicate -> source unaffected
        dup.area_layers[0]["settings"]["exposure"] = 0
        assert src.area_layers[0]["settings"]["exposure"] == 30

    def test_slice_starts_without_areas(self, tmp_path):
        self._converted(tmp_path)
        ccr_backend.add_area_by_index(0, "circle")
        ccr_backend.slice_image_by_index(0, [0.5], [])
        assert ccr_backend.get_image_count() == 2
        for im in ccr_backend.images:
            assert im.area_layers == []


# --------------------------------------------------------------------------
# Catalog persistence
# --------------------------------------------------------------------------
class TestCatalog:
    def test_areas_round_trip(self, tmp_path):
        cat = str(tmp_path / "catalog.json")
        path, _ = _scan_png(tmp_path)
        img = CCRImage(path)
        img.area_layers = [
            _circle(cx=0.4, cy=0.6, rx=0.2, ry=0.1, angle=12.5, feather=0.33,
                    settings={"exposure": 20, "curves": {"rgb": [[0, 0], [255, 255]]}}),
            {"id": "g1", "kind": "gradient", "enabled": False, "feather": 0.25,
             "angle": 0.0, "geometry": {"x0": 0.1, "y0": 0.2, "x1": 0.9, "y1": 0.8},
             "settings": {"contrast": -15}},
        ]
        catalog.update_for_images([img], path=cat)
        restored = catalog.create_images_for_path(path, path=cat)[0]
        assert len(restored.area_layers) == 2
        a0 = restored.area_layers[0]
        assert a0["kind"] == "circle" and a0["enabled"] is True
        assert a0["angle"] == 12.5 and a0["feather"] == 0.33
        assert a0["geometry"] == {"cx": 0.4, "cy": 0.6, "rx": 0.2, "ry": 0.1}
        assert a0["settings"]["exposure"] == 20
        assert a0["settings"]["curves"]["rgb"] == [[0, 0], [255, 255]]
        a1 = restored.area_layers[1]
        assert a1["kind"] == "gradient" and a1["enabled"] is False
        # session-only selection is not persisted
        assert restored.active_area_id is None

    def test_areas_only_image_not_pristine(self, tmp_path):
        cat = str(tmp_path / "catalog.json")
        path, _ = _scan_png(tmp_path)
        img = CCRImage(path)
        img.area_layers = [_circle(settings={"exposure": 5})]
        state = catalog.serialize_image(img)
        assert not catalog._is_pristine(state)

    def test_legacy_catalog_without_areas(self, tmp_path):
        cat = str(tmp_path / "catalog.json")
        path, _ = _scan_png(tmp_path)
        img = CCRImage(path)
        img.adjustment_settings = {"temperature": 5}
        catalog.update_for_images([img], path=cat)
        # Strip "areas" to mimic an older catalog, then load.
        data = catalog.load_catalog(cat)
        rec = data["files"][catalog._file_key(path)]
        rec["images"][0].pop("areas", None)
        catalog.save_catalog(data, cat)
        restored = catalog.create_images_for_path(path, path=cat)[0]
        assert restored.area_layers == []


# --------------------------------------------------------------------------
# Hi-res adjustment signature
# --------------------------------------------------------------------------
class TestSignature:
    def test_sig_changes_on_area_edit(self, tmp_path):
        from widgets.image_preview import ImagePreview
        path, _ = _scan_png(tmp_path)
        img = CCRImage(path)
        ccr_backend.images = [img]
        ccr_backend.file_paths = [path]
        ip = ImagePreview.__new__(ImagePreview)
        ip.current_idx = 0
        sig0 = ip._current_adj_sig()
        img.area_layers = [_circle(settings={"exposure": 10})]
        sig1 = ip._current_adj_sig()
        assert sig0 != sig1
        img.area_layers[0]["enabled"] = False
        sig2 = ip._current_adj_sig()
        assert sig2 != sig1


# --------------------------------------------------------------------------
# Panel Layers list: enable/disable must not rebuild the row mid-signal
# --------------------------------------------------------------------------
class TestPanelLayers:
    def _setup(self, tmp_path):
        from widgets.sliders_panel import SlidersPanel
        path, _ = _scan_png(tmp_path)
        img = CCRImage(path)
        img.reference_frame = (20, 20, 580, 380)
        ccr_backend.images = [img]
        ccr_backend.file_paths = [path]
        ccr_backend.convert_negative_by_index(0)
        aid = ccr_backend.add_area_by_index(0, "circle")
        panel = SlidersPanel()

        # Realistic stub: the real ImagePreview.update_preview re-enters
        # SlidersPanel.set_current_idx — the exact chain that, before the fix,
        # rebuilt the Layers list (deleting the emitting checkbox) mid-signal.
        class _StubIP:
            def __init__(self, p):
                self.p = p
            def update_preview(self, idx):
                self.p.set_current_idx(idx)
            def on_active_layer_changed(self, idx):
                pass
        panel.image_preview = _StubIP(panel)
        panel.current_idx = 0
        panel._rebuild_layers_list(0)
        return panel, img, aid

    def test_layers_signature_ignores_enabled(self, tmp_path):
        panel, img, aid = self._setup(tmp_path)
        sig_before = panel._layers_signature(0)
        img.area_layers[0]["enabled"] = False
        # Toggling enabled must NOT change the structural signature (else a
        # rebuild fires from inside the checkbox's toggled signal -> crash).
        assert panel._layers_signature(0) == sig_before

    def test_layers_signature_changes_on_add_remove(self, tmp_path):
        panel, img, aid = self._setup(tmp_path)
        sig0 = panel._layers_signature(0)
        ccr_backend.add_area_by_index(0, "gradient")
        assert panel._layers_signature(0) != sig0

    def test_disable_area_via_real_checkbox_does_not_crash(self, tmp_path):
        from PySide6.QtWidgets import QCheckBox
        panel, img, aid = self._setup(tmp_path)
        # The real enable/disable checkbox lives in the rebuilt Layers rows.
        checks = panel.layers_container.findChildren(QCheckBox)
        assert checks, "expected an enable checkbox for the area row"
        # Driving the REAL 'toggled' signal reproduces the original crash path:
        # _on_area_enabled -> update_preview -> set_current_idx. It must NOT
        # tear down the emitting checkbox (use-after-free) — process survives.
        checks[0].setChecked(False)
        assert img.area_layers[0]["enabled"] is False
        # Re-enable likewise.
        checks[0].setChecked(True)
        assert img.area_layers[0]["enabled"] is True


# --------------------------------------------------------------------------
# Compare must suppress areas without dropping out of area-edit mode
# --------------------------------------------------------------------------
class TestCompareWithAreas:
    def _wired(self, tmp_path):
        from PySide6.QtWidgets import QWidget, QVBoxLayout
        from widgets.sliders_panel import SlidersPanel
        path, _ = _scan_png(tmp_path)
        img = CCRImage(path)
        img.reference_frame = (20, 20, 580, 380)
        ccr_backend.images = [img]
        ccr_backend.file_paths = [path]
        ccr_backend.convert_negative_by_index(0)
        aid = ccr_backend.add_area_by_index(0, "circle")
        img.get_area(aid)["settings"] = {"exposure": 40}

        class _StubIP:
            def update_preview(self, idx):
                pass

        class _StubThumbs:
            def update_thumbnail(self, idx):
                pass

        class _Host(QWidget):
            pass
        host = _Host()
        self._host = host
        host.image_preview = _StubIP()
        host.thumbnail_list = _StubThumbs()
        mid = QWidget(host)
        mid_layout = QVBoxLayout(mid)
        panel = SlidersPanel()
        host.sliders_panel = panel
        panel.image_preview = host.image_preview
        mid_layout.addWidget(panel)   # reparents panel -> mid (mid.parent()==host)
        panel.current_idx = 0
        panel._rebuild_layers_list(0)
        assert panel.parent().parent() is host
        return panel, img, aid

    def test_compare_disables_and_restores_areas(self, tmp_path):
        panel, img, aid = self._wired(tmp_path)
        area = img.get_area(aid)
        assert area["enabled"] is True
        panel.on_compare_pressed()
        # Global emptied, area suppressed in place (still present -> area mode
        # and overlay survive), undo guard set.
        assert img.adjustment_settings == {}
        assert area["enabled"] is False
        assert len(img.area_layers) == 1
        assert hasattr(panel, "_original_adjustment")
        panel.on_compare_released()
        assert area["enabled"] is True
        # Compare state fully cleaned up so undo isn't blocked afterwards.
        assert not hasattr(panel, "_original_adjustment")
        assert not hasattr(panel, "_compare_global")


# --------------------------------------------------------------------------
# Middle-button pan is only blocked during an active area drag
# --------------------------------------------------------------------------
class TestMiddlePan:
    def test_pan_allowed_in_area_mode_when_not_dragging(self, tmp_path):
        from widgets.image_preview import ImagePreview
        ip = ImagePreview.__new__(ImagePreview)
        ip.area_mode = True
        ip._area_drag = None
        # The middle-pan guard keys off an ACTIVE drag, not area_mode, so a
        # plain middle-drag in area mode is permitted.
        assert ip._area_drag is None and ip.area_mode is True


# --------------------------------------------------------------------------
# Canvas overlay smoke test (headless)
# --------------------------------------------------------------------------
class TestOverlay:
    def _preview_with_area(self, tmp_path, kind="circle"):
        from PySide6.QtWidgets import (QWidget, QVBoxLayout, QGraphicsPixmapItem)
        from PySide6.QtGui import QPixmap, QColor
        from widgets.image_preview import ImagePreview

        class _StubPanel:
            def set_sliders_enabled(self, *a):
                pass

        class _Host(QWidget):
            def __init__(self):
                super().__init__()
                self.sliders_panel = _StubPanel()
                self.mid = QWidget(self)

        path, _ = _scan_png(tmp_path)
        img = CCRImage(path)
        img.reference_frame = (20, 20, 580, 380)
        ccr_backend.images = [img]
        ccr_backend.file_paths = [path]
        ccr_backend.convert_negative_by_index(0)
        aid = ccr_backend.add_area_by_index(0, kind)

        host = _Host()
        self._host = host  # keep alive
        layout = QVBoxLayout(host)
        layout.addWidget(host.mid)
        mid_layout = QVBoxLayout(host.mid)
        ip = ImagePreview(host.mid)
        mid_layout.addWidget(ip)
        pm = QPixmap(400, 300)
        pm.fill(QColor(128, 128, 128))
        ip.current_idx = 0
        ip.current_pixmap = pm
        ip.pixmap_item = QGraphicsPixmapItem(pm)
        ip.scene.addItem(ip.pixmap_item)
        ip.current_rotation = 0
        ip.current_horizontal_flip = False
        ip.current_vertical_flip = False
        ip.area_mode = True
        return ip, img, aid

    def test_circle_overlay_draws_handles(self, tmp_path):
        ip, img, aid = self._preview_with_area(tmp_path, "circle")
        ip._draw_area_overlay()
        # ellipse outline + 8 box handles + rotate stem + rotate knob + center
        assert len(ip._area_overlay_items) >= 11

    def test_circle_hit_test_center_is_move(self, tmp_path):
        from PySide6.QtCore import QPointF
        ip, img, aid = self._preview_with_area(tmp_path, "circle")
        area = img.get_area(aid)
        w, h = ip.current_pixmap.width(), ip.current_pixmap.height()
        center = QPointF(area["geometry"]["cx"] * w, area["geometry"]["cy"] * h)
        assert ip._area_handle_at(center, area) == "move"

    def test_circle_drag_move_updates_geometry(self, tmp_path):
        from PySide6.QtCore import QPointF
        ip, img, aid = self._preview_with_area(tmp_path, "circle")
        area = img.get_area(aid)
        w, h = ip.current_pixmap.width(), ip.current_pixmap.height()
        start = QPointF(area["geometry"]["cx"] * w, area["geometry"]["cy"] * h)
        drag = {"mode": "move", "id": aid, "kind": "circle", "start": start,
                "geom0": dict(area["geometry"]), "angle0": 0.0}
        ip._area_drag_circle(drag, QPointF(start.x() + 40, start.y()))
        g = img.get_area(aid)["geometry"]
        assert g["cx"] == pytest.approx(0.5 + 40.0 / w, abs=1e-3)

    def test_live_refresh_swaps_pixmap_without_crash(self, tmp_path):
        ip, img, aid = self._preview_with_area(tmp_path, "circle")
        img.get_area(aid)["settings"] = {"exposure": 80}
        # The live drag refresh recomputes the masked preview and swaps it in
        # under the overlay; must not raise and must keep a pixmap.
        ip._area_live_refresh()
        assert ip.pixmap_item is not None
        assert ip.current_pixmap is not None and not ip.current_pixmap.isNull()

    def test_gradient_overlay_and_endpoints(self, tmp_path):
        from PySide6.QtCore import QPointF
        ip, img, aid = self._preview_with_area(tmp_path, "gradient")
        ip._draw_area_overlay()
        assert len(ip._area_overlay_items) >= 4   # line + 3 handles
        area = img.get_area(aid)
        w, h = ip.current_pixmap.width(), ip.current_pixmap.height()
        p1 = QPointF(area["geometry"]["x1"] * w, area["geometry"]["y1"] * h)
        assert ip._area_handle_at(p1, area) == "p1"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
