#!/usr/bin/env python3
"""
Tests for Adobe DCP (DNG camera profile) support — core.dcp_profile. Parse
(II/MM, SRATIONAL, inline/offset), apply (ForwardMatrix + ColorMatrix fallback,
dual-illuminant), generate (build_camera_dcp round-trip), and the linear-Adobe
output contract. See spec/dcp-camera-profile.md §8. Pure numpy/struct — no Qt.
"""
import os
import struct
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core import dcp_profile as dcp        # noqa: E402
from core import it8_profile as it8        # noqa: E402
from core import color_management as cm    # noqa: E402

D50 = np.array(cm.D50_XYZ)
M0 = np.array([[0.46, 0.31, 0.17], [0.23, 0.70, 0.07], [0.02, 0.12, 0.93]])


class _Fit:
    def __init__(self, m): self.matrix = m


def _neutral(M):
    n = np.linalg.inv(M) @ D50
    return n / np.max(np.abs(n))


# --------------------------------------------------------------------------- #
# Generate -> parse round-trip.
# --------------------------------------------------------------------------- #

def test_build_parse_roundtrip():
    data = dcp.build_camera_dcp(_Fit(M0), "Cam D50", illuminant=23)
    assert data[0:2] == b'II' and struct.unpack('<H', data[2:4])[0] == 42
    p = dcp.parse_dcp_bytes(data)
    assert p.name == "Cam D50" and p.illuminant_1 == 23
    assert p.has_forward and not p.is_dual
    N = _neutral(M0)
    np.testing.assert_allclose(p.forward_matrix_1, M0 @ np.diag(N), atol=1e-4)
    np.testing.assert_allclose(p.color_matrix_1, np.linalg.inv(M0 @ np.diag(N)), atol=1e-4)


def test_parse_big_endian_mm():
    # A minimal MM (big-endian) DCP with only ColorMatrix1 (SRATIONAL, offset).
    en = '>'
    vals = M0.ravel()
    data_off = 8 + 2 + 12 + 4
    body = b''.join(struct.pack(en + 'ii', int(round(v * 1e6)), 1000000) for v in vals)
    entry = struct.pack(en + 'HHI', 50721, 10, 9) + struct.pack(en + 'I', data_off)
    ifd = struct.pack(en + 'H', 1) + entry + struct.pack(en + 'I', 0)
    blob = struct.pack(en + '2sHI', b'MM', 42, 8) + ifd + body
    p = dcp.parse_dcp_bytes(blob)
    np.testing.assert_allclose(p.color_matrix_1, M0, atol=1e-5)
    assert not p.has_forward                        # CM-only -> fallback path


def test_inline_short_and_negative_srational():
    p = dcp.parse_dcp_bytes(dcp.build_camera_dcp(_Fit(M0), "x", illuminant=17))
    assert p.illuminant_1 == 17                      # SHORT inline
    # ColorMatrix has negative entries (inverse of a positive matrix) -> SRATIONAL signs
    assert np.any(p.color_matrix_1 < 0)


# --------------------------------------------------------------------------- #
# Apply.
# --------------------------------------------------------------------------- #

def test_apply_forward_roundtrip_and_icc_parity():
    p = dcp.parse_dcp_bytes(dcp.build_camera_dcp(_Fit(M0), "c"))
    N = _neutral(M0)
    rng = np.random.default_rng(0)
    cam = np.clip(N * rng.uniform(0, 1, (8, 8, 3)), 0, 1)     # in-range after WB
    cam_u16 = (cam * 65535).astype(np.uint16)
    out = dcp.apply_dcp(p, cam_u16, as_shot_wb=1.0 / N)
    expect = np.clip((cam_u16 / 65535.0) @ (cm.M_XYZ_D50_2_ADOBE @ M0).T, 0, 1)
    assert np.abs(out.astype(int) - np.rint(expect * 65535).astype(int)).max() <= 3
    # parity: the same fit as a matrix ICC produces the same linear-Adobe output.
    mp = cm.InputProfile.from_bytes(it8.build_camera_icc(_Fit(M0), "m"))
    assert np.abs(out.astype(int) - mp.apply(cam_u16).astype(int)).max() <= 3


