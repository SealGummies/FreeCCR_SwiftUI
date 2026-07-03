"""Classical auto-white-balance (AWB) illuminant estimation.

Learning-free estimators over the converted base (spec/auto-white-balance.md).
Each algorithm returns the RGB triple that *should* be neutral — exactly what a
perfect grey-spot pick would sample — so the result feeds
compute_neutral_temp_tint unchanged and lands on the Temperature/Tint sliders
through the same inverse the WB eyedropper uses.
"""

import numpy as np

from core.ccr_processor import (MIN_CONTENT_FRACTION, WS_B, _WS_INV_WIDTH,
                                compute_neutral_temp_tint)

# (id, UI label) — ordered as shown in the Settings dropdown.
AWB_ALGORITHMS = [
    ("gray_world", "Gray World"),
    ("white_patch", "White Patch"),
    ("shades_of_gray", "Shades of Gray"),
    ("gray_edge", "Gray Edge"),
]
AWB_DEFAULT = "gray_world"

AWB_LO = 0.02          # noise floor: sub-black / film-holder rejection
AWB_HI = 0.98          # near-clip / headroom rejection
AWB_EPS = 1e-6
_WP_PERCENTILE = 99.0  # white_patch: robust max-RGB
_MINK_P = 6.0          # Minkowski norm for shades_of_gray / gray_edge
_GE_SIGMA = 1.0        # gray_edge pre-smoothing


def _gray_edge_estimate(d, valid_mask):
    """van de Weijer gray-edge: Minkowski p-mean of the smoothed per-channel
    gradient magnitude, over pixels whose source value is in-bound."""
    import cv2
    sm = cv2.GaussianBlur(d, (0, 0), _GE_SIGMA)
    gy, gx = np.gradient(sm, axis=(0, 1))
    mag = np.sqrt(gx * gx + gy * gy)
    m = mag.reshape(-1, 3)[valid_mask.reshape(-1)]
    if m.shape[0] == 0:
        return None
    return np.mean(m ** _MINK_P, axis=0) ** (1.0 / _MINK_P)


def estimate_neutral_rgb(base_u16, ws_windowed=False, algorithm=AWB_DEFAULT):
    """Estimated neutral (cast) RGB triple of a converted base, or None when
    there is not enough usable content. The scale is arbitrary —
    compute_neutral_temp_tint only uses channel ratios. Index 0=R, 1=G, 2=B.
    Unknown algorithm ids fall back to gray_world (forward-compat settings)."""
    if base_u16 is None or getattr(base_u16, "size", 0) == 0:
        return None
    d = base_u16.astype(np.float32)
    if ws_windowed:
        d -= np.float32(WS_B)
        d *= np.float32(_WS_INV_WIDTH)     # de-window; headroom masked below
    else:
        d *= np.float32(1.0 / 65535.0)
    flat = d.reshape(-1, 3)
    valid = np.all((flat >= AWB_LO) & (flat <= AWB_HI), axis=1)
    if valid.sum() < MIN_CONTENT_FRACTION * flat.shape[0]:
        return None
    if algorithm == "white_patch":
        est = np.percentile(flat[valid], _WP_PERCENTILE, axis=0)
    elif algorithm == "shades_of_gray":
        est = np.mean(flat[valid] ** _MINK_P, axis=0) ** (1.0 / _MINK_P)
    elif algorithm == "gray_edge":
        est = _gray_edge_estimate(d, valid.reshape(d.shape[:2]))
    else:                                  # gray_world + unknown-id fallback
        est = np.mean(flat[valid], axis=0)
    if est is None or not np.all(np.isfinite(est)) or np.any(est <= AWB_EPS):
        return None
    return float(est[0]), float(est[1]), float(est[2])


def compute_awb_temp_tint(ccr_image, algorithm=None):
    """(temperature, tint) slider ints that neutralize the image's estimated
    cast, or None. Runs on the converted base (resized_raw), so the result is
    independent of the current slider values (idempotent). Uses the
    backend-selected algorithm when none is given."""
    if ccr_image is None or getattr(ccr_image, "resized_raw", None) is None:
        return None
    if algorithm is None:
        from core.ccr_backend import ccr_backend   # deferred: circular at load
        algorithm = getattr(ccr_backend, "awb_algorithm", AWB_DEFAULT)
    est = estimate_neutral_rgb(ccr_image.resized_raw,
                               getattr(ccr_image, "_ws_windowed", False),
                               algorithm)
    if est is None:
        return None
    return compute_neutral_temp_tint(
        est[0], est[1], est[2],
        getattr(ccr_image, "tint_balance_factor", 1.0))
