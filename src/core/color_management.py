"""
Color management for FreeCCR.

Two responsibilities, both pure-numpy (no Pillow/lcms runtime dependency — see
spec/color-management.md for why Pillow's ImageCms is unusable for our 16-bit
pipeline):

1. EXPORT color space. The internal pipeline produces an uncalibrated result that
   every viewer interprets as sRGB-encoded display RGB. ``apply_export_colorspace``
   either passes that through (sRGB, pixel-identical to before) or re-encodes it
   into ProPhoto RGB (ROMM), returning the matching ICC profile bytes to embed so
   a colour-managed viewer shows the same colours.

2. INPUT ICC profile. ``InputProfile`` parses a *matrix-shaper* ICC profile and
   converts decoded pixels from that profile's space into the working sRGB
   encoding, applied at decode time before conversion/adjustments. LUT-based and
   CMYK profiles are rejected with ``UnsupportedICCError``.

ICC profiles are synthesised in-process (``build_matrix_shaper_icc``) so nothing
needs to be bundled and there is no profile-licensing/trademark concern. The
synthesised bytes are valid ICC v2.4 matrix-shaper profiles (verified to embed
via tifffile tag 34675 and to parse + build a transform under littleCMS).
"""
from __future__ import annotations

import struct
from typing import Optional, Tuple

import numpy as np


class UnsupportedICCError(Exception):
    """Raised when an input ICC profile is not a simple RGB matrix-shaper
    profile (e.g. LUT/cLUT-based, CMYK, or otherwise unparseable). The caller
    surfaces this to the user instead of silently mis-converting."""


# Global active input profile (a single app-wide setting, by design). Decode
# (CCRImage.read_image) reads it via get_active_input_profile() so it does not
# need to import the backend singleton — avoiding an import cycle.
_active_input_profile: "Optional[InputProfile]" = None


def set_active_input_profile(profile) -> None:
    global _active_input_profile
    _active_input_profile = profile


def get_active_input_profile():
    return _active_input_profile


def load_input_profile(path: str) -> "InputProfile":
    """Read and parse a matrix-shaper ICC file. Raises UnsupportedICCError for
    LUT/CMYK/unparseable profiles, or OSError if the file can't be read."""
    with open(path, "rb") as f:
        data = f.read()
    return InputProfile.from_bytes(data)


# --------------------------------------------------------------------------- #
# Reference matrices (float64). Sources: Lindbloom / ninedegreesbelow /
# colour-science. See spec/color-management.md §5.1.
# --------------------------------------------------------------------------- #

# linear sRGB (D65) -> XYZ (D65)
M_SRGB2XYZ = np.array([
    [0.4123908, 0.3575843, 0.1804808],
    [0.2126390, 0.7151687, 0.0721923],
    [0.0193308, 0.1191948, 0.9505322],
], dtype=np.float64)

# Bradford chromatic adaptation D65 -> D50
M_BRADFORD_D65_D50 = np.array([
    [1.0478112, 0.0228866, -0.0501270],
    [0.0295424, 0.9904844, -0.0170491],
    [-0.0092345, 0.0150436, 0.7521316],
], dtype=np.float64)

# linear ProPhoto/ROMM (D50) -> XYZ (D50); columns are the ProPhoto primaries.
M_PROPHOTO2XYZ = np.array([
    [0.7976749, 0.1351917, 0.0313534],
    [0.2880402, 0.7118741, 0.0000857],
    [0.0000000, 0.0000000, 0.8252100],
], dtype=np.float64)

# Derived inverses / adaptations.
M_XYZ2SRGB = np.linalg.inv(M_SRGB2XYZ)                       # XYZ(D65) -> linear sRGB
M_BRADFORD_D50_D65 = np.linalg.inv(M_BRADFORD_D65_D50)       # D50 -> D65
M_XYZ2PROPHOTO = np.linalg.inv(M_PROPHOTO2XYZ)               # XYZ(D50) -> linear ProPhoto

# Combined linear-sRGB(D65) -> linear-ProPhoto(D50) (one matmul on export).
M_SRGB2PROPHOTO = M_XYZ2PROPHOTO @ M_BRADFORD_D65_D50 @ M_SRGB2XYZ
_M_SRGB2PROPHOTO_F32 = M_SRGB2PROPHOTO.astype(np.float32)   # export math runs in float32
# Combined XYZ(D50) -> linear sRGB(D65) (used by InputProfile to reach working space).
M_XYZ_D50_2_SRGB = M_XYZ2SRGB @ M_BRADFORD_D50_D65

