#!/usr/bin/env python3
"""
color_eval.py — general image colour-fidelity evaluator (standalone).

Reads one or more FINISHED images (JPG/PNG/TIFF/…) and reports how each reproduces
a colour reference, as five CIELab drift curves (hue/saturation/value drift cross-
tabbed against hue and value), in graphic + text form.

Reference (target) — two modes:
  --target chart          an in-frame X-Rite/Calibrite ColorChecker Classic 24.
                          Locate it ONCE (4-corner picker, or --chart-corners); the
                          SAME location is reused for every image.
  --target GOLD_IMAGE     one image is the gold standard; the others are registered
                          to it and compared per pixel.

No app coupling: depends only on numpy, opencv-python, matplotlib. Read-only.

Examples:
  python color_eval.py shot1.jpg shot2.jpg --target chart
  python color_eval.py folder_of_jpgs --target chart --ref-illuminant D65
  python color_eval.py a.jpg b.jpg c.jpg --target reference.jpg
"""
import argparse
import csv
import glob
import math
import os
import sys

import numpy as np
import cv2
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

IMG_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}
HUE_BANDS = [("R", 0, 30), ("O", 30, 60), ("Y", 60, 90), ("G", 90, 165),
             ("C", 165, 195), ("B", 195, 255), ("P", 255, 315), ("M", 315, 360)]
LBINS = [(0, 20), (20, 35), (35, 50), (50, 65), (65, 80), (80, 100)]
REF_ILLUMINANTS = ("D50", "D55", "D65")

GREY_MAX = 6.0   # CIELab chroma below this = neutral/grey (the ColorChecker grey ramp / neutral pixels)
# The four drift cross-plots: (key, x-source, y-source, select, circular-y, xlabel, ylabel, title).
# select: "colored" = chromatic patches/pixels only (the hue circle); "grey" = the neutral ramp only.
# Hue-axis plots use the colour patches (grey has no meaningful hue); value-axis plots use the grey
# ramp (it spans brightness and is the neutral-axis tone/cast reference).
DRIFTS = [
    ("hue_vs_hue",   "hue", "dH", "colored", True,  "hue (deg)", "hue drift (deg)",  "hue-vs-hue drift (colour patches)"),
    ("value_vs_hue", "val", "dH", "grey",    True,  "L*",        "hue drift (deg)",  "value-vs-hue drift (grey ramp)"),
    ("hue_vs_sat",   "hue", "dS", "colored", False, "hue (deg)", "sat drift (Cab)",  "hue-vs-saturation drift (colour patches)"),
    ("value_vs_val", "val", "dV", "grey",    False, "L*",        "value drift (L*)", "value-vs-value drift (grey ramp)"),
]
MAXPIX = 200000

# ColorChecker Classic 24 REFERENCE — X-Rite "November 2014 edition and newer",
# measured on an i1Pro 2 (M0). CIELab (D50, 2deg), row-major (dark-skin first).
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
_WHITES = {"D50": (0.96422, 1.0, 0.82521), "D55": (0.95682, 1.0, 0.92149), "D65": (0.95047, 1.0, 1.08883)}
_M_BRADFORD = np.array([[0.8951, 0.2664, -0.1614], [-0.7502, 1.7135, 0.0367], [0.0389, -0.0685, 1.0296]])
_M_XYZ2SRGB = np.array([[3.2404542, -1.5371385, -0.4985314],
                        [-0.9692660, 1.8760108, 0.0415560], [0.0556434, -0.2040259, 1.0572252]])
_HUE_GRAD = None


# ----------------------------------------------------------------------------- reference / colour
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
    """ColorChecker reference sRGB under a chosen adopted white (Bradford D50->white)."""
    xyz = _lab_to_xyz(COLORCHECKER24_LAB_D50, _WHITES["D50"]) @ _cat_bradford(_WHITES["D50"], _WHITES[illuminant]).T
    lin = np.clip(xyz @ _M_XYZ2SRGB.T, 0, 1)
    srgb = np.where(lin <= 0.0031308, 12.92 * lin, 1.055 * np.power(lin, 1 / 2.4) - 0.055)
    return [tuple(int(x) for x in row) for row in np.clip(np.rint(srgb * 255), 0, 255).astype(int)]


def lab_hsv(bgr):
    """L*(value), Cab(saturation), hue(deg) maps from a BGR uint8 image."""
    L = cv2.cvtColor(bgr, cv2.COLOR_BGR2Lab).astype(np.float32)
    a, b = L[..., 1] - 128.0, L[..., 2] - 128.0
    return L[..., 0] * 100 / 255.0, np.hypot(a, b), np.degrees(np.arctan2(b, a)) % 360


def lab_hsv_srgb(rgb_tuples):
    arr = np.array(rgb_tuples, np.uint8).reshape(-1, 1, 3)[:, :, ::-1]     # RGB -> BGR
    L = cv2.cvtColor(arr, cv2.COLOR_BGR2Lab).astype(np.float32).reshape(-1, 3)
    a, b = L[:, 1] - 128.0, L[:, 2] - 128.0
    return L[:, 0] * 100 / 255.0, np.hypot(a, b), np.degrees(np.arctan2(b, a)) % 360


