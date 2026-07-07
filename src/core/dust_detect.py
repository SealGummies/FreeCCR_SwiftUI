"""
AI dust detection (hybrid) — ONNX BOPBTL U-Net detector + classical fill.

The neural net only *detects* dust; the actual removal is a clone-heal fill
(see ccr_processor.apply_dust_removal). This module is deliberately
self-contained and imports `onnxruntime` ONLY inside functions, so importing it
(and the dust panel that uses it) never fails when onnxruntime is absent — the
manual brush path is wholly independent of anything here.

Model asset (downloaded on first use, not bundled) is the BOPBTL scratch
detector published by the openenlarge project. See spec/dust-removal.md §5.3.
"""
import hashlib
import logging
import os
import tempfile
import threading

import cv2
import numpy as np

# --- Model asset constants (repointable) -----------------------------------
MODEL_FILENAME = "detector.onnx"
MODEL_URL = ("https://github.com/MohaElder/openenlarge/releases/download/"
             "autodust-assets-v1/detector.onnx")
MODEL_SHA256 = "61e4a93d4e94b4fc6212e2e9b785fa12b5cbc9654724b02aaf8b212075bb729f"

# --- Detection tuning -------------------------------------------------------
DETECT_SHORT = 512      # short side the detector runs at
DETECT_MULTIPLE = 16    # both dims rounded to a multiple of this (U-Net req.)
MAX_BLOB = 400          # connected-component pixel cap at ~2k px (resolution-normalized);
                        # drops large detections (film border / real image content)
MAX_ASPECT = 3.0        # drop elongated detections (thin LINES are usually real
                        # structure — a bike frame, the horizon — not dust)
SPOT_PAD = 1.5          # px added to each detected spot's radius (inpaint margin)
# Film dust inverts to BRIGHT/white specks, so a real dust blob is brighter than
# its surroundings. The gate is NOISE-RELATIVE: dust sits off the focal plane,
# so most real specks are soft-edged and faint — a fixed absolute lift missed
# nearly all of them on smooth sky while their lift stood many grain-sigmas
# above the surround. A blob passes when its luma lift (0..1) over the
# surrounding ring exceeds min(BRIGHT_MARGIN, max(BRIGHT_FLOOR, BRIGHT_SNR·σ)),
# σ = the ring's own luma std: sharp dust (≥ BRIGHT_MARGIN) always passes;
# faint dust passes where the surround is smooth; on busy texture the bar
# stays at the full absolute margin. Normal-toned/dark content (a face, a
# dark feature) still fails — its lift is ~0 or negative.
BRIGHT_MARGIN = 0.06    # absolute lift that always passes (sharp dust)
BRIGHT_FLOOR = 0.02     # minimum lift for the noise-relative pass
BRIGHT_SNR = 3.0        # ...or the lift must beat this many surround-sigmas
BRIGHT_RING = 3         # px ring around a blob used as the "surround" reference

_session = None          # cached ort.InferenceSession
_session_path = None     # model path the cached session was built from
_session_lock = threading.Lock()  # guards the cache (detect vs download threads)


def models_dir() -> str:
    """`<APPDATA>/FreeCCR/models` (same app folder family as the catalog)."""
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    folder = os.path.join(base, "FreeCCR", "models")
    os.makedirs(folder, exist_ok=True)
    return folder


def model_path() -> str:
    return os.path.join(models_dir(), MODEL_FILENAME)


def is_available() -> bool:
    """True when onnxruntime can be imported (AI detection is possible)."""
    try:
        import onnxruntime  # noqa: F401  (late import — never at module level)
        return True
    except Exception:
        return False


def availability_reason() -> str:
    """Human-readable reason the AI detector can't run, or '' when available.
    Surfaced in the panel so the user knows exactly what to do."""
    try:
        import onnxruntime  # noqa: F401
        return ""
    except Exception as e:
        return ("AI detection needs the 'onnxruntime' package, which isn't "
                f"importable ({type(e).__name__}). Run "
                "`pip install -r requirements.txt`, then restart FreeCCR.")