# D50 PCS white point (ICC reference illuminant).
D50_XYZ = (0.9642, 1.0000, 0.8249)


# --------------------------------------------------------------------------- #
# Transfer functions (operate on float arrays normalised to [0, 1]).
# --------------------------------------------------------------------------- #

def srgb_decode(v: np.ndarray) -> np.ndarray:
    """sRGB EOTF: encoded -> linear."""
    a = 0.055
    return np.where(v <= 0.04045, v / 12.92, np.power((v + a) / (1 + a), 2.4))


def srgb_encode(x: np.ndarray) -> np.ndarray:
    """sRGB inverse EOTF: linear -> encoded."""
    a = 0.055
    x = np.clip(x, 0.0, 1.0)
    return np.where(x <= 0.0031308, x * 12.92, (1 + a) * np.power(x, 1 / 2.4) - a)


def romm_encode(x: np.ndarray) -> np.ndarray:
    """ProPhoto/ROMM gamma encode (gamma 1.8 with the 1/512 linear toe, slope 16).
    Continuous at x = 1/512 because 16*(1/512) == (1/512)**(1/1.8) == 1/32."""
    Et = 1.0 / 512.0
    x = np.clip(x, 0.0, 1.0)
    return np.where(x < Et, 16.0 * x, np.power(x, 1.0 / 1.8))


# --------------------------------------------------------------------------- #
# ICC profile synthesis (matrix-shaper, ICC v2.4 mntr/RGB/XYZ).
# --------------------------------------------------------------------------- #

def _s15f16(x: float) -> int:
    """Encode a float as an ICC s15Fixed16Number (signed 16.16)."""
    return int(round(x * 65536.0))


def _xyz_type(x: float, y: float, z: float) -> bytes:
    return struct.pack('>4sI3i', b'XYZ ', 0, _s15f16(x), _s15f16(y), _s15f16(z))


def _para_type3(g: float, a: float, b: float, c: float, d: float) -> bytes:
    """parametricCurveType, function type 3:  Y=(aX+b)^g for X>=d ; Y=cX for X<d."""
    body = struct.pack('>4sIH2x', b'para', 0, 3)
    for v in (g, a, b, c, d):
        body += struct.pack('>i', _s15f16(v))
    return body


def _desc_type(text: str) -> bytes:
    """textDescriptionType (ICC v2)."""
    b = text.encode('ascii') + b'\x00'
    out = struct.pack('>4sII', b'desc', 0, len(b)) + b
    out += struct.pack('>II', 0, 0)          # unicode language + count
    out += struct.pack('>HB', 0, 0)          # scriptcode code + count
    out += b'\x00' * 67                      # macintosh description
    return out


def _text_type(text: str) -> bytes:
    return struct.pack('>4sI', b'text', 0) + text.encode('ascii') + b'\x00'


def build_matrix_shaper_icc(desc: str,
                            r_xyz: Tuple[float, float, float],
                            g_xyz: Tuple[float, float, float],
                            b_xyz: Tuple[float, float, float],
                            trc_para: Tuple[float, float, float, float, float],
                            wtpt: Tuple[float, float, float] = D50_XYZ,
                            copyright_text: str = "Public Domain. No rights reserved.") -> bytes:
    """Build a valid ICC v2.4 RGB matrix-shaper profile from D50 colorants and a
    shared parametric (type-3) TRC. Returns the raw profile bytes."""
    tags = {
        'desc': _desc_type(desc),
        'wtpt': _xyz_type(*wtpt),
        'rXYZ': _xyz_type(*r_xyz),
        'gXYZ': _xyz_type(*g_xyz),
        'bXYZ': _xyz_type(*b_xyz),
        'cprt': _text_type(copyright_text),
    }
    trc = _para_type3(*trc_para)
    tags['rTRC'] = trc
    tags['gTRC'] = trc
    tags['bTRC'] = trc

    order = ['desc', 'rXYZ', 'gXYZ', 'bXYZ', 'wtpt', 'rTRC', 'gTRC', 'bTRC', 'cprt']
    n = len(order)
    header_size = 128
    table_size = 4 + n * 12
    base = header_size + table_size

    data = b''
    entries = []
    seen: dict = {}
    for name in order:
        payload = tags[name]
        key = bytes(payload)
        if key in seen:
            off, ln = seen[key]
        else:
            if len(data) % 4:
                data += b'\x00' * (4 - len(data) % 4)
            off = base + len(data)
            ln = len(payload)
            data += payload
            seen[key] = (off, ln)
        entries.append((name.encode('ascii'), off, ln))

    table = struct.pack('>I', n)
    for tag, off, ln in entries:
        table += struct.pack('>4sII', tag, off, ln)

    total = header_size + len(table) + len(data)
    header = bytearray(128)
    struct.pack_into('>I', header, 0, total)            # profile size
    struct.pack_into('>4s', header, 4, b'lcms')         # preferred CMM (cosmetic)
    struct.pack_into('>I', header, 8, 0x02400000)       # version 2.4.0
    struct.pack_into('>4s', header, 12, b'mntr')        # device class: display
    struct.pack_into('>4s', header, 16, b'RGB ')        # data colour space
    struct.pack_into('>4s', header, 20, b'XYZ ')        # PCS
    struct.pack_into('>4s', header, 36, b'acsp')        # signature
    struct.pack_into('>i', header, 68, _s15f16(wtpt[0]))  # PCS illuminant (D50)
    struct.pack_into('>i', header, 72, _s15f16(wtpt[1]))
    struct.pack_into('>i', header, 76, _s15f16(wtpt[2]))
    return bytes(header) + table + data


