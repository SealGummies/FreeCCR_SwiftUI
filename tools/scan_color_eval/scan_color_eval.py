#!/usr/bin/env python3
"""
scan_color_eval.py — Film-scan colour-fidelity evaluator.

Measures how faithfully each capture method (LIGHT SOURCE x CAMERA PROFILE)
reproduces a colour reference, after a two-point (bwpoint) negative conversion.
Reports five CIELab drift curves (graphic + text): hue/saturation/value drift
cross-tabbed against hue and value. See README.md for the full procedure.

Geometry you provide ONCE (assumed identical for every light source):
  * BLACK / WHITE bwpoint sample rects, read from each light's film-lead shot:
      BLACK = unexposed film base (orange mask, Dmin)  -> black
      WHITE = exposed  film base (dense,       Dmax)   -> white
  * (chart mode) the ColorChecker bounding box.
  Provide them on the CLI (--black/--white/--chart-rect, fractions) OR omit them
  to open an interactive picker (drag a box; needs a display).

ROOT: a folder whose SUBFOLDERS are named by light source. In each subfolder:
  file 1 (sorted)     = film-lead shot (bwpoint sample)
  files 2..N (sorted) = test negatives (same scenes across folders)
  A subfolder named 'trichrome' is auto-loaded in RGB-merge mode.

TARGET (reference):
  --target <foldername>  a light source is the truth (relative; others registered)
  --target chart         an in-frame Calibrite ColorChecker (ABSOLUTE accuracy)

Profiles: none (camera-native) | matrix (libraw Adobe) | dcp (apply_dcp + as-shot WB).
Read-only. Outputs PNGs + CSVs + report.md to <root>/scan_color_eval_out (or --out).
"""
import argparse
import csv
import glob
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
os.environ.setdefault("FREECCR_WORKING_SPACE", "0")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(REPO, "src"))
sys.path.insert(0, REPO)

import numpy as np
import cv2
import rawpy
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from core.ccr_merge import RAW_EXTENSIONS, sort_for_merge, group_into_triplets, merge_raw_channels
from core.ccr_processor import _twopoint_invert

HUE_BANDS = [("R", 0, 30), ("O", 30, 60), ("Y", 60, 90), ("G", 90, 165),
             ("C", 165, 195), ("B", 195, 255), ("P", 255, 315), ("M", 315, 360)]
LBINS = [(0, 20), (20, 35), (35, 50), (50, 65), (65, 80), (80, 100)]
PROFILE_COLORS = {"none": "#888888", "matrix": "#d62728", "dcp": "#2ca02c"}
MAXPIX = 200000  # per variant per test (subsample to bound memory)

# ColorChecker Classic 24 REFERENCE — X-Rite "November 2014 edition and newer",
# measured on an i1Pro 2 (MeasurementCondition M0). CIELab (D50, 2deg), row-major
# (dark-skin patch first). This is the authoritative baked-in reference; the sRGB
# table used by chart mode is DERIVED from it (Lab D50 -> Bradford D50/D65 -> sRGB),
# so absolute-accuracy comparisons use the real chart values, not an approximation.
COLORCHECKER24_LAB_D50 = [
    (37.54, 14.37, 14.92), (64.66, 19.27, 17.50), (49.32, -3.82, -22.54),
    (43.46, -12.74, 22.72), (54.94, 9.61, -24.79), (70.48, -32.26, -0.37),
    (62.73, 35.83, 56.50), (39.43, 10.75, -45.17), (50.57, 48.64, 16.67),
    (30.10, 22.54, -20.87), (71.77, -24.13, 58.19), (71.51, 18.24, 67.37),
    (28.37, 15.42, -49.80), (54.38, -39.72, 32.27), (42.43, 51.05, 28.62),
    (81.80, 2.67, 80.41), (50.63, 51.28, -14.12), (49.57, -29.71, -28.32),
    (95.19, -1.03, 2.93), (81.29, -0.57, 0.44), (66.89, -0.75, -0.06),
    (50.76, -0.13, 0.14), (35.63, -0.46, -0.48), (20.64, 0.07, -0.46),
]


REF_ILLUMINANTS = ("D50", "D55", "D65")
_WHITES = {"D50": (0.96422, 1.0, 0.82521),           # CIE 2-deg white points (XYZ, Y=1)
           "D55": (0.95682, 1.0, 0.92149),
           "D65": (0.95047, 1.0, 1.08883)}
_M_BRADFORD = np.array([[0.8951, 0.2664, -0.1614],
                        [-0.7502, 1.7135, 0.0367],
                        [0.0389, -0.0685, 1.0296]])
_M_XYZ2SRGB = np.array([[3.2404542, -1.5371385, -0.4985314],
                        [-0.9692660, 1.8760108, 0.0415560],
                        [0.0556434, -0.2040259, 1.0572252]])  # XYZ(D65) -> linear sRGB


def _cat_bradford(src, dst):
    s = _M_BRADFORD @ np.asarray(src, float)
    d = _M_BRADFORD @ np.asarray(dst, float)
    return np.linalg.inv(_M_BRADFORD) @ np.diag(d / s) @ _M_BRADFORD


def _lab_to_xyz(lab, white):
    lab = np.asarray(lab, float)
    L, a, b = lab[:, 0], lab[:, 1], lab[:, 2]
    fy = (L + 16) / 116; fx = fy + a / 500; fz = fy - b / 200
    eps, kappa = 216 / 24389, 24389 / 27

    def finv(f):
        f3 = f ** 3
        return np.where(f3 > eps, f3, (116 * f - 16) / kappa)
    xr = finv(fx)
    yr = np.where(L > kappa * eps, ((L + 16) / 116) ** 3, L / kappa)
    zr = finv(fz)
    return np.stack([xr * white[0], yr * white[1], zr * white[2]], 1)