def test_apply_is_linear_not_srgb():
    # A 50% linear-Adobe grey must come out ~mid (linear), not sRGB-bumped (~0.73).
    p = dcp.parse_dcp_bytes(dcp.build_camera_dcp(_Fit(M0), "c"))
    N = _neutral(M0)
    cam = np.clip(N * 0.5, 0, 1)
    cam_u16 = np.tile((cam * 65535).astype(np.uint16).reshape(1, 1, 3), (2, 2, 1))
    out = dcp.apply_dcp(p, cam_u16, as_shot_wb=1.0 / N)[0, 0].astype(float) / 65535.0
    assert out.max() - out.min() < 0.03              # near-neutral
    assert 0.40 < out.mean() < 0.60                  # linear ~0.5, not ~0.73


def test_colormatrix_fallback_near_neutral():
    # A profile with ONLY a ColorMatrix (no ForwardMatrix): the fallback path
    # still lands the camera neutral near-neutral in linear Adobe.
    N = _neutral(M0)
    CM = np.linalg.inv(M0 @ np.diag(N))
    p = dcp.DcpProfile(name="cm-only", color_matrix_1=CM, illuminant_1=23,
                       camera_calibration_1=np.eye(3), camera_calibration_2=np.eye(3),
                       analog_balance=np.ones(3))
    assert not p.has_forward
    grey = np.tile((np.clip(N * 0.5, 0, 1) * 65535).astype(np.uint16).reshape(1, 1, 3),
                   (2, 2, 1))
    out = dcp.apply_dcp(p, grey, as_shot_wb=1.0 / N)[0, 0].astype(float)
    assert out.max() - out.min() < 0.06 * 65535


# --------------------------------------------------------------------------- #
# Dual-illuminant interpolation.
# --------------------------------------------------------------------------- #

def test_dual_illuminant_weight_and_blend():
    FM1 = M0 @ np.diag(_neutral(M0))
    FM2 = (M0 * 1.1) @ np.diag(_neutral(M0 * 1.1))
    p = dcp.DcpProfile(name="dual", forward_matrix_1=FM1, forward_matrix_2=FM2,
                       color_matrix_1=np.linalg.inv(FM1), color_matrix_2=np.linalg.inv(FM2),
                       illuminant_1=21, illuminant_2=17,        # D65 / StdA
                       camera_calibration_1=np.eye(3), camera_calibration_2=np.eye(3),
                       analog_balance=np.ones(3))
    assert p.is_dual
    # weight is in [0,1] and the mired formula hits the endpoints at the cal CCTs
    inv = lambda t: 1e6 / t
    T1, T2 = 6504.0, 2856.0
    for T, expect in [(6504.0, 1.0), (2856.0, 0.0)]:
        w = np.clip((inv(T) - inv(T2)) / (inv(T1) - inv(T2)), 0, 1)
        assert abs(w - expect) < 1e-6
    # a dual profile applies and the result lies between the two single-FM results
    N = _neutral(M0)
    cam = np.tile((np.clip(N * 0.4, 0, 1) * 65535).astype(np.uint16).reshape(1, 1, 3), (2, 2, 1))
    out = dcp.apply_dcp(p, cam, as_shot_wb=1.0 / N)
    assert out.shape == cam.shape and out.dtype == np.uint16


# --------------------------------------------------------------------------- #
# Synthetic-camera round-trip (key).
# --------------------------------------------------------------------------- #

