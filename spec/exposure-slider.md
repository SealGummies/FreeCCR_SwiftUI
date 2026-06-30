# Exposure slider (photographic EV), under Gain

## Goals

- Add a new **Exposure** slider directly **under the existing Gain slider**, implementing
  OpenEnlarge's exposure algorithm: a **photographic, per-stop, linear-light gain**
  (`×2^EV`) applied in scene-linear space, black-anchored, before the tone/contrast curve.
- One slider unit behaves like a fraction of a photographic stop; the control covers a
  wide ±5 EV range (matching OpenEnlarge's `exposure: EV stops (−5..5)`), so it is the
  *primary* brightness mover while the existing Gain stays the *fine* trim.
- Headroom-safe: in the working space the multiply runs **un-clamped before the window
  clamp**, so +EV that pushes detail above display-white lands in recoverable highlight
  headroom (and is itself recoverable by lowering EV), exactly like Gain/White Point.
- CPU and GPU byte-identical (rides the same shared pre-stages the Gain control uses).

## Non-goals

- No change to the existing **Gain** slider (`exposure` param, `1/(1−v/300)`), nor to
  Auto Gain / auto-exposure-base, which keep riding the Gain stage. Exposure is an
  independent, orthogonal control that stacks multiplicatively.
- Not a port of OpenEnlarge's *filmic* exposure arm (`2^(0.14·EV)` scaling the
  normalised **log-density** `t`). That arm is coupled to OpenEnlarge's logistic filmic
  curve and its density working domain, neither of which FreeCCR has. FreeCCR's working
  buffer is **linear display light**, so the faithful linear-light `×2^EV` (OpenEnlarge's
  `FAITHFUL_EXPO_K = 1.0`, "one EV ≈ one photographic stop") is the correct, native port.
- No new tone curve, no density round-trip (`L = 10^d − 1`): OpenEnlarge needs that only
  because it stores density; FreeCCR already stores linear, so a plain multiply *is* the
  black-anchored linear-light exposure.
- Not applied to positive mode differently — it is a generic slider available like the
  rest; nothing special-cases it (unlike Auto Gain, which is conversion-only).

## Reference: OpenEnlarge's exposure algorithm

From `crates/film-core/src/engine.rs`:

- Filmic arm (`EXPO_K = 0.14`): `expo_gain = 2^(EXPO_K·EV)` multiplies the WB-neutralised
  normalised log-density, pivoting at black.
- **Faithful arm (`FAITHFUL_EXPO_K = 1.0`)**: exposure is *"a LINEAR-LIGHT gain on the
  reconstructed scene, applied BEFORE the contrast curve — we treat the log-inverted
  negative as a positive and 'expose' it like a TIFF. Black-anchored linear scene
  `L = 10^d − 1` … gain ×2^EV … EV 0 is the identity."*

The faithful arm is the one ported here. Its key invariants we preserve:
- **EV 0 = identity** (gain = 1).
- **Black-anchored** (a 0 scene value stays 0 at every EV — no black lift/colour cast).
- **Pre-curve, linear-light** (it scales scene light, then the existing FreeCCR tone path
  — contrast/brightness/curves — runs after, on the result).
- **One EV ≈ one photographic stop.**

## UX / interaction

- New slider labelled **"Exposure"**, placed in `SlidersPanel` immediately after **Gain**
  (between Gain and Brightness), styled like every other tone slider.
- Raw range **[−100, +100]**, default **0** (integer `QSlider`, unitless display — the
  same convention as Brightness/Contrast; Gain uses [−200, 200]). Internally
  `EV = (v/100)·5`, i.e. each unit = 0.05 EV; the extremes are ±5 EV (`/32`…`×32`).
- Resets to 0 with **Reset**; participates in **Compare**, **Sync to All** (in the existing
  "Tone" group), copy/paste adjustments, and per-image catalog persistence — all
  automatically, because it is just another key in the positional `ADJUSTMENT_KEYS`/
  `self.sliders` zip and the generic adjustment dict.

## Data model

- New adjustment key: **`exposure_ev`** (distinct from the legacy `exposure` key, which
  drives the *Gain* UI slider — the historical naming is unfortunate but locked; the new
  key avoids any collision).
- Default 0 ⇒ multiply 1.0 ⇒ exact identity. `s.get('exposure_ev', 0)` everywhere, so old
  catalogs without the key load as 0 (fully backward compatible).
- Lives only in the per-image adjustment dict — no new `CCRImage` field, no catalog schema
  change (the dict is serialised whole).

## Processing / math

Constant + helper in `ccr_processor.py`:

```
EXPO_EV_MAX = 5.0   # EV magnitude at slider ±100 (matches OpenEnlarge ±5 EV)

def _exposure_ev_gain(v):           # raw slider value → linear-light multiply
    ev = float(np.clip(v, -100.0, 100.0)) / 100.0 * EXPO_EV_MAX
    return float(2.0 ** ev)         # v=0 → 1.0 (identity); v=±20 → ×2 / ÷2 (±1 EV)
```

Applied as a black-anchored linear multiply `d *= 2^EV` in scene-linear light:

- **Working space ON (default)** — inside `_apply_working_space_recovery` (the single
  shared CPU+GPU pre-stage). De-window to linear `d`; apply WB, then White Point, then
  the existing Gain multiply, then **`d *= _exposure_ev_gain(exposure_ev)` un-clamped**;
  then the existing `clip(d,0,1)` window clamp. Because it is a multiply on the same
  un-clamped linear `d`, it commutes with Gain/WP and recovers/relegates highlights to
  headroom identically. CPU and GPU both route through this function ⇒ automatic parity.
