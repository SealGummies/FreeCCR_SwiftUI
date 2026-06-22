# Spec: Optional White Point with Calibrated Default Slope

Status: IMPLEMENTED (v4, density + single scalar)
Owner: FreeCCR
Feature branch: `feature/density-slope-calibration`

> Revision history: v3 baked a per-channel LINEAR slope. Field testing across
> multiple light sources showed bad colour shifts — a per-channel linear gain
> encodes one light source's colour balance (the gain ratios swung from
> B/G≈2.55 under one light to ≈1.22 under another). v4 switches to a DENSITY
> (log) inversion with a SINGLE SCALAR slope, which is robust across light
> sources (see §3).

## 1. Summary

Make the **white point optional** in the B/W-point film-negative conversion.
With only a black point (clear film base) sampled, conversion falls back to a
**calibrated single-scalar slope in optical-density space**:
`out = clip(DEFAULT_DENSITY_SLOPE · max(log10(base/img), 0), 0, 1)`. Re-sampling
the black point under a new light source keeps colour consistent **without** a
white point.

Three behaviours:
1. **No white point** → density default-slope inversion (black point only).
2. **White point set** → current linear two-point behaviour, unchanged.
3. A small **"×" button** clears the white point and reverts to the default slope.

## 2. Goals / Non-goals

### Goals
- Black-point-only conversion using a baked **single scalar** density slope
  `DEFAULT_DENSITY_SLOPE` (≈0.8), calibrated via `log_bwpoint_slopes`.
- **Robust to light-source changes**: re-sampling the black point absorbs the
  new light's per-channel scaling (it cancels in `log10(base/img)`); the scalar
  slope bakes in no per-channel colour.
- White point optional everywhere: dispatch, export, preview, hi-res replay,
  catalog persistence, tethering.
- A clear-white-point "×" affordance + an active-mode label.
- Resolution-independent (preview = zoom = export).
- White-point mode stays **byte-identical** to today.

### Non-goals
- Not changing the existing white-point (linear two-point) mode.
- Not a full OpenEnlarge port (no gamma-1.59 shoulder / look layer / HDR). Only
  the density inversion + single scalar are adopted.
- No user-editable slope UI; the scalar is a baked constant (recalibrate via
  `log_bwpoint_slopes` + edit the constant).
- No auto film-base detection (black point is user-sampled).
- No automatic white balance (the existing Auto WB picker handles residual cast).

## 3. Background / decisions made

- **Density, not linear.** A per-channel linear slope is scan/light specific and
  caused colour shifts across light sources (v3 field test). In density space,
  `log10(base/img)` is invariant to a per-channel light factor `k` (it scales
  both `base` and `img`), so re-sampling the black point self-corrects colour.
  Empirically the per-channel DENSITY ratios barely moved across light sources
  (B/G 1.16→1.20) where the LINEAR ratios swung wildly (B/G 2.55→1.22).
- **Single scalar, not per-channel.** A per-channel slope (even in density)
  bakes in the sampled dense area's colour and still drifts across lights. A
  single scalar carries only contrast; all colour balance comes from the
  per-channel (re-sampled) black-point divide — OpenEnlarge's architecture.
- **Scalar value ≈ 0.8.** Mean of the per-channel DENSITY values logged across
  three rolls / light sources (means 0.73, 1.10, 0.69; grand mean ≈ 0.84).
  Contrast only, so not colour-critical — tune via the scalar or contrast slider.
- **Black point is re-sampled per light source.** This is the one input that
  must adapt; it is quick (one rectangle) and is the whole point of the feature.
- **Residual cast → white balance.** The Auto WB picker / temperature-tint clean
  up any per-image residual, as in the normal workflow.

## 4. UX / Interaction

### 4.1 Sampling
- Black/white point sampling unchanged when used.
- New small **"×"** button beside "Set White Point" clears the stored white point
  (→ `None`) and re-renders with the default slope.

### 4.2 Mode indication
- A label shows the active slope source:
  - white point present → "white point (two-point)".
  - white point absent → "default slope (black point only)".
- Convert is allowed with only a black point. Neither point set is still an error.