def _adapt_columns_d65_to_d50(m_rgb2xyz_d65: np.ndarray):
    """Adapt the primary XYZ columns of a D65 RGB->XYZ matrix to D50, returning
    (r_xyz, g_xyz, b_xyz) tuples suitable for ICC colorant tags."""
    adapted = M_BRADFORD_D65_D50 @ m_rgb2xyz_d65
    return (tuple(adapted[:, 0]), tuple(adapted[:, 1]), tuple(adapted[:, 2]))


# sRGB working-space profile (D50-adapted sRGB colorants; sRGB piecewise TRC as a
# parametric type-3 curve). Pixel-identical export to before, just now tagged.
_SR, _SG, _SB = _adapt_columns_d65_to_d50(M_SRGB2XYZ)
SRGB_ICC_BYTES = build_matrix_shaper_icc(
    "FreeCCR sRGB",
    _SR, _SG, _SB,
    # sRGB EOTF as Y=(aX+b)^g for X>=d else cX : g=2.4, a=1/1.055, b=0.055/1.055,
    # c=1/12.92, d=0.04045.
    (2.4, 1.0 / 1.055, 0.055 / 1.055, 1.0 / 12.92, 0.04045),
)

# ProPhoto/ROMM profile (colorants already D50; gamma 1.8 + 1/512 toe as type-3).
PROPHOTO_ICC_BYTES = build_matrix_shaper_icc(
    "FreeCCR ROMM (ProPhoto)",
    tuple(M_PROPHOTO2XYZ[:, 0]),
    tuple(M_PROPHOTO2XYZ[:, 1]),
    tuple(M_PROPHOTO2XYZ[:, 2]),
    (1.8, 1.0, 0.0, 0.0625, 0.03125),
)


# --------------------------------------------------------------------------- #
# Export: working(sRGB-encoded) -> target colour space.
# --------------------------------------------------------------------------- #

def export_icc_bytes(target: str) -> bytes:
    return PROPHOTO_ICC_BYTES if target == "prophoto" else SRGB_ICC_BYTES


def apply_export_colorspace(rgb_u16: np.ndarray, target: str) -> Tuple[np.ndarray, bytes]:
    """Map an export array (HxWx3 uint16 RGB, treated as sRGB-encoded) to the
    target colour space. Returns (out_uint16, icc_bytes).

    - 'srgb'      : pixels returned unchanged (the file is merely tagged sRGB).
    - 'prophoto'  : re-encode into ProPhoto/ROMM at full precision.
    """
    if target != "prophoto":
        return rgb_u16, SRGB_ICC_BYTES
    # float32 keeps the memory footprint in line with the rest of the export
    # pipeline; precision stays well under 1 LSB at 16-bit.
    lin = srgb_decode(rgb_u16.astype(np.float32) / np.float32(65535.0))
    pro = lin @ _M_SRGB2PROPHOTO_F32.T
    np.clip(pro, 0.0, 1.0, out=pro)       # clip imaginary/overflow before encode
    enc = romm_encode(pro)
    out = np.rint(enc * 65535.0).astype(np.uint16)
    return out, PROPHOTO_ICC_BYTES


# --------------------------------------------------------------------------- #
# JPEG ICC embedding (cv2 cannot embed; inject APP2 ICC_PROFILE segments).
# --------------------------------------------------------------------------- #

