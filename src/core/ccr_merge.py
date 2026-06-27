"""
3-way RGB-light merge — trichrome (three-colour) capture.

The user shoots one static scene three times under a single pure light each —
red, then green, then blue — and every consecutive triplet of source RAWs is
merged into one full-colour image by taking each frame's OWN colour channel and
discarding the other two, WITHOUT demosaicing.

Two sensor kinds are supported:

* **Bayer (RGGB)** — read the RAW Bayer mosaic directly and take ONLY the
  wanted colour's photosites: one site per 2x2 quad, with NO averaging, NO
  quad-merge, NO demosaic, and NO libraw colour pipeline. This eliminates
  inter-channel crosstalk entirely (we never touch the other three sites of the
  quad — not even to average the two greens, of which only one is used). A Bayer
  merge is therefore half-sensor resolution (one site per quad = its full
  resolution). NB: `half_size=True` would be the quick, lower-rigor alternative
  (it bins the quad, averaging the greens), but it mixes the four sites, so it is
  deliberately NOT used here.

* **Monochrome** — no CFA at all, so there is nothing to demosaic: every
  photosite measured the (single) light's intensity. The whole grayscale frame
  IS that frame's channel, at FULL sensor resolution. A monochrome sensor is in
  fact the ideal trichrome sensor (no wasted photosites, full resolution).

Either way each frame contributes exactly one channel (R from the red-light
frame, G from green, B from blue), scaled to 16-bit by 65535/white_level.

This module is split so everything except `merge_raw_channels` is pure and
unit-testable without rawpy/Qt. See spec/three-way-rgb-merge.md.
"""
import os
from typing import List, Optional, Sequence, Tuple

import numpy as np

# Exactly read_image's rawpy-Bayer-decodable extension set (ccr_image.py:434,
# == export_estimator.RAW_EXTS). Deliberately NOT the broader folder glob, and
# excludes .fff (read_image treats it as TIFF, not a CFA). Only files this set
# covers can be channel-extracted, so anything else is rejected in merge mode.
RAW_EXTENSIONS = frozenset({
    ".cr3", ".cr2", ".nef", ".arw", ".dng", ".rw2", ".orf", ".raf",
    ".srw", ".pef", ".3fr",
})

# Number of source frames per merged image (red, green, blue).
MERGE_GROUP_SIZE = 3


def is_raw_path(path: str) -> bool:
    """True when the file extension is a RAW format this module can merge."""
    return os.path.splitext(path)[1].lower() in RAW_EXTENSIONS


def sort_for_merge(paths: Sequence[str]) -> List[str]:
    """Order paths by case-insensitive basename (directory ignored), so the
    triplet ordering is determined purely by filename, as the feature requires.
    Ties (same basename in different dirs) keep a stable order via the full
    normalized path as a secondary key."""
    return sorted(paths, key=lambda p: (os.path.basename(p).lower(),
                                        os.path.normcase(p)))


