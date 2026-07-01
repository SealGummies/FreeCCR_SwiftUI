#!/usr/bin/env python3
"""Tests for the Gamma slider (midtone gamma bow).

Gamma is a clean, endpoint-pinned power curve on the visible [0,1] window:

    gamma_exp = 2 ** (-gamma / 100)   # +100 -> 0.5, 0 -> 1.0, -100 -> 2.0
    out = clip(in/65535, 0, 1) ** gamma_exp * 65535

+ lifts midtones, - lowers them, black/white stay pinned. See
spec/gamma-slider.md.
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.ccr_processor import (  # noqa: E402
    adjust_image, adjust_image_opencl, OPENCL_AVAILABLE,
)


def _solid(value):
    """A small solid 16-bit RGB image at the given level."""
    return np.full((4, 4, 3), value, dtype=np.uint16)


def test_gamma_zero_is_identity():
    """gamma=0 skips the block; the image is returned unchanged."""
    img = np.linspace(0, 65535, 4 * 4 * 3).reshape(4, 4, 3).astype(np.uint16)
    out = adjust_image(img, gamma=0.0)
    assert np.array_equal(out, img)


def test_gamma_positive_lifts_midtones():
    """+100 -> exponent 0.5: mid-gray 0.25 -> ~0.5 (brighter)."""
    mid = int(round(0.25 * 65535))
    out = int(adjust_image(_solid(mid), gamma=100.0)[0, 0, 0])
    expected = int((mid / 65535.0) ** (2.0 ** -1.0) * 65535)
    assert abs(out - expected) <= 2
    assert out > mid                      # clearly brighter


def test_gamma_negative_lowers_midtones():
    """-100 -> exponent 2.0: mid-gray 0.5 -> ~0.25 (darker)."""
    mid = int(round(0.5 * 65535))
    out = int(adjust_image(_solid(mid), gamma=-100.0)[0, 0, 0])
    expected = int((mid / 65535.0) ** (2.0 ** 1.0) * 65535)
    assert abs(out - expected) <= 2
    assert out < mid                      # clearly darker


@pytest.mark.parametrize("g", [100.0, -100.0, 50.0, -37.0])
def test_gamma_pins_endpoints(g):
    """Pure black and pure white are invariant for any gamma (0^e=0, 1^e=1)."""
    black, white = _solid(0), _solid(65535)
    assert np.array_equal(adjust_image(black, gamma=g), black)
    assert np.array_equal(adjust_image(white, gamma=g), white)


def test_gamma_symmetry_roundtrip():
    """+g then -g are reciprocal exponents, so a mid value round-trips."""
    mid = int(round(0.4 * 65535))
    up = adjust_image(_solid(mid), gamma=60.0)
    back = int(adjust_image(up, gamma=-60.0)[0, 0, 0])
    assert abs(back - mid) <= 3           # allow a little quantization drift


@pytest.mark.skipif(not OPENCL_AVAILABLE, reason="OpenCL not available")
def test_gamma_cpu_opencl_parity():
    """The OpenCL kernel matches the CPU path for a non-zero gamma."""
    rng = np.random.default_rng(0)
    img = rng.integers(0, 65536, size=(64, 64, 3), dtype=np.uint16)
    cpu = adjust_image(img, gamma=70.0).astype(np.int32)
    gpu = adjust_image_opencl(img, gamma=70.0).astype(np.int32)
    assert np.max(np.abs(cpu - gpu)) <= 4   # float32 kernel vs float64 numpy
