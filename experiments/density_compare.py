"""A/B compare the standard vs density-space negative inversion.

The standard pipeline inverts the scan in linear space (out = 65535 - v); the
density pipeline inverts in optical-density (log) space the way the film curve
actually encodes the scene (see _invert_negative_density in ccr_processor).

Usage:
    # On one of your own negatives (RAW or TIFF/PNG/JPG):
    python experiments/density_compare.py PATH [--ref x1,y1,x2,y2] [--gamma 0.55] [--out DIR]

    # Self-contained sanity check on a simulated negative (no file needed):
    python experiments/density_compare.py --synthetic

Writes <name>_standard.png, <name>_density.png and a side-by-side
<name>_compare.png next to the input (or in --out). The reference frame
defaults to the centre 60% box; pass --ref to match what you'd pick in the app.
"""
import os
import sys
import argparse
import numpy as np
import cv2

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import core.ccr_processor as P  # noqa: E402

RAW_EXTS = {".nef", ".cr2", ".cr3", ".arw", ".dng", ".raf",
            ".rw2", ".orf", ".pef", ".srw", ".nrw", ".raw"}


def decode_linear(path, max_side=1080):
    """Decode to a linear 16-bit RGB array, mirroring FreeCCR's rawpy params
    (linear gamma, no white balance, raw colour space)."""
    ext = os.path.splitext(path)[1].lower()
    if ext in RAW_EXTS:
        import rawpy
        with rawpy.imread(path) as raw:
            rgb = raw.postprocess(
                output_bps=16, no_auto_bright=True, gamma=(1, 1),
                use_camera_wb=False,
                output_color=rawpy.ColorSpace.raw,
                demosaic_algorithm=rawpy.DemosaicAlgorithm.AHD,
                half_size=False)
    else:
        img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
        if img is None:
            raise SystemExit(f"Could not read {path}")
        if img.ndim == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)
        else:
            img = cv2.cvtColor(img[..., :3], cv2.COLOR_BGR2RGB)
        rgb = (img.astype(np.uint16) << 8) if img.dtype == np.uint8 \
            else img.astype(np.uint16)
    h, w = rgb.shape[:2]
    s = max_side / max(h, w)
    if s < 1.0:
        rgb = cv2.resize(rgb, (int(round(w * s)), int(round(h * s))),
                         interpolation=cv2.INTER_AREA)
    return rgb


def simulate_negative(size=512):
    """Build a synthetic scene (positive) and the C-41 negative a camera would
    record from it: v = base * H**(-gamma) per channel, with an orange mask
    (per-channel film base). Returns (negative_linear_u16, scene_positive_u8)."""
    h = w = size
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    # Scene exposure H in (0,1]: horizontal luminance ramp x vertical tint ramp.
    lum = np.clip(0.04 + 0.95 * (xx / w), 1e-3, 1.0)
    scene = np.empty((h, w, 3), np.float32)
    scene[..., 0] = lum * (0.6 + 0.4 * (yy / h))      # R varies top->bottom
    scene[..., 1] = lum
    scene[..., 2] = lum * (1.0 - 0.4 * (yy / h))      # B opposite
    # colour patches so casts are obvious
    scene[40:140, 40:140] = (0.75, 0.15, 0.15)        # red
    scene[40:140, 200:300] = (0.15, 0.55, 0.20)       # green
    scene[40:140, 360:460] = (0.15, 0.25, 0.75)       # blue
    scene = np.clip(scene, 1e-3, 1.0)

    gamma = np.array([0.62, 0.58, 0.55], np.float32)  # per-layer film contrast
    mask_base = np.array([0.95, 0.55, 0.30], np.float32)  # orange mask (R>G>B)
    neg = mask_base[None, None, :] * np.power(scene, -gamma[None, None, :])
    neg = neg / neg.max()                              # fit into [0,1]
    neg_u16 = np.clip(neg * 65535.0, 0, 65535).astype(np.uint16)
    scene_u8 = (np.clip(scene, 0, 1) ** (1 / 2.2) * 255).astype(np.uint8)
    return neg_u16, scene_u8


