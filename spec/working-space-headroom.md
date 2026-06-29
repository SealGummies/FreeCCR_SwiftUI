# Spec: Working Space with Headroom (windowed base buffer)

Status: IMPLEMENTING v1 (default-slope + two-point landed; reference deferred)
Owner: FreeCCR
Feature branch: `feature/working-space-headroom`

## 0. Implementation status

Landed (gated behind `FREECCR_WORKING_SPACE`, byte-identical when off):
- Window helpers `encode_window` / `_apply_working_space_recovery` and the
  `WS_B`/`WS_W` geometry (10-bit window, `FREECCR_WS_BITS` / `FREECCR_WS_LO`).
- **Default-slope** and **two-point (density + linear)** inversions emit a
  windowed base; `apply_bwpoint_normalization` replay matches.
- Gain/Exposure recovery + window clamp shared by the CPU and GPU adjust paths
  (numpy pre-stage → kernel never sees headroom, so GPU/CPU parity is free).
- `apply_adjustments` identity fast-path de-windows; per-image `_ws_windowed`
  flag propagated through convert / slice / slice-reset / duplicate / catalog
  reload; auto-exposure and the WB neutral picker de-window their samples.
- Tests: `tests/test_working_space.py` (+ legacy tests pinned to full-range).

Deferred to a follow-up commit on this branch:
- **Reference-frame** mode still emits a full-range base (its saturation/shadow
  look is baked into the inversion and clips at `[0,1]`; lifting that look into
  the look-domain is the prerequisite). It is explicitly flagged non-windowed,
  so it renders exactly as today.
- §5.1 float32 export base (currently export uses the uint16 windowed base, i.e.
  10-bit precision in the window — fine for 8-bit/JPEG, slightly soft for a
  16-bit TIFF master).

## 1. Summary

Today the negative inversion maps the converted image to the **full** 16-bit
range and hard-clips everything above display white (and below display black).
Any highlight denser than the inversion's ceiling is lost permanently before the
user ever touches a slider (see the highlight-clipping analysis of the
black-point-only mode).

This feature introduces a **working space with headroom**: the inverted "base"
buffer reserves a narrow **display window** inside the 16-bit container and keeps
out-of-window data instead of clipping it. Display and export render only the
window; the over/under-range data is invisible but **recoverable** by moving the
Gain / Exposure / White-point sliders, which operate on the un-clipped data
*before* the window clamp.

Concretely: the visible range is a **10-bit window** (`W − B = 1024` codes)
placed low in the 16-bit container, giving **~6 stops of highlight headroom**
above white plus a small shadow margin below black. Cached preview/zoom bases
stay `uint16` (the existing currency; 10-bit is visually lossless on the 8-bit
display). The **export base is computed in float32** so 16-bit masters carry no
quantization from the narrow window.

## 2. Goals / Non-goals

### Goals
- The inversion stops hard-clipping at display white; over/under-range data is
  preserved in the base buffer as headroom.
- The **White Point** slider recovers headroom (wide-range, stops-based across the
  full ~6 stops); Gain/Exposure fine-tunes on top.
- Display and export render **only the window**; data outside it is disregarded
  at the render boundary (clamp to `[0,1]` after recovery).
- **ON by default** on a normal launch (`FREECCR_WORKING_SPACE=0` forces legacy).
  A neutral edit (White Point = 0) looks the same as legacy — only 10-bit-quantized
  in the window; forced-off is bit-for-bit identical.
- Applies to **all conversion modes** (default-slope, two-point, reference);
  un-converted positive-mode scans are unaffected (stay full-range).
- Resolution independent: preview, hi-res zoom, and export agree (the replay
  path produces the same windowed base).
- Auto-exposure places the highlight target **into headroom** instead of
  clipping the top of the image.

### Non-goals
- No log/density container encoding in v1 (linear window only; logged as a future
  option for uniform per-stop precision in uint16).
- No new UI controls beyond an optional histogram/headroom indicator (§7).
- No change to the look/order of the *look-domain* operators (brightness,
  contrast, highlights/shadows, saturation, curves, bands).
- No scene-linear color management rewrite — this is a range/headroom remap only.

## 3. The window model

### 3.1 Encoding

A display value `d` (0.0 = display black, 1.0 = display white, may exceed `[0,1]`)
maps linearly to a container code:

```
span = 1 + WS_LO + WS_HI          # display units across the whole container
B    = round(WS_LO / span * 65535)        # code for d = 0
W    = round((1 + WS_LO) / span * 65535)  # code for d = 1

encode_window(d) -> u16:  clip(round(B + d * (W - B)), 0, 65535)
decode_window(code) -> d: (code - B) / (W - B)
```

