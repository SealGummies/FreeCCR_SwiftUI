#!/usr/bin/env python3
"""
Tests for color management: export colour-space transforms + ICC embedding,
and the input-ICC matrix-shaper engine. See spec/color-management.md §8.

Pure numpy/cv2/tifffile — no Qt required. Pillow-based assertions are skipped
when Pillow is not importable (it is not a runtime dependency).
"""
import io
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import cv2  # noqa: E402
import tifffile  # noqa: E402
from core import color_management as cm  # noqa: E402

try:
    from PIL import Image as PILImage, ImageCms  # noqa: E402
    HAS_PIL = True
except Exception:
    HAS_PIL = False


def _rand_u16(h=24, w=32, seed=3):
    rng = np.random.default_rng(seed)
    return (rng.random((h, w, 3)) * 65535).astype(np.uint16)


# --- 1. sRGB passthrough ---------------------------------------------------

def test_srgb_passthrough_is_pixel_identical():
    img = _rand_u16()
    out, icc = cm.apply_export_colorspace(img, "srgb")
    assert np.array_equal(out, img)
    assert icc == cm.SRGB_ICC_BYTES and len(icc) > 128


# --- 2. ProPhoto transform sanity ------------------------------------------

def test_prophoto_dtype_range_and_anchors():
    white = np.full((1, 1, 3), 65535, np.uint16)
    black = np.zeros((1, 1, 3), np.uint16)
    pw, icc = cm.apply_export_colorspace(white, "prophoto")
    pb, _ = cm.apply_export_colorspace(black, "prophoto")
    assert icc == cm.PROPHOTO_ICC_BYTES
    assert pw.dtype == np.uint16 and pb.dtype == np.uint16
    # sRGB white -> ProPhoto white (within a couple LSB of fixed-point rounding)
    assert (pw >= 65530).all()
    assert (pb == 0).all()


def test_prophoto_grey_roundtrips_through_inverse():
    # A mid grey re-encoded to ProPhoto then decoded back via the inverse
    # transform should land near the original sRGB value.
    mid = np.full((1, 1, 3), 128 * 257, np.uint16)
    pro, _ = cm.apply_export_colorspace(mid, "prophoto")
    v = pro.astype(np.float64) / 65535.0
    lin = np.where(v < 1 / 32, v / 16.0, np.power(v, 1.8))          # ROMM decode
    srgb_lin = lin @ np.linalg.inv(cm.M_SRGB2PROPHOTO).T
    back = cm.srgb_encode(np.clip(srgb_lin, 0, 1)) * 65535.0
    assert abs(float(back[0, 0, 0]) - mid[0, 0, 0]) < 200


def test_prophoto_is_clipped_and_uint16():
    img = _rand_u16(seed=11)
    out, _ = cm.apply_export_colorspace(img, "prophoto")
    assert out.dtype == np.uint16
    assert out.min() >= 0 and out.max() <= 65535
    assert out.shape == img.shape


# --- 3. ROMM toe -----------------------------------------------------------

def test_romm_toe_continuity_and_monotonic():
    Et = 1.0 / 512.0
    assert abs(16 * Et - Et ** (1 / 1.8)) < 1e-9
    xs = np.linspace(0, 1, 4096)
    ys = cm.romm_encode(xs)
    assert np.all(np.diff(ys) >= -1e-9)            # monotonic non-decreasing
    assert abs(ys[0]) < 1e-9 and abs(ys[-1] - 1.0) < 1e-9


def test_srgb_decode_encode_inverse():
    xs = np.linspace(0, 1, 1000)
    assert np.allclose(cm.srgb_decode(cm.srgb_encode(xs)), xs, atol=1e-6)


# --- 4. ICC validity -------------------------------------------------------

@pytest.mark.parametrize("target", ["srgb", "prophoto"])
def test_icc_profiles_are_valid(target):
    icc = cm.export_icc_bytes(target)
    assert icc[36:40] == b"acsp"                   # ICC signature
    assert icc[16:20] == b"RGB "                   # data colour space
    # In-house parser accepts our own matrix-shaper profile.
    prof = cm.InputProfile.from_bytes(icc)
    assert isinstance(prof, cm.InputProfile)
    if HAS_PIL:
        opened = ImageCms.getOpenProfile(io.BytesIO(icc))
        # building a transform proves lcms accepts it as a real profile
        ImageCms.buildTransform(opened, ImageCms.createProfile("sRGB"), "RGB", "RGB")


# --- 5. TIFF embed ---------------------------------------------------------