### 4.3 Toggle behaviour
The white-point mode is linear and the default mode is density, so toggling the
white point changes the midtone rendering (endpoints still match). This is
accepted: the default mode is optimised for cross-light robustness; the
white-point mode is the precise per-image tool, left untouched. Residual look
differences are handled with the contrast slider / WB.

## 5. Data model

- `ccr_processor.py` constants:
  - `DEFAULT_DENSITY_SLOPE = 0.8` (single scalar, contrast).
  - `DEFAULT_DENSITY_GAMMA = 1.0` (optional display gamma; 1.0 = off).
  - `_DENSITY_FLOOR = 1.0` (log floor for near-black pixels).
- `CCRBackend.white_point_bgr` may be `None` with `black_point_bgr` set.
  `clear_white_point()` added; `set_white_point(None)` allowed.
- `conversion_inputs` keeps `mode: "bw"` with `bw: (black, None)` when no white
  point (no separate mode — all consumers unpack and pass through; the functions
  treat `white=None` as the default-slope branch).
- Catalog: serialize/restore a `None` white point (null in JSON).

## 6. Processing / math

### 6.1 Default-slope inversion — black point only (`_default_slope_invert`)
Per channel `c` (BGR), `base = black_point_bgr[c]`:
```
d   = max(log10(base / max(img[c], _DENSITY_FLOOR)), 0)
out = clip(DEFAULT_DENSITY_SLOPE · d, 0, 1)
out = out ** (1/DEFAULT_DENSITY_GAMMA)      # no-op while gamma == 1.0
out16 = out · 65535
```
- `img == base` → `d=0` → black. ✓
- `img` at density `1/slope` → white (the brightest cut). ✓
- Light-source invariance: `log10(k·base / k·img) = log10(base/img)`.
- Single scalar → equal density per channel renders neutral regardless of the
  per-channel base values.

### 6.2 White-point inversion — unchanged (verbatim linear two-point)
```
norm = clip((img − white)/(base − white) · 65535, 0, 65535) ; inverted = 65535 − norm
```

## 7. Integration points

- `ccr_processor.py`
  - `DEFAULT_DENSITY_SLOPE`, `DEFAULT_DENSITY_GAMMA`, `_DENSITY_FLOOR`.
  - `_default_slope_invert(img_f, black)` shared helper.
  - `apply_bwpoint_normalization(img, black, white=None)` + `ccr_normalize_with_bwpoint(..., white=None)` route `white=None` to the helper; two-point branch unchanged.
  - `log_bwpoint_slopes` logs linear + density slopes for recalibration.
- `ccr_backend.py` — `clear_white_point()`; dispatch + `apply_bwpoint_to_all_images` allow black-only; snapshot stores `(black, None)`.
- `catalog.py` / `tether_watcher.py` — persist & auto-convert with `None` white.
- `ccr_image.py` — replay passes `(black, None)` through (functions handle None).
- `main_window.py` — tether banner black-only; `persist_bwpoint` removes a cleared white point from QSettings.
- `sliders_panel.py` — "×" clear button, mode label, relaxed convert guards, hints.

## 8. Test plan (`tests/test_default_slope.py`)

- base → black; density `1/slope` → white; clamps; monotonic; matches the
  density formula.
- **Light-source invariance**: scaling base & img by a per-channel `k` yields
  identical output (the crux).
- **Scalar carries no colour**: equal per-channel density → neutral output.
- White-point mode matches the closed form to ≤1 LSB (regression) + endpoints.
- `white` arg optional; catalog round-trip with and without a white point.
- Tether: black-only capture converts and records `white=None`.

## 9. Open questions — resolution

1. Domain — RESOLVED: density (linear rejected after the multi-light field test).
2. Per-channel vs scalar — RESOLVED: single scalar (per-channel drifts across lights).
3. Display encode — `DEFAULT_DENSITY_GAMMA = 1.0` shipped; raise toward ~2.2 only
   if the default render is too dark on a real frame (verify live).
4. Toggle consistency — ACCEPTED look change (§4.3); white-point mode untouched.
