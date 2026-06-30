#!/usr/bin/env python3
"""Tests for widgets/histogram_widget.HistogramWidget.

The widget owns all histogram presentation: the robust clipped vertical scale
(the fix for "overexposed crushes everything"), smoothing, and the actual
QPainter render. These exercise the scaling math directly and smoke-test the
paint path headless via QWidget.grab().
"""
import os
import sys

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])

from widgets.histogram_widget import HistogramWidget  # noqa: E402


def _widget(data=None):
    w = HistogramWidget()
    w.resize(320, 150)
    if data is not None:
        w.set_data(data)
    return w


def _gaussian(center, height, sigma=25.0):
    x = np.arange(256, dtype=np.float32)
    return height * np.exp(-((x - center) / sigma) ** 2)


# --- set_data validation --------------------------------------------------

def test_set_data_accepts_3x256():
    w = _widget(np.ones((3, 256), dtype=np.float32))
    assert w._data is not None and w._data.shape == (3, 256)


def test_set_data_rejects_bad_shape():
    w = _widget(np.ones((3, 255), dtype=np.float32))
    assert w._data is None        # wrong shape → stored as None, paints empty


def test_set_data_none_clears():
    w = _widget(np.ones((3, 256), dtype=np.float32))
    w.set_data(None)
    assert w._data is None


def _broad(weight, center, sigma):
    """A broad tonal lobe normalised to hold `weight` fraction of N pixels."""
    a = _gaussian(center, 1.0, sigma)
    return a / a.sum() * weight


# --- the scale fix: a dominant pile must NOT crush the rest ---------------

def test_blown_pile_clips_but_content_is_not_crushed():
    """The canonical complaint: a blown-highlight pile near white clips flat at
    the top while the rest of the (broad) tonal content still fills the height —
    not crushed to the floor as it was before."""
    N = 800_000
    scene = _broad(0.7, 110, 45) * N          # broad scene across the range
    scene[250:256] += 0.30 * N / 6            # blown pile near white (6 bins)
    h = _widget(np.tile(scene, (3, 1)))._scaled_heights()
    assert h[:, 250:256].max() >= 0.999       # the pile clips flat at the top
    assert h[0, 60:230].max() > 0.8           # scene fills the height (not crushed)


def test_normal_distribution_reaches_near_top():
    """With no dominant pile, the tallest bin reaches near full height (familiar
    histogram) — the scale isn't needlessly compressing a clean image."""
    data = np.tile(_gaussian(128, 5000.0), (3, 1)).astype(np.float32)
    h = _widget(data)._scaled_heights()
    assert 0.9 <= h.max() <= 1.0


def test_spread_bright_region_does_not_crush_subject():
    """A bright region spread over many tonal bins (a gradient sky / window) must
    not crush a structured subject to the floor — the per-bin mass threshold of
    the first cut failed this; the percentile reference keeps the subject visible
    and clips the bright cluster."""
    N = 800_000
    subject = _broad(0.64, 120, 45) * N       # structured subject, midtones
    subject[235:255] += 0.36 * N / 20         # bright region spread over 20 bins
    h = _widget(np.tile(subject, (3, 1)))._scaled_heights()
    assert h[0, 235:255].max() > 0.9          # bright cluster sits at the top
    assert h[0, 60:200].max() > 0.35          # subject clearly visible, not floored


def test_bimodal_transition_not_overamplified():
    """Two dominant piles with a sparse transition between them: the piles clip,
    but the near-empty transition must NOT be amplified to full height (the floor
    term bounds it). Regression for the bimodal over-amplification finding."""
    N = 800_000
    a = np.zeros(256, dtype=np.float32)
    a[18:23] += 0.45 * N / 5                  # dark pile
    a[233:238] += 0.45 * N / 5                # bright pile
    a[60:200] += 0.10 * N / 140               # sparse transition (0.07%/bin)
    h = _widget(np.tile(a, (3, 1)))._scaled_heights()
    assert h[0, 18:23].max() > 0.9 and h[0, 233:238].max() > 0.9   # piles at the top
    assert h[0, 60:200].max() < 0.3           # transition stays subordinate


def test_clip_pile_draws_no_full_height_edge_spike():
    """A pile at the pure-clip bins (0/255) must not draw a full-height column at
    the plot edge, nor bleed into its neighbours via smoothing — the bins are
    zeroed before the blur, and clipping is shown by the corner wedges instead.
    Regression for the reintroduced 'vertical line where it clipped'."""
    bins = _gaussian(128, 4000.0)
    bins[255] = 300_000.0                      # huge highlight-clip pile
    bins[0] = 200_000.0                        # huge shadow-clip pile
    h = _widget(np.tile(bins, (3, 1)))._scaled_heights()
    assert h[0, 255] < 0.05 and h[0, 0] < 0.05         # no edge spike
    assert h[0, 253] < 0.1 and h[0, 2] < 0.1           # no bleed into neighbours
    assert h[0, 120:136].max() > 0.8                   # real content unaffected


# --- paint smoke tests (headless) ----------------------------------------

def test_paint_with_data_does_not_crash():
    data = np.tile(_gaussian(128, 5000.0), (3, 1)).astype(np.float32)
    pm = _widget(data).grab()
    assert not pm.isNull()


def test_paint_with_clipping_does_not_crash():
    bins = _gaussian(128, 3000.0)
    bins[0] = 80000.0       # shadow clip → bottom-left wedge
    bins[255] = 80000.0     # highlight clip → bottom-right wedge
    data = np.tile(bins, (3, 1)).astype(np.float32)
    pm = _widget(data).grab()
    assert not pm.isNull()


def test_paint_empty_does_not_crash():
    pm = _widget(None).grab()       # no data → background + grid only
    assert not pm.isNull()


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