@pytest.mark.parametrize("target", ["srgb", "prophoto"])
def test_tiff_embeds_icc_tag(tmp_path, target):
    img = _rand_u16()
    out, icc = cm.apply_export_colorspace(img, target)
    p = str(tmp_path / f"{target}.tiff")
    tifffile.imwrite(p, out, photometric="rgb", compression="deflate", iccprofile=icc)
    with tifffile.TiffFile(p) as t:
        assert bytes(t.pages[0].tags[34675].value) == icc
        assert np.array_equal(t.pages[0].asarray(), out)


# --- 6. JPEG inject --------------------------------------------------------

def test_inject_jpeg_icc_single_and_multichunk():
    img = (_rand_u16() / 257).astype(np.uint8)
    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
    assert ok
    jpg = buf.tobytes()

    for icc in (cm.PROPHOTO_ICC_BYTES, bytes(range(256)) * 600):  # small, then >2 chunks
        out = cm.inject_jpeg_icc(jpg, icc)
        # still a decodable JPEG
        dec = cv2.imdecode(np.frombuffer(out, np.uint8), cv2.IMREAD_COLOR)
        assert dec is not None and dec.shape[2] == 3
        if HAS_PIL:
            got = PILImage.open(io.BytesIO(out)).info.get("icc_profile")
            assert got == icc


def test_inject_jpeg_icc_noop_on_non_jpeg():
    assert cm.inject_jpeg_icc(b"not a jpeg", cm.SRGB_ICC_BYTES) == b"not a jpeg"
    assert cm.inject_jpeg_icc(b"\xff\xd8rest", b"") == b"\xff\xd8rest"


# --- 7. InputProfile (matrix-shaper) ---------------------------------------

def _adobe_like_icc():
    """A synthetic Adobe-RGB-like matrix-shaper profile (gamma ~2.2)."""
    m = np.array([[0.5767309, 0.1855540, 0.1881852],
                  [0.2973769, 0.6273491, 0.0752741],
                  [0.0270343, 0.0706872, 0.9911085]])
    adapted = cm.M_BRADFORD_D65_D50 @ m
    return cm.build_matrix_shaper_icc(
        "Adobe-like", tuple(adapted[:, 0]), tuple(adapted[:, 1]), tuple(adapted[:, 2]),
        (2.19921875, 1.0, 0.0, 0.0, 0.0))   # type-3 with d=0 -> pure power curve


def test_input_profile_applies_and_preserves_shape():
    ip = cm.InputProfile.from_bytes(_adobe_like_icc())
    img = _rand_u16(seed=5)
    out = ip.apply(img)
    assert out.shape == img.shape and out.dtype == np.uint16
    # An Adobe-RGB-ish profile is a different gamut/gamma -> pixels must change.
    assert not np.array_equal(out, img)


def test_input_profile_adobe_linear_is_near_identity():
    # The input-profile working space is LINEAR Adobe RGB (so the density-based
    # negative inversion sees consistent linear data). An Adobe-primary profile
    # with a LINEAR TRC therefore round-trips to (near) the input.
    adobe_d50 = cm.M_BRADFORD_D65_D50 @ cm.M_ADOBE2XYZ_D65
    icc = cm.build_matrix_shaper_icc(
        "Adobe-linear", tuple(adobe_d50[:, 0]), tuple(adobe_d50[:, 1]),
        tuple(adobe_d50[:, 2]), (1.0, 1.0, 0.0, 1.0, 0.0))   # linear (identity) TRC
    ip = cm.InputProfile.from_bytes(icc)
    img = _rand_u16(seed=9)
    out = ip.apply(img)
    # Identity up to ICC s15Fixed16 colorant precision.
    assert int(np.abs(out.astype(int) - img.astype(int)).max()) < 64


def test_input_profile_srgb_relinearizes_to_adobe():
    # An sRGB profile is NO LONGER identity: it linearises the sRGB-encoded input
    # and re-expresses it in linear Adobe primaries (the working space), so the
    # output must differ substantially from the input.
    ip = cm.InputProfile.from_bytes(cm.SRGB_ICC_BYTES)
    img = _rand_u16(seed=9)
    out = ip.apply(img)
    assert int(np.abs(out.astype(int) - img.astype(int)).max()) > 64


def test_input_profile_passthrough_non_rgb():
    ip = cm.InputProfile.from_bytes(cm.SRGB_ICC_BYTES)
    gray = np.zeros((4, 4), np.uint16)
    assert ip.apply(gray).shape == (4, 4)


