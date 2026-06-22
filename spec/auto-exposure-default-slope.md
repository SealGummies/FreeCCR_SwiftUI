# Spec: Auto-Exposure for Default-Slope Mode

Status: IMPLEMENTED (v2, tone-aware exposure) — tests written; run pending infra
Owner: FreeCCR
Feature branch: `feature/density-slope-calibration`

> Revision: v1 applied the auto-exposure as a UNIFORM Input-Gain multiply. Field
> testing showed it "feels weird" and **clips highlights** — a uniform gain slams
> everything above the 98th pct into white and lifts shadows/highlights equally.
> v2 applies the same computed value through the **tone-aware Exposure** lever
> instead (UI slider stays neutral): highlights roll off near white rather than
> hard-clipping, and the boost mainly lifts midtones.

## 1. Summary

In **default-slope mode** (B/W-point conversion with no white point — see
`spec/optional-white-point-default-slope.md`), there is no white point setting
the brightest cut, so per-image brightness drifts. Add an **automatic exposure**
computed at conversion that nominally places the image's top-2%
(98th-percentile) luminance near the top of the histogram, applied as a
**non-destructive base on the tone-aware Exposure** (UI shows 0). Pure-white
pixels (film holder / clear surround) are excluded from the estimate. Because the
exposure lever protects highlights, the result lifts midtones while the top
compresses gracefully toward white instead of clipping.

## 2. Goals / Non-goals

### Goals
- Per-image auto-exposure computed at every default-slope conversion.
- Stored as a non-destructive `exposure_base` (exposure-slider units), like the
  existing `contrast_base` / `temperature_base` / `brightness_base`.
- Exclude near-pure-white (holder) pixels from the highlight estimate.
- Applied via the **tone-aware Exposure** so highlights roll off (no hard clip).
- Persisted, snapshotted for zoom, copied on duplicate/slice.
- WYSIWYG: computed once from the 1080px preview; export/zoom reuse the value.

### Non-goals
- Not active in two-point (white-point) mode (forced to 0 there), reference, or
  positive mode.
- Not a uniform gain (v1) — it clipped highlights.
- No new UI control (automatic + non-destructive; user adjusts via the existing
  Exposure / Brightness / Input-Gain sliders on top).

## 3. Algorithm

At default-slope conversion (preview), from the converted 16-bit BGR image:
```
lum   = 0.299*R + 0.587*G + 0.114*B                  # BGR: R=[...,2], G=[...,1], B=[...,0]
mask  = lum < WHITE_EXCLUDE_FRACTION * 65535          # drop holder / pure-white surround
vals  = lum[mask]
if vals.size < MIN_CONTENT_FRACTION * lum.size: exposure_base = 0   # not enough content
else:
    v98 = percentile(vals, AUTO_EXPOSURE_PERCENTILE)  # top-2% highlight
    g   = (AUTO_EXPOSURE_TARGET * 65535) / max(v98, 1)
    exposure_base = clip(50 * log2(g), EXPOSURE_BASE_MIN, EXPOSURE_BASE_MAX)
```
Constants: `AUTO_EXPOSURE_PERCENTILE=98.0`, `AUTO_EXPOSURE_TARGET=0.98`,
`WHITE_EXCLUDE_FRACTION=0.99`, `MIN_CONTENT_FRACTION=0.005`,
`EXPOSURE_BASE_MIN=-100`, `EXPOSURE_BASE_MAX=100` (±2 EV nominal).

`exposure_base` is in exposure-slider units: the kernel uses
`factor = 2^(exposure·2/100) = 2^(x/50)` as the *nominal* stop multiplier, then
scales it by a tone-aware sigmoid (`~full strength in midtones, rolling to ~3%
at the very top`). So `g` is the nominal target; the highlight compresses toward
it rather than reaching/exceeding it. Applied in `apply_adjustments` as
`exposure = s.get('exposure', 0) + exposure_base`.

## 4. Data model

- `CCRImage.exposure_base: float = 0.0` (exposure-slider units). Default 0
  (no-op). Reset to 0 on revert and in two-point conversion.
- `apply_adjustments(..., exposure_base=None)`: `eb = self.exposure_base if
  exposure_base is None else exposure_base`; passes
  `exposure = s.get('exposure', 0) + eb`. The no-op early-return guard also
  checks `eb == 0`.
- Persisted as a float in the catalog. Area layers do NOT add it.

## 5. Integration points

- `ccr_processor.py` — `compute_auto_exposure_gain(img_bgr)` + constants (§3);
  `ccr_normalize_with_bwpoint` (preview): default-slope → set `exposure_base`,
  two-point → 0; export reuses the stored value.
- `ccr_image.py` — `exposure_base` init/revert; `apply_adjustments` param +
  `exposure` wiring + guard.
- `catalog.py` — save + restore.
- `image_preview.py` — hi-res cache signature, zoom snapshot + apply call.
- `ccr_backend.py` — duplicate / slice-child / slice-reset propagation.

## 6. Test plan (`tests/test_auto_exposure.py`)

- Mid-gray (98th pct at 50%) → nominal base ≈ `50·log2(0.98/0.5)`.
- Pure-white holder region excluded (doesn't change the estimate).
- Too little non-white content → 0.
- Already-bright → ~0. Very dark → clamps to `EXPOSURE_BASE_MAX`.
- `exposure_base` round-trips in the catalog; two-point conversion leaves it 0.

## 7. Open questions

1. Target/percentile/exclusion constants — defaults in §3; tune on real frames.
   If midtones feel too bright/dark, adjust `AUTO_EXPOSURE_TARGET` or base the
   boost on a lower percentile.
2. Reference/positive mode auto-exposure — out of scope.