def test_synthetic_camera_roundtrip():
    # IT8 reference patches; camera-native device via inv(M0); build DCP; apply;
    # back to XYZ -> Lab gives avg dE2000 ~ 0 (the DCP reproduces the chart).
    patches = {}
    for k, Y in enumerate(np.linspace(89, 3, 24)):
        patches[f'GS{k}'] = (Y / 100.0) * D50 * 100.0
    rng = np.random.default_rng(7)
    for cid in it8.COLOR_IDS[:60]:
        Y = rng.uniform(8, 80)
        patches[cid] = np.array([Y * rng.uniform(0.8, 1.2), Y, Y * rng.uniform(0.6, 1.1)])
    N = _neutral(M0)
    fit = _Fit(M0)
    p = dcp.parse_dcp_bytes(dcp.build_camera_dcp(fit, "c"))
    Ainv = np.linalg.inv(cm.M_XYZ_D50_2_ADOBE)
    de = []
    for xyz in patches.values():
        cam = np.linalg.inv(M0) @ (xyz / 100.0)               # camera-native device
        if np.any(cam < 0) or np.any(cam * (1.0 / N) > 1):    # skip out-of-gamut/clipped
            continue
        cu = np.tile((np.clip(cam, 0, 1) * 65535).astype(np.uint16).reshape(1, 1, 3), (2, 2, 1))
        out = dcp.apply_dcp(p, cu, as_shot_wb=1.0 / N)[0, 0].astype(float) / 65535.0
        got = (Ainv @ out) * 100.0
        de.append(float(it8.delta_e_2000(it8.xyz_to_lab(got), it8.xyz_to_lab(xyz))[0]))
    assert len(de) > 20 and float(np.mean(de)) < 0.5


# --------------------------------------------------------------------------- #
# Look tables (apply_look).
# --------------------------------------------------------------------------- #

def test_look_table_off_by_default_and_identity_noop():
    N = _neutral(M0)
    cam = (np.clip(N * 0.6, 0, 1) * 65535).astype(np.uint16)
    img = np.tile(cam.reshape(1, 1, 3), (4, 4, 1))
    dims = (6, 4, 1)
    identity = np.zeros((*dims, 3)); identity[..., 1] = 1.0; identity[..., 2] = 1.0  # dH0,sS1,vS1
    p = dcp.DcpProfile(name="look", forward_matrix_1=M0 @ np.diag(N),
                       color_matrix_1=np.linalg.inv(M0 @ np.diag(N)), illuminant_1=23,
                       camera_calibration_1=np.eye(3), camera_calibration_2=np.eye(3),
                       analog_balance=np.ones(3), hsm_dims=dims, hsm_data_1=identity)
    base = dcp.apply_dcp(p, img, as_shot_wb=1.0 / N, apply_look=False)
    ident = dcp.apply_dcp(p, img, as_shot_wb=1.0 / N, apply_look=True)
    assert np.abs(base.astype(int) - ident.astype(int)).max() <= 4      # identity look ~ no-op


def test_look_table_saturation_scale_changes_output():
    N = _neutral(M0)
    cam = (np.clip(N * 0.6, 0, 1) * 65535).astype(np.uint16)
    img = np.tile(cam.reshape(1, 1, 3), (4, 4, 1))
    dims = (6, 4, 1)
    boost = np.zeros((*dims, 3)); boost[..., 0] = 0.0; boost[..., 1] = 1.5; boost[..., 2] = 1.0
    p = dcp.DcpProfile(name="look", forward_matrix_1=M0 @ np.diag(N),
                       color_matrix_1=np.linalg.inv(M0 @ np.diag(N)), illuminant_1=23,
                       camera_calibration_1=np.eye(3), camera_calibration_2=np.eye(3),
                       analog_balance=np.ones(3), hsm_dims=dims, hsm_data_1=boost)
    off = dcp.apply_dcp(p, img, as_shot_wb=1.0 / N, apply_look=False)
    on = dcp.apply_dcp(p, img, as_shot_wb=1.0 / N, apply_look=True)
    assert np.abs(off.astype(int) - on.astype(int)).max() > 4           # sat boost changes it


# --------------------------------------------------------------------------- #
# Errors.
# --------------------------------------------------------------------------- #

