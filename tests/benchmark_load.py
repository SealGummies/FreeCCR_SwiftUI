#!/usr/bin/env python3
"""
Manual benchmark for the image-loading pipeline. Not collected by pytest.

Usage:  python tests/benchmark_load.py "H:/TempPhoto/folder" [n_images]

Times each stage of the load path plus the per-slider-tick refresh cost so
optimizations can be verified against real RAW files.
"""

import os
import sys
import time
import glob

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import numpy as np  # noqa: E402
import cv2  # noqa: E402
import rawpy  # noqa: E402
import exifread  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv[:1])

from core.ccr_image import CCRImage  # noqa: E402


def t(label, fn, *args, **kwargs):
    start = time.perf_counter()
    result = fn(*args, **kwargs)
    elapsed = time.perf_counter() - start
    print(f"  {label:<42} {elapsed * 1000:8.1f} ms")
    return result, elapsed


def bench_one(path):
    print(f"\n=== {os.path.basename(path)} ===")

    # 1. EXIF info read
    def read_exif():
        with open(path, 'rb') as f:
            return exifread.process_file(f, details=False)
    t("exifread (details=False)", read_exif)

    # 2. RAW decode (half size, current params)
    def decode():
        with rawpy.imread(path) as raw:
            wl = raw.white_level
            rgb = raw.postprocess(
                output_bps=16, no_auto_bright=True, gamma=(1, 1), user_flip=0,
                demosaic_algorithm=rawpy.DemosaicAlgorithm.AHD, half_size=True,
                use_camera_wb=False, use_auto_wb=False,
                output_color=rawpy.ColorSpace.raw, no_auto_scale=True,
                four_color_rgb=False)
        return wl, rgb
    (wl, rgb), _ = t("rawpy decode half_size", decode)
    print(f"  {'(decoded shape)':<42} {str(rgb.shape):>11}")

    # 3. White-level scaling at FULL decoded size (current approach)
    def scale_full():
        return np.clip(rgb.astype(np.float32) * (65535.0 / wl), 0, 65535).astype(np.uint16)
    scaled, _ = t("white-level scale @ decoded size (current)", scale_full)

    # 4. Resize to 1080
    def resize_1080(img):
        h, w = img.shape[:2]
        s = 1080.0 / max(h, w)
        return cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    small, _ = t("resize to 1080 (INTER_AREA)", resize_1080, scaled)

    # 4b. Alternative order: resize first, scale small
    def resize_then_scale():
        sm = resize_1080(rgb)
        return np.clip(sm.astype(np.float32) * (65535.0 / wl), 0, 65535).astype(np.uint16)
    alt, _ = t("ALT: resize first, then scale @1080", resize_then_scale)
    diff = np.abs(alt.astype(np.int32) - small.astype(np.int32))
    print(f"  {'(alt-order max pixel diff)':<42} {int(diff.max()):>8} lsb")

    # 5. Full CCRImage construction (the real load path)
    img_obj, total = t("CCRImage(path) TOTAL", CCRImage, path)

    # 6. Per-slider-tick refresh cost (what runs on every adjustment)
    img_obj.adjustment_settings = {"temperature": 20, "contrast": 15, "saturation": 10}
    _, tick = t("update_thumbnail_and_preview (slider tick)", img_obj.update_thumbnail_and_preview)

    # 6b. Isolate the histogram render inside that tick
    adjusted = img_obj.apply_adjustments(img_obj.resized_raw)
    _, adj_t = t("  - apply_adjustments only", img_obj.apply_adjustments, img_obj.resized_raw)
    print(f"  {'  - (=> histogram+pixmaps remainder approx)':<42} {(tick - adj_t) * 1000:8.1f} ms")
    return total, tick


def main():
    folder = sys.argv[1] if len(sys.argv) > 1 else r"H:\TempPhoto\20260607rescan(sakuralomo)"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    files = sorted(glob.glob(os.path.join(folder, "*.ARW")))[:n]
    if not files:
        print(f"No ARW files in {folder}")
        return 1
    totals, ticks = [], []
    for p in files:
        total, tick = bench_one(p)
        totals.append(total)
        ticks.append(tick)
    print(f"\n=== SUMMARY over {len(files)} images ===")
    print(f"  mean CCRImage load: {np.mean(totals) * 1000:8.1f} ms")
    print(f"  mean slider tick:   {np.mean(ticks) * 1000:8.1f} ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