def is_model_present() -> bool:
    """True when the detector model file exists locally and is non-empty."""
    try:
        return os.path.getsize(model_path()) > 0
    except OSError:
        return False


def download_model(progress_cb=None, should_cancel=None) -> str:
    """Download the detector model to `model_path()`, verifying SHA-256 and
    writing atomically. `progress_cb(done_bytes, total_bytes)` is called as it
    streams; `should_cancel()` (if given) aborts cleanly. Raises on network
    error / checksum mismatch / cancel. Returns the final path on success."""
    import requests  # already a project dependency
    path = model_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    resp = requests.get(MODEL_URL, stream=True, timeout=30)
    resp.raise_for_status()
    total = int(resp.headers.get("Content-Length", 0) or 0)
    hasher = hashlib.sha256()
    done = 0
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".part")
    try:
        with os.fdopen(fd, "wb") as f:
            for block in resp.iter_content(chunk_size=1 << 20):
                if should_cancel is not None and should_cancel():
                    raise RuntimeError("Download cancelled")
                if not block:
                    continue
                f.write(block)
                hasher.update(block)
                done += len(block)
                if progress_cb is not None:
                    progress_cb(done, total)
        digest = hasher.hexdigest()
        if MODEL_SHA256 and digest.lower() != MODEL_SHA256.lower():
            raise ValueError(
                f"Detector model checksum mismatch (got {digest})")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    # Drop any stale cached session so the next detect() reloads the new file.
    global _session, _session_path
    with _session_lock:
        _session = None
        _session_path = None
    return path


def _get_session():
    """Lazily build (and cache) the CPU ONNX inference session."""
    global _session, _session_path
    with _session_lock:
        path = model_path()
        if _session is not None and _session_path == path:
            return _session
        if not is_model_present():
            raise FileNotFoundError("Detector model not downloaded")
        import onnxruntime as ort  # late import
        sess = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
        _session = sess
        _session_path = path
        return sess


def _detect_size(h: int, w: int) -> tuple:
    """Detection resolution: short side ~DETECT_SHORT (never upscaled), both
    dims rounded to a multiple of DETECT_MULTIPLE."""
    short_side = max(1, min(h, w))
    scale = min(1.0, DETECT_SHORT / short_side)
    dh = max(DETECT_MULTIPLE,
             int(round(h * scale / DETECT_MULTIPLE)) * DETECT_MULTIPLE)
    dw = max(DETECT_MULTIPLE,
             int(round(w * scale / DETECT_MULTIPLE)) * DETECT_MULTIPLE)
    return dh, dw


def detect(positive_rgb16: np.ndarray) -> tuple:
    """Run the detector on a 16-bit RGB positive. Returns
    `(prob, luma)` — both float32 at detection resolution: the probability map
    (0..1) and the grayscale luma (0..1, used for the bright-speck gate in
    prob_to_spots). Raises if the model or onnxruntime is unavailable. Cache the
    pair per image — a Sensitivity change re-runs only prob_to_spots, not the net."""
    sess = _get_session()
    h, w = positive_rgb16.shape[:2]
    dh, dw = _detect_size(h, w)
    small = cv2.resize(positive_rgb16, (dw, dh), interpolation=cv2.INTER_AREA)
    small_f = small.astype(np.float32) / 65535.0
    luma = (0.2126 * small_f[..., 0] + 0.7152 * small_f[..., 1]
            + 0.0722 * small_f[..., 2])
    inp = (luma * 2.0 - 1.0)[None, None, :, :].astype(np.float32)
    name = sess.get_inputs()[0].name
    out = sess.run(None, {name: inp})[0]
    arr = np.asarray(out, dtype=np.float32).squeeze()
    if arr.ndim > 2:
        arr = arr.reshape(arr.shape[-2], arr.shape[-1])
    elif arr.ndim < 2:
        # Unexpected flat output — reshape to the detection grid (raises a clear
        # ValueError if the size doesn't fit, rather than a cryptic IndexError).
        arr = arr.reshape(dh, dw)
    prob = 1.0 / (1.0 + np.exp(-arr))
    if prob.shape != (dh, dw):
        prob = cv2.resize(prob, (dw, dh), interpolation=cv2.INTER_LINEAR)
    return prob.astype(np.float32), luma.astype(np.float32)