def test_errors():
    with pytest.raises(dcp.DcpError):
        dcp.parse_dcp_bytes(b"xx")                              # too short
    with pytest.raises(dcp.DcpError):
        dcp.parse_dcp_bytes(b"ZZ" + b"\x00" * 10)               # bad byte-order
    # valid TIFF but no ColorMatrix1/ForwardMatrix1 -> unusable
    en = '<'
    ifd = struct.pack(en + 'H', 1) + struct.pack(en + 'HHI', 50936, 2, 2) + b'ab' + b'\x00\x00'
    ifd += struct.pack(en + 'I', 0)
    blob = struct.pack(en + '2sHI', b'II', 42, 8) + ifd
    with pytest.raises(dcp.DcpError):
        dcp.parse_dcp_bytes(blob)


# --------------------------------------------------------------------------- #
# Hand-built IFD coverage: type decoding, error guards, look tables.
# --------------------------------------------------------------------------- #

def _srat(vals):
    return b''.join(struct.pack('<ii', int(round(v * 1e6)), 1000000) for v in vals)


def _ifd(entries):
    """entries: list of (tag, type_code, count, raw_bytes). Little-endian DCP."""
    n = len(entries)
    base = 8 + 2 + n * 12 + 4
    ent = b''; data = b''
    for tag, tc, cnt, raw in entries:
        if len(raw) <= 4:
            field = raw + b'\x00' * (4 - len(raw))
        else:
            field = struct.pack('<I', base + len(data)); data += raw
            if len(data) % 2:
                data += b'\x00'
        ent += struct.pack('<HHI', tag, tc, cnt) + field
    return (struct.pack('<2sHI', b'II', 42, 8) + struct.pack('<H', n) + ent
            + struct.pack('<I', 0) + data)


_CM1 = (50721, 10, 9, _srat(M0.ravel()))       # a valid ColorMatrix1 entry


def test_parse_offset_past_eof():
    # ColorMatrix1 (SRATIONAL×9 -> 72 bytes, offset-typed) with offset past EOF.
    ent = struct.pack('<HHI', 50721, 10, 9) + struct.pack('<I', 10 ** 6)
    blob = (struct.pack('<2sHI', b'II', 42, 8) + struct.pack('<H', 1) + ent
            + struct.pack('<I', 0))
    with pytest.raises(dcp.DcpError):
        dcp.parse_dcp_bytes(blob)


def test_parse_matrix_count_not_9():
    blob = _ifd([(50721, 10, 6, _srat([1, 2, 3, 4, 5, 6]))])     # count 6, not 9
    with pytest.raises(dcp.DcpError, match="9"):
        dcp.parse_dcp_bytes(blob)


def test_parse_rational_and_tonecurve():
    ab = struct.pack('<IIIIII', 1, 1, 0, 0, 2, 1)               # 1/1, 0/0 (zero-denom), 2/1
    tone = struct.pack('<6f', 0.0, 0.0, 0.5, 0.6, 1.0, 1.0)
    blob = _ifd([_CM1, (50727, 5, 3, ab), (50940, 11, 6, tone)])
    p = dcp.parse_dcp_bytes(blob)
    np.testing.assert_allclose(p.analog_balance, [1.0, 0.0, 2.0])   # RATIONAL + zero-denom guard
    assert p.tone_curve.shape == (3, 2)
    np.testing.assert_allclose(p.tone_curve, [[0, 0], [0.5, 0.6], [1, 1]])


def test_parse_huesatmap_ordering_and_size_mismatch():
    dims = (2, 2, 2)
    flat = []
    for h in range(2):
        for s in range(2):
            for v in range(2):
                flat += [h * 100 + s * 10 + v, 1.0, 1.0]        # marker in component 0
    data = struct.pack('<%df' % len(flat), *flat)
    blob = _ifd([_CM1, (50937, 4, 3, struct.pack('<3I', *dims)), (50938, 11, len(flat), data)])
    p = dcp.parse_dcp_bytes(blob)
    assert p.hsm_data_1.shape == (2, 2, 2, 3)
    # value-axis-fastest C-order: cell (h,s,v) component0 == h*100+s*10+v
    assert p.hsm_data_1[1, 0, 1, 0] == 101 and p.hsm_data_1[0, 1, 1, 0] == 11
    # wrong-length data -> DcpError
    bad = _ifd([_CM1, (50937, 4, 3, struct.pack('<3I', *dims)),
                (50938, 11, 12, struct.pack('<12f', *([0.0] * 12)))])
    with pytest.raises(dcp.DcpError):
        dcp.parse_dcp_bytes(bad)