- **Working space OFF (legacy `FREECCR_WORKING_SPACE=0`)** —
  - `adjust_image` (CPU): before the Gain block, `img = clip(img*m, 0, 65535)`.
  - `adjust_image_opencl` (GPU): apply the same numpy `clip(img*m,0,65535)` after the WB
    numpy step, then set `exposure_ev=0` so neither the kernel nor the CPU fallback
    re-applies it (mirrors exactly how WB is consumed in numpy for parity). The kernel is
    untouched.
  - Identity holds: `clip(img/65535*m,0,1)*65535 == clip(img*m,0,65535)`, so the CPU and
    GPU non-ws forms are byte-identical, and `m=1` is an exact no-op.

Ordering note: Exposure and Gain are both linear multiplies and commute in the un-clamped
ws path; in the clamped non-ws path Exposure is applied just before Gain. Either way EV 0
is an exact identity and the EV-0 look is unchanged.

### Interaction with Auto Gain / auto-exposure-base

Unchanged and orthogonal. Auto Gain (`ag`) and auto-exposure-base (`eb`) are computed from
the *base* pixels and ride the **Gain** value (`s['exposure'] + eb_eff + ag`); they are
invariant to the sliders, including Exposure. The realised output is
`base × gain_stage(exposure+eb+ag) × 2^EV`. If Auto Gain seats the highlight at 99.8% and
the user dials +1 EV, highlights intentionally brighten by a stop — the expected,
predictable behaviour. No Auto-Gain code changes.

## Integration points (files)

1. `src/core/ccr_processor.py`
   - Add `EXPO_EV_MAX` + `_exposure_ev_gain`.
   - `_apply_working_space_recovery(... , exposure_ev=0.0)`: un-clamped multiply pre-clamp.
   - `adjust_image(... , exposure_ev=0.0)`: ws branch passes it to the recovery (then
     zeroes it); non-ws branch applies the clamped multiply before Gain.
   - `adjust_image_opencl(... , exposure_ev=0.0)`: ws branch passes to recovery; non-ws
     branch applies the numpy multiply, zeroes it, and threads `exposure_ev` through both
     CPU-fallback `adjust_image(...)` calls.
   - New param added at the **end** of both signatures (after `ws_windowed`) so no existing
     positional caller shifts.
2. `src/widgets/sliders_panel.py`
   - `ADJUSTMENT_KEYS`: insert `"exposure_ev"` right after `"exposure"`.
   - `slider_labels`: insert `"Exposure"` after `"Gain"` (kept in sync though vestigial).
   - Create `self.exposure_ev_slider_layout = self.create_slider("Exposure")` right after
     the Gain slider and add it to `scroll_layout` directly under the Gain row.
   - `SYNC_GROUPS` "tone" tuple: add `"exposure_ev"`.
3. `src/core/ccr_image.py`
   - Both `adjust_image_opencl(...)` call sites (`apply_adjustments` and `_adjust_for_area`)
     pass `exposure_ev=s.get('exposure_ev', 0)`.

## Test plan (`tests/test_exposure_slider.py`, pure-numpy, headless)

- `test_constants`: `EXPO_EV_MAX == 5.0`; `_exposure_ev_gain(0) == 1.0`;
  `_exposure_ev_gain(20) ≈ 2.0`; `_exposure_ev_gain(-20) ≈ 0.5`; `±100 → ×32 / ÷32`.
- `test_identity_is_byte_noop_ws`: a windowed base through `adjust_image` with
  `exposure_ev=0` equals the same with the arg omitted (exact).
- `test_plus_one_ev_doubles_linear_ws`: a mid-grey windowed base (well inside headroom)
  at `exposure_ev=+20` ≈ doubles the de-windowed linear value vs EV 0.
- `test_black_is_anchored`: a pure-black base stays 0 at any EV (no lift).
- `test_headroom_recoverable_ws`: a base whose highlight is in headroom, pushed by +EV
  then pulled back by the equal −EV, round-trips (no hard clip lost) — contrast Gain's
  same property.
- `test_plus_ev_clips_to_white_when_no_headroom`: +EV on near-white in-range content
  saturates toward 65535 (expected exposure behaviour).
- `test_cpu_gpu_parity` (skip if no OpenCL): `adjust_image` vs `adjust_image_opencl` at a
  few EV values, ws on and off, max abs diff ≤ 1.
- `test_nonws_matches_ws_midtone`: non-ws and ws paths place a mid-grey at the same EV
  multiply (within quantization).
- `test_compose_with_gain`: Exposure and Gain together ≈ product of the two multiplies on
  an in-headroom midtone (commute, un-clamped ws path).
- `test_slider_wiring`: `SlidersPanel.ADJUSTMENT_KEYS` has `"exposure_ev"` immediately
  after `"exposure"`, and `len(ADJUSTMENT_KEYS)` lines up with the created slider count.

## Refinement (resolved before implementation)

- **Which OpenEnlarge arm** → faithful linear-light `×2^EV` (rationale above); the filmic
  log-density arm is out of scope and architecturally inapplicable.
- **Units on the UI** → unitless raw [−100,100] like sibling sliders (no float/EV label;
  `create_slider` has no value-scaling), mapped internally to ±5 EV. Keeps the panel
  visually uniform; documented mapping in code.
- **Range** → ±5 EV to match OpenEnlarge's stated `exposure (−5..5)`.
- **Order vs Gain** → Exposure is the wide primary mover (±5 EV), Gain the fine trim
  (±0.74 stop); placing Exposure under Gain matches the request and the coarse/fine split.
- **Param name** → `exposure_ev` (the legacy `exposure` key is the Gain slider; do not
  reuse).
- **Parity strategy** → ride the shared `_apply_working_space_recovery` (ws) and a numpy
  pre-multiply consumed before the kernel (non-ws), mirroring the WB pattern.