def reference_srgb(illuminant="D65"):
    """ColorChecker reference sRGB under a chosen ADOPTED WHITE. The measured chart
    Lab is D50; we Bradford-adapt D50 -> `illuminant`, then encode with the (D65)
    sRGB matrix. D65 = the chart adapted to daylight/display-neutral (matches a
    white-balanced daylight shot; the sensible default). D50/D55 leave the reference
    progressively WARMER, to match a lower-temperature adopted white or a conversion
    that is not fully white-balanced to daylight."""
    xyz = _lab_to_xyz(COLORCHECKER24_LAB_D50, _WHITES["D50"]) @ _cat_bradford(_WHITES["D50"], _WHITES[illuminant]).T
    lin = np.clip(xyz @ _M_XYZ2SRGB.T, 0, 1)
    srgb = np.where(lin <= 0.0031308, 12.92 * lin, 1.055 * np.power(lin, 1 / 2.4) - 0.055)
    return [tuple(int(x) for x in row) for row in np.clip(np.rint(srgb * 255), 0, 255).astype(int)]


COLORCHECKER_CLASSIC24_SRGB = reference_srgb("D65")   # default reference (daylight)

def _hue_gradient(n=360, L8=180, C=55):
    """A 1xN sRGB strip of the CIELab hue circle (0..360 deg) at fixed L*,chroma,
    so the hue axis can be annotated with its actual colours. Out-of-gamut hues
    clip; that is fine for a reference swatch."""
    deg = np.arange(n)
    lab = np.zeros((1, n, 3), np.uint8)
    lab[0, :, 0] = L8
    lab[0, :, 1] = np.clip(128 + C * np.cos(np.radians(deg)), 0, 255).astype(np.uint8)
    lab[0, :, 2] = np.clip(128 + C * np.sin(np.radians(deg)), 0, 255).astype(np.uint8)
    bgr = cv2.cvtColor(lab, cv2.COLOR_Lab2BGR)
    return (bgr[:, :, ::-1].astype(np.float32) / 255.0)   # (1, n, 3) RGB


_HUE_GRAD = None  # lazily built (needs cv2)


# The five drift cross-plots: (key, x-source, y-source, chromatic-only, circular-y, xlabel, ylabel, title)
DRIFTS = [
    ("hue_vs_hue",   "hue", "dH", True,  True,  "hue (deg)", "hue drift (deg)",  "hue-vs-hue drift"),
    ("value_vs_hue", "val", "dH", True,  True,  "L*",        "hue drift (deg)",  "value-vs-hue drift"),
    ("hue_vs_sat",   "hue", "dS", True,  False, "hue (deg)", "sat drift (Cab)",  "hue-vs-saturation drift"),
    ("value_vs_sat", "val", "dS", False, False, "L*",        "sat drift (Cab)",  "value-vs-saturation drift"),
    ("value_vs_val", "val", "dV", False, False, "L*",        "value drift (L*)", "value-vs-value drift"),
]


# ----------------------------------------------------------------------------- decode
def _post(path, matrix):
    with rawpy.imread(path) as raw:
        wl = raw.white_level
        wb = np.asarray(raw.camera_whitebalance[:3], float)
        rgb = raw.postprocess(
            output_bps=16, no_auto_bright=True, gamma=(1, 1), user_flip=0,
            demosaic_algorithm=rawpy.DemosaicAlgorithm.AHD, half_size=True,
            use_camera_wb=False, use_auto_wb=False,
            output_color=(rawpy.ColorSpace.Adobe if matrix else rawpy.ColorSpace.raw),
            no_auto_scale=(not matrix), adjust_maximum_thr=0.0, four_color_rgb=False
        ).astype(np.float32)
    if (not matrix) and 0 < wl < 65535:
        rgb = np.clip(rgb * (65535.0 / wl), 0, 65535)
    return rgb, wb


def decode(path, profile, dcp_obj, cache):
    key = (path, profile)
    if key in cache:
        return cache[key]
    if profile == "matrix":
        out = _post(path, True)[0]
    elif profile == "dcp":
        from core.dcp_profile import apply_dcp
        nat, wb = _post(path, False)
        out = apply_dcp(dcp_obj, np.clip(nat, 0, 65535).astype(np.uint16), as_shot_wb=wb).astype(np.float32)
    else:
        out = _post(path, False)[0]
    cache[key] = out
    return out


def decode_merge(paths, cache):
    key = ("merge",) + tuple(paths)
    if key in cache:
        return cache[key]
    cache[key] = merge_raw_channels(paths, preview=True)[0].astype(np.float32)
    return cache[key]


# ----------------------------------------------------------------------------- convert
def sample_points(lead_rgb, black_rect, white_rect):
    h, w = lead_rgb.shape[:2]

    def med(rc):
        x0, y0, x1, y1 = int(rc[0] * w), int(rc[1] * h), int(rc[2] * w), int(rc[3] * h)
        return np.median(lead_rgb[y0:y1, x0:x1].reshape(-1, 3), axis=0)
    return med(black_rect), med(white_rect)


def to_srgb8(pos_rgb, long_edge):
    """RGB positive (uint16) -> 8-bit sRGB in **BGR** order. The decode/_twopoint_invert
    chain is RGB; everything downstream (cv2 imshow/imwrite, cvtColor BGR2Lab, the
    chart sampler) is cv2/BGR — so we convert here. Missing this swaps R<->B and a
    correct warm positive renders/measures as cyan."""
    x = np.clip(pos_rgb / 65535.0, 0, 1)
    v = cv2.cvtColor((np.power(x, 1 / 2.2) * 255).astype(np.uint8), cv2.COLOR_RGB2BGR)
    h, w = v.shape[:2]
    s = long_edge / max(h, w)
    return cv2.resize(v, (max(1, int(w * s)), max(1, int(h * s))), interpolation=cv2.INTER_AREA)


