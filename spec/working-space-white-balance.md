# Spec: White Balance in the Working Space

Status: REFINED v2 (decisions locked — ready to implement)
Owner: FreeCCR
Feature branch: `feature/working-space-white-balance`
Related: [`spec/working-space-headroom.md`](working-space-headroom.md), `spec/auto-exposure-default-slope.md`

## 0. Decisions (locked 2026-06-29)

All §9 open questions resolved (confirmed against a measured demo of the current WB):
- **Q1 — flat WB.** Replace the tone-aware/perceptual WB with a **flat per-channel
  gain**. The headroom removes the reason for the highlight de-weighting.
- **Q2 — constants matched to today's midtone** (so the slider feel is unchanged
  at shadows/midtones; verified identical at midtone, near-identical in shadows):
  - Temperature: `s = 0.004·temp_slider` → `R·=(1+s)`, `B·=(1−s)`.
  - Tint: `t = tanh(0.02·tint_slider)·0.26·tint_balance_factor` →
    `G·=(1−t)`, `R·=(1+0.3·t)`, `B·=(1+0.3·t)`. (`0.26 = 0.18·skin(0.45)` — the
    current tint's midtone effective strength with the skin-tone factor folded in.)
- **Highlights — pure flat (full strength).** Highlights get full-strength WB
  (correct balance), not today's `0.25×` roll-off; headroom-safe.
- **Q3 — everywhere.** Apply the flat WB on all paths (windowed, reference,
  legacy). One WB code path.
- **Q4 — fold into one gain.** WB + White Point + Gain/Exposure are all
  multiplicative in the working domain → combine into a single per-channel gain.
- **Neutral picker** (`compute_neutral_temp_tint`) updated to invert the flat
  model exactly (drop the per-pixel `tone_curve`/`skin` terms; use 0.40 and 0.26).

Measured trade-off (demo): flat matches today at shadow/mid; the only visible
difference is that highlight tint no longer rolls toward neutral — accepted.

## 1. Summary

White balance (Temperature / Tint) is currently applied **after** the
working-space window clamp, so it operates on the already-clamped display range
instead of on the windowed base that carries highlight headroom. The result:

- WB pushing a channel up **clips** it (the headroom can't absorb it → data is
  lost), and
- WB **cannot** pull a channel's highlights back from headroom (they were
  clamped away before WB ran).

This makes each channel's clip point move as you drag WB and effectively
redefines the working window per channel — the bug reported against the new
histogram (the histogram is faithfully showing it).

This spec moves white balance into the scene-linear working domain, applied
**before** the window clamp alongside the existing recovery controls (White
Point / Gain / Exposure), so WB-induced over-range lands in recoverable headroom
instead of clipping, and the clamp reflects the white-balanced data.

## 2. Goals / Non-goals

### Goals
- Apply white balance in the un-windowed, scene-linear domain **before** the
  display-window clamp, so highlight headroom absorbs WB-induced over-range
  (recoverable via the White Point / Gain / Exposure recovery) instead of being
  clipped away.
- Keep WB **neutral at (0, 0)** — byte-identical to no WB.
- Preserve **CPU/GPU parity**: WB moves into the shared numpy pre-stage (the same
  place exposure/whitepoint recovery already live), so the OpenCL kernel never
  needs its own WB and parity is automatic.
- Keep the per-channel **neutral picker** (`compute_neutral_temp_tint`) exact
  against the new WB math.

### Non-goals
- **Stopping the clip indicator from moving** when WB is dragged. Scaling a
  channel physically moves where it reaches white; that is correct. The fix is
  about *not losing data*, not about freezing the clip point.
- Changing the WB **UI** (sliders, ranges, gradients) — the controls stay as-is.
- The **reference-frame** conversion path (non-windowed; no headroom). Its WB is
  unchanged in behaviour except as required by a shared code path (see §6).
- Any change to exposure / White Point / Gain recovery semantics.

## 3. Current behaviour (as-is)

Pipeline in `adjust_image` (`src/core/ccr_processor.py`), windowed base:

