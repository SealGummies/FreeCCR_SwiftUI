"""
IT8 camera-profile core (pure numpy + OpenCV, no Qt).

Builds a camera *input* ICC profile from a photograph of an IT8 calibration
target. See spec/it8-camera-profile.md. The pipeline is:

  1. parse_it8_reference()   -- read the batch CGATS.17 reference file
  2. decode_target()         -- raw-linear device RGB of the target shot
  3. grid_sample_points()    -- 4-corner homography -> 288 patch centres
  4. sample_patches()        -- robust per-patch device RGB (clip-rejected)
  5. fit_camera_matrix()     -- least-squares 3x3 device->XYZ(D50), D50-pinned
  6. build_camera_icc()      -- valid ICC v2.4 matrix-shaper bytes

The fit reduces to a 3x3 matrix + identity tone curve, exactly what an ICC
matrix-shaper profile (and FreeCCR's InputProfile apply path) can represent.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

try:
    import cv2
    _CV2 = True
except ImportError:                       # pragma: no cover - cv2 is a hard dep
    _CV2 = False

from core import color_management


class IT8ReferenceError(Exception):
    """Raised when an IT8 reference (CGATS) file can't be parsed or is invalid.
    Surfaced to the user instead of a raw traceback."""


# --------------------------------------------------------------------------- #
# Patch identifiers (the geometry the dialog overlays and the fit consumes).
# --------------------------------------------------------------------------- #

_ROWS = "ABCDEFGHIJKL"                     # 12 rows, top -> bottom
# Colour grid IDs, column-fastest (A1..A22, B1.., L22) = 264.
COLOR_IDS: List[str] = [f"{r}{c}" for r in _ROWS for c in range(1, 23)]
# Grayscale strip GS0..GS23 = 24, left (light/Dmin) -> right (dark/Dmax).
GRAY_IDS: List[str] = [f"GS{k}" for k in range(24)]
ALL_IDS: List[str] = COLOR_IDS + GRAY_IDS

# Chart proportions from ArgyllCMS it8.cht (units are template-internal but
# proportionally exact). The colour block is the user-placed quad; the gray
# strip is positioned relative to it. See spec §3.2 / §5.2.
_CELL = 25.625
_CB_X0, _CB_W = 26.625, 22 * _CELL         # colour block x-origin / width (563.75)
_CB_Y0, _CB_H = 26.625, 12 * _CELL         # colour block y-origin / height (307.5)
_GS_CY = 358.75 + 51.25 / 2.0              # gray strip centre y in cht units (384.375)
GRAY_V0 = (_GS_CY - _CB_Y0) / _CB_H        # gray strip v in colour-block unit space (~1.1634)
DEFAULT_GRAY_OFFSET = 0.0


def _gray_u(k: int) -> float:
    """Unit-space x (colour-block normalised) of grayscale cell k (0..23)."""
    return ((k + 0.5) * _CELL - _CB_X0) / _CB_W


# --------------------------------------------------------------------------- #
# 1. Reference file (CGATS.17).
# --------------------------------------------------------------------------- #

def _normalize_sample_id(sid: str) -> str:
    """Map a reference SAMPLE_ID onto our canonical ids.

    Colour patches: a letter A-L + column number, with optional zero padding
    ('A01' -> 'A1'). Grayscale: 'GS'/'GS0n' -> 'GS<int>'. Anything else is
    upper-cased and returned as-is (ignored downstream if not in ALL_IDS).
    """
    s = sid.strip().upper()
    if not s:
        return s
    if s.startswith("GS"):
        num = s[2:].lstrip("0") or "0"
        return f"GS{int(num)}" if num.isdigit() else s
    if s[0] in _ROWS and s[1:].isdigit():
        return f"{s[0]}{int(s[1:])}"
    return s


def _read_text(path: str) -> str:
    with open(path, "rb") as f:
        raw = f.read()
    if raw[:3] == b"\xef\xbb\xbf":         # strip UTF-8 BOM
        raw = raw[3:]
    for enc in ("utf-8", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", "replace")


def _strip_comment(line: str) -> str:
    """Remove a '#' comment (to end of line) but not inside a quoted value."""
    out, in_q = [], False
    for ch in line:
        if ch == '"':
            in_q = not in_q
        if ch == "#" and not in_q:
            break
        out.append(ch)
    return "".join(out)


@dataclass
class IT8Reference:
    chart_type: str = ""                   # 'IT8.7/1' | 'IT8.7/2' | ''
    batch: str = ""                        # SERIAL value (for display)
    descriptor: str = ""
    originator: str = ""
    fields: List[str] = field(default_factory=list)
    patches: Dict[str, Dict[str, float]] = field(default_factory=dict)

    def xyz(self, sample_id: str) -> Optional[np.ndarray]:
        """XYZ (D50, 2deg, Y~=100 scale) for a patch, or None.

        Uses XYZ_* columns when present, else derives from LAB_* via the D50
        white point.
        """
        p = self.patches.get(sample_id)
        if p is None:
            return None
        if "XYZ_X" in p and "XYZ_Y" in p and "XYZ_Z" in p:
            return np.array([p["XYZ_X"], p["XYZ_Y"], p["XYZ_Z"]], dtype=np.float64)
        lab = self.lab(sample_id)
        return None if lab is None else lab_to_xyz(lab)

    def lab(self, sample_id: str) -> Optional[np.ndarray]:
        """CIE Lab (D50) for a patch, or None. Uses LAB_* columns when present,
        else derives from XYZ_*."""
        p = self.patches.get(sample_id)
        if p is None:
            return None
        if "LAB_L" in p and "LAB_A" in p and "LAB_B" in p:
            return np.array([p["LAB_L"], p["LAB_A"], p["LAB_B"]], dtype=np.float64)
        if "XYZ_X" in p and "XYZ_Y" in p and "XYZ_Z" in p:
            return xyz_to_lab(np.array([p["XYZ_X"], p["XYZ_Y"], p["XYZ_Z"]]))
        return None

    @property
    def matched_ids(self) -> List[str]:
        """Canonical ids present in the file (intersection with ALL_IDS)."""
        return [i for i in ALL_IDS if i in self.patches]


def parse_it8_reference(path: str) -> IT8Reference:
    """Parse a CGATS.17 IT8 reference file. Reads columns dynamically from the
    DATA_FORMAT block (the field set varies by batch). Raises IT8ReferenceError
    on a malformed/empty file."""
    text = _read_text(path)
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    ref = IT8Reference()
    state = "PRE"                          # PRE | FORMAT | DATA
    fmt: List[str] = []
    n_fields: Optional[int] = None
    n_sets: Optional[int] = None
    first_token_seen = False
    bad_rows = 0

    for raw_line in lines:
        line = _strip_comment(raw_line).strip()
        if not line:
            continue

        if not first_token_seen:
            first_token_seen = True
            tok = line.split()[0]
            if tok.upper().startswith("IT8"):
                ref.chart_type = tok
                continue                   # consume the bare type token

        upper = line.upper()
        if state == "PRE":
            if upper.startswith("BEGIN_DATA_FORMAT"):
                state = "FORMAT"
                continue
            if upper.startswith("BEGIN_DATA"):
                state = "DATA"
                continue
            parts = line.split(None, 1)
            kw = parts[0].upper()
            val = parts[1].strip().strip('"').strip() if len(parts) > 1 else ""
            if kw == "NUMBER_OF_FIELDS":
                try:
                    n_fields = int(val.split()[0])
                except (ValueError, IndexError):
                    pass
            elif kw == "NUMBER_OF_SETS":
                try:
                    n_sets = int(val.split()[0])
                except (ValueError, IndexError):
                    pass
            elif kw == "SERIAL":
                ref.batch = val
            elif kw == "DESCRIPTOR":
                ref.descriptor = val
            elif kw == "ORIGINATOR":
                ref.originator = val
            # KEYWORD "NAME" / other preamble keywords are ignored — the
            # DATA_FORMAT block is the authority on column names.
        elif state == "FORMAT":
            if upper.startswith("END_DATA_FORMAT"):
                state = "PRE"
                continue
            fmt.extend(line.split())
        elif state == "DATA":
            if upper.startswith("END_DATA"):
                state = "PRE"
                continue
            toks = line.split()
            if not fmt or len(toks) != len(fmt):
                # Skip rows whose token count doesn't match DATA_FORMAT — never
                # zip-truncate them into partial records (a truncated row would
                # otherwise store e.g. XYZ_X/Y without Z). Gross truncation is
                # then caught by the patch-count guard below.
                bad_rows += 1
                continue
            rec: Dict[str, float] = {}
            sid = None
            for name, tok in zip(fmt, toks):
                if name.upper() in ("SAMPLE_ID", "SAMPLE_NAME") and sid is None:
                    sid = tok
                    continue
                try:
                    rec[name.upper()] = float(tok)
                except ValueError:
                    pass
            if sid is not None:
                ref.patches[_normalize_sample_id(sid)] = rec

    ref.fields = fmt
    if not fmt:
        raise IT8ReferenceError(
            "No DATA_FORMAT block found — this does not look like a CGATS/IT8 "
            "reference file.")
    if not ref.patches:
        extra = (f" ({bad_rows} row(s) had a token count != NUMBER_OF_FIELDS.)"
                 if bad_rows else "")
        raise IT8ReferenceError("No data patches found in the reference file."
                                + extra)
    # The reliably-present colorimetry must be there for at least the neutrals.
    if not any(("XYZ_X" in p or "LAB_L" in p) for p in ref.patches.values()):
        raise IT8ReferenceError(
            "Reference file has no XYZ_* or LAB_* columns — cannot build a "
            "profile from it.")
    if n_sets is not None and len(ref.patches) < min(n_sets, 200) // 2:
        extra = f" ({bad_rows} malformed row(s) skipped.)" if bad_rows else ""
        raise IT8ReferenceError(
            f"Reference file declares {n_sets} patches but only "
            f"{len(ref.patches)} were parsed — the file looks truncated." + extra)
    return ref


# --------------------------------------------------------------------------- #
# 2. Target decode (raw-linear device RGB, no input ICC).
# --------------------------------------------------------------------------- #

SAMPLE_MAX = 2048                          # long-side cap for the sampling decode


def decode_target(path: str, sample_max: int = SAMPLE_MAX) -> Optional[np.ndarray]:
    """Decode the IT8 target shot to raw-linear device RGB (uint16, HxWx3, RGB
    order), bypassing Positive mode and any active input ICC — the bare device
    space the profile is fitted on. See spec §5.1 / §6.1.

    read_image is called on a bare CCRImage created via __new__ so the
    heavyweight __init__ (a redundant 1080px decode + lens correction +
    thumbnail) is skipped; read_image only needs `source_ops` on the instance.
    A half-size (preview) decode capped at sample_max gives ample pixels per
    patch and matches the runtime preview's device space."""
    from core.ccr_image import CCRImage
    img = CCRImage.__new__(CCRImage)
    img.source_ops = []
    return img.read_image(path, preview=True, max_long_side=sample_max,
                          positive_override=False, apply_input_icc=False)