def _lab_to_bgr(L, C, H):
    """Reconstruct a BGR swatch from L*/chroma/hue (for the visual chart check)."""
    a, b = C * np.cos(np.radians(H)), C * np.sin(np.radians(H))
    lab = np.stack([np.clip(L * 255 / 100, 0, 255), np.clip(a + 128, 0, 255),
                    np.clip(b + 128, 0, 255)], -1).astype(np.uint8).reshape(-1, 1, 3)
    return cv2.cvtColor(lab, cv2.COLOR_Lab2BGR).reshape(-1, 3)


def cdiff(h2, h1):
    return (h2 - h1 + 180) % 360 - 180


def wmean(v, w, circular):
    if np.size(w) == 0 or w.sum() < 1e-6:
        return float("nan")
    if circular:
        r = np.radians(v)
        return math.degrees(math.atan2((w * np.sin(r)).sum(), (w * np.cos(r)).sum()))
    return float(np.average(v, weights=w))


def _hue_gradient(n=360, L8=180, C=55):
    deg = np.arange(n)
    lab = np.zeros((1, n, 3), np.uint8)
    lab[0, :, 0] = L8
    lab[0, :, 1] = np.clip(128 + C * np.cos(np.radians(deg)), 0, 255).astype(np.uint8)
    lab[0, :, 2] = np.clip(128 + C * np.sin(np.radians(deg)), 0, 255).astype(np.uint8)
    return (cv2.cvtColor(lab, cv2.COLOR_Lab2BGR)[:, :, ::-1].astype(np.float32) / 255.0)


# ----------------------------------------------------------------------------- chart geometry
def parse_corners(s):
    if s is None:
        return None
    v = [float(x) for x in s.split(",")]
    if len(v) != 8:
        raise argparse.ArgumentTypeError("chart-corners = x0,y0,x1,y1,x2,y2,x3,y3 (TL,TR,BR,BL fractions)")
    return [(v[0], v[1]), (v[2], v[3]), (v[4], v[5]), (v[6], v[7])]


def _grid_centers(quad_px, cols, rows):
    unit = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], np.float32)
    H = cv2.getPerspectiveTransform(unit, np.asarray(quad_px, np.float32))
    uv = np.array([[[(c + 0.5) / cols, (r + 0.5) / rows]] for r in range(rows) for c in range(cols)], np.float32)
    return cv2.perspectiveTransform(uv, H).reshape(-1, 2)


def sample_chart_quad(bgr, corners_frac, cols, rows, patch=0.45):
    """Sample cols*rows patches from the 4 chart corners (fractions, [TL,TR,BR,BL]).
    Perspective-correct -> tilt/rotation/mirror/perspective all handled; corner ORDER
    fixes orientation (corner 1 = patch#1 / dark-skin). Returns L*,C,hue arrays."""
    h, w = bgr.shape[:2]
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
        p = bgr[y0:y1, x0:x1]
        if p.size == 0:
            out.append((np.nan, np.nan, np.nan)); continue
        med = np.median(p.reshape(-1, 3), axis=0).astype(np.uint8).reshape(1, 1, 3)
        lab = cv2.cvtColor(med, cv2.COLOR_BGR2Lab).astype(np.float32).reshape(3)
        a, b = lab[1] - 128.0, lab[2] - 128.0
        out.append((lab[0] * 100 / 255.0, math.hypot(a, b), math.degrees(math.atan2(b, a)) % 360))
    arr = np.array(out)
    return arr[:, 0], arr[:, 1], arr[:, 2]


