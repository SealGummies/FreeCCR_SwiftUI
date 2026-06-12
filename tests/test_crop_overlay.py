#!/usr/bin/env python3
"""
Tests for the crop-mode darkening overlay: entering crop mode dims the whole
canvas when no box is drawn yet, and dims everything *outside* the box (with
control handles) once a selection exists.
"""

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout,  # noqa: E402
                               QGraphicsPixmapItem, QGraphicsRectItem,
                               QGraphicsPathItem)
from PySide6.QtGui import QPixmap, QColor  # noqa: E402
from PySide6.QtCore import QRectF  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])

from widgets.image_preview import ImagePreview  # noqa: E402


class _StubPanel:
    def set_sliders_enabled(self, *a):
        pass


class _Host(QWidget):
    def __init__(self):
        super().__init__()
        self.sliders_panel = _StubPanel()
        self.mid = QWidget(self)


_HOSTS = []


def _make_preview(w=400, h=300):
    host = _Host()
    _HOSTS.append(host)
    layout = QVBoxLayout(host)
    layout.addWidget(host.mid)
    mid_layout = QVBoxLayout(host.mid)
    ip = ImagePreview(host.mid)
    mid_layout.addWidget(ip)
    pm = QPixmap(w, h)
    pm.fill(QColor(128, 128, 128))
    ip.current_pixmap = pm
    ip.pixmap_item = QGraphicsPixmapItem(pm)
    ip.scene.addItem(ip.pixmap_item)
    # Coarse-orientation state _base_transform reads
    ip.current_rotation = 0
    ip.current_horizontal_flip = False
    ip.current_vertical_flip = False
    return ip


def test_no_box_dims_whole_canvas():
    ip = _make_preview()
    ip.crop_mode = True
    ip._pending_crop_local = None
    ip._draw_crop_overlay()
    overlay = ip._crop_overlay_item
    assert isinstance(overlay, QGraphicsRectItem)
    # Covers the full pixmap and is actually dark (non-zero alpha)
    assert overlay.rect() == QRectF(0, 0, 400, 300)
    assert overlay.brush().color().alpha() > 0
    # No handles when there is no selection yet
    assert ip._crop_handle_items == []


def test_box_dims_outside_and_shows_handles():
    ip = _make_preview()
    ip.crop_mode = True
    ip._pending_crop_local = QRectF(100, 75, 200, 150)
    ip._pending_crop_angle = 0.0
    ip._draw_crop_overlay()
    overlay = ip._crop_overlay_item
    # Hole-punched dim is a path item (full rect minus the selection polygon)
    assert isinstance(overlay, QGraphicsPathItem)
    assert overlay.brush().color().alpha() > 0
    # 8 corner/edge handles + rotate stem + rotate knob + center handle
    assert len(ip._crop_handle_items) >= 10


def test_not_in_crop_mode_draws_nothing():
    ip = _make_preview()
    ip.crop_mode = False
    ip._pending_crop_local = None
    ip._draw_crop_overlay()
    assert ip._crop_overlay_item is None
    assert ip._crop_handle_items == []


def test_tiny_box_falls_back_to_full_dim():
    ip = _make_preview()
    ip.crop_mode = True
    ip._pending_crop_local = QRectF(10, 10, 1, 1)  # sub-2px: not a real box
    ip._draw_crop_overlay()
    assert isinstance(ip._crop_overlay_item, QGraphicsRectItem)
    assert ip._crop_handle_items == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