# --- 8. InputProfile rejects unsupported profiles --------------------------

def test_input_profile_rejects_lut_profile():
    import struct
    hdr = bytearray(128)
    struct.pack_into('>4s', hdr, 36, b'acsp')
    struct.pack_into('>4s', hdr, 16, b'RGB ')
    # Only a LUT tag, no colorant/TRC tags -> unsupported.
    table = struct.pack('>I', 1) + struct.pack('>4sII', b'A2B0', 128 + 16, 4) + b'\x00' * 4
    with pytest.raises(cm.UnsupportedICCError):
        cm.InputProfile.from_bytes(bytes(hdr) + table)


def test_input_profile_rejects_non_icc():
    with pytest.raises(cm.UnsupportedICCError):
        cm.InputProfile.from_bytes(b"definitely not an icc profile")


# --- 9. Export write-path regression (srgb pixel-identical) -----------------

def test_write_export_image_srgb_tiff_pixel_identical(tmp_path):
    from core import ccr_processor as cp

    class _Dummy:
        def resize_image_to_max_pixel(self, im, n):
            return im

    img = _rand_u16(seed=21)
    base = str(tmp_path / "out")
    cp.write_export_image(_Dummy(), img, base, jpg_out=False, jpg_quality=92,
                          max_long_side=None, output_colorspace="srgb")
    with tifffile.TiffFile(base + ".tiff") as t:
        assert np.array_equal(t.pages[0].asarray(), img)   # unchanged pixels
        assert bytes(t.pages[0].tags[34675].value) == cm.SRGB_ICC_BYTES


def test_write_export_image_prophoto_jpeg_has_profile(tmp_path):
    from core import ccr_processor as cp

    class _Dummy:
        def resize_image_to_max_pixel(self, im, n):
            return im

    img = _rand_u16(seed=22)
    base = str(tmp_path / "out")
    cp.write_export_image(_Dummy(), img, base, jpg_out=True, jpg_quality=90,
                          max_long_side=None, output_colorspace="prophoto")
    assert os.path.exists(base + ".jpg")
    if HAS_PIL:
        got = PILImage.open(base + ".jpg").info.get("icc_profile")
        assert got == cm.PROPHOTO_ICC_BYTES


# --- 10. Runtime integration (decode hook + backend lifecycle) -------------

def _make_qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv[:1])