`WS_LO` / `WS_HI` are the shadow / highlight headroom in display units. The
window width `W − B = 65535 / span` fixes the visible precision; the highlight
headroom in stops is `≈ log2(span − WS_LO) ≈ 16 − window_bits`.

### 3.2 Recommended constants (10-bit window, ~6 stops highlight)

| Symbol | Value | Meaning |
|---|---|---|
| `WS_LO` | 0.5 | shadow margin (display units below black) |
| `window_bits` | 10 | visible precision → `W − B = 1024` |
| `span` | 64.0 | `65535 / 1024` |
| `WS_HI` | 62.5 | `span − 1 − WS_LO` |
| `B` | 512 | code for display-black |
| `W` | 1536 | code for display-white |

Result: visible range `[512, 1536]` (1024 codes, 10-bit); highlight headroom
`(1536, 65535]` = display `(1.0, 63.5]` ≈ **+5.99 stops**; shadow margin
`[0, 512)` = display `[−0.5, 0)`.

The window sits low in the container by design — almost all codes are highlight
headroom. The linear encoding therefore lavishes precision on recovered
highlights (codes are uniform per display-unit, so upper stops get many codes):
recovery is always smooth; only the neutral window is 10-bit, which is lossless
for the 8-bit display.

### 3.3 Tunables (env, FreeCCR `FREECCR_*` convention)

- `FREECCR_WORKING_SPACE` — unset/`0` = legacy full-range, bit-identical. Set =
  enable windowed working space.
- `FREECCR_WS_BITS` — window precision (default 10).
- `FREECCR_WS_LO` — shadow margin in display units (default 0.5).

`B`/`W` are derived from these once at module load.

## 4. Pipeline: two domains split by one clamp

The adjustment chain (`adjust_image` CPU + the OpenCL kernel) is divided at a new
window clamp. Input arrives as windowed `uint16` (cached preview/zoom) or float
`d` (export); either way the kernel works in float `d`.

1. **De-window (entry):** `d = decode_window(code)` (skip if input already float
   `d`). `d` may be `< 0` or `> 1`.