class FakeImage:
    """Minimal stand-in for CCRImage in preview mode (output_path=None)."""
    def __init__(self, resized_raw, reference_frame):
        self.resized_raw = resized_raw
        self.reference_frame = reference_frame
        self.fine_rotation_angle = 0
        self.horizontal_mirrored = False
        self.vertical_mirrored = False
        self.color_profile = "color"


def to_bgr8(rgb16):
    b8 = (rgb16.astype(np.uint32) >> 8).astype(np.uint8)
    return cv2.cvtColor(b8, cv2.COLOR_RGB2BGR)


def convert(rgb, ref, density):
    P.USE_DENSITY_INVERSION = density
    return P.ccr_normalize_with_reference(FakeImage(rgb.copy(), ref))


def convert_bw(rgb, black, white, density):
    P.USE_DENSITY_INVERSION = density
    return P.ccr_normalize_with_bwpoint(FakeImage(rgb.copy(), None), black, white)


def _triple(s):
    vals = [float(v) for v in s.split(",")]
    if len(vals) != 3:
        raise SystemExit("expected three comma-separated values B,G,R")
    return vals


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image", nargs="?", help="negative file (RAW/TIFF/PNG/JPG)")
    ap.add_argument("--synthetic", action="store_true",
                    help="run on a simulated negative instead of a file")
    ap.add_argument("--ref", default=None, help="x1,y1,x2,y2 in resized px")
    ap.add_argument("--black", default=None,
                    help="B/W-point tool: clear-film sample as B,G,R sensor values")
    ap.add_argument("--white", default=None,
                    help="B/W-point tool: dense-area sample as B,G,R sensor values")
    ap.add_argument("--gamma", type=float, default=None, help="density film gamma")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    if a.gamma is not None:
        P.DENSITY_FILM_GAMMA = a.gamma
    bw = a.black is not None and a.white is not None

    extras = []
    if a.synthetic:
        rgb, scene_u8 = simulate_negative()
        base, outdir = "synthetic", a.out or os.path.dirname(os.path.abspath(__file__))
        extras = [("scene_truth", cv2.cvtColor(scene_u8, cv2.COLOR_RGB2BGR))]
    elif a.image:
        rgb = decode_linear(a.image)
        base = os.path.splitext(os.path.basename(a.image))[0]
        outdir = a.out or os.path.dirname(os.path.abspath(a.image))
    else:
        ap.error("provide an image path or --synthetic")

    h, w = rgb.shape[:2]
    if a.ref:
        x1, y1, x2, y2 = (int(v) for v in a.ref.split(","))
    else:
        x1, y1, x2, y2 = int(w * 0.2), int(h * 0.2), int(w * 0.8), int(h * 0.8)
    ref = (x1, y1, x2, y2)
    mode = "B/W-point" if bw else "reference"
    print(f"image {w}x{h}, {mode} mode, film_gamma={P.DENSITY_FILM_GAMMA}")

    if bw:
        black, white = _triple(a.black), _triple(a.white)
        std8 = to_bgr8(convert_bw(rgb, black, white, density=False))
        den8 = to_bgr8(convert_bw(rgb, black, white, density=True))
    else:
        std8 = to_bgr8(convert(rgb, ref, density=False))
        den8 = to_bgr8(convert(rgb, ref, density=True))

    os.makedirs(outdir, exist_ok=True)
    cv2.imwrite(os.path.join(outdir, f"{base}_standard.png"), std8)
    cv2.imwrite(os.path.join(outdir, f"{base}_density.png"), den8)

    panels = [("STANDARD", std8), ("DENSITY", den8)] + \
             [(n.upper(), im) for n, im in extras]
    gap = np.full((std8.shape[0], 12, 3), 32, np.uint8)
    tiles = []
    for i, (label, im) in enumerate(panels):
        if i:
            tiles.append(gap)
        lab = im.copy()
        cv2.putText(lab, label, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9,
                    (255, 255, 255), 2, cv2.LINE_AA)
        tiles.append(lab)
    combo_path = os.path.join(outdir, f"{base}_compare.png")
    cv2.imwrite(combo_path, np.hstack(tiles))
    print("wrote:", combo_path)


if __name__ == "__main__":
    main()
