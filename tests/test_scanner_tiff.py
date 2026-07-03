#!/usr/bin/env python3
"""
Scanner-TIFF loading + macOS report regressions (GitHub issues #86 / #87).

Pakon / Nikon Coolscan TIFFs exposed three classes of bug:
- PLANAR TIFFs (PlanarConfiguration=2) were misread by OpenCV into scrambled
  channels without failing; LZW-compressed files needed imagecodecs; float
  and wide-integer sample formats slipped through un-normalized.
- The un-converted preview's auto-brightness subtracted a per-frame black
  offset, shifting the film base's hue differently on every frame.
- A print() containing an em dash crashed every B/W-point conversion on
  macOS, where the .app's stdout is ASCII (UnicodeEncodeError).
"""
import io
import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])

import tifffile  # noqa: E402
from core.ccr_image import (CCRImage, _read_tiff_bgr,  # noqa: E402
                            _to_uint16_full_range)


def _rgb_ramp(h=64, w=96):
    """16-bit RGB with distinct per-channel content (means far apart)."""
    img = np.zeros((h, w, 3), np.uint16)
    img[..., 0] = 50000   # R
    img[..., 1] = 30000   # G
    img[..., 2] = 10000   # B
    img[: h // 4, : w // 4] = (60000, 40000, 20000)  # some structure
    return img


def _assert_channels(loaded, expected_means, tol=1500):
    got = loaded.astype(np.float64).reshape(-1, 3).mean(axis=0)
    for g, e in zip(got, expected_means):
        assert abs(g - e) < tol, f"channel means {got} != {expected_means}"


# --- TIFF layouts through the real load path ---------------------------------
class TestScannerTiffLoading:
    def test_planar_tiff_channels_not_scrambled(self, tmp_path):
        # Pakon-style: 16-bit, PlanarConfiguration=2 (RRR..GGG..BBB).
        src = _rgb_ramp()
        path = str(tmp_path / "planar.tif")
        tifffile.imwrite(path, src, planarconfig="separate")
        img = CCRImage(path)
        assert img.resized_raw is not None
        _assert_channels(img.resized_raw,
                         src.reshape(-1, 3).mean(axis=0))

    def test_interleaved_tiff_still_loads(self, tmp_path):
        src = _rgb_ramp()
        path = str(tmp_path / "contig.tif")
        tifffile.imwrite(path, src, planarconfig="contig")
        img = CCRImage(path)
        _assert_channels(img.resized_raw,
                         src.reshape(-1, 3).mean(axis=0))

    def test_lzw_tiff_loads(self, tmp_path):
        # VueScan/NikonScan-style compressed output (needs imagecodecs).
        src = _rgb_ramp()
        path = str(tmp_path / "lzw.tif")
        tifffile.imwrite(path, src, compression="lzw")
        img = CCRImage(path)
        _assert_channels(img.resized_raw,
                         src.reshape(-1, 3).mean(axis=0))

    def test_lzw_planar_tiff_loads(self, tmp_path):
        src = _rgb_ramp()
        path = str(tmp_path / "lzw_planar.tif")
        tifffile.imwrite(path, src, compression="lzw", planarconfig="separate")
        img = CCRImage(path)
        _assert_channels(img.resized_raw,
                         src.reshape(-1, 3).mean(axis=0))

    def test_float_tiff_normalizes(self, tmp_path):
        # Float TIFFs (0..1) previously slipped through un-normalized and
        # rendered black.
        src = np.zeros((32, 48, 3), np.float32)
        src[..., 0], src[..., 1], src[..., 2] = 0.75, 0.5, 0.25
        path = str(tmp_path / "float.tif")
        tifffile.imwrite(path, src)
        img = CCRImage(path)
        _assert_channels(img.resized_raw,
                         (0.75 * 65535, 0.5 * 65535, 0.25 * 65535))


# --- helpers ------------------------------------------------------------------
class TestHelpers:
    def test_read_tiff_bgr_moves_planar_axis(self, tmp_path):
        src = _rgb_ramp()
        path = str(tmp_path / "p.tif")
        tifffile.imwrite(path, src, planarconfig="separate")
        bgr = _read_tiff_bgr(path)
        assert bgr.shape == src.shape
        assert bgr[0, -1, 0] == src[0, -1, 2]   # B plane in slot 0
        assert bgr[0, -1, 2] == src[0, -1, 0]   # R plane in slot 2

    def test_to_uint16_full_range(self):
        u8 = np.array([[0, 255]], np.uint8)
        assert _to_uint16_full_range(u8).tolist() == [[0, 65535]]
        f32 = np.array([[0.0, 0.5, 1.0]], np.float32)
        out = _to_uint16_full_range(f32)
        assert out.dtype == np.uint16
        assert abs(int(out[0, 1]) - 32768) <= 1 and out[0, 2] == 65535
        i16 = np.array([[0, 32767]], np.int16)
        out = _to_uint16_full_range(i16)
        assert out[0, 1] == 65535
        u16 = np.array([[123]], np.uint16)
        assert _to_uint16_full_range(u16) is u16


# --- preview auto-brightness is hue-preserving --------------------------------
class TestPreviewAutoBrightness:
    def _panel_img(self, base, blank_fraction):
        h, w = 64, 64
        img = np.zeros((h, w, 3), np.uint16)
        rows = int(h * blank_fraction)
        img[:rows] = base
        return img

    def test_base_colour_identical_across_frames(self, tmp_path):
        # A mostly-blank frame and a mostly-exposed frame must render the
        # film base with the SAME colour (the old offset subtraction turned
        # pink base orange/red on blank frames — issue #86).
        import cv2
        base = (48000, 32000, 36000)
        path = str(tmp_path / "x.png")
        cv2.imwrite(path, np.zeros((8, 8, 3), np.uint8))
        img = CCRImage(path)
        mostly_blank = img._auto_brightness_for_preview(
            self._panel_img(base, 0.9))
        mostly_exposed = img._auto_brightness_for_preview(
            self._panel_img(base, 0.2))
        a = mostly_blank[0, 0].astype(np.float64)
        b = mostly_exposed[0, 0].astype(np.float64)
        assert np.allclose(a, b, atol=2.0)
        # Channel ratios (hue) match the source base.
        src = np.array(base, np.float64)
        assert np.allclose(a / a.max(), src / src.max(), atol=0.01)


# --- print() must survive an ASCII stdout (macOS .app) ------------------------
class TestStreamHardening:
    def test_em_dash_print_survives_ascii_stdout(self, monkeypatch):
        from utils.stream_hardening import harden_std_streams
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding="ascii", write_through=True)
        monkeypatch.setattr(sys, "stdout", stream)
        with pytest.raises(UnicodeEncodeError):
            print("highlight headroom — recover")
        harden_std_streams()
        print("highlight headroom — recover")   # must not raise
        stream.flush()
        assert b"highlight headroom" in raw.getvalue()

    def test_none_streams_are_tolerated(self, monkeypatch):
        from utils.stream_hardening import harden_std_streams
        monkeypatch.setattr(sys, "stdout", None)
        harden_std_streams()   # must not raise


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
