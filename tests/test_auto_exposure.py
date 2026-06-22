#!/usr/bin/env python3
"""
Tests for default-slope auto-exposure (spec/auto-exposure-default-slope.md).

compute_auto_exposure_gain places the (holder-excluded) 98th-percentile
luminance at ~98% of full scale, as a uniform Input-Gain base (ch_input_gain
units, ig = 2^(x/50)). Pure-numpy, runs headless.
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.ccr_processor import (  # noqa: E402
    compute_auto_exposure_gain,
    AUTO_EXPOSURE_TARGET,
    EXPOSURE_BASE_MAX,
)

MAX = 65535.0


def _gray(value, h=100, w=100):
    """A uniform gray image (all channels equal → luminance == value)."""
    return np.full((h, w, 3), int(round(value)), dtype=np.uint16)


def test_nominal_gain_targets_98_percent():
    """A mid-gray image (98th pct at 50%) yields the nominal exposure base that
    WOULD place it at ~98% (the pipeline then applies it tone-aware, so the top
    rolls off rather than clipping)."""
    img = _gray(0.5 * MAX)
    gain = compute_auto_exposure_gain(img)
    expected = 50.0 * np.log2((AUTO_EXPOSURE_TARGET * MAX) / (0.5 * MAX))
    assert abs(gain - expected) < 1.0
    nominal_factor = 2 ** (gain / 50.0)          # nominal stop multiplier (2^(x/50))
    assert abs(0.5 * MAX * nominal_factor - AUTO_EXPOSURE_TARGET * MAX) < 0.02 * MAX


def test_excludes_pure_white_holder():
    """A large pure-white region (film holder) must not change the estimate."""
    base = compute_auto_exposure_gain(_gray(0.5 * MAX))
    img = _gray(0.5 * MAX)
    img[:, :50, :] = 65535                       # half the frame is holder-white
    assert abs(base - compute_auto_exposure_gain(img)) < 1.0


def test_too_little_content_returns_zero():
    """If almost everything is pure white, there isn't enough content → 0."""
    img = np.full((100, 100, 3), 65535, dtype=np.uint16)
    img[0, 0, :] = 30000                          # 1/10000 pixels non-white (<0.5%)
    assert compute_auto_exposure_gain(img) == 0.0


def test_already_bright_no_gain():
    """An image whose 98th pct already sits at the target needs ~no gain."""
    img = _gray(AUTO_EXPOSURE_TARGET * MAX)
    assert abs(compute_auto_exposure_gain(img)) < 1.0


def test_clamps_very_dark_image():
    """A very dark image would need a huge boost → clamped to the max."""
    assert compute_auto_exposure_gain(_gray(100)) == EXPOSURE_BASE_MAX


def test_gain_is_float():
    assert isinstance(compute_auto_exposure_gain(_gray(0.4 * MAX)), float)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