def pick_chart_quad(bgr, cols, rows):
    """4-corner ColorChecker picker with MOUSE-WHEEL ZOOM (centered on the cursor) and
    a live 24-patch grid overlay. Click corners IN ORDER: 1=patch#1 (dark-skin) corner,
    2=far end of that top row, 3=opposite/bottom corner, 4=white-patch corner. Robust
    to tilt/rotation/mirror. Shown at native resolution unless it exceeds 1080p.
    Keys: r=reset, Enter=confirm, Esc=cancel. Returns 4 corner fractions [TL,TR,BR,BL]."""
    h, w = bgr.shape[:2]
    s = min(1.0, 1920.0 / w, 1080.0 / h)          # full resolution unless it exceeds 1080p
    base = cv2.resize(bgr, (int(w * s), int(h * s))) if s < 1 else bgr.copy()
    dh, dw = base.shape[:2]
    pts = []                                       # corner points in BASE-image coords
    view = {"z": 1.0, "cx": dw / 2.0, "cy": dh / 2.0}   # zoom + view centre (base coords)
    win = "Chart corners  1=dark-skin 2=top-end 3=bottom 4=white | wheel=zoom  r=reset  Enter=ok  Esc=cancel"

    def w2b(x, y):                                 # window pixel -> base coords
        return (view["cx"] + (x - dw / 2.0) / view["z"], view["cy"] + (y - dh / 2.0) / view["z"])

    def clamp():
        hw, hh = (dw / view["z"]) / 2.0, (dh / view["z"]) / 2.0
        view["cx"] = min(max(view["cx"], hw), dw - hw)
        view["cy"] = min(max(view["cy"], hh), dh - hh)

    def on_mouse(ev, x, y, flags, param):
        if ev == cv2.EVENT_MOUSEWHEEL:
            try:
                delta = cv2.getMouseWheelDelta(flags)          # not in all cv2 builds
            except Exception:
                delta = (flags >> 16) & 0xFFFF                 # high word = wheel delta...
                if delta >= 0x8000:
                    delta -= 0x10000                           # ...as a signed short
            bx, by = w2b(x, y)                      # keep this base point under the cursor
            view["z"] = float(np.clip(view["z"] * (1.25 if delta > 0 else 0.8), 1.0, 16.0))
            view["cx"], view["cy"] = bx - (x - dw / 2.0) / view["z"], by - (y - dh / 2.0) / view["z"]
            clamp()
        elif ev == cv2.EVENT_LBUTTONDOWN and len(pts) < 4:
            pts.append(w2b(x, y))
    cv2.namedWindow(win, cv2.WINDOW_AUTOSIZE)
    cv2.setMouseCallback(win, on_mouse)
    labels = {0: "1", cols - 1: str(cols), cols * rows - cols: str(cols * rows - cols + 1), cols * rows - 1: str(cols * rows)}
    while True:
        z, cx, cy = view["z"], view["cx"], view["cy"]
        M = np.array([[z, 0, dw / 2.0 - z * cx], [0, z, dh / 2.0 - z * cy]], np.float32)
        img = cv2.warpAffine(base, M, (dw, dh))

        def b2w(p):                                # base coords -> window pixel
            return (int(z * (p[0] - cx) + dw / 2.0), int(z * (p[1] - cy) + dh / 2.0))
        for i, p in enumerate(pts):
            wp = b2w(p)
            cv2.circle(img, wp, 5, (0, 0, 255), -1)
            cv2.putText(img, str(i + 1), (wp[0] + 6, wp[1] - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
        if len(pts) >= 2:
            cv2.polylines(img, [np.array([b2w(p) for p in pts], np.int32)], len(pts) == 4, (0, 255, 255), 1)
        if len(pts) == 4:
            for j, c in enumerate(_grid_centers(pts, cols, rows)):
                wc = b2w(c)
                cv2.circle(img, wc, 3, (0, 255, 0), -1)
                if j in labels:
                    cv2.putText(img, labels[j], (wc[0] + 3, wc[1] - 3), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
        cv2.putText(img, f"corner {len(pts)}/4   zoom {z:.1f}x   (wheel to zoom)", (6, dh - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        cv2.imshow(win, img)
        k = cv2.waitKey(20) & 0xFF
        if k == ord('r'):
            pts.clear(); view.update(z=1.0, cx=dw / 2.0, cy=dh / 2.0)
        elif k in (13, 10) and len(pts) == 4:
            break
        elif k == 27:
            cv2.destroyWindow(win); raise RuntimeError("chart pick cancelled")
    cv2.destroyWindow(win)
    return [(p[0] / dw, p[1] / dh) for p in pts]


# ----------------------------------------------------------------------------- registration (gold mode)
def register(var_bgr, gold_bgr):
    g1 = cv2.cvtColor(gold_bgr, cv2.COLOR_BGR2GRAY)
    g2 = cv2.cvtColor(var_bgr, cv2.COLOR_BGR2GRAY)
    orb = cv2.ORB_create(6000)
    k1, d1 = orb.detectAndCompute(g1, None)
    k2, d2 = orb.detectAndCompute(g2, None)
    if d1 is None or d2 is None:
        return None
    good = [m for m, n in cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(d2, d1, k=2) if m.distance < 0.75 * n.distance]
    if len(good) < 12:
        return None
    sp = np.float32([k2[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dp = np.float32([k1[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    H, _ = cv2.findHomography(sp, dp, cv2.RANSAC, 3.0)
    if H is None:
        return None
    th, tw = gold_bgr.shape[:2]
    warp = cv2.warpPerspective(var_bgr, H, (tw, th))
    vm = cv2.erode(cv2.warpPerspective(np.ones(var_bgr.shape[:2], np.uint8), H, (tw, th)),
                   np.ones((7, 7), np.uint8)).astype(bool)
    gw = cv2.cvtColor(warp, cv2.COLOR_BGR2GRAY).astype(np.float32)
    gt = g1.astype(np.float32)
    a, b = gt[vm] - gt[vm].mean(), gw[vm] - gw[vm].mean()
    ncc = float((a * b).sum() / (np.sqrt((a * a).sum() * (b * b).sum()) + 1e-9))
    return warp, vm, ncc


def _checker(a, b, mask, n=14):
    h, w = a.shape[:2]; bs = max(h, w) // n; o = a.copy()
    for y in range(0, h, bs):
        for x in range(0, w, bs):
            if ((x // bs) + (y // bs)) % 2 == 1:
                o[y:y + bs, x:x + bs] = b[y:y + bs, x:x + bs]
    o[~mask] = (o[~mask] * 0.3).astype(np.uint8)
    return o


# ----------------------------------------------------------------------------- drift
def drift_arrays(tL, tC, tH, vL, vC, vH, sel):
    idx = np.flatnonzero(sel)
    if idx.size > MAXPIX:
        idx = idx[:: max(1, idx.size // MAXPIX)]
    return dict(val=tL.ravel()[idx], sat=tC.ravel()[idx], hue=tH.ravel()[idx],
                dV=(vL - tL).ravel()[idx], dS=(vC - tC).ravel()[idx], dH=cdiff(vH, tH).ravel()[idx])


def _csel(r):
    """Chroma used to split colour vs grey. In chart mode this is the ILLUMINANT-
    INDEPENDENT D50 chroma ('csel'), so the 6 neutral patches stay classed as grey
    even when --ref-illuminant renders the reference warm. Gold mode: pixel chroma."""
    return r["csel"] if "csel" in r else r["sat"]


def _sel(r, select, chroma_thr):
    """Boolean mask selecting colour patches/pixels, the grey ramp, or all."""
    c = _csel(r)
    if select == "colored":
        return c > chroma_thr
    if select == "grey":
        return c < GREY_MAX
    return np.ones(np.shape(c), bool)


def binned_curve(r, drift, chroma_thr):
    _, xsrc, ysrc, select, circ, _, _, _ = drift
    if np.size(r["hue"]) == 0:
        return None
    x, y = r[xsrc], r[ysrc]
    sel = np.isfinite(x) & np.isfinite(y) & _sel(r, select, chroma_thr)
    w = r["sat"][sel] if select == "colored" else np.ones(int(sel.sum()))   # sat-weight colours; grey uniform
    x, y = x[sel], y[sel]
    if xsrc == "hue":
        xs, width = np.arange(0, 360, 10), 10
    else:
        xs, width = np.arange(2.5, 100, 5), 5
    ys = []
    for xb in xs:
        m = (x >= xb - (0 if xsrc == "hue" else width / 2)) & (x < xb + (width if xsrc == "hue" else width / 2))
        ys.append(wmean(y[m], w[m], circ) if m.sum() > 40 else np.nan)
    return xs, np.array(ys)


# ----------------------------------------------------------------------------- io helpers
def collect_images(patterns):
    out = []
    for p in patterns:
        if os.path.isdir(p):
            out += sorted(f for f in glob.glob(os.path.join(p, "*")) if os.path.splitext(f)[1].lower() in IMG_EXTS)
        elif any(ch in p for ch in "*?["):
            out += sorted(glob.glob(p))
        else:
            out.append(p)
    return [p for p in out if os.path.splitext(p)[1].lower() in IMG_EXTS and os.path.isfile(p)]


def load_bgr(path, long_edge):
    img = cv2.imread(path, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"cannot read image: {path}")
    h, w = img.shape[:2]
    s = long_edge / max(h, w)
    return cv2.resize(img, (max(1, int(w * s)), max(1, int(h * s))), interpolation=cv2.INTER_AREA) if s < 1 else img


# ----------------------------------------------------------------------------- modes
def run_chart(paths, args):
    cols, rows = (int(x) for x in args.chart_grid.lower().split("x"))
    imgs = {p: load_bgr(p, args.long) for p in paths}
    if args.chart_corners:
        quad = args.chart_corners
    elif args.no_gui:
        sys.exit("chart mode needs --chart-corners or a display")
    else:
        pick_on = paths[min(args.chart_image, len(paths) - 1)]
        print(f"Locate the ColorChecker on '{os.path.basename(pick_on)}' — this location is reused for ALL images.")
        full = cv2.imread(pick_on, cv2.IMREAD_COLOR)      # native resolution for precise clicking
        quad = pick_chart_quad(full if full is not None else imgs[pick_on], cols, rows)
    refL, refC, refH = lab_hsv_srgb(reference_srgb(args.ref_illuminant))
    base = np.array(COLORCHECKER24_LAB_D50, float)
    csel = np.hypot(base[:, 1], base[:, 2])           # D50 chroma -> illuminant-independent grey/colour split
    ref_bgr = np.array(reference_srgb(args.ref_illuminant), np.uint8)[:, ::-1]   # RGB -> BGR reference swatches
    print(f"Reference: X-Rite ColorChecker24 (post-Nov2014), adopted white {args.ref_illuminant}\n")
    pool, measured = {}, {}
    for p in paths:
        name = os.path.basename(p)
        mL, mC, mH = sample_chart_quad(imgs[p], quad, cols, rows)
        measured[name] = (mL, mC, mH)                 # sampled L*,C,hue per patch (for the visual checks)
        pool[name] = dict(val=refL, sat=refC, hue=refH, dV=mL - refL, dS=mC - refC, dH=cdiff(mH, refH), csel=csel)
        good = (refC > 10) & np.isfinite(pool[name]["dH"])
        print(f"  {name:30s} mean|Δhue|={np.nanmean(np.abs(pool[name]['dH'][good])):5.1f}  "
              f"mean|Δsat|={np.nanmean(np.abs(pool[name]['dS'])):5.1f}  mean|ΔL|={np.nanmean(np.abs(pool[name]['dV'])):5.1f}")
    _write_chart_check(measured, ref_bgr, refL, refC, args.out)
    write_outputs(pool, list(pool.keys()), args, mode="chart")


def run_gold(paths, args):
    gold_path = os.path.abspath(args.target)
    gold = load_bgr(gold_path, args.long)
    gL, gC, gH = lab_hsv(gold)
    eval_paths = [p for p in paths if os.path.abspath(p) != gold_path]
    if not eval_paths:
        sys.exit("no images to evaluate (all match the gold image)")
    print(f"Gold standard: {os.path.basename(gold_path)}  ({len(eval_paths)} image(s) to compare)\n")
    pool, overlays = {}, []
    for p in eval_paths:
        name = os.path.basename(p)
        var = load_bgr(p, args.long)
        r = register(var, gold)
        if r is None:
            print(f"  {name:30s} REGISTRATION FAILED (different scene?)"); continue
        warp, vm, ncc = r
        print(f"  {name:30s} NCC={ncc:.3f}" + ("  <- weak, excluded" if ncc < 0.6 else ""))
        if ncc < 0.6:
            continue
        overlays.append((name, _checker(gold, warp, vm)))
        vL, vC, vH = lab_hsv(warp)
        pool[name] = drift_arrays(gL, gC, gH, vL, vC, vH, vm & (gL > 3) & (gL < 99))
    write_outputs(pool, list(pool.keys()), args, mode="pixel", overlays=overlays)


# ----------------------------------------------------------------------------- outputs
def _overall(r):
    """hue & saturation drift on the COLOUR patches; value drift (bias + |ΔL|) on the GREY ramp."""
    c = _csel(r)
    col = (c > 8) & np.isfinite(r["dH"])
    grey = c < GREY_MAX
    return (float(np.average(np.abs(r["dH"][col]), weights=r["sat"][col])) if col.sum() else float("nan"),
            float(np.nanmean(np.abs(r["dS"][col]))) if col.sum() else float("nan"),
            float(np.nanmean(r["dV"][grey])) if grey.sum() else float("nan"),
            float(np.nanmean(np.abs(r["dV"][grey]))) if grey.sum() else float("nan"))


def _hue_band_val(r, lo, hi, ysrc, circ, chroma, min_n):
    """Colour-patch metric per hue band (chroma-weighted)."""
    sel = (r["hue"] >= lo) & (r["hue"] < hi) & (_csel(r) > chroma) & np.isfinite(r[ysrc])
    return wmean(r[ysrc][sel], _csel(r)[sel], circ) if sel.sum() >= min_n else float("nan")


def _lbin_val(r, lo, hi, ysrc, circ, select, chroma, min_n):
    """Metric per L* bin over the selected patches/pixels ('grey' or 'colored')."""
    sel = (r["val"] >= lo) & (r["val"] < hi) & np.isfinite(r[ysrc]) & _sel(r, select, chroma)
    w = r["sat"][sel] if select == "colored" else np.ones(int(sel.sum()))
    return wmean(r[ysrc][sel], w, circ) if sel.sum() >= min_n else float("nan")


def write_outputs(pool, keys, args, mode, overlays=None):
    keys = [k for k in keys if k in pool and np.size(pool[k]["hue"])]
    if not keys:
        print("no results to write"); return
    out = args.out
    chr_thr = args.chroma if mode == "pixel" else 8.0
    mn = 1 if mode == "chart" else 20
    with open(os.path.join(out, "overall.csv"), "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["image", "mean_abs_hue_drift_deg", "mean_abs_sat_drift", "value_bias_L", "mean_abs_value_drift_L"])
        for k in keys:
            w.writerow([k] + [f"{x:.2f}" for x in _overall(pool[k])])
    with open(os.path.join(out, "drift_per_hue_band.csv"), "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["band"] + [f"{k} dHue" for k in keys] + [f"{k} dSat" for k in keys])
        for bn, lo, hi in HUE_BANDS:
            w.writerow([bn] + [f"{_hue_band_val(pool[k], lo, hi, 'dH', True, chr_thr, mn):.1f}" for k in keys]
                       + [f"{_hue_band_val(pool[k], lo, hi, 'dS', False, chr_thr, mn):.1f}" for k in keys])
    with open(os.path.join(out, "drift_vs_value_greyramp.csv"), "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["L_low", "L_high"] + [f"{k} dHue" for k in keys] + [f"{k} dVal" for k in keys])
        for lo, hi in LBINS:
            w.writerow([lo, hi]
                       + [f"{_lbin_val(pool[k], lo, hi, 'dH', True, 'grey', 0, mn):.1f}" for k in keys]
                       + [f"{_lbin_val(pool[k], lo, hi, 'dV', False, 'grey', 0, mn):.1f}" for k in keys])
    if mode == "chart":
        with open(os.path.join(out, "chart_per_patch.csv"), "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["patch", "ref_L", "ref_C", "ref_hue"] + [f"{k} dHue" for k in keys] + [f"{k} dSat" for k in keys] + [f"{k} dVal" for k in keys])
            ref = pool[keys[0]]
            for i in range(np.size(ref["hue"])):
                w.writerow([i + 1, f"{ref['val'][i]:.1f}", f"{ref['sat'][i]:.1f}", f"{ref['hue'][i]:.1f}"]
                           + [f"{pool[k]['dH'][i]:.1f}" for k in keys]
                           + [f"{pool[k]['dS'][i]:.1f}" for k in keys]
                           + [f"{pool[k]['dV'][i]:.1f}" for k in keys])
    _plot(keys, pool, out, chr_thr, mode)
    if overlays:
        H = max(o.shape[0] for _, o in overlays)
        tiles = []
        for name, o in overlays:
            cv2.rectangle(o, (0, 0), (o.shape[1], 20), (0, 0, 0), -1)
            cv2.putText(o, name, (4, 15), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
            tiles.append(cv2.copyMakeBorder(o, 0, H - o.shape[0], 0, 6, cv2.BORDER_CONSTANT, value=(50, 50, 50)))
        cv2.imwrite(os.path.join(out, "registration_overlays.jpg"), np.hstack(tiles), [cv2.IMWRITE_JPEG_QUALITY, 90])
    _report_md(pool, keys, args, out, mode, mn, chr_thr)
    _write_pdf(out)
    print("\nOverall (mean|Δhue|deg, mean|Δsat|, ΔL bias, mean|ΔL|):")
    for k in keys:
        o = _overall(pool[k]); print(f"  {k:30s} hue={o[0]:5.1f}  sat={o[1]:5.1f}  Lbias={o[2]:+5.1f}  |L|={o[3]:5.1f}")
    print(f"\nDone. Outputs in: {out}")


def _plot(keys, pool, out, chr_thr, mode):
    global _HUE_GRAD
    if _HUE_GRAD is None:
        _HUE_GRAD = _hue_gradient()
    colors = plt.cm.tab10(np.linspace(0, 1, 10))
    ncol = 2                                          # 2x2 landscape: left col = hue-axis, right = value-axis
    nrow = -(-len(DRIFTS) // ncol)
    fig, axes = plt.subplots(nrow, ncol, figsize=(8 * ncol, 4.6 * nrow))
    ax = np.atleast_1d(axes).ravel()
    for i, drift in enumerate(DRIFTS):
        _, xsrc, ysrc, select, circ, xlab, ylab, title = drift
        for ki, k in enumerate(keys):
            col = colors[ki % 10]
            r = pool[k]
            if mode == "chart":
                sel = np.isfinite(r[xsrc]) & np.isfinite(r[ysrc]) & _sel(r, select, 8)
                xv, yv = r[xsrc][sel], r[ysrc][sel]
                o = np.argsort(xv)
                ax[i].plot(xv[o], yv[o], "-o", ms=4, color=col, label=k)
            else:
                cur = binned_curve(r, drift, chr_thr)
                if cur is None:
                    continue
                xs, ys = cur
                ax[i].plot(xs, ys, "-o", ms=3, color=col, label=k)
        ax[i].axhline(0, color="k", lw=.6); ax[i].set_title(title); ax[i].set_ylabel(ylab)
        ax[i].legend(fontsize=7); ax[i].grid(alpha=.3)
        if mode == "chart" and keys:                  # patch numbers under the x-axis (chart mode)
            r0 = pool[keys[0]]
            xref = np.asarray(r0[xsrc], float)
            m = _sel(r0, select, 8) & np.isfinite(xref)
            tr = ax[i].get_xaxis_transform()
            py = -0.05 if xsrc == "hue" else -0.13     # hue: above the strip; value: below the L* ticks
            for xv, num in zip(xref[m], (np.arange(xref.size) + 1)[m]):
                ax[i].text(xv, py, str(int(num)), transform=tr, ha="center", va="top",
                           fontsize=6.5, color="0.35", clip_on=False)
        if xsrc == "hue":
            ax[i].set_xlim(0, 360); ax[i].set_xticklabels([]); ax[i].set_xlabel("")
            cax = ax[i].inset_axes([0, -0.17, 1, 0.06])
            cax.imshow(_HUE_GRAD, aspect="auto", extent=[0, 360, 0, 1])
            cax.set_yticks([]); cax.set_xlim(0, 360); cax.set_xticks(range(0, 361, 30))
            cax.tick_params(labelsize=8); cax.set_xlabel(xlab)
        else:
            ax[i].set_xlabel(xlab)
    for j in range(len(DRIFTS), len(ax)):             # hide any unused cell
        ax[j].axis("off")
    plt.subplots_adjust(hspace=0.62, wspace=0.16, top=0.95, bottom=0.11, left=0.06, right=0.98)
    plt.savefig(os.path.join(out, "drift.png"), dpi=200); plt.close()


def _report_md(pool, keys, args, out, mode, mn, chr_thr):
    tgt = "in-frame ColorChecker (absolute)" if mode == "chart" else f"gold image `{os.path.basename(args.target)}`"
    lines = ["# Colour-fidelity report", "",
             f"- Images: {len(keys)}", f"- Target: {tgt}",
             (f"- Chart reference: X-Rite CC24 (post-Nov2014), adopted white {args.ref_illuminant}" if mode == "chart" else ""),
             "", "Drift = image − reference in CIELab (hue=atan2(b,a)°, saturation=Cab, value=L*).", "",
             "## Overall fidelity (lower = closer to reference)", "",
             "| image | mean \\|Δhue\\| ° | mean \\|Δsat\\| | ΔL bias | mean \\|ΔL\\| |", "|---|---|---|---|---|"]
    for k in keys:
        o = _overall(pool[k])
        lines.append(f"| {k} | {o[0]:.1f} | {o[1]:.1f} | {o[2]:+.1f} | {o[3]:.1f} |")
    lines += ["", "![drift](drift.png)", "",
              "### hue drift per colour band (deg)", "",
              "| band | " + " | ".join(keys) + " |", "|" + "---|" * (len(keys) + 1)]
    for bn, lo, hi in HUE_BANDS:
        lines.append(f"| {bn} | " + " | ".join(f"{_hue_band_val(pool[k], lo, hi, 'dH', True, chr_thr, mn):.1f}" for k in keys) + " |")
    lines += ["", "### grey-ramp drift vs brightness L* (Δhue° / ΔL)", "",
              "| L* | " + " | ".join(keys) + " |", "|" + "---|" * (len(keys) + 1)]
    for lo, hi in LBINS:
        cell = [f"{_lbin_val(pool[k], lo, hi, 'dH', True, 'grey', 0, mn):+.1f}/"
                f"{_lbin_val(pool[k], lo, hi, 'dV', False, 'grey', 0, mn):+.1f}" for k in keys]
        lines.append(f"| {lo}-{hi} | " + " | ".join(cell) + " |")
    if mode == "chart":
        lines += ["", "## Chart swatch checks", "",
                  "Reference swatches on the top row; each image's sampled patches below (column = patch #).", "",
                  "**1. Sampled colours (as-is)** — confirms the chart is mapped correctly and shows the raw drift:", "",
                  "![sampled](chart_check.png)", "",
                  "**2. Lightness matched (hue + chroma)** — sampled swatches at the reference L*, so lightness "
                  "differences are removed and hue/chroma shifts stand out:", "",
                  "![hue and chroma](chart_check_hue.png)", "",
                  "**3. Lightness + saturation matched (pure hue)** — sampled swatches at the reference L* and "
                  "chroma, so only the hue difference remains:", "",
                  "![pure hue](chart_check_purehue.png)", ""]
    with open(os.path.join(out, "report.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(x for x in lines if x is not None))


def _render_check(ref_bgr, rows, out, fname, note, cw=110, ch=150):
    """Draw: header (patch #/name) + REFERENCE swatch row + one SAMPLED row per image.
    Column i is patch i+1, so identities line up column-by-column. Green-family names
    are highlighted green. cw/ch = swatch column width / row height (big + tall so the
    swatches read large in the PDF, which fits the wide grid to the page width)."""
    SHORT = ["dk", "ls", "sky", "fol", "bf", "bg", "or", "pb", "mr", "pu", "yg", "oy",
             "bl", "GRN", "rd", "ye", "mg", "cy", "wh", "n8", "n6", "n5", "n3", "bk"]
    keys = list(rows.keys()); n = len(ref_bgr)
    L, HDR, FOOT = 150, 60, 34
    W, H = L + n * cw, HDR + (1 + len(keys)) * ch + FOOT
    img = np.full((H, W, 3), 30, np.uint8)
    F = cv2.FONT_HERSHEY_SIMPLEX
    for i in range(n):
        cx = L + i * cw + cw // 2
        cv2.putText(img, str(i + 1), (cx - (14 if i >= 9 else 7), 26), F, 0.7, (255, 255, 255), 2)
        grn = SHORT[i] in ("fol", "bg", "yg", "GRN")
        cv2.putText(img, SHORT[i], (cx - 20, 50), F, 0.55, (0, 230, 0) if grn else (185, 185, 185), 1)
    y = HDR
    cv2.putText(img, "REFERENCE", (8, y + ch // 2), F, 0.6, (0, 255, 255), 2)
    for i in range(n):
        img[y:y + ch, L + i * cw:L + (i + 1) * cw] = ref_bgr[i]
    for ri, k in enumerate(keys):
        y = HDR + (ri + 1) * ch
        cv2.putText(img, k[:16], (8, y + ch // 2), F, 0.55, (255, 255, 255), 1)
        for i in range(n):
            img[y:y + ch, L + i * cw:L + (i + 1) * cw] = rows[k][i]
    cv2.putText(img, note, (8, H - 10), F, 0.6, (200, 200, 200), 2)
    cv2.imwrite(os.path.join(out, fname), img)


def _write_chart_check(measured_lch, ref_bgr, refL, refC, out):
    """Three swatch grids (reference row on top, sampled rows below, col = patch#):
      chart_check.png          sampled colours as-is (L*, chroma, hue all measured)
      chart_check_hue.png      L* set to reference (lightness matched)  -> hue + chroma difference
      chart_check_purehue.png  L* AND chroma set to reference           -> PURE hue difference"""
    full = {k: _lab_to_bgr(mL, mC, mH) for k, (mL, mC, mH) in measured_lch.items()}
    huec = {k: _lab_to_bgr(refL, mC, mH) for k, (mL, mC, mH) in measured_lch.items()}
    hueo = {k: _lab_to_bgr(refL, refC, mH) for k, (mL, mC, mH) in measured_lch.items()}
    _render_check(ref_bgr, full, out, "chart_check.png",
                  "sampled colours as-is (col = patch#). top row = reference. green family in green.")
    _render_check(ref_bgr, huec, out, "chart_check_hue.png",
                  "sampled at the reference L* (lightness matched) -> hue + chroma difference.")
    _render_check(ref_bgr, hueo, out, "chart_check_purehue.png",
                  "sampled at the reference L* AND chroma -> PURE hue difference (lightness + saturation matched).")


def _write_pdf(out):
    """Render report.md -> report.pdf with the markdown-pdf package. It embeds the
    PNGs at their native resolution (no re-rasterising), so images stay crisp/zoomable.
    Uses A4 landscape so the wide plot/swatch grids get more width. Skipped with a
    hint if the package is missing."""
    try:
        from markdown_pdf import MarkdownPdf, Section
    except ImportError:
        print("  report.pdf skipped - run `pip install markdown-pdf linkify-it-py` to enable PDF export")
        return
    try:
        import re
        text = open(os.path.join(out, "report.md"), encoding="utf-8").read()
        pdf = MarkdownPdf(toc_level=2, mode="gfm-like", optimize=True)   # deflate images: 22MB -> 0.4MB, same res
        # Give the drift plot and each wide swatch grid its own page/section, else
        # PyMuPDF shrinks an image to whatever vertical space is left after the tables.
        before, dsep, after = text.partition("![drift](drift.png)")
        secs = [before]                                          # title + overall table
        if dsep:
            secs.append("## Drift curves\n\n" + dsep)           # the drift plot, alone -> full page
            head, sep, tail = after.partition("## Chart swatch checks")
            secs.append(head)                                   # drift tables
            if sep:
                parts = re.split(r"(?=\*\*\d+\. )", sep + tail)  # [heading+intro, cap1+img1, cap2+img2, ...]
                if len(parts) > 1:
                    parts[1] = parts[0] + parts[1]; parts = parts[1:]   # fold intro onto first grid's page
                secs += parts
        else:
            secs = [text]
        for s in secs:
            if s.strip():
                pdf.add_section(Section(s, root=out, paper_size="A4-L"))
        pdf.meta["title"] = "Colour-fidelity report"
        pdf.save(os.path.join(out, "report.pdf"))
    except Exception as e:                             # never let a PDF hiccup kill the report
        print(f"  report.pdf skipped - {type(e).__name__}: {e}")


def main():
    ap = argparse.ArgumentParser(description="General image colour-fidelity evaluator (chart or gold-image reference)")
    ap.add_argument("images", nargs="+", help="image files, folders, or globs (JPG/PNG/TIFF/…)")
    ap.add_argument("--target", required=True, help="'chart' (in-frame ColorChecker) or a path to a gold-standard image")
    ap.add_argument("--chart-corners", type=parse_corners, default=None,
                    help="(chart) 4 corner fractions x0,y0,..,x3,y3 in order TL(dark-skin),TR,BR,BL(white)")
    ap.add_argument("--chart-grid", default="6x4")
    ap.add_argument("--chart-image", type=int, default=0, help="(chart) index of the image to locate the chart on")
    ap.add_argument("--ref-illuminant", default="D65", choices=REF_ILLUMINANTS,
                    help="(chart) adopted white for the reference; D65 = daylight (default)")
    ap.add_argument("--long", type=int, default=1500, help="analysis long-edge px")
    ap.add_argument("--chroma", type=float, default=12.0, help="min CIELab chroma for a hue to count (gold mode)")
    ap.add_argument("--no-gui", action="store_true", help="never open the picker (chart mode needs --chart-corners)")
    ap.add_argument("--out", default=None,
                    help="output folder (default: '<first-image>_color_report' next to the image)")
    args = ap.parse_args()

    paths = collect_images(args.images)
    if not paths:
        sys.exit("no readable images found")
    if args.out is None:
        first = os.path.abspath(paths[0])
        stem = os.path.splitext(os.path.basename(first))[0]
        args.out = os.path.join(os.path.dirname(first), f"{stem}_color_report")
    os.makedirs(args.out, exist_ok=True)
    print(f"{len(paths)} image(s); target={args.target}\nOutput folder: {args.out}")
    if args.target == "chart":
        run_chart(paths, args)
    else:
        if not os.path.isfile(args.target):
            sys.exit(f"gold image not found: {args.target}")
        run_gold(paths, args)


if __name__ == "__main__":
    main()