```
img16 (windowed base, headroom in [WS_W .. 65535])
  └─ _apply_working_space_recovery(img16, exposure, whitepoint)   # :2300
        de-window → d ; White Point ×2^(stops) ; Gain/Exposure ×1/(1−v/300)   [UN-clamped]
        np.clip(d, 0, 1) ; ×65535                                  # :1069  ← WINDOW CLAMP, headroom gone
  └─ Temperature / Tint                                            # :2316  ← WB runs AFTER the clamp
  └─ brightness, highlights, shadows, contrast, B/W remap, sat, curves, bands
```

`adjust_image_opencl` is equivalent: the same numpy recovery pre-stage runs, then
the kernel (which contains its own Temperature/Tint, `:79–199`) runs on the
clamped data.

So in the working space the headroom is reserved **only** for White Point / Gain
/ Exposure. WB — a per-channel gain — runs downstream on `[0, 65535]` and
therefore clips, defeating the headroom.

The current WB is also **tone-aware / perceptual**: it weights its strength by
luminance (`shadow 0.8 / midtone 1.0 / highlight 0.25` via a sigmoid, `:2326–
2351`) and applies a Kelvin curve for temperature. Note this highlight
de-weighting (0.25) exists largely to *stop WB from blowing out highlights* —
i.e. it is a workaround for exactly the clipping this spec removes. There is also
a pre-existing mismatch: `compute_neutral_temp_tint` (`:2208`) models WB as a
**flat** per-channel gain (`temp: r×(1+s), b×(1−s); tint: g×(1−t), r×(1+0.3t),
b×(1+0.3t)`), which the tone-aware path does not exactly implement.

## 4. Design

### 4.1 Core idea
In the scene-linear working domain, White Balance, White Point, Gain and Exposure
are **all multiplicative**:
- WB = a **per-channel** gain `(wb_r, wb_g, wb_b)`.
- White Point / Gain / Exposure = **uniform** (all-channel) gains.

So they commute and can be folded into a single per-channel gain vector applied
once to the un-windowed value `d`, then clamped:

```
d = (img16 − WS_B) / WS_WIDTH                      # de-window (un-clamped, keeps headroom)
gain_c = wb_c · 2^(HEADROOM_STOPS·wp/100) · 1/(1 − exp/300)    # per channel c
d[...,c] *= gain_c                                  # UN-clamped → over-range stays as headroom
np.clip(d, 0, 1) ; d *= 65535                       # window clamp: now reflects WB'd data
```

This is a minimal extension of `_apply_working_space_recovery`: add the
per-channel WB factor to the gain it already computes.

### 4.2 WB gain mapping (recommended: flat per-channel gain)
Define WB as a flat per-channel linear gain derived from the sliders, dropping
the tone-aware luminance masking. Rationale:
- The headroom now absorbs WB over-range, so the highlight de-weighting (0.25)
  that motivated the tone-aware curve is no longer needed.
- A flat gain is the physically-correct form of white balance and makes the
  linear-domain fold in §4.1 exact.
- It lets us **unify** the WB math with `compute_neutral_temp_tint`, removing the
  existing model/implementation mismatch (the neutral picker becomes exact).

Proposed mapping (to be reconciled with the current slider feel during
implementation so midtone strength at a given slider value is close to today):

```
temperature s:  wb_r = 1 + k_t·s ,  wb_b = 1 − k_t·s          (s = kelvin_shift/100)
tint        t:  wb_g = 1 − k_n·t ,  wb_r·= 1 + 0.3·k_n·t , wb_b·= 1 + 0.3·k_n·t
```
with `k_t`, `k_n` chosen to match the present perceptual strength at the neutral
end of the range. The exact constants are finalized in §9-Q1 / implementation.

### 4.3 What stays in the look chain
Everything that is genuinely tone/look and operates on the display range is
unchanged and still runs **after** the clamp: brightness, highlights, shadows,
contrast, B/W-point remap, saturation, curves, bands.