def test_parse_count0_scalar_and_bad_dims():
    # CalibrationIlluminant1 (SHORT) with count 0 must not IndexError.
    blob = _ifd([_CM1, (50778, 3, 0, b'')])
    p = dcp.parse_dcp_bytes(blob)
    assert p.illuminant_1 == 21                                 # left at default
    # HueSatMapDims with < 3 values -> DcpError, not IndexError.
    bad = _ifd([_CM1, (50937, 4, 2, struct.pack('<2I', 2, 2)),
                (50938, 11, 12, struct.pack('<12f', *([0.0] * 12)))])
    with pytest.raises(dcp.DcpError):
        dcp.parse_dcp_bytes(bad)


def test_apply_as_shot_wb_none_is_unbalanced():
    p = dcp.parse_dcp_bytes(dcp.build_camera_dcp(_Fit(M0), "c"))
    cam = (np.clip(_neutral(M0) * 0.5, 0, 1) * 65535).astype(np.uint16)
    img = np.tile(cam.reshape(1, 1, 3), (2, 2, 1))
    none_out = dcp.apply_dcp(p, img)                            # m = ones (unbalanced)
    wb_out = dcp.apply_dcp(p, img, as_shot_wb=1.0 / _neutral(M0))
    assert none_out.dtype == np.uint16 and none_out.shape == img.shape
    assert not np.array_equal(none_out, wb_out)                # WB genuinely matters


def test_apply_highlight_not_preclipped():
    # d*m can exceed 1 (film-base highlights); the matrix must see the unclipped
    # value (clip only at the final Adobe output), matching the ICC path.
    p = dcp.parse_dcp_bytes(dcp.build_camera_dcp(_Fit(M0), "c"))
    N = _neutral(M0)
    m = (1.0 / N) * 1.4
    cam = (np.clip(N * 0.9, 0, 1) * 65535).astype(np.uint16)    # d*m ≈ 1.26 in-channel
    img = np.tile(cam.reshape(1, 1, 3), (2, 2, 1))
    out = dcp.apply_dcp(p, img, as_shot_wb=m)[0, 0]
    FM = p.forward_matrix_1
    expect = np.clip(((cam / 65535.0 * m) @ FM.T) @ cm.M_XYZ_D50_2_ADOBE.T, 0, 1)
    np.testing.assert_allclose(out / 65535.0, expect, atol=1e-3)   # no pre-matrix clip


def test_interp_weight_clamps_outside_calibration_range():
    FM1 = M0 @ np.diag(_neutral(M0))
    p = dcp.DcpProfile(name="d", forward_matrix_1=FM1, forward_matrix_2=FM1 * 1.1,
                       color_matrix_1=np.eye(3), color_matrix_2=np.eye(3),
                       illuminant_1=21, illuminant_2=17,        # D65 / StdA
                       camera_calibration_1=np.eye(3), camera_calibration_2=np.eye(3),
                       analog_balance=np.ones(3))
    # CM=I so _neutral_cct uses the neutral n=1/m directly as XYZ -> xy -> McCamy.
    hot = 1.0 / np.array([0.6, 0.9, 1.6])      # blue-heavy neutral -> very high CCT
    cold = 1.0 / np.array([1.6, 0.9, 0.5])     # red-heavy neutral -> very low CCT
    assert dcp._interp_weight(p, hot) == 1.0    # clamped to set 1 (D65)
    assert dcp._interp_weight(p, cold) == 0.0   # clamped to set 2 (StdA)
    mid = dcp._interp_weight(p, 1.0 / np.array([1.0, 1.0, 1.0]))
    assert 0.0 <= mid <= 1.0
