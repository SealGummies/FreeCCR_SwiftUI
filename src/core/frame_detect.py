"""ONNX frame detection — find the photographic frame(s) inside a film scan.

A tiny U-Net (0.48M params, trained on synthetic 135 strips — see
tools/frame_training/) segments the EXPOSED frame region from luma; the bounding
box is read from the probability PROJECTIONS, which is robust to stray pixels and
reliably excludes the sprocket strips / film base that the classical
density-projection heuristic could not.

Mirrors src/core/dust_detect.py: imports onnxruntime ONLY inside functions (so
importing this module never fails when onnxruntime is absent), a lazily-built and
cached CPU InferenceSession, 1-channel luma normalized to [-1,1] at short-side
512. Returns [] when onnxruntime or the model file is unavailable, so callers
fall back to the classical detector. See spec/namicolor-bwpoint-conversion.md §2.7.
"""
import os
import sys
import threading

import cv2
import numpy as np

MODEL_FILENAME = "frame_detector.onnx"
DETECT_SHORT = 512       # short side the segmenter runs at
DETECT_MULTIPLE = 16     # both dims rounded to a multiple of this (U-Net requirement)
PROB_THR = 0.5           # frame-probability threshold for the projections

_session = None
_session_path = None
_session_lock = threading.Lock()


def model_path() -> str:
    """Bundled (Nuitka): `<exe_dir>/models/...`; dev: `src/models/...`."""
    if getattr(sys, "frozen", False):
        p = os.path.join(os.path.dirname(sys.executable), "models", MODEL_FILENAME)
        if os.path.exists(p):
            return p
    return os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         "..", "models", MODEL_FILENAME))


def is_available() -> bool:
    """True when onnxruntime can be imported."""
    try:
        import onnxruntime  # noqa: F401  (late import — never at module level)
        return True
    except Exception:
        return False


def is_model_present() -> bool:
    try:
        return os.path.getsize(model_path()) > 0
    except OSError:
        return False


def is_ready() -> bool:
    """True when both onnxruntime and the bundled model are usable."""
    return is_available() and is_model_present()


def _get_session():
    global _session, _session_path
    with _session_lock:
        path = model_path()
        if _session is not None and _session_path == path:
            return _session
        import onnxruntime as ort  # late import
        sess = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
        _session, _session_path = sess, path
        return sess


def _runs(on, minlen):
    """Contiguous True runs of length >= minlen as (start, end) pairs."""
    out = []
    s = None
    for i, v in enumerate(on):
        if v and s is None:
            s = i
        elif (not v) and s is not None:
            if i - s >= minlen:
                out.append((s, i))
            s = None
    if s is not None and len(on) - s >= minlen:
        out.append((s, len(on)))
    return out


def boxes_from_prob(prob, thr=PROB_THR):
    """Frame boxes from a probability map via projections. Pure (no ONNX) so it is
    unit-testable. The row-mean profile gives the frame band (sprocket strips fall
    below thr); the column-mean inside it splits side-by-side frames at clear
    sub-threshold inter-frame gaps. Returns normalized (x1, y1, x2, y2) boxes,
    largest first. (A 2-up strip whose gap stays above thr returns one box — that
    wants a model refinement, not a brittle valley heuristic that mis-splits
    single frames.)"""
    dh, dw = prob.shape[:2]
    rb = _runs(prob.mean(1) > thr, max(1, int(0.12 * dh)))
    if not rb:
        return []
    y0, y1 = max(rb, key=lambda r: r[1] - r[0])
    segs = _runs(prob[y0:y1].mean(0) > thr, max(1, int(0.10 * dw)))
    boxes = [((x0 / dw, y0 / dh, x1 / dw, y1 / dh), x1 - x0) for (x0, x1) in segs]
    boxes.sort(key=lambda b: -b[1])
    return [b[0] for b in boxes]


def detect_frames(neg_rgb16: np.ndarray, thr=PROB_THR):
    """`neg_rgb16`: HxWx3 uint16 (Adobe-)linear negative. Returns normalized
    (x1, y1, x2, y2) frame boxes (largest first), or [] when onnxruntime / the
    model is unavailable or nothing is found — the caller then falls back to the
    classical detector or the whole image."""
    if not is_ready():
        return []
    try:
        sess = _get_session()
    except Exception:
        return []
    h, w = neg_rgb16.shape[:2]
    scale = min(1.0, DETECT_SHORT / max(1, min(h, w)))
    dh = max(DETECT_MULTIPLE, int(round(h * scale / DETECT_MULTIPLE)) * DETECT_MULTIPLE)
    dw = max(DETECT_MULTIPLE, int(round(w * scale / DETECT_MULTIPLE)) * DETECT_MULTIPLE)
    small = cv2.resize(neg_rgb16, (dw, dh), interpolation=cv2.INTER_AREA).astype(np.float32) / 65535.0
    luma = 0.2126 * small[..., 0] + 0.7152 * small[..., 1] + 0.0722 * small[..., 2]
    inp = (luma * 2.0 - 1.0)[None, None, :, :].astype(np.float32)
    name = sess.get_inputs()[0].name
    out = np.asarray(sess.run(None, {name: inp})[0], dtype=np.float32).squeeze()
    if out.ndim != 2:
        out = out.reshape(dh, dw)
    prob = 1.0 / (1.0 + np.exp(-out))
    return boxes_from_prob(prob, thr)