## 5. Data model
No new persisted fields. `temperature` / `tint` adjustment keys are unchanged;
only *where* they are applied moves. `_ws_windowed` continues to gate the
windowed path. Catalog / undo / copy-paste / sync are unaffected (same keys).

## 6. Integration points
- `_apply_working_space_recovery(img16, exposure, whitepoint, kelvin, tint, tint_balance_factor)`
  — extend to accept WB and fold it into the per-channel gain. (Becomes the
  single WB+recovery linear pre-stage.)
- `adjust_image` — pass `kelvin_shift`/`tint_shift` into the pre-stage when
  `ws_windowed`; **remove** the post-clamp Temperature/Tint block for that path;
  zero them so they aren't double-applied.
- `adjust_image_opencl` — feed WB through the same numpy pre-stage and zero
  `kelvin_shift`/`tint_shift` before the kernel/fallback, so the kernel's WB block
  is **never executed** (params[0]=params[1]=0) → CPU/GPU parity is automatic.
  NOTE (as implemented): the kernel's old tone-aware WB C-source is left in place
  but bypassed (dead) rather than deleted, to avoid an un-GPU-testable kernel edit;
  remove it in a follow-up once verified on a GPU.
- Non-windowed paths (reference mode, legacy/`FREECCR_WORKING_SPACE=0`): no
  headroom, so behaviour is "WB then clip" as today. Decision Q3: either (a)
  leave the existing tone-aware WB for these paths, or (b) unify on the flat WB
  everywhere (one code path, slight look change for non-windowed too).
- `compute_neutral_temp_tint` — keep in lockstep with §4.2 so the WB neutral
  picker stays exact.

## 7. Edge cases
- WB neutral (0,0): `gain_c = 1` → identical to plain de-window (byte-identical
  no-op). Tested.
- Extreme WB + already-bright channel: may exceed the container ceiling (65535)
  even in headroom → genuinely clips; acceptable (it's beyond ~6 stops of
  headroom). The corner clip wedges flag it.
- Per-channel gain < 1 (e.g. cooling reduces red): pulls red highlights down out
  of headroom into the visible window — the recovery that is impossible today.

## 8. Test plan
- **Neutral no-op**: `adjust_image(ws_windowed=True, kelvin=0, tint=0)` byte-
  identical to the no-WB de-window.
- **Headroom-safe WB**: a channel with values in headroom, warmed by WB, is
  *recoverable* (distinct, monotonic, sub-white after a compensating White Point
  pull) rather than clipped flat — the current behaviour clips it.
- **CPU/GPU parity**: `adjust_image` vs `adjust_image_opencl` on a windowed base
  with non-zero WB agree within tolerance.
- **Neutral picker round-trip**: sampling a pixel and applying the returned
  temp/tint neutralizes it (R==G==B) under the new WB math.
- **Commutativity**: WB-before-clamp result equals folding WB into the recovery
  gain (sanity for §4.1).
- **Legacy/reference path**: documented behaviour per Q3 (unchanged, or the
  flat-WB look change captured in a baseline test).

## 9. Open questions — RESOLVED (see §0 for the locked answers)
- **Q1 — tone-aware vs flat WB.** Recommend dropping the luminance-masked
  perceptual WB in favour of a flat per-channel gain (the headroom removes the
  reason for the highlight de-weighting, and it makes the math exact). This
  changes the WB look for shadows/midtones somewhat. Accept, or preserve the
  perceptual feel (apply the tone curve on clamped-for-masking luminance while
  gaining the un-clamped value)?
- **Q2 — strength constants.** Pick `k_t`, `k_n` (and whether to keep the Kelvin
  curve) so a given slider value feels close to today at the neutral end.
- **Q3 — scope.** Apply the new flat WB everywhere (one path, minor look change
  for non-windowed/reference too), or only on the windowed path and leave the old
  WB for non-windowed?
- **Q4 — order vs recovery.** All multiplicative, so they commute; confirm we
  fold into one gain (simplest) rather than sequencing.