_ICC_APP2_MAXCHUNK = 65519   # 65533 marker-payload limit - 12-byte ICC header - 2


def inject_jpeg_icc(jpeg_bytes: bytes, icc: bytes) -> bytes:
    """Insert an ICC profile into an encoded JPEG byte string as one or more
    APP2 'ICC_PROFILE' marker segments (right after SOI). Returns new bytes.
    No-op (returns input) if the stream does not start with SOI."""
    if not icc or jpeg_bytes[:2] != b'\xff\xd8':
        return jpeg_bytes
    chunks = [icc[i:i + _ICC_APP2_MAXCHUNK]
              for i in range(0, len(icc), _ICC_APP2_MAXCHUNK)] or [b'']
    n = len(chunks)
    segs = b''
    for i, ch in enumerate(chunks, start=1):
        payload = b'ICC_PROFILE\x00' + bytes([i, n]) + ch
        segs += b'\xff\xe2' + struct.pack('>H', len(payload) + 2) + payload
    return jpeg_bytes[:2] + segs + jpeg_bytes[2:]


# --------------------------------------------------------------------------- #
# Input ICC profile -> working sRGB encoding (matrix-shaper only).
# --------------------------------------------------------------------------- #

def _read_tag_table(icc: bytes) -> dict:
    if len(icc) < 132 or icc[36:40] != b'acsp':
        raise UnsupportedICCError("not a valid ICC profile")
    count = struct.unpack('>I', icc[128:132])[0]
    tags = {}
    pos = 132
    for _ in range(count):
        if pos + 12 > len(icc):
            break
        sig = icc[pos:pos + 4]
        off, size = struct.unpack('>II', icc[pos + 4:pos + 12])
        tags[sig] = (off, size)
        pos += 12
    return tags


def _parse_xyz(icc: bytes, off: int) -> np.ndarray:
    x, y, z = struct.unpack('>3i', icc[off + 8:off + 20])
    return np.array([x, y, z], dtype=np.float64) / 65536.0


def _parse_trc_to_lut(icc: bytes, off: int, n: int = 65536) -> np.ndarray:
    """Return an n-entry float64 LUT mapping device value [0,1] -> linear [0,1].
    n == 65536 so a 16-bit device value indexes the LUT exactly (no quantisation)."""
    tag_type = icc[off:off + 4]
    xs = np.linspace(0.0, 1.0, n)
    if tag_type == b'curv':
        count = struct.unpack('>I', icc[off + 8:off + 12])[0]
        if count == 0:
            return xs.copy()                          # identity (gamma 1.0)
        if count == 1:
            gamma = struct.unpack('>H', icc[off + 12:off + 14])[0] / 256.0  # u8Fixed8
            return np.power(xs, gamma)
        samples = np.frombuffer(icc[off + 12:off + 12 + 2 * count], dtype='>u2').astype(np.float64) / 65535.0
        src = np.linspace(0.0, 1.0, count)
        return np.interp(xs, src, samples)
    if tag_type == b'para':
        func = struct.unpack('>H', icc[off + 8:off + 10])[0]
        nparams = {0: 1, 1: 3, 2: 4, 3: 5, 4: 7}.get(func)
        if nparams is None:
            raise UnsupportedICCError(f"unsupported parametric curve type {func}")
        raw = struct.unpack('>%di' % nparams, icc[off + 12:off + 12 + 4 * nparams])
        p = [v / 65536.0 for v in raw]
        return _eval_para(func, p, xs)
    raise UnsupportedICCError(f"unsupported TRC tag type {tag_type!r}")


def _eval_para(func: int, p: list, x: np.ndarray) -> np.ndarray:
    g = p[0]
    if func == 0:
        return np.power(np.clip(x, 0, 1), g)
    if func == 1:
        a, b = p[1], p[2]
        thr = -b / a if a != 0 else 0.0
        return np.where(x >= thr, np.power(np.clip(a * x + b, 0, None), g), 0.0)
    if func == 2:
        a, b, c = p[1], p[2], p[3]
        thr = -b / a if a != 0 else 0.0
        return np.where(x >= thr, np.power(np.clip(a * x + b, 0, None), g) + c, c)
    if func == 3:
        a, b, c, d = p[1], p[2], p[3], p[4]
        return np.where(x >= d, np.power(np.clip(a * x + b, 0, None), g), c * x)
    if func == 4:
        a, b, c, d, e, f = p[1], p[2], p[3], p[4], p[5], p[6]
        return np.where(x >= d, np.power(np.clip(a * x + b, 0, None), g) + e, c * x + f)
    raise UnsupportedICCError(f"unsupported parametric curve type {func}")