def _write_negative_png(path, w=320, h=240, seed=7):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    base = 20000 + 25000 * (xx / w) + 10000 * (yy / h)
    img = np.stack([base * 1.2, base, base * 0.7], axis=-1)
    img += rng.normal(0, 1500, img.shape)
    img = np.clip(img, 1000, 64000).astype(np.uint16)
    cv2.imwrite(path, cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
    return img


def test_read_image_applies_active_input_profile(tmp_path):
    _make_qapp()
    from core.ccr_image import CCRImage
    p = str(tmp_path / "neg.png")
    _write_negative_png(p)
    cm.set_active_input_profile(None)
    try:
        a = CCRImage(p).resized_raw.copy()             # no profile
        cm.set_active_input_profile(cm.InputProfile.from_bytes(_adobe_like_icc()))
        b = CCRImage(p).resized_raw.copy()             # profile burned in at decode
        assert a.shape == b.shape
        assert not np.array_equal(a, b)
    finally:
        cm.set_active_input_profile(None)


def test_backend_set_and_clear_input_icc(tmp_path, monkeypatch):
    _make_qapp()
    monkeypatch.setenv("APPDATA", str(tmp_path))   # redirect app-data folder
    from core.ccr_backend import ccr_backend
    src = tmp_path / "srcprofile.icc"
    src.write_bytes(_adobe_like_icc())
    try:
        name = ccr_backend.set_input_icc(str(src))
        assert name
        assert cm.get_active_input_profile() is not None
        assert ccr_backend.input_icc_path and os.path.exists(ccr_backend.input_icc_path)
        assert str(tmp_path) in ccr_backend.input_icc_path     # copied under APPDATA
        ccr_backend.clear_input_icc()
        assert cm.get_active_input_profile() is None
        assert ccr_backend.input_icc_path is None
        assert not os.path.exists(str(tmp_path / "FreeCCR" / "input_profile.icc"))
    finally:
        cm.set_active_input_profile(None)
        ccr_backend.input_icc_path = None
        ccr_backend.input_icc_name = None


def _camera_dcp():
    from core import dcp_profile

    class _F:
        matrix = np.array([[0.46, 0.31, 0.17], [0.23, 0.70, 0.07], [0.02, 0.12, 0.93]])
    return dcp_profile.build_camera_dcp(_F(), "Cam")


def test_read_image_applies_active_dcp(tmp_path):
    _make_qapp()
    from core.ccr_image import CCRImage
    from core import dcp_profile
    p = str(tmp_path / "neg.png")
    _write_negative_png(p)
    cm.set_active_dcp_profile(None)
    try:
        a = CCRImage(p).resized_raw.copy()             # no profile
        cm.set_active_dcp_profile(dcp_profile.parse_dcp_bytes(_camera_dcp()))
        b = CCRImage(p).resized_raw.copy()             # DCP burned in at decode
        assert a.shape == b.shape and not np.array_equal(a, b)
    finally:
        cm.set_active_dcp_profile(None)


def test_backend_dcp_exclusivity_and_storage(tmp_path, monkeypatch):
    _make_qapp()
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from core.ccr_backend import ccr_backend
    dsrc = tmp_path / "c.dcp"; dsrc.write_bytes(_camera_dcp())
    isrc = tmp_path / "p.icc"; isrc.write_bytes(_adobe_like_icc())
    icc_store = str(tmp_path / "FreeCCR" / "input_profile.icc")
    dcp_store = str(tmp_path / "FreeCCR" / "input_profile.dcp")
    try:
        ccr_backend.set_input_icc(str(isrc))
        ccr_backend.set_input_dcp(str(dsrc))           # DCP set -> ICC must clear
        assert cm.get_active_dcp_profile() is not None
        assert cm.get_active_input_profile() is None
        assert os.path.exists(dcp_store) and not os.path.exists(icc_store)
        ccr_backend.set_input_icc(str(isrc))           # ICC set -> DCP must clear
        assert cm.get_active_input_profile() is not None
        assert cm.get_active_dcp_profile() is None
        assert not os.path.exists(dcp_store)
        ccr_backend.clear_input_icc()
        ccr_backend.clear_input_dcp()
        assert cm.get_active_dcp_profile() is None and cm.get_active_input_profile() is None
    finally:
        cm.set_active_input_profile(None)
        cm.set_active_dcp_profile(None)
        ccr_backend.input_icc_path = ccr_backend.input_icc_name = None
        ccr_backend.input_dcp_path = ccr_backend.input_dcp_name = None


def test_backend_rejects_lut_profile_unchanged(tmp_path, monkeypatch):
    _make_qapp()
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from core.ccr_backend import ccr_backend
    import struct
    hdr = bytearray(128)
    struct.pack_into('>4s', hdr, 36, b'acsp')
    struct.pack_into('>4s', hdr, 16, b'RGB ')
    table = struct.pack('>I', 1) + struct.pack('>4sII', b'A2B0', 144, 4) + b'\x00' * 4
    bad = tmp_path / "lut.icc"
    bad.write_bytes(bytes(hdr) + table)
    cm.set_active_input_profile(None)
    try:
        with pytest.raises(cm.UnsupportedICCError):
            ccr_backend.set_input_icc(str(bad))
        # active profile untouched on failure
        assert cm.get_active_input_profile() is None
    finally:
        cm.set_active_input_profile(None)
        ccr_backend.input_icc_path = None
        ccr_backend.input_icc_name = None


def test_reprocess_full_resets_all_images(tmp_path):
    """Switching the input ICC changes the decode itself, so every image is
    FULLY reset: conversion + non-destructive edits dropped and the scan
    re-decoded fresh (replaying a conversion computed against the old decode
    would mis-colour the result)."""
    _make_qapp()
    from core.ccr_image import CCRImage
    from core.ccr_backend import ccr_backend
    p = str(tmp_path / "neg.png")
    _write_negative_png(p)
    saved_images = ccr_backend.images
    cm.set_active_input_profile(None)
    try:
        a, b = CCRImage(p), CCRImage(p)
        for im in (a, b):
            im.converted = True
            im.adjustment_settings = {"exposure": 50}
            im.reference_frame = (1, 1, 50, 50)
        ccr_backend.images = [a, b]
        cm.set_active_input_profile(cm.InputProfile.from_bytes(_adobe_like_icc()))
        ccr_backend.reprocess_all_for_input_icc_change()
        for im in (a, b):
            assert im.converted is False
            assert im.adjustment_settings == {}
            assert im.reference_frame is None
            assert im.resized_raw is not None      # re-decoded fresh
    finally:
        ccr_backend.images = saved_images
        cm.set_active_input_profile(None)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
