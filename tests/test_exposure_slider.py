#!/usr/bin/env python3
"""Tests for the Exposure slider (spec/exposure-slider.md).

Exposure is a second, wide brightness control under Gain, ported from
OpenEnlarge's faithful exposure arm: a black-anchored LINEAR-LIGHT gain `×2^EV`
applied in scene-linear light before the tone curve. The raw slider value
v ∈ [-100,100] maps to EV = (v/100)·5, so ±100 = ±5 EV and v=0 is an exact
identity. In the working space it runs un-clamped (pre window-clamp), so +EV
lands in recoverable highlight headroom. Pure numpy core, runs headless.
"""
import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.ccr_processor import (  # noqa: E402
    adjust_image, adjust_image_opencl, encode_window,
    _exposure_ev_gain, EXPO_EV_MAX, OPENCL_AVAILABLE,
)


def _ws_base(d):
    """Windowed base from a display array `d` (overshoot beyond 1 is kept as
    headroom up to the container ceiling)."""
    return encode_window(np.asarray(d, dtype=np.float32).copy())


# --- the EV → multiply mapping -------------------------------------------

def test_constants():
    assert EXPO_EV_MAX == 5.0
    assert _exposure_ev_gain(0) == 1.0                     # exact identity
    assert _exposure_ev_gain(20) == pytest.approx(2.0)     # +1 EV
    assert _exposure_ev_gain(-20) == pytest.approx(0.5)    # -1 EV
    assert _exposure_ev_gain(100) == pytest.approx(32.0)   # +5 EV
    assert _exposure_ev_gain(-100) == pytest.approx(1.0 / 32.0)
    # clamps beyond ±100
    assert _exposure_ev_gain(500) == pytest.approx(32.0)
    assert _exposure_ev_gain(-500) == pytest.approx(1.0 / 32.0)


# --- working-space (default) path ----------------------------------------

def test_identity_is_byte_noop_ws():
    """exposure_ev=0 is byte-identical to omitting it (the multiply branch is
    skipped entirely)."""
    base = _ws_base(np.full((16, 16, 3), 0.4))
    a = adjust_image(base, ws_windowed=True)
    b = adjust_image(base, exposure_ev=0.0, ws_windowed=True)
    assert np.array_equal(a, b)


def test_plus_one_ev_doubles_linear_ws():
    """A mid-grey base well inside headroom: +1 EV (slider +20) ≈ doubles the
    de-windowed linear output vs EV 0."""
    base = _ws_base(np.full((16, 16, 3), 0.25))            # 0.25 is window-exact
    ev0 = adjust_image(base, exposure_ev=0.0, ws_windowed=True).astype(np.float32)
    ev1 = adjust_image(base, exposure_ev=20.0, ws_windowed=True).astype(np.float32)
    assert ev1.mean() / ev0.mean() == pytest.approx(2.0, rel=0.01)


def test_black_is_anchored():
    """Pure black stays 0 at any EV (black-anchored, no lift / colour cast)."""
    base = _ws_base(np.zeros((16, 16, 3)))
    for v in (-100.0, -50.0, 50.0, 100.0):
        out = adjust_image(base, exposure_ev=v, ws_windowed=True)
        assert int(out.max()) == 0


def test_headroom_recoverable_ws():
    """A highlight stored in headroom (d=1.5, above display white) clips to white
    at EV 0 but is pulled back below white by -1 EV — headroom is recoverable,
    exactly like the Gain control."""
    base = _ws_base(np.full((16, 16, 3), 1.5))             # 1.5 stored in headroom
    ev0 = adjust_image(base, exposure_ev=0.0, ws_windowed=True).astype(np.float32)
    evm = adjust_image(base, exposure_ev=-20.0, ws_windowed=True).astype(np.float32)
    assert ev0.mean() == pytest.approx(65535.0, rel=1e-3)  # clipped to white
    assert evm.mean() < ev0.mean()                          # recovered
    assert evm.mean() / 65535.0 == pytest.approx(0.75, rel=0.02)  # 1.5 × 0.5