# --------------------------------------------------------------------------- #
# 3. Patch grid geometry (4-corner homography -> sample centres).
# --------------------------------------------------------------------------- #

def _homography(quad: np.ndarray) -> np.ndarray:
    """Perspective transform mapping the unit square (TL,TR,BR,BL) -> quad."""
    src = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], dtype=np.float32)
    dst = np.asarray(quad, dtype=np.float32)
    if _CV2:
        return cv2.getPerspectiveTransform(src, dst)
    return _perspective_transform_np(src, dst)


def _perspective_transform_np(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """Solve the 8-DOF homography (fallback when cv2 is unavailable)."""
    A, b = [], []
    for (x, y), (u, v) in zip(src, dst):
        A.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        A.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        b.extend([u, v])
    h = np.linalg.solve(np.asarray(A, float), np.asarray(b, float))
    return np.array([[h[0], h[1], h[2]],
                     [h[3], h[4], h[5]],
                     [h[6], h[7], 1.0]], dtype=np.float64)


def _apply_h(H: np.ndarray, uv: np.ndarray) -> np.ndarray:
    """Map (N,2) unit-space points through homography H -> (N,2) array coords."""
    pts = np.hstack([uv, np.ones((len(uv), 1))])
    out = pts @ np.asarray(H, float).T
    return out[:, :2] / out[:, 2:3]


def flip_quad(quad):
    """180-degree relabel of a (TL, TR, BR, BL) quad — for a chart placed
    upside-down. new TL=old BR, TR=old BL, BR=old TL, BL=old TR."""
    q = list(quad)
    return [q[2], q[3], q[0], q[1]]


def grid_sample_points(quad, gray_offset: float = DEFAULT_GRAY_OFFSET
                       ) -> Dict[str, Tuple[float, float]]:
    """Sample centres (array coords) for all 288 patches.

    quad: the 4 corners (TL, TR, BR, BL) of the COLOUR block in array space.
    gray_offset nudges the grayscale row vertically (batch variation)."""
    H = _homography(np.asarray(quad, dtype=np.float64))
    uv = []
    for r in range(12):
        for c in range(22):
            uv.append(((c + 0.5) / 22.0, (r + 0.5) / 12.0))
    gv = GRAY_V0 + gray_offset
    for k in range(24):
        uv.append((_gray_u(k), gv))
    pts = _apply_h(H, np.asarray(uv, dtype=np.float64))
    return {i: (float(x), float(y)) for i, (x, y) in zip(ALL_IDS, pts)}


# --------------------------------------------------------------------------- #
# 4. Patch sampling (robust central window, clip rejection).
# --------------------------------------------------------------------------- #

@dataclass
class PatchSample:
    rgb: np.ndarray                        # (3,) float, device RGB in [0, 65535]
    valid: bool                            # False if clipped / out of frame
    n_pix: int


def _quad_cell_halfsize(quad: np.ndarray, frac: float) -> Tuple[float, float]:
    """Half-window (px) for sampling, derived from the colour block's mean cell
    size so the window scales with the chart's size in the frame."""
    q = np.asarray(quad, dtype=np.float64)
    top = np.linalg.norm(q[1] - q[0]); bot = np.linalg.norm(q[2] - q[3])
    left = np.linalg.norm(q[3] - q[0]); right = np.linalg.norm(q[2] - q[1])
    cell_w = 0.5 * (top + bot) / 22.0
    cell_h = 0.5 * (left + right) / 12.0
    return max(1.0, 0.5 * frac * cell_w), max(1.0, 0.5 * frac * cell_h)


def sample_patches(img_u16: np.ndarray, points: Dict[str, Tuple[float, float]],
                   quad, frac: float = 0.5, clip_lo: float = 0.005,
                   clip_hi: float = 0.995) -> Dict[str, PatchSample]:
    """Sample each patch's device RGB from a central window via a per-channel
    trimmed mean; flag patches with >2% clipped pixels or out of frame."""
    h, w = img_u16.shape[:2]
    full = 65535.0
    lo, hi = clip_lo * full, clip_hi * full
    half_w, half_h = _quad_cell_halfsize(quad, frac)
    out: Dict[str, PatchSample] = {}
    for sid, (cx, cy) in points.items():
        x0 = int(round(cx - half_w)); x1 = int(round(cx + half_w)) + 1
        y0 = int(round(cy - half_h)); y1 = int(round(cy + half_h)) + 1
        x0c, x1c = max(0, x0), min(w, x1)
        y0c, y1c = max(0, y0), min(h, y1)
        if x1c - x0c < 2 or y1c - y0c < 2:
            out[sid] = PatchSample(np.zeros(3), False, 0)
            continue
        win = img_u16[y0c:y1c, x0c:x1c, :3].reshape(-1, 3).astype(np.float64)
        # Per-channel trimmed mean (drop 10/90 tails) — robust to dust/edges.
        rgb = np.empty(3)
        for ch in range(3):
            col = win[:, ch]
            p10, p90 = np.percentile(col, [10, 90])
            keep = col[(col >= p10) & (col <= p90)]
            rgb[ch] = keep.mean() if keep.size else col.mean()
        clipped_frac = np.mean((win <= lo) | (win >= hi))
        # Also invalid if the patch fell partly outside the frame.
        in_frame = (x0 >= 0 and y0 >= 0 and x1 <= w and y1 <= h)
        out[sid] = PatchSample(rgb, bool(clipped_frac < 0.02 and in_frame), win.shape[0])
    return out


# --------------------------------------------------------------------------- #
# 5. Colour-science helpers (XYZ<->Lab, deltaE). XYZ on the Y~=100 scale.
# --------------------------------------------------------------------------- #

_D50_W100 = np.array([96.422, 100.0, 82.521])   # D50 white, Y=100 scale
_EPS = 216.0 / 24389.0
_KAPPA = 24389.0 / 27.0


def xyz_to_lab(xyz: np.ndarray, white: np.ndarray = _D50_W100) -> np.ndarray:
    xyz = np.asarray(xyz, dtype=np.float64)
    xr = xyz / white
    f = np.where(xr > _EPS, np.cbrt(np.clip(xr, 0, None)), (_KAPPA * xr + 16) / 116)
    if xyz.ndim == 1:
        L = 116 * f[1] - 16; a = 500 * (f[0] - f[1]); b = 200 * (f[1] - f[2])
        return np.array([L, a, b])
    L = 116 * f[..., 1] - 16
    a = 500 * (f[..., 0] - f[..., 1])
    b = 200 * (f[..., 1] - f[..., 2])
    return np.stack([L, a, b], axis=-1)


def lab_to_xyz(lab: np.ndarray, white: np.ndarray = _D50_W100) -> np.ndarray:
    lab = np.asarray(lab, dtype=np.float64)
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    fy = (L + 16) / 116.0
    fx = fy + a / 500.0
    fz = fy - b / 200.0
    def inv(t):
        t3 = t ** 3
        return np.where(t3 > _EPS, t3, (116 * t - 16) / _KAPPA)
    xyz = np.stack([inv(fx), inv(fy), inv(fz)], axis=-1) * white
    return xyz


def delta_e_76(lab1: np.ndarray, lab2: np.ndarray) -> np.ndarray:
    return np.linalg.norm(np.asarray(lab1) - np.asarray(lab2), axis=-1)


def delta_e_2000(lab1: np.ndarray, lab2: np.ndarray) -> np.ndarray:
    """CIEDE2000 (kL=kC=kH=1), Sharma formulation. Accepts (N,3) or (3,)."""
    lab1 = np.atleast_2d(np.asarray(lab1, dtype=np.float64))
    lab2 = np.atleast_2d(np.asarray(lab2, dtype=np.float64))
    L1, a1, b1 = lab1.T; L2, a2, b2 = lab2.T
    C1 = np.hypot(a1, b1); C2 = np.hypot(a2, b2)
    Cbar = 0.5 * (C1 + C2)
    G = 0.5 * (1 - np.sqrt(Cbar ** 7 / (Cbar ** 7 + 25.0 ** 7)))
    a1p, a2p = (1 + G) * a1, (1 + G) * a2
    C1p, C2p = np.hypot(a1p, b1), np.hypot(a2p, b2)
    h1p = np.degrees(np.arctan2(b1, a1p)) % 360
    h2p = np.degrees(np.arctan2(b2, a2p)) % 360
    dLp = L2 - L1
    dCp = C2p - C1p
    dhp = h2p - h1p
    dhp = np.where(dhp > 180, dhp - 360, dhp)
    dhp = np.where(dhp < -180, dhp + 360, dhp)
    dhp = np.where((C1p * C2p) == 0, 0.0, dhp)
    dHp = 2 * np.sqrt(C1p * C2p) * np.sin(np.radians(dhp) / 2)
    Lbp = 0.5 * (L1 + L2)
    Cbp = 0.5 * (C1p + C2p)
    hsum = h1p + h2p
    hbp = np.where(np.abs(h1p - h2p) > 180, (hsum + 360) / 2, hsum / 2)
    hbp = np.where((C1p * C2p) == 0, hsum, hbp)
    T = (1 - 0.17 * np.cos(np.radians(hbp - 30))
         + 0.24 * np.cos(np.radians(2 * hbp))
         + 0.32 * np.cos(np.radians(3 * hbp + 6))
         - 0.20 * np.cos(np.radians(4 * hbp - 63)))
    dTheta = 30 * np.exp(-(((hbp - 275) / 25) ** 2))
    Rc = 2 * np.sqrt(Cbp ** 7 / (Cbp ** 7 + 25.0 ** 7))
    Sl = 1 + (0.015 * (Lbp - 50) ** 2) / np.sqrt(20 + (Lbp - 50) ** 2)
    Sc = 1 + 0.045 * Cbp
    Sh = 1 + 0.015 * Cbp * T
    Rt = -np.sin(np.radians(2 * dTheta)) * Rc
    de = np.sqrt((dLp / Sl) ** 2 + (dCp / Sc) ** 2 + (dHp / Sh) ** 2
                 + Rt * (dCp / Sc) * (dHp / Sh))
    return de


# --------------------------------------------------------------------------- #
# 6. Matrix fit (device-linear RGB -> XYZ D50), D50 neutral-pinned.
# --------------------------------------------------------------------------- #

D50_XYZ = np.array(color_management.D50_XYZ, dtype=np.float64)   # [0,1] scale, Y=1


@dataclass
class CameraFit:
    matrix: np.ndarray                     # (3,3) device-norm RGB[0,1] -> XYZ D50 (Y~1)
    avg_de: float
    med_de: float
    p95_de: float
    max_de: float
    per_patch: List[Tuple[str, float]]     # (id, dE2000), worst-first
    used_ids: List[str]
    dropped_ids: List[str]
    wb_id: str


def _pick_wb_id(samples: Dict[str, PatchSample], ref: IT8Reference,
                preferred: str) -> Optional[str]:
    """The lightest valid neutral to white-balance on: preferred (GS0) if valid,
    else the highest-L valid GS patch, else the highest-Y valid patch."""
    if preferred in samples and samples[preferred].valid and ref.lab(preferred) is not None:
        return preferred
    best, best_L = None, -1.0
    for k in GRAY_IDS:
        if k in samples and samples[k].valid:
            lab = ref.lab(k)
            if lab is not None and lab[0] > best_L:
                best, best_L = k, lab[0]
    if best is not None:
        return best
    best, best_Y = None, -1.0
    for sid, ps in samples.items():
        xyz = ref.xyz(sid)
        if ps.valid and xyz is not None and xyz[1] > best_Y:
            best, best_Y = sid, xyz[1]
    return best


def fit_camera_matrix(samples: Dict[str, PatchSample], ref: IT8Reference, *,
                      weight: str = "none", wb_id: str = "GS0") -> CameraFit:
    """Fit the 3x3 camera matrix from sampled device RGB + reference XYZ.

    weight: 'none' (plain XYZ least squares, the documented baseline) or '1/Y'
    (down-weight bright patches). See spec §5.4."""
    chosen_wb = _pick_wb_id(samples, ref, wb_id)
    if chosen_wb is None:
        raise IT8ReferenceError("No valid neutral patch to white-balance on — "
                                "check the chart placement and exposure.")

    used, dropped = [], []
    d_list, x_list = [], []
    for sid in ALL_IDS:
        ps = samples.get(sid)
        xyz = ref.xyz(sid)
        if ps is None or xyz is None:
            continue
        if not ps.valid:
            dropped.append(sid)
            continue
        used.append(sid)
        d_list.append(ps.rgb / 65535.0)
        x_list.append(xyz / 100.0)
    if len(used) < 6:
        raise IT8ReferenceError(
            f"Only {len(used)} usable patches — too few to fit a profile. "
            "Re-check corner placement, exposure (clipping), and the reference "
            "file.")

    d = np.asarray(d_list)                  # (N,3) normalised device RGB
    X = np.asarray(x_list)                  # (N,3) reference XYZ, Y in [0,~1]

    # White balance on the chosen neutral so it becomes equal-RGB.
    d_wb_ref = samples[chosen_wb].rgb / 65535.0
    d_wb_ref = np.clip(d_wb_ref, 1e-6, None)
    gains = d_wb_ref.mean() / d_wb_ref
    d_wb = d * gains

    # (Weighted) least squares  X ~= d_wb @ A ;  M_wb = A.T  => XYZ = M_wb @ rgb.
    if weight == "1/Y":
        w = 1.0 / np.clip(X[:, 1], 0.02, None)
        sw = np.sqrt(w)[:, None]
        A, *_ = np.linalg.lstsq(d_wb * sw, X * sw, rcond=None)
    else:
        A, *_ = np.linalg.lstsq(d_wb, X, rcond=None)
    M_wb = A.T

    # Pin so equal-RGB (post-WB) maps exactly to D50 chromaticity (well-behaved
    # neutral axis). This per-column scale fixes chromaticity but leaves an
    # overall brightness gain, removed by the exposure step below.
    white = M_wb @ np.ones(3)
    white = np.where(np.abs(white) < 1e-9, 1e-9, white)
    M_pin = M_wb @ np.diag(D50_XYZ / white)

    # Fold WB back so the stored matrix consumes un-white-balanced device RGB.
    M = M_pin @ np.diag(gains)

    # Normalise overall exposure so the chosen neutral reproduces its reference
    # Y exactly. This keeps the reported deltaE about colour (not a benign
    # brightness scale) and makes the fit reproduce the chart when consistent.
    pred_wb_Y = float((M @ (samples[chosen_wb].rgb / 65535.0))[1])
    ref_wb_Y = float(ref.xyz(chosen_wb)[1] / 100.0)
    if pred_wb_Y > 1e-9:
        M = M * (ref_wb_Y / pred_wb_Y)

    # Quality: predicted Lab vs reference Lab for the used patches.
    pred_xyz = (d @ M.T) * 100.0
    pred_lab = xyz_to_lab(pred_xyz)
    ref_lab = np.array([ref.lab(s) for s in used], dtype=np.float64)
    de = delta_e_2000(ref_lab, pred_lab)
    order = np.argsort(-de)
    per_patch = [(used[i], float(de[i])) for i in order]

    return CameraFit(
        matrix=M,
        avg_de=float(de.mean()),
        med_de=float(np.median(de)),
        p95_de=float(np.percentile(de, 95)),
        max_de=float(de.max()),
        per_patch=per_patch,
        used_ids=used,
        dropped_ids=dropped,
        wb_id=chosen_wb,
    )


# --------------------------------------------------------------------------- #
# 7. ICC synthesis (matrix-shaper, identity TRC).
# --------------------------------------------------------------------------- #

# Identity parametric (type-3) TRC: y = x for x>=0  (g=1, a=1, b=0, c=1, d=0).
_IDENTITY_TRC = (1.0, 1.0, 0.0, 1.0, 0.0)


def build_camera_icc(fit: CameraFit, desc: str,
                     copyright_text: str = "Public Domain. No rights reserved."
                     ) -> bytes:
    """Synthesise ICC v2.4 matrix-shaper bytes from the fitted camera matrix.
    Colorant columns are M's columns (already XYZ D50); TRC is identity (the
    device space is raw-linear). Round-trips through InputProfile.from_bytes."""
    M = np.asarray(fit.matrix, dtype=np.float64)
    # The ICC desc/text tag writers only emit ASCII (textType/textDescription),
    # so coerce user-supplied names + illuminant notes to ASCII to avoid a crash.
    desc = str(desc).encode("ascii", "replace").decode("ascii")
    copyright_text = str(copyright_text).encode("ascii", "replace").decode("ascii")
    return color_management.build_matrix_shaper_icc(
        desc,
        tuple(M[:, 0]), tuple(M[:, 1]), tuple(M[:, 2]),
        _IDENTITY_TRC,
        copyright_text=copyright_text,
    )