def group_into_triplets(sorted_paths: Sequence[str]) -> List[Tuple[str, str, str]]:
    """Group an already-sorted path list into consecutive (R, G, B) triplets.
    The caller is responsible for validating the count is a multiple of 3 first;
    any trailing remainder (< 3) is dropped here."""
    n = (len(sorted_paths) // MERGE_GROUP_SIZE) * MERGE_GROUP_SIZE
    return [tuple(sorted_paths[i:i + MERGE_GROUP_SIZE])  # type: ignore[misc]
            for i in range(0, n, MERGE_GROUP_SIZE)]


def validate_merge_inputs(paths: Sequence[str]) -> Tuple[bool, Optional[str]]:
    """Pre-decode validation for a merge import. Returns (ok, error_message).

    Checks, in order: non-empty, every file is a supported RAW, count is a
    multiple of 3. The sensor check (Bayer vs monochrome vs unsupported) can only
    happen at decode time (see merge_raw_channels), so it is NOT done here."""
    if not paths:
        return False, "No files selected for 3-way RGB merge."
    non_raw = [p for p in paths if not is_raw_path(p)]
    if non_raw:
        shown = "\n".join(os.path.basename(p) for p in non_raw[:8])
        if len(non_raw) > 8:
            shown += f"\n… and {len(non_raw) - 8} more"
        return False, ("3-way RGB merge requires RAW files. "
                       f"These are not supported RAW:\n\n{shown}")
    if len(paths) % MERGE_GROUP_SIZE != 0:
        return False, ("3-way RGB merge needs a multiple of 3 images "
                       f"(got {len(paths)}). Select 3 frames per shot: "
                       "red, green, blue.")
    return True, None


def bayer_channel_indices(color_desc) -> Tuple[int, int, int]:
    """Map (R, G, B) to the output-channel indices a camera-native (output_color
    =raw) decode emits, which follow libraw's internal colour order == the
    `color_desc` string. For the canonical b'RGBG' this is (0, 1, 2); a permuted
    desc is honoured. Raises ValueError when the sensor is not an R/G/B Bayer
    (e.g. monochrome b'G', or 4-colour b'RGBE'/CYGM), which must be rejected."""
    if isinstance(color_desc, (bytes, bytearray)):
        s = bytes(color_desc).decode("ascii", "ignore").upper()
    else:
        s = str(color_desc).upper()
    r, g, b = s.find("R"), s.find("G"), s.find("B")
    if -1 in (r, g, b):
        raise ValueError(
            f"3-way merge requires a Bayer (RGGB) sensor; color_desc "
            f"{color_desc!r} is not R/G/B (X-Trans, monochrome or 4-colour).")
    return r, g, b


def combine_channels(plane_r: np.ndarray, plane_g: np.ndarray, plane_b: np.ndarray,
                     white_levels: Sequence[float]) -> np.ndarray:
    """Pure merge core: take the red frame's R-plane, the green frame's G-plane,
    and the blue frame's B-plane (each a 2-D array, already the correct channel),
    scale each by 65535/white_level (matching read_image's white-level scaling),
    and stack into one (H, W, 3) uint16 RGB image.

    Planes may differ slightly in size; all are cropped to the common (min H,
    min W). Returns linear RGB in [0, 65535]."""
    planes = [plane_r, plane_g, plane_b]
    if len(white_levels) != 3:
        raise ValueError("white_levels must have 3 entries (R, G, B)")
    h = min(p.shape[0] for p in planes)
    w = min(p.shape[1] for p in planes)
    out = np.empty((h, w, 3), dtype=np.uint16)
    for i, (plane, wl) in enumerate(zip(planes, white_levels)):
        cropped = plane[:h, :w].astype(np.float32)
        scale = 65535.0 / wl if wl and wl > 0 else 1.0
        out[..., i] = np.clip(cropped * scale, 0, 65535).astype(np.uint16)
    return out


def _desc_bytes(color_desc) -> bytes:
    if isinstance(color_desc, (bytes, bytearray)):
        return bytes(color_desc)
    return str(color_desc).encode("ascii", "ignore")


def is_monochrome_sensor(num_colors, color_desc, raw_pattern=None) -> bool:
    """Whether a RAW comes from a monochrome (no-CFA) sensor. Mirrors
    read_image's detection (ccr_image.py): num_colors == 1, a grey color_desc,
    or an RGBG desc whose CFA pattern is all one colour index. Pure (takes the
    rawpy primitives, not a raw object), so it is unit-testable."""
    if num_colors == 1:
        return True
    cd = _desc_bytes(color_desc)
    if cd in (b"G", b"GRAY", b"GREY"):
        return True
    if cd == b"RGBG" and raw_pattern is not None:
        try:
            if int(np.asarray(raw_pattern).max()) == 0:
                return True
        except Exception:
            pass
    return False


def extract_cfa_plane(mosaic: np.ndarray, colors: np.ndarray,
                      target_index: int) -> np.ndarray:
    """From a raw Bayer mosaic (2-D sensor read-out) and its per-pixel CFA colour
    indices, return the half-resolution plane of ONLY the photosites whose colour
    == target_index — by phase-slicing the 2x2 lattice, NOT by binning/averaging.

    This is the crosstalk-free extraction: no quad merge, no green averaging, no
    demosaic, no colour matrix — just the bare sites of one colour, exactly as the
    sensor measured them. The colour's phase within the tile is read from the
    actual `colors` at the visible origin (offset-safe), and the CFA is periodic
    with period 2, so `mosaic[dy::2, dx::2]` is precisely that colour's sites.
    Pure (numpy in/out), so it is unit-testable without rawpy."""
    eff = np.asarray(colors)[:2, :2]
    pos = np.argwhere(eff == target_index)
    if pos.size == 0:
        raise ValueError(f"CFA colour index {target_index} not present in the "
                         f"2x2 tile {eff.tolist()}")
    dy, dx = int(pos[0][0]), int(pos[0][1])
    return np.asarray(mosaic)[dy::2, dx::2]


def _decode_frame_plane(path: str, frame_pos: int, preview: bool = False):
    """Decode one source RAW and return (plane_2d, white_level, is_mono,
    sensor_full) for the single colour this frame contributes (frame_pos
    0=R/1=G/2=B).

    * Bayer (RGGB): read the RAW Bayer mosaic directly (`raw.raw_image_visible`)
      and take ONLY this frame's colour photosites — one site per 2x2 quad, no
      averaging, no quad-merge, no demosaic, no libraw colour pipeline — so there
      is zero inter-channel crosstalk. The black pedestal is subtracted manually
      (raw_image carries it). Half-sensor resolution (one site per quad) is the
      Bayer merge's full resolution; `preview` is irrelevant (the read is already
      cheap). For the green channel only one of the two green sites is used
      (taking both would average == merge, which we avoid).
    * Monochrome: no CFA — the whole grayscale frame IS the channel, at FULL
      sensor resolution (nothing to demosaic, no quad to mix). `preview` may
      decode at half size for a fast preview/zoom tile, but the canonical full
      resolution reported is always the full sensor.

    Raises ValueError on an unsupported sensor (X-Trans, 4-colour)."""
    import rawpy

    with rawpy.imread(path) as raw:
        white_level = float(raw.white_level)
        sensor_full = (int(raw.sizes.height), int(raw.sizes.width))
        num_colors = int(getattr(raw, "num_colors", 0) or 0)
        color_desc = getattr(raw, "color_desc", b"")
        pattern = getattr(raw, "raw_pattern", None)

        if is_monochrome_sensor(num_colors, color_desc, pattern):
            rgb = raw.postprocess(
                output_bps=16,
                no_auto_bright=True,
                gamma=(1, 1),                                  # linear
                user_flip=0,
                demosaic_algorithm=rawpy.DemosaicAlgorithm.LINEAR,
                half_size=preview,         # full res unless a fast preview decode
                use_camera_wb=False,
                use_auto_wb=False,
                output_color=rawpy.ColorSpace.raw,
                no_auto_scale=True,        # absolute sensor values; scale manually
                adjust_maximum_thr=0.0,
                four_color_rgb=False,
            )
            plane = rgb if rgb.ndim == 2 else rgb[..., 0]   # channels are equal
            return np.ascontiguousarray(plane), white_level, True, sensor_full

        # Bayer (RGGB): require a 3-colour 2x2 R/G/B mosaic.
        if num_colors != 3:
            raise ValueError(
                f"3-way merge requires a Bayer (RGGB) or monochrome sensor; "
                f"{os.path.basename(path)} reports {num_colors} colours "
                f"(e.g. 4-colour CYGM/RGBE).")
        if pattern is not None and tuple(np.asarray(pattern).shape) != (2, 2):
            raise ValueError(
                f"3-way merge requires a 2x2 Bayer mosaic or a monochrome "
                f"sensor; {os.path.basename(path)} is non-Bayer (e.g. X-Trans).")
        r_idx, g_idx, b_idx = bayer_channel_indices(color_desc)  # raises if not RGB
        target = (r_idx, g_idx, b_idx)[frame_pos]               # CFA colour index

        # Read the RAW mosaic and pull only this colour's sites — no merge.
        mosaic = np.asarray(raw.raw_image_visible)
        colors = np.asarray(raw.raw_colors_visible)
        plane = extract_cfa_plane(mosaic, colors, target).astype(np.float32)
        # Subtract this colour's black pedestal (raw_image carries it); combine
        # then scales by 65535/white_level, matching read_image's negative decode.
        try:
            black = float(raw.black_level_per_channel[target])
        except Exception:
            black = 0.0
        plane = np.clip(plane - black, 0, None)
        return (np.ascontiguousarray(plane), white_level, False,
                (plane.shape[0], plane.shape[1]))


def merge_raw_channels(sources: Sequence[str],
                       preview: bool = False) -> Tuple[np.ndarray, Tuple[int, int]]:
    """Merge a (red, green, blue) triplet of RAW files into one (H, W, 3) uint16
    linear-RGB image, taking only each frame's own colour channel with no
    demosaicing. Bayer → half-sensor resolution (2x2 bin); monochrome → full
    sensor resolution (no CFA). `preview` lets a monochrome decode run at half
    size for fast preview/zoom (Bayer ignores it).

    Returns (merged_rgb, full_size=(H, W)) where full_size is the merged image's
    canonical FULL (export) resolution. Raises ValueError on an unsupported
    sensor or a decode failure (the caller surfaces it)."""
    if len(sources) != MERGE_GROUP_SIZE:
        raise ValueError(f"merge_raw_channels needs exactly {MERGE_GROUP_SIZE} "
                         f"sources, got {len(sources)}")
    for p in sources:
        if not os.path.exists(p):
            raise ValueError(f"3-way merge source missing: {p}")

    planes: List[np.ndarray] = []
    white_levels: List[float] = []
    monos: List[bool] = []
    sensor_full: Optional[Tuple[int, int]] = None
    for frame_pos, path in enumerate(sources):       # 0=red, 1=green, 2=blue
        plane, white_level, mono, sfull = _decode_frame_plane(path, frame_pos, preview)
        planes.append(plane)
        white_levels.append(white_level)
        monos.append(mono)
        if sensor_full is None:
            sensor_full = sfull

    # All three frames must be the same sensor type — mixing a monochrome
    # (full-res) frame with Bayer (half-res) ones would silently min-crop into a
    # misaligned merge. A real trichrome set is always one body, so reject.
    any_mono = any(monos)
    if any_mono and not all(monos):
        raise ValueError("3-way merge sources must all be the same sensor type "
                         "(all Bayer, or all monochrome).")

    merged = combine_channels(planes[0], planes[1], planes[2], white_levels)
    # Canonical FULL (export) resolution: full sensor for monochrome (the merge
    # is full-res; a preview decode may have produced a smaller `merged`), the
    # 2x2-binned size for Bayer (its only resolution).
    full_size = sensor_full if any_mono else (merged.shape[0], merged.shape[1])
    return merged, full_size