def test_plus_ev_clips_to_white_when_no_headroom():
    """+EV on near-white in-range content saturates toward white (expected)."""
    base = _ws_base(np.full((16, 16, 3), 0.9))
    out = adjust_image(base, exposure_ev=20.0, ws_windowed=True).astype(np.float32)
    assert out.mean() == pytest.approx(65535.0, rel=1e-3)


def test_compose_with_gain_commutes_ws():
    """Exposure and Gain are both linear multiplies on the un-clamped working
    buffer, so adding +1 EV on top of any Gain multiplies the output by ≈2."""
    base = _ws_base(np.full((16, 16, 3), 0.125))           # leaves headroom
    gain_only = adjust_image(base, exposure=60.0, ws_windowed=True).astype(np.float32)
    gain_ev = adjust_image(base, exposure=60.0, exposure_ev=20.0,
                           ws_windowed=True).astype(np.float32)
    assert gain_ev.mean() / gain_only.mean() == pytest.approx(2.0, rel=0.01)


# --- legacy non-windowed path --------------------------------------------

def test_nonws_plus_one_ev_doubles():
    """Without the working space the base is full-range; +1 EV doubles a midtone
    (clamped at white)."""
    img = np.full((16, 16, 3), int(0.25 * 65535), dtype=np.uint16)
    ev0 = adjust_image(img, exposure_ev=0.0, ws_windowed=False).astype(np.float32)
    ev1 = adjust_image(img, exposure_ev=20.0, ws_windowed=False).astype(np.float32)
    assert ev1.mean() / ev0.mean() == pytest.approx(2.0, rel=0.01)


def test_nonws_matches_ws_midtone():
    """ws and non-ws paths place an in-range midtone at the same EV multiply."""
    d = 0.25
    ws = adjust_image(_ws_base(np.full((16, 16, 3), d)), exposure_ev=20.0,
                      ws_windowed=True).astype(np.float32)
    full = adjust_image(np.full((16, 16, 3), int(d * 65535), dtype=np.uint16),
                        exposure_ev=20.0, ws_windowed=False).astype(np.float32)
    assert ws.mean() / 65535.0 == pytest.approx(0.5, rel=0.01)
    assert full.mean() / 65535.0 == pytest.approx(0.5, rel=0.01)


# --- CPU / GPU parity -----------------------------------------------------

@pytest.mark.skipif(not OPENCL_AVAILABLE, reason="OpenCL not available")
@pytest.mark.parametrize("ws", [True, False])
@pytest.mark.parametrize("v", [30.0, -30.0])
def test_cpu_gpu_parity(ws, v):
    """The Exposure multiply rides shared numpy pre-stages (the recovery in ws
    mode, a pre-kernel multiply otherwise), so CPU and GPU stay byte-identical."""
    rng = np.random.default_rng(1)
    d = rng.uniform(0.05, 0.8, size=(48, 48, 3)).astype(np.float32)
    src = _ws_base(d) if ws else (d * 65535.0).astype(np.uint16)
    cpu = adjust_image(src, exposure_ev=v, ws_windowed=ws).astype(np.int32)
    gpu = adjust_image_opencl(src, exposure_ev=v, ws_windowed=ws).astype(np.int32)
    assert np.abs(cpu - gpu).max() <= 1


# --- UI wiring ------------------------------------------------------------

def test_slider_wiring():
    """exposure_ev is positioned right after exposure in ADJUSTMENT_KEYS, lives
    in the Tone sync group, and the created-slider count matches the key count."""
    from PySide6.QtWidgets import QApplication
    from widgets.sliders_panel import SlidersPanel, SYNC_GROUPS

    keys = SlidersPanel.ADJUSTMENT_KEYS
    assert "exposure_ev" in keys
    assert keys.index("exposure_ev") == keys.index("exposure") + 1

    tone = dict((gid, members) for gid, _label, members in SYNC_GROUPS)["tone"]
    assert "exposure_ev" in tone

    _app = QApplication.instance() or QApplication(sys.argv[:1])
    panel = SlidersPanel()
    # Every adjustment key must have a matching slider (strict positional zip).
    assert len(panel.sliders) == len(panel.adjustment_keys)
    ev_idx = panel.adjustment_keys.index("exposure_ev")
    sl = panel.sliders[ev_idx]
    assert (sl.minimum(), sl.maximum()) == (-100, 100)
    assert sl.value() == 0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