def prob_to_spots(prob: np.ndarray, luma: np.ndarray, sensitivity: float,
                  kind: str = "auto") -> list:
    """Threshold + filter a probability map into normalized dust spots.

    Pure / model-free (operates on numpy arrays), so it is unit-testable without
    ONNX. `luma` is the detection-resolution grayscale (0..1) used for the
    bright-speck gate. Threshold follows openenlarge: thr = 0.85 - 0.60*(s/100),
    so higher sensitivity removes more. A surviving component must be:
      - small enough (not film border / real content),
      - compact (elongated lines are structure/scratches → manual brush),
      - BRIGHTER than its surroundings (film dust inverts to white specks; this
        rejects normal-toned content the detector wrongly fires on — e.g. a
        face). See spec/dust-removal.md §5.3.
    Each survivor becomes one circular spot (centroid + area-equivalent radius).
    """
    h, w = prob.shape[:2]
    s = max(0.0, min(100.0, float(sensitivity)))
    thr = 0.85 - 0.60 * (s / 100.0)
    binary = (prob >= thr).astype(np.uint8)
    if not binary.any():
        return []
    n, labels, stats, centroids = cv2.connectedComponentsWithStats(
        binary, connectivity=8)
    max_blob = MAX_BLOB * max(h, w) / 2000.0
    spots = []
    for i in range(1, n):  # 0 is background
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area > max_blob:
            continue  # too big — film border / real image content, not dust
        left = int(stats[i, cv2.CC_STAT_LEFT])
        top = int(stats[i, cv2.CC_STAT_TOP])
        cw = int(stats[i, cv2.CC_STAT_WIDTH])
        ch = int(stats[i, cv2.CC_STAT_HEIGHT])
        # Drop elongated detections. Real dust is compact; the scratch detector
        # also fires on legitimate thin LINES (a bike frame, the horizon, a path
        # edge), and circle-inpainting those smears real detail. Leave linear
        # defects to the manual brush (a stroke). See spec/dust-removal.md §5.4.
        if max(cw, ch) / max(1, min(cw, ch)) > MAX_ASPECT:
            continue
        # Bright-speck gate: compare the blob's luma to a surrounding ring. Film
        # dust is brighter (white) than its surroundings after inversion; a face
        # or other real feature is not, so it is rejected.
        x0 = max(0, left - BRIGHT_RING)
        y0 = max(0, top - BRIGHT_RING)
        x1 = min(w, left + cw + BRIGHT_RING)
        y1 = min(h, top + ch + BRIGHT_RING)
        win_comp = labels[y0:y1, x0:x1] == i
        win_luma = luma[y0:y1, x0:x1]
        comp_luma = float(win_luma[win_comp].mean())
        surround = win_luma[~win_comp]
        surround_luma = float(surround.mean()) if surround.size else comp_luma
        # Noise-relative bar (see BRIGHT_* above): soft off-focus dust is
        # faint in absolute terms but stands many grain-sigmas above a
        # smooth surround; busy texture keeps the full absolute margin.
        noise = float(surround.std()) if surround.size else 0.0
        need = min(BRIGHT_MARGIN, max(BRIGHT_FLOOR, BRIGHT_SNR * noise))
        if comp_luma - surround_luma < need:
            continue  # not a bright speck → not film dust
        cx, cy = centroids[i]
        # Area-EQUIVALENT radius (+ small margin), NOT the bounding-box extent:
        # a tight circle matches the speck so the inpaint stays invisible
        # instead of leaving a big smudge over the surrounding pixels.
        r_px = float(np.sqrt(area / np.pi)) + SPOT_PAD
        spots.append({
            "kind": kind,
            "pts": [[float(cx) / w, float(cy) / h]],
            "r": float(r_px / w),
        })
    return spots