def _soft_gamut_rolloff(x: np.ndarray, knee: float = 0.8) -> np.ndarray:
    """Compress values above `knee` smoothly into [knee, 1.0) instead of hard-
    clipping to 1.0. Used on the NamiColor path so an out-of-gamut highlight (e.g. a
    saturated red lifted by the camera matrix) rolls off rather than slamming the
    channel ceiling — a hard clip destroys the per-channel tonal structure that
    NamiColor's per-channel auto-levels rebalance, which is what spikes red/orange
    saturation (see the IT8 ICC over-saturation analysis)."""
    out = np.array(x, dtype=np.float32, copy=True)
    hi = out > knee
    out[hi] = knee + (1.0 - knee) * np.tanh((out[hi] - knee) / (1.0 - knee))
    return out


class InputProfile:
    """A parsed RGB matrix-shaper input profile that converts decoded device
    pixels into the working sRGB encoding."""

    def __init__(self, combined_matrix: np.ndarray, luts: list, desc: str):
        self._matrix = combined_matrix.astype(np.float32)   # device-linRGB -> linear sRGB
        # 3 device->linear LUTs, length 65536 so a uint16 value indexes directly.
        self._luts = [np.ascontiguousarray(l, dtype=np.float32) for l in luts]
        self.description = desc

    @classmethod
    def from_bytes(cls, icc: bytes) -> "InputProfile":
        tags = _read_tag_table(icc)
        if icc[16:20] != b'RGB ':
            raise UnsupportedICCError("input profile is not an RGB profile")
        needed = [b'rXYZ', b'gXYZ', b'bXYZ', b'rTRC', b'gTRC', b'bTRC']
        if any(t not in tags for t in needed):
            # Missing colorant/TRC tags => LUT-based or otherwise unsupported.
            raise UnsupportedICCError(
                "only RGB matrix-shaper ICC profiles are supported "
                "(LUT/cLUT/CMYK profiles are not)")
        r = _parse_xyz(icc, tags[b'rXYZ'][0])
        g = _parse_xyz(icc, tags[b'gXYZ'][0])
        b = _parse_xyz(icc, tags[b'bXYZ'][0])
        m_colorants = np.stack([r, g, b], axis=1)        # columns = primaries (XYZ D50)
        combined = M_XYZ_D50_2_SRGB @ m_colorants        # device-linRGB -> linear sRGB
        luts = [_parse_trc_to_lut(icc, tags[t][0])
                for t in (b'rTRC', b'gTRC', b'bTRC')]
        desc = cls._read_desc(icc, tags)
        return cls(combined, luts, desc)

    @staticmethod
    def _read_desc(icc: bytes, tags: dict) -> str:
        if b'desc' not in tags:
            return ""
        off, size = tags[b'desc']
        try:
            if icc[off:off + 4] == b'desc':
                n = struct.unpack('>I', icc[off + 8:off + 12])[0]
                return icc[off + 12:off + 12 + n].split(b'\x00')[0].decode('ascii', 'replace')
        except Exception:
            pass
        return ""

    def apply(self, rgb_u16: np.ndarray, linear_target: bool = False) -> np.ndarray:
        """Convert HxWx3 uint16 device RGB into the working space.

        linear_target=False (default): sRGB-ENCODED display working RGB, hard-clipped
        to the [0,1] gamut — for the positive / classic / export paths.
        linear_target=True: LINEAR working RGB with a SOFT highlight roll-off instead
        of a hard clip — for the NamiColor path, which expects linear input and runs
        its own per-channel auto-levels. A hard clip there destroys the per-channel
        structure those levels rebalance and spikes red/orange saturation."""
        if rgb_u16.ndim != 3 or rgb_u16.shape[2] != 3 or rgb_u16.dtype != np.uint16:
            return rgb_u16
        # LUTs are length 65536, so a uint16 value indexes them directly.
        lin = np.empty(rgb_u16.shape, dtype=np.float32)
        for c in range(3):
            lin[..., c] = self._luts[c][rgb_u16[..., c]]
        work_lin = lin @ self._matrix.T
        if linear_target:
            out = _soft_gamut_rolloff(np.maximum(work_lin, 0.0))   # linear, soft roll-off
        else:
            out = srgb_encode(np.clip(work_lin, 0.0, 1.0))         # display sRGB (unchanged)
        return np.rint(np.clip(out, 0.0, 1.0) * 65535.0).astype(np.uint16)