# ----------------------------------------------------------------------------- registration
def register(var_bgr, truth_bgr):
    g1 = cv2.cvtColor(truth_bgr, cv2.COLOR_BGR2GRAY)
    g2 = cv2.cvtColor(var_bgr, cv2.COLOR_BGR2GRAY)
    orb = cv2.ORB_create(6000)
    k1, d1 = orb.detectAndCompute(g1, None)
    k2, d2 = orb.detectAndCompute(g2, None)
    if d1 is None or d2 is None:
        return None
    good = [m for m, n in cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(d2, d1, k=2)
            if m.distance < 0.75 * n.distance]
    if len(good) < 12:
        return None
    sp = np.float32([k2[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dp = np.float32([k1[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    H, _ = cv2.findHomography(sp, dp, cv2.RANSAC, 3.0)
    if H is None:
        return None
    th, tw = truth_bgr.shape[:2]
    warp = cv2.warpPerspective(var_bgr, H, (tw, th))
    vm = cv2.erode(cv2.warpPerspective(np.ones(var_bgr.shape[:2], np.uint8), H, (tw, th)),
                   np.ones((7, 7), np.uint8)).astype(bool)
    gw = cv2.cvtColor(warp, cv2.COLOR_BGR2GRAY).astype(np.float32)
    gt = g1.astype(np.float32)
    a, b = gt[vm] - gt[vm].mean(), gw[vm] - gw[vm].mean()
    ncc = float((a * b).sum() / (np.sqrt((a * a).sum() * (b * b).sum()) + 1e-9))
    return warp, vm, ncc


# ----------------------------------------------------------------------------- colour (CIELab: hue/sat/value)
def lab_hsv(bgr):
    """Return L* (value), Cab (chroma=saturation), hue(deg) maps from an sRGB BGR image."""
    L = cv2.cvtColor(bgr, cv2.COLOR_BGR2Lab).astype(np.float32)
    Ls = L[..., 0] * 100 / 255.0
    a = L[..., 1] - 128.0
    b = L[..., 2] - 128.0
    return Ls, np.hypot(a, b), np.degrees(np.arctan2(b, a)) % 360


def lab_hsv_srgb(rgb_tuples):
    arr = np.array(rgb_tuples, np.uint8).reshape(-1, 1, 3)[:, :, ::-1]
    L = cv2.cvtColor(arr, cv2.COLOR_BGR2Lab).astype(np.float32).reshape(-1, 3)
    a, b = L[:, 1] - 128.0, L[:, 2] - 128.0
    return L[:, 0] * 100 / 255.0, np.hypot(a, b), np.degrees(np.arctan2(b, a)) % 360


def cdiff(h2, h1):
    return (h2 - h1 + 180) % 360 - 180


def wmean(deg_or_lin, w, circular):
    if np.size(w) == 0 or w.sum() < 1e-6:
        return float("nan")
    if circular:
        r = np.radians(deg_or_lin)
        return math.degrees(math.atan2((w * np.sin(r)).sum(), (w * np.cos(r)).sum()))
    return float(np.average(deg_or_lin, weights=w))


# ----------------------------------------------------------------------------- enumeration / GUI
def list_raws(folder):
    files = [p for p in glob.glob(os.path.join(folder, "*"))
             if os.path.splitext(p)[1].lower() in RAW_EXTENSIONS and os.path.isfile(p)]
    return sort_for_merge(files)


def folder_lead_tests(folder, is_merge):
    files = list_raws(folder)
    if is_merge:
        groups = group_into_triplets(files)
        return (groups[0], groups[1:]) if len(groups) >= 2 else (None, [])
    return (files[0], files[1:]) if len(files) >= 2 else (None, [])


def parse_rect(s):
    if s is None:
        return None
    v = [float(x) for x in s.split(",")]
    if len(v) != 4:
        raise argparse.ArgumentTypeError("rect must be x0,y0,x1,y1 fractions")
    return tuple(v)


def parse_corners(s):
    if s is None:
        return None
    v = [float(x) for x in s.split(",")]
    if len(v) != 8:
        raise argparse.ArgumentTypeError("chart-corners = x0,y0,x1,y1,x2,y2,x3,y3 (TL,TR,BR,BL fractions)")
    return [(v[0], v[1]), (v[2], v[3]), (v[4], v[5]), (v[6], v[7])]


def is_merge_folder(name, merge_set):
    return "trichrome" in name.lower() or name in merge_set


def pick_rect(srgb_bgr, title):
    """Open a window; user drags a box. Returns (x0,y0,x1,y1) fractions. Needs a display."""
    h, w = srgb_bgr.shape[:2]
    s = 1000.0 / max(h, w)
    disp = cv2.resize(srgb_bgr, (int(w * s), int(h * s))) if s < 1 else srgb_bgr.copy()
    dh, dw = disp.shape[:2]
    x, y, ww, hh = cv2.selectROI(title, disp, showCrosshair=True, fromCenter=False)
    cv2.destroyWindow(title)
    if ww == 0 or hh == 0:
        raise RuntimeError(f"no region selected for {title}")
    return (x / dw, y / dh, (x + ww) / dw, (y + hh) / dh)


# ----------------------------------------------------------------------------- chart sampling
def _grid_centers(quad_px, cols, rows):
    """24 patch-centre pixels from 4 chart-corner pixels ordered [TL,TR,BR,BL]
    (perspective homography from a unit grid -> handles tilt/rotation/perspective)."""
    unit = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], np.float32)
    H = cv2.getPerspectiveTransform(unit, np.asarray(quad_px, np.float32))
    uv = np.array([[[(c + 0.5) / cols, (r + 0.5) / rows]] for r in range(rows) for c in range(cols)], np.float32)
    return cv2.perspectiveTransform(uv, H).reshape(-1, 2)


def sample_chart_quad(srgb_bgr, corners_frac, cols, rows, patch=0.45):
    """Sample cols*rows patches from the 4 chart corners (fractions, [TL,TR,BR,BL]).
    Perspective-correct, so tilt/rotation/mirror/perspective are all handled; the
    corner ORDER fixes orientation (corner 1 = patch#1 / dark-skin). Returns L*,C,hue."""
    h, w = srgb_bgr.shape[:2]
    quad = [(x * w, y * h) for x, y in corners_frac]
    unit = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], np.float32)
    H = cv2.getPerspectiveTransform(unit, np.asarray(quad, np.float32))
    ctrs = _grid_centers(quad, cols, rows)
    out = []
    for i, (cx, cy) in enumerate(ctrs):
        r, c = divmod(i, cols)
        off = cv2.perspectiveTransform(
            np.array([[[(c + 0.5 + patch * 0.5) / cols, (r + 0.5 + patch * 0.5) / rows]]], np.float32), H)[0, 0]
        rad = max(2, int(0.7 * math.hypot(off[0] - cx, off[1] - cy)))
        x0, x1 = max(0, int(cx - rad)), min(w, int(cx + rad))
        y0, y1 = max(0, int(cy - rad)), min(h, int(cy + rad))
        p = srgb_bgr[y0:y1, x0:x1]
        if p.size == 0:
            out.append((np.nan, np.nan, np.nan)); continue
        med = np.median(p.reshape(-1, 3), axis=0).astype(np.uint8).reshape(1, 1, 3)
        lab = cv2.cvtColor(med, cv2.COLOR_BGR2Lab).astype(np.float32).reshape(3)
        a, b = lab[1] - 128.0, lab[2] - 128.0
        out.append((lab[0] * 100 / 255.0, math.hypot(a, b), math.degrees(math.atan2(b, a)) % 360))
    arr = np.array(out)
    return arr[:, 0], arr[:, 1], arr[:, 2]   # L, C, hue


def pick_chart_quad(srgb_bgr, cols, rows):
    """Interactive 4-corner ColorChecker picker: magnifier for precise clicks on a
    small/hand-held chart, and a live grid overlay to verify orientation. Click the
    corners IN ORDER — 1: patch#1 (dark-skin) corner, 2: far end of that top row
    (patch#%d), 3: opposite/bottom corner (patch#%d), 4: patch#%d (white) corner.
    Robust to tilt / rotation / mirror. Keys: r=reset, Enter=confirm, Esc=cancel.
    Returns 4 corner fractions [TL,TR,BR,BL].""" % (cols, cols * rows, cols * rows - cols + 1)
    h, w = srgb_bgr.shape[:2]
    s = min(1.0, 1500.0 / max(h, w))
    base = cv2.resize(srgb_bgr, (int(w * s), int(h * s))) if s < 1 else srgb_bgr.copy()
    dh, dw = base.shape[:2]
    pts, st = [], {"m": (0, 0)}
    win = "Chart corners: 1=dark-skin 2=top-end 3=bottom 4=white | r reset  Enter ok  Esc cancel"

    def on_mouse(ev, x, y, flags, param):
        st["m"] = (x, y)
        if ev == cv2.EVENT_LBUTTONDOWN and len(pts) < 4:
            pts.append((x, y))
    cv2.namedWindow(win, cv2.WINDOW_AUTOSIZE)
    cv2.setMouseCallback(win, on_mouse)
    labels = {0: "1", cols - 1: str(cols), cols * rows - cols: str(cols * rows - cols + 1), cols * rows - 1: str(cols * rows)}
    while True:
        img = base.copy()
        for i, (px, py) in enumerate(pts):
            cv2.circle(img, (px, py), 5, (0, 0, 255), -1)
            cv2.putText(img, str(i + 1), (px + 6, py - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
        if len(pts) >= 2:
            cv2.polylines(img, [np.array(pts, np.int32)], len(pts) == 4, (0, 255, 255), 1)
        if len(pts) == 4:
            for j, (cx, cy) in enumerate(_grid_centers(pts, cols, rows)):
                cv2.circle(img, (int(cx), int(cy)), 3, (0, 255, 0), -1)
                if j in labels:
                    cv2.putText(img, labels[j], (int(cx) + 3, int(cy) - 3), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
        mx, my = st["m"]                                   # magnifier
        if 0 <= mx < dw and 0 <= my < dh:
            R, Z = 34, 6
            x0, y0 = max(0, mx - R), max(0, my - R)
            crop = base[y0:y0 + 2 * R, x0:x0 + 2 * R]
            if crop.size:
                mag = cv2.resize(crop, (crop.shape[1] * Z, crop.shape[0] * Z), interpolation=cv2.INTER_NEAREST)
                cv2.drawMarker(mag, (mag.shape[1] // 2, mag.shape[0] // 2), (0, 0, 255), cv2.MARKER_CROSS, 18, 1)
                mh, mw = mag.shape[:2]
                img[2:2 + mh, dw - mw - 2:dw - 2] = mag
        cv2.putText(img, f"corner {len(pts)}/4", (6, dh - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        cv2.imshow(win, img)
        k = cv2.waitKey(20) & 0xFF
        if k == ord('r'):
            pts.clear()
        elif k in (13, 10) and len(pts) == 4:
            break
        elif k == 27:
            cv2.destroyWindow(win); raise RuntimeError("chart pick cancelled")
    cv2.destroyWindow(win)
    return [(px / dw, py / dh) for px, py in pts]


# ----------------------------------------------------------------------------- drift collection
def drift_arrays(tL, tC, tH, vL, vC, vH, sel):
    """Subsampled per-pixel drift record on a boolean selection."""
    idx = np.flatnonzero(sel)
    if idx.size > MAXPIX:
        idx = idx[:: max(1, idx.size // MAXPIX)]
    return dict(val=tL.ravel()[idx], sat=tC.ravel()[idx], hue=tH.ravel()[idx],
                dV=(vL - tL).ravel()[idx], dS=(vC - tC).ravel()[idx], dH=cdiff(vH, tH).ravel()[idx])


def empty_pool():
    return dict(val=[], sat=[], hue=[], dV=[], dS=[], dH=[])


def add_pool(pool, rec):
    for k in pool:
        pool[k].append(rec[k])


def finalize(pool):
    return {k: (np.concatenate(v) if v else np.array([])) for k, v in pool.items()}


def binned_curve(r, drift, chroma_thr):
    _, xsrc, ysrc, chromatic, circ, _, _, _ = drift
    if r["hue"].size == 0:
        return None
    x = r[xsrc]; y = r[ysrc]
    sel = np.isfinite(x) & np.isfinite(y)
    if chromatic:
        sel &= r["sat"] > chroma_thr
    w = np.where(chromatic, r["sat"], 1.0)[sel] if chromatic else np.ones(sel.sum())
    x, y = x[sel], y[sel]
    if xsrc == "hue":
        xs = np.arange(0, 360, 10); width = 10
    else:
        xs = np.arange(2.5, 100, 5); width = 5
    ys = []
    for xb in xs:
        m = (x >= xb - (0 if xsrc == "hue" else width / 2)) & (x < xb + (width if xsrc == "hue" else width / 2))
        ys.append(wmean(y[m], w[m], circ) if m.sum() > 40 else np.nan)
    return xs, np.array(ys)


# ----------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="Film-scan colour-fidelity evaluator")
    ap.add_argument("--root", required=True)
    ap.add_argument("--target", default="trichrome", help="a subfolder name, or 'chart'")
    ap.add_argument("--merge", default="", help="extra folder names to RGB-merge ('trichrome' auto-detected)")
    ap.add_argument("--black", type=parse_rect, default=None, help="unexposed-base rect x0,y0,x1,y1 (omit -> picker)")
    ap.add_argument("--white", type=parse_rect, default=None, help="exposed-base rect x0,y0,x1,y1 (omit -> picker)")
    ap.add_argument("--profiles", default="none,matrix,dcp")
    ap.add_argument("--dcp", default=None)
    ap.add_argument("--density", action="store_true")
    ap.add_argument("--long", type=int, default=1500)
    ap.add_argument("--chroma", type=float, default=12.0)
    ap.add_argument("--chart-rect", type=parse_rect, default=None,
                    help="(chart) axis-aligned box (legacy; use --chart-corners or the picker for tilted charts)")
    ap.add_argument("--chart-corners", type=parse_corners, default=None,
                    help="(chart) 4 corner fractions x0,y0,..,x3,y3 in order TL(dark-skin),TR,BR,BL(white)")
    ap.add_argument("--chart-grid", default="6x4")
    ap.add_argument("--chart-from", default="test:0", help="'test:N' or 'lead'")
    ap.add_argument("--ref-illuminant", default="D65", choices=REF_ILLUMINANTS,
                    help="(--target chart) adopted white for the reference; D65 = daylight (default)")
    ap.add_argument("--no-gui", action="store_true", help="never open the picker (error if a rect is missing)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    root = os.path.abspath(args.root)
    out = args.out or os.path.join(root, "scan_color_eval_out")
    os.makedirs(out, exist_ok=True)
    merge_set = {m.strip() for m in args.merge.split(",") if m.strip()}
    profiles = [p.strip() for p in args.profiles.split(",") if p.strip()]
    dcp_obj = None
    if "dcp" in profiles:
        if not args.dcp or not os.path.exists(args.dcp):
            print("WARNING: 'dcp' requested but --dcp missing; dropping dcp.")
            profiles = [p for p in profiles if p != "dcp"]
        else:
            from core.dcp_profile import parse_dcp
            dcp_obj = parse_dcp(args.dcp)
    subfolders = sorted(d for d in os.listdir(root)
                        if os.path.isdir(os.path.join(root, d)) and list_raws(os.path.join(root, d)))
    if not subfolders:
        sys.exit(f"no light-source subfolders with RAWs under {root}")
    cache = {}

    def convert_image(folder, src, profile):
        is_m = is_merge_folder(folder, merge_set)
        lead, _ = folder_lead_tests(os.path.join(root, folder), is_m)
        if is_m:
            bk, wt = sample_points(decode_merge(lead, cache), args.black, args.white)
            pos = _twopoint_invert(decode_merge(src, cache), bk, wt, args.density)
        else:
            bk, wt = sample_points(decode(lead, profile, dcp_obj, cache), args.black, args.white)
            pos = _twopoint_invert(decode(src, profile, dcp_obj, cache), bk, wt, args.density)
        return to_srgb8(pos, args.long)

    def folder_profiles(folder):
        return ["none"] if is_merge_folder(folder, merge_set) else profiles

    # ---- resolve geometry (CLI or interactive picker) ----
    _resolve_geometry(args, root, subfolders, merge_set, profiles, dcp_obj, cache)

    print(f"Root: {root}\nSubfolders: {subfolders}\nTarget: {args.target}  "
          f"black={args.black} white={args.white} density={args.density}")
    if args.target == "chart":
        run_chart_mode(args, root, subfolders, merge_set, convert_image, folder_profiles, out)
    else:
        run_source_mode(args, root, subfolders, merge_set, convert_image, folder_profiles, out)
    print(f"\nDone. Outputs in: {out}")


def _resolve_geometry(args, root, subfolders, merge_set, profiles, dcp_obj, cache):
    # Chart geometry -> a 4-corner quad. Explicit corners/rect apply to ALL lights;
    # otherwise it is picked PER light in run_chart_mode (hand-held -> location varies).
    args.chart_quad = None
    if args.target == "chart":
        if args.chart_corners:
            args.chart_quad = args.chart_corners
        elif args.chart_rect:
            x0, y0, x1, y1 = args.chart_rect
            args.chart_quad = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    if args.black is not None and args.white is not None:
        return
    if args.no_gui:
        sys.exit("missing --black/--white and --no-gui set. Provide them as x0,y0,x1,y1 fractions.")
    pick_folder = next((f for f in subfolders if not is_merge_folder(f, merge_set)), subfolders[0])
    is_m = is_merge_folder(pick_folder, merge_set)
    lead, _ = folder_lead_tests(os.path.join(root, pick_folder), is_m)
    p0 = profiles[0] if profiles else "none"
    lead_disp = to_srgb8(decode_merge(lead, cache) if is_m else decode(lead, p0, dcp_obj, cache), args.long)
    if args.black is None:
        print("PICKER: drag the UNEXPOSED film-base region -> BLACK point. Enter/Space to confirm.")
        args.black = pick_rect(lead_disp, "BLACK = unexposed base - drag box")
    if args.white is None:
        print("PICKER: drag the EXPOSED (dense) film-base region -> WHITE point.")
        args.white = pick_rect(lead_disp, "WHITE = exposed dense base - drag box")
    print(f"Picked black={tuple(round(v,3) for v in args.black)} white={tuple(round(v,3) for v in args.white)}")


# ----------------------------------------------------------------------------- mode: source-as-truth
def run_source_mode(args, root, subfolders, merge_set, convert_image, folder_profiles, out):
    if args.target not in subfolders:
        sys.exit(f"target '{args.target}' not in {subfolders}")
    tlead, ttests = folder_lead_tests(os.path.join(root, args.target), is_merge_folder(args.target, merge_set))
    if tlead is None:
        sys.exit(f"target '{args.target}' lacks lead + tests")
    n_tests = len(ttests)
    variants = [(f, p) for f in subfolders if f != args.target for p in folder_profiles(f)]
    pool = {f"{f} {p}": empty_pool() for f, p in variants}
    overlays = []
    for ti in range(n_tests):
        truth = convert_image(args.target, ttests[ti], "none")
        tL, tC, tH = lab_hsv(truth)
        print(f"\n[test {ti + 1}/{n_tests}] NCC vs truth:")
        for f, p in variants:
            lead, tests = folder_lead_tests(os.path.join(root, f), is_merge_folder(f, merge_set))
            if lead is None or ti >= len(tests):
                continue
            try:
                var = convert_image(f, tests[ti], p)
            except Exception as e:
                print(f"  {f} {p}: convert FAILED {e}"); continue
            r = register(var, truth)
            if r is None:
                print(f"  {f} {p}: REG FAILED"); continue
            warp, vm, ncc = r
            print(f"  {f:14s} {p:6s}: NCC={ncc:.3f}" + ("  <-weak,excl" if ncc < 0.6 else ""))
            if ncc < 0.6:
                continue
            if ti == 0:
                overlays.append((f"{f} {p}", _checker(truth, warp, vm)))
            vL, vC, vH = lab_hsv(warp)
            sel = vm & (tL > 3) & (tL < 99)
            add_pool(pool[f"{f} {p}"], drift_arrays(tL, tC, tH, vL, vC, vH, sel))
    pool = {k: finalize(v) for k, v in pool.items()}
    pool = {k: v for k, v in pool.items() if v["hue"].size}
    _write_outputs(pool, list(pool.keys()), args, out, overlays, mode="pixel")


# ----------------------------------------------------------------------------- mode: chart-as-truth
def run_chart_mode(args, root, subfolders, merge_set, convert_image, folder_profiles, out):
    cols, rows = (int(x) for x in args.chart_grid.lower().split("x"))
    refL, refC, refH = lab_hsv_srgb(reference_srgb(args.ref_illuminant))
    print(f"  chart reference: X-Rite CC24 (post-Nov2014), adopted white {args.ref_illuminant}")
    kind, idx = (args.chart_from.split(":") + ["0"])[:2]
    idx = int(idx)

    def chart_src(f):
        lead, tests = folder_lead_tests(os.path.join(root, f), is_merge_folder(f, merge_set))
        if lead is None:
            return None
        return lead if kind == "lead" else (tests[idx] if idx < len(tests) else None)

    # Resolve the chart quad PER light (hand-held -> different location/tilt each shot).
    quads = {}
    for f in subfolders:
        src = chart_src(f)
        if src is None:
            continue
        if args.chart_quad is not None:               # explicit corners/rect -> shared
            quads[f] = args.chart_quad
            continue
        if args.no_gui:
            sys.exit("chart mode needs a display to pick the chart, or pass --chart-corners.")
        fp = folder_profiles(f)[0]
        try:
            srgb = convert_image(f, src, fp)
        except Exception as e:
            print(f"  {f}: convert FAILED {e}"); continue
        print(f"PICK the ColorChecker in '{f}' (converted positive) — click 4 corners.")
        quads[f] = pick_chart_quad(srgb, cols, rows)

    variants = [(f, p) for f in subfolders if f in quads for p in folder_profiles(f)]
    pool = {}
    for f, p in variants:
        src = chart_src(f)
        try:
            srgb = convert_image(f, src, p)
        except Exception as e:
            print(f"  {f} {p}: convert FAILED {e}"); continue
        mL, mC, mH = sample_chart_quad(srgb, quads[f], cols, rows)
        rec = dict(val=refL, sat=refC, hue=refH, dV=mL - refL, dS=mC - refC, dH=cdiff(mH, refH))
        pool[f"{f} {p}"] = {k: np.asarray(v, float) for k, v in rec.items()}
        good = (refC > 10) & np.isfinite(rec["dH"])
        print(f"  {f:14s} {p:6s}: mean|Δhue|={np.nanmean(np.abs(rec['dH'][good])):5.1f}  "
              f"mean|Δsat|={np.nanmean(np.abs(rec['dS'])):5.1f}  mean|ΔL|={np.nanmean(np.abs(rec['dV'])):5.1f}")
    _write_outputs(pool, list(pool.keys()), args, out, [], mode="chart")


# ----------------------------------------------------------------------------- outputs
def _checker(a, b, mask, n=14):
    h, w = a.shape[:2]; bs = max(h, w) // n; o = a.copy()
    for y in range(0, h, bs):
        for x in range(0, w, bs):
            if ((x // bs) + (y // bs)) % 2 == 1:
                o[y:y + bs, x:x + bs] = b[y:y + bs, x:x + bs]
    o[~mask] = (o[~mask] * 0.3).astype(np.uint8)
    return o


def _overall(r):
    g = (r["sat"] > 8) & np.isfinite(r["dH"])
    return (float(np.average(np.abs(r["dH"][g]), weights=r["sat"][g])) if g.sum() else float("nan"),
            float(np.nanmean(np.abs(r["dS"]))), float(np.nanmean(r["dV"])), float(np.nanmean(np.abs(r["dV"]))))


def _hue_band_val(r, lo, hi, ysrc, circ, chroma, min_n=20):
    sel = (r["hue"] >= lo) & (r["hue"] < hi) & (r["sat"] > chroma) & np.isfinite(r[ysrc])
    return wmean(r[ysrc][sel], r["sat"][sel], circ) if sel.sum() >= min_n else float("nan")


def _lbin_val(r, lo, hi, ysrc, circ, chroma, min_n=20):
    sel = (r["val"] >= lo) & (r["val"] < hi) & np.isfinite(r[ysrc])
    if circ or chroma:
        sel &= r["sat"] > chroma
        w = r["sat"][sel]
    else:
        w = np.ones(int(sel.sum()))
    return wmean(r[ysrc][sel], w, circ) if sel.sum() >= min_n else float("nan")


def _write_outputs(pool, keys, args, out, overlays, mode):
    if not keys:
        print("no results to write"); return
    chr_thr = args.chroma if mode == "pixel" else 8.0
    mn = 1 if mode == "chart" else 20          # min samples per bin (24 patches vs millions of px)
    # ---- overall CSV ----
    with open(os.path.join(out, "overall.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["light_profile", "mean_abs_hue_drift_deg", "mean_abs_sat_drift", "value_bias_L", "mean_abs_value_drift_L"])
        for k in keys:
            o = _overall(pool[k]); w.writerow([k] + [f"{x:.2f}" for x in o])
    # ---- per-hue-band CSV (hue & sat drift) ----
    with open(os.path.join(out, "drift_per_hue_band.csv"), "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["band"] + [f"{k} dHue" for k in keys] + [f"{k} dSat" for k in keys])
        for bn, lo, hi in HUE_BANDS:
            w.writerow([bn] + [f"{_hue_band_val(pool[k], lo, hi, 'dH', True, chr_thr, mn):.1f}" for k in keys]
                       + [f"{_hue_band_val(pool[k], lo, hi, 'dS', False, chr_thr, mn):.1f}" for k in keys])
    # ---- per-L-bin CSV (hue, sat, value drift) ----
    with open(os.path.join(out, "drift_vs_value.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["L_low", "L_high"] + [f"{k} dHue" for k in keys] + [f"{k} dSat" for k in keys] + [f"{k} dVal" for k in keys])
        for lo, hi in LBINS:
            w.writerow([lo, hi]
                       + [f"{_lbin_val(pool[k], lo, hi, 'dH', True, chr_thr, mn):.1f}" for k in keys]
                       + [f"{_lbin_val(pool[k], lo, hi, 'dS', False, 0, mn):.1f}" for k in keys]
                       + [f"{_lbin_val(pool[k], lo, hi, 'dV', False, 0, mn):.1f}" for k in keys])
    # ---- chart per-patch CSV (the most useful absolute-accuracy table) ----
    if mode == "chart":
        with open(os.path.join(out, "chart_per_patch.csv"), "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["patch", "ref_L", "ref_C", "ref_hue"]
                       + [f"{k} dHue" for k in keys] + [f"{k} dSat" for k in keys] + [f"{k} dVal" for k in keys])
            ref = pool[keys[0]]
            for i in range(ref["hue"].size):
                w.writerow([i + 1, f"{ref['val'][i]:.1f}", f"{ref['sat'][i]:.1f}", f"{ref['hue'][i]:.1f}"]
                           + [f"{pool[k]['dH'][i]:.1f}" for k in keys]
                           + [f"{pool[k]['dS'][i]:.1f}" for k in keys]
                           + [f"{pool[k]['dV'][i]:.1f}" for k in keys])
    # ---- graphic: per light source, 5 drift panels ----
    lights = []
    for k in keys:
        f = k.rsplit(" ", 1)[0]
        if f not in lights:
            lights.append(f)
    pngs = []
    for f in lights:
        ks = [k for k in keys if k.rsplit(" ", 1)[0] == f]
        png = _plot_drifts(f, ks, pool, out, chr_thr, mode)
        pngs.append((f, png))
    if overlays:
        H = max(o.shape[0] for _, o in overlays)
        tiles = []
        for name, o in overlays:
            cv2.rectangle(o, (0, 0), (o.shape[1], 20), (0, 0, 0), -1)
            cv2.putText(o, name, (4, 15), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
            tiles.append(cv2.copyMakeBorder(o, 0, H - o.shape[0], 0, 6, cv2.BORDER_CONSTANT, value=(50, 50, 50)))
        cv2.imwrite(os.path.join(out, "registration_overlays.jpg"), np.hstack(tiles), [cv2.IMWRITE_JPEG_QUALITY, 90])
    # ---- text report ----
    _write_report_md(pool, keys, lights, args, out, mode, pngs, chr_thr, mn)
    print("\nOverall (mean|Δhue|deg, mean|Δsat|Cab, ΔL bias, mean|ΔL|):")
    for k in keys:
        o = _overall(pool[k]); print(f"  {k:22s} hue={o[0]:5.1f}  sat={o[1]:5.1f}  Lbias={o[2]:+5.1f}  |L|={o[3]:5.1f}")


def _plot_drifts(light, keys, pool, out, chr_thr, mode="pixel"):
    global _HUE_GRAD
    if _HUE_GRAD is None:
        _HUE_GRAD = _hue_gradient()
    fig, ax = plt.subplots(len(DRIFTS), 1, figsize=(11, 3.5 * len(DRIFTS)))
    for i, drift in enumerate(DRIFTS):
        key, xsrc, ysrc, chromatic, circ, xlab, ylab, title = drift
        for k in keys:
            prof = k.rsplit(" ", 1)[1]
            if mode == "chart":                       # 24 patches -> scatter the raw points
                r = pool[k]
                sel = np.isfinite(r[xsrc]) & np.isfinite(r[ysrc])
                if chromatic:
                    sel &= r["sat"] > 8
                xv, yv = r[xsrc][sel], r[ysrc][sel]
                o = np.argsort(xv)
                ax[i].plot(xv[o], yv[o], "-o", ms=5, color=PROFILE_COLORS.get(prof), label=prof)
            else:
                cur = binned_curve(pool[k], drift, chr_thr)
                if cur is None:
                    continue
                xs, ys = cur
                ax[i].plot(xs, ys, "-o", ms=3, color=PROFILE_COLORS.get(prof), label=prof)
        ax[i].axhline(0, color="k", lw=.6)
        ax[i].set_title(f"{light} — {title}"); ax[i].set_ylabel(ylab)
        ax[i].legend(fontsize=8); ax[i].grid(alpha=.3)
        if xsrc == "hue":
            # colour the hue axis: a CIELab hue strip sits ON TOP of the degree numbers.
            ax[i].set_xlim(0, 360)
            ax[i].set_xticklabels([]); ax[i].set_xlabel("")
            cax = ax[i].inset_axes([0, -0.17, 1, 0.06])
            cax.imshow(_HUE_GRAD, aspect="auto", extent=[0, 360, 0, 1])
            cax.set_yticks([]); cax.set_xlim(0, 360)
            cax.set_xticks(range(0, 361, 30)); cax.tick_params(labelsize=8)
            cax.set_xlabel(xlab)
        else:
            ax[i].set_xlabel(xlab)
    plt.subplots_adjust(hspace=0.6, top=0.97, bottom=0.04, left=0.08, right=0.97)
    path = os.path.join(out, f"drift_{light.replace(' ', '_')}.png")
    plt.savefig(path, dpi=105); plt.close()
    return os.path.basename(path)


def _write_report_md(pool, keys, lights, args, out, mode, pngs, chr_thr, min_n=20):
    lines = ["# Scan colour-fidelity report", "",
             f"- Root: `{args.root}`", f"- Target: `{args.target}` ({'absolute chart' if mode == 'chart' else 'source-as-truth'})",
             f"- bwpoint: black={args.black} white={args.white} density={args.density}",
             f"- Profiles: {args.profiles}", "",
             "Drift = variant − reference, in CIELab (hue=atan2(b,a) deg, saturation=Cab chroma, value=L*).", ""]
    lines += ["## Overall fidelity (lower = closer to reference)", "",
              "| light · profile | mean \\|Δhue\\| ° | mean \\|Δsat\\| | ΔL bias | mean \\|ΔL\\| |",
              "|---|---|---|---|---|"]
    for k in keys:
        o = _overall(pool[k])
        lines.append(f"| {k} | {o[0]:.1f} | {o[1]:.1f} | {o[2]:+.1f} | {o[3]:.1f} |")
    lines.append("")
    for f in lights:
        ks = [k for k in keys if k.rsplit(" ", 1)[0] == f]
        profs = [k.rsplit(" ", 1)[1] for k in ks]
        lines += [f"## {f}", "", f"![{f}](drift_{f.replace(' ', '_')}.png)", "",
                  "### hue drift per colour band (deg)", "",
                  "| band | " + " | ".join(profs) + " |", "|" + "---|" * (len(profs) + 1)]
        for bn, lo, hi in HUE_BANDS:
            lines.append(f"| {bn} | " + " | ".join(f"{_hue_band_val(pool[k], lo, hi, 'dH', True, chr_thr, min_n):.1f}" for k in ks) + " |")
        lines += ["", "### drift vs brightness L* (Δhue° / Δsat / ΔL)", "",
                  "| L* | " + " | ".join(profs) + " |", "|" + "---|" * (len(profs) + 1)]
        for lo, hi in LBINS:
            cell = []
            for k in ks:
                dh = _lbin_val(pool[k], lo, hi, "dH", True, chr_thr, min_n)
                ds = _lbin_val(pool[k], lo, hi, "dS", False, 0, min_n)
                dv = _lbin_val(pool[k], lo, hi, "dV", False, 0, min_n)
                cell.append(f"{dh:+.1f}/{ds:+.1f}/{dv:+.1f}")
            lines.append(f"| {lo}-{hi} | " + " | ".join(cell) + " |")
        lines.append("")
    with open(os.path.join(out, "report.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


if __name__ == "__main__":
    main()
