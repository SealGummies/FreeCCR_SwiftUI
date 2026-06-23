#!/usr/bin/env python3
"""Headless NamiColor converter — drive the FreeCCR negative pipeline from a shell.

Loads a scan, (optionally) samples the Film B/W points from rectangles, runs the
LIVE NamiColor conversion (auto-levels anchored by the points / percentiles ->
Cineon -> Rec.709/2.2 CST), optionally auto-exposes, and writes the final positive.
This reuses the exact backend the GUI uses (CCRImage + ccr_backend export), so the
output matches what the app produces.

Examples
--------
  # Auto-levels only (no points), write a PNG:
  python tools/namicolor_cli.py scan.tif -o out.png

  # Sample a clear-base black point and a dense white point (pixel rects x1,y1,x2,y2),
  # crop to the image area (normalized), and auto-expose:
  python tools/namicolor_cli.py neg.cr2 -o out.tif --tiff \
      --black 40,40,90,90 --white 600,300,640,340 \
      --crop 0.08,0.06,0.93,0.95 --auto-gain

Rect coords for --black/--white are PIXELS on the decoded preview image (use
--print-size to see its dimensions). --crop is NORMALISED (0..1).
"""
import argparse
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))


def _rect(s):
    v = [float(x) for x in s.split(",")]
    if len(v) != 4:
        raise argparse.ArgumentTypeError("expected x1,y1,x2,y2")
    return v


def _sample(arr, rect):
    """Average a pixel rectangle of the decoded image -> (ch0, ch1, ch2). The
    image is RGB-order, so this matches the GUI's B/W-point sampling."""
    import numpy as np
    h, w = arr.shape[:2]
    x1, y1, x2, y2 = rect
    x1, x2 = sorted((max(0, int(x1)), min(w, int(x2))))
    y1, y2 = sorted((max(0, int(y1)), min(h, int(y2))))
    if x2 - x1 < 1 or y2 - y1 < 1:
        raise SystemExit(f"sample rect {rect} is empty for a {w}x{h} image")
    m = arr[y1:y2, x1:x2, :3].reshape(-1, 3).mean(axis=0)
    return (float(m[0]), float(m[1]), float(m[2]))


def main(argv=None):
    p = argparse.ArgumentParser(description="Headless NamiColor negative converter")
    p.add_argument("input", help="scan to convert (RAW or TIFF/PNG/JPEG)")
    p.add_argument("-o", "--output", help="output path (default: <input>_namicolor.png)")
    p.add_argument("--black", type=_rect, metavar="x1,y1,x2,y2",
                   help="pixel rect to sample the clear film base (black point)")
    p.add_argument("--white", type=_rect, metavar="x1,y1,x2,y2",
                   help="pixel rect to sample the dense/exposed area (white point)")
    p.add_argument("--crop", type=_rect, metavar="x1,y1,x2,y2",
                   help="normalised crop (image area; excludes holder/sprockets)")
    p.add_argument("--auto-gain", action="store_true",
                   help="auto-expose: lift highlights to clipping")
    p.add_argument("--tiff", action="store_true", help="write 16-bit TIFF instead of 8-bit")
    p.add_argument("--quality", type=int, default=95, help="JPEG quality (when -o is .jpg)")
    p.add_argument("--max-side", type=int, default=None, help="cap the long edge (px)")
    p.add_argument("--watermark", action="store_true", help="keep the demo watermark")
    p.add_argument("--print-size", action="store_true",
                   help="just print the decoded preview size and exit")
    a = p.parse_args(argv)

    from PySide6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication(sys.argv[:1])

    from core.ccr_backend import ccr_backend
    from core.ccr_image import CCRImage
    from core.ccr_processor import NAMICOLOR_CONVERSION
    if not NAMICOLOR_CONVERSION:
        print("WARNING: FREECCR_NAMICOLOR=0 — NamiColor conversion is disabled.")

    img = CCRImage(a.input)
    img.resized_raw = img.read_image(a.input, preview=True)
    if img.resized_raw is None:
        raise SystemExit(f"could not decode: {a.input}")
    h, w = img.resized_raw.shape[:2]
    if a.print_size:
        print(f"{w}x{h}")
        return 0

    ccr_backend.images = [img]
    ccr_backend.positive_mode = False
    ccr_backend.software_activated = not a.watermark   # suppress the demo watermark by default
    ccr_backend.black_point_bgr = _sample(img.resized_raw, a.black) if a.black else None
    ccr_backend.white_point_bgr = _sample(img.resized_raw, a.white) if a.white else None
    if a.crop:
        img.crop_rect = tuple(a.crop)
        img.crop_angle = 0.0

    if a.auto_gain:
        off = img.compute_namicolor_gain()
        print(f"auto-gain master-gain offset: {off:+.1f}")

    out = a.output or (os.path.splitext(a.input)[0] + "_namicolor."
                       + ("tif" if a.tiff else "png"))
    jpg = out.lower().endswith((".jpg", ".jpeg"))
    ok = ccr_backend.export_image_by_index(
        0, out, jpg_output=jpg, jpg_quality=a.quality, max_long_side=a.max_side)
    if not ok:
        raise SystemExit("export failed")
    print("anchors:",
          "black=" + (str(tuple(round(v) for v in ccr_backend.black_point_bgr))
                      if ccr_backend.black_point_bgr else "auto(percentile)"),
          "white=" + (str(tuple(round(v) for v in ccr_backend.white_point_bgr))
                      if ccr_backend.white_point_bgr else "auto(percentile)"))
    print("wrote:", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