2. **Recovery domain — operate on un-clamped `d` (in `_apply_working_space_recovery`):**
   - **White Point — the wide-range recovery (primary).** Stops-based across the
     FULL headroom: `d *= 2^(_WS_HEADROOM_STOPS · wp/100)`, so `WP=-100` maps the
     container ceiling exactly to white (recovers everything), `WP=0` is neutral.
     This is the control that actually reaches the headroom — a linear `/300` gain
     (below) only spans ~0.74 of the ~6 stops, which is why an early build appeared
     to "not recover." Consumed here and zeroed before the look chain (so the
     look-domain B/W remap doesn't re-apply it).
   - Gain / Exposure: `d /= (1 − clip(exposure,−200,200)/300)` un-clamped — the
     existing linear gain (±~0.74 stops), kept for fine tone control on top.
3. **Window clamp:** `d = clip(d, 0, 1)` — enter the display window. Everything
   outside is now disregarded.
4. **Look domain — unchanged math, on `[0,1]`:** temperature/tint, brightness
   (`pow`), highlights/shadows (`x³(1−x)` bumps), contrast (S-curve), channel
   R/G/B, saturation, sub-saturation, curves, bands. These are defined on
   `[0,1]`; keeping them after the clamp means **no change to their math**.
   (temp/tint moves to after the clamp — its luminance tone-mask assumes `[0,1]`.)
5. **Output tail — unchanged:** `clip(0,1) · 65535 → uint16` (export) or `· 255 →
   uint8` (display). Output is full-range, so display (`/257`) and the export
   writers need **no change**.

### 4.1 Identity fast-path (critical)

`apply_adjustments` currently returns the base as-is when no sliders/bases are
active (`ccr_image.py:839-842`). The base is now *windowed*, so that path must
still **de-window → window-clamp → emit full-range** (steps 1, 3, 5). It is a
single vectorized op; cheap, but mandatory or a neutral image renders dark/shifted.

## 5. Integration points

| Area | File / function | Change |
|---|---|---|
| Window helpers | `ccr_processor.py` (new) | `encode_window`, `decode_window`, `B`/`W`/constants from env |
| Default-slope invert | `_default_slope_invert` (`:1000`) | Return float `d` (overshoot kept); drop `clip(...,1)` |
| Two-point invert | `_twopoint_invert` (`:1061`) | Same — keep data above the dense point as headroom |
| Reference normalize | `apply_reference_normalization` (`:1633`), `compute_reference_norm_params` | Same windowed/float output |
| Preview convert | `ccr_normalize_with_bwpoint` (`:1111`), `ccr_normalize_with_reference` (`:613`) | Cache base via `encode_window` (uint16); **export path keeps float `d`** |
| Replay (zoom) | `apply_bwpoint_normalization` (`:1660`), `render_hires_base` (`ccr_image.py:935`) | Emit identical windowed base or zoom won't match preview |
| Adjust (CPU) | `adjust_image` (`:2131`) | De-window entry; move/un-clamp recovery group; insert window clamp; accept uint16-windowed or float `d` |
| Adjust (GPU) | OpenCL kernel (`:~150-272`) | Mirror CPU exactly (parity required) |
| Identity path | `apply_adjustments` (`:839`) | De-window+clamp even when no sliders (§4.1) |
| Auto-exposure | `compute_auto_exposure_gain` (`:1034`) | Operate in `d`-space; thresholds (`WHITE_EXCLUDE_FRACTION`) redefined in `d`; place target into headroom; stale "rolls off" docstrings corrected |
| Analysis consumers | histogram, Auto-WB picker, any sampler of the converted buffer | De-window before measuring. Raw-scan pickers (B/W point) unaffected |
| Cache invalidation | conversion + adjustment signatures (`_hires_signature`, `adj_sig`) | Add a working-space version constant so pre-change caches don't render wrong |

### 5.1 Export float path

Export is one-shot and uncached, so the export base is produced as float `d`
(no `encode_window` quantization). `adjust_image` detects float32 input → uses it
as `d` directly (de-window is identity). 16-bit TIFF masters thus carry full
precision regardless of `window_bits`.

## 6. Math notes / correctness

- **Neutral identity:** with the feature off, `encode_window`/`decode_window` and
  the recovery/clamp restructure are bypassed → byte-identical to today. Guard
  with a golden-image test.
- **GPU/CPU parity:** the kernel and numpy path must remain bit-compatible after
  the restructure; this is the highest-risk regression.
- **Recovery direction:** `exposure < 0` → denominator `> 1` → `d` shrinks →
  highlight overshoot drops below 1.0 (recovered). `exposure > 0` lifts content
  toward/over white (headroom now absorbs it instead of clipping).
- **Reorder of White/Black-point and temp/tint** changes results for non-neutral
  edits under the new mode only; documented and gated.

## 7. UX (optional, recommended)

- Histogram of the **windowed** (de-windowed `d`, clamped) data, plus a
  **headroom indicator** showing how much data sits above white / below black so
  the user knows recovery is available. Natural surface for the new capability.

## 8. Migration / gating

- `FREECCR_WORKING_SPACE` is **ON by default** on a normal launch; set it to `0`
  (or `false`/`off`) to force legacy full-range behavior.
- A working-space version constant participates in conversion/adjustment
  signatures so caches and catalog-replayed conversions from before the change
  are invalidated rather than rendered with the wrong decode.
- Catalog: the persisted base (if any) is keyed by the version; conversion is
  re-derived from `conversion_inputs` on load, so no on-disk migration needed.

## 9. Test plan

- **Golden identity:** feature off → bit-identical output vs `main` across a
  fixture set (all three conversion modes).
- **Round-trip encode:** `decode_window(encode_window(d)) ≈ d` within 1/1024 for
  `d ∈ [−WS_LO, span−WS_LO]`.
- **Headroom preserved:** a synthetic negative with highlights above the ceiling
  → after inversion, codes exist above `W`; before this change they were pinned
  at 65535.
- **Recovery:** push `exposure` negative → previously-clipped highlights regain
  distinct, monotonic detail (no flat 65535 plateau).
- **GPU == CPU:** kernel vs numpy max abs diff within tolerance on the fixture
  set, feature on.
- **Resolution agreement:** preview vs `render_hires_base` vs export produce
  matching tone at the same crop (windowed base replay + float export agree
  within quantization).
- **Auto-exposure:** high-key fixture no longer hard-clips the top 2%; the top
  lands in headroom.
- **Identity fast-path:** image with zero sliders renders correctly (not dark/
  shifted) with the feature on.

## 10. Open questions (resolve in REFINE pass)

- Exact `d`-space thresholds for `compute_auto_exposure_gain`
  (`WHITE_EXCLUDE_FRACTION`, percentile target) under headroom.
- Whether per-channel R/G/B gain/blackpoint join the recovery group or stay in
  the look domain (v1: stay in look domain, clamped).
- Whether the optional recovery-domain safety clamp `[−WS_LO, span−WS_LO]` is
  needed or noise is tolerable.
- Histogram/headroom indicator: ship in v1 or defer.
