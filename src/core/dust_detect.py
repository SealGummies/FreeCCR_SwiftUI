"""
AI dust detection (hybrid) — ONNX BOPBTL U-Net detector + classical fill.

The neural net only *detects* dust; the actual removal is `cv2.inpaint`
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
MAX_BLOB = 600          # connected-component pixel cap at ~2k px (resolution-normalized)
SPOT_PAD = 2.0          # px added to each detected spot's radius (inpaint margin)

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


def detect(positive_rgb16: np.ndarray) -> np.ndarray:
    """Run the detector on a 16-bit RGB positive and return a float32
    probability map (values 0..1) at detection resolution. Raises if the model
    or onnxruntime is unavailable. Cache this per image — re-thresholding for a
    Sensitivity change uses `prob_to_spots`, no net re-run."""
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
    return prob.astype(np.float32)


def prob_to_spots(prob: np.ndarray, sensitivity: float, kind: str = "auto") -> list:
    """Threshold + size-gate a probability map into normalized dust spots.

    Pure / model-free (operates on a numpy prob map), so it is unit-testable
    without ONNX. Threshold follows openenlarge: thr = 0.85 - 0.60*(s/100), so
    higher sensitivity removes more. Components larger than a resolution-
    normalized cap are dropped (real image detail, not dust). Each surviving
    component becomes one circular spot (centroid + extent-derived radius).
    """
    h, w = prob.shape[:2]
    s = max(0.0, min(100.0, float(sensitivity)))
    thr = 0.85 - 0.60 * (s / 100.0)
    binary = (prob >= thr).astype(np.uint8)
    if not binary.any():
        return []
    n, _labels, stats, centroids = cv2.connectedComponentsWithStats(
        binary, connectivity=8)
    max_blob = MAX_BLOB * max(h, w) / 2000.0
    spots = []
    for i in range(1, n):  # 0 is background
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area > max_blob:
            continue
        cw = int(stats[i, cv2.CC_STAT_WIDTH])
        ch = int(stats[i, cv2.CC_STAT_HEIGHT])
        cx, cy = centroids[i]
        r = (0.5 * max(cw, ch) + SPOT_PAD) / w
        spots.append({
            "kind": kind,
            "pts": [[float(cx) / w, float(cy) / h]],
            "r": float(r),
        })
    return spots
