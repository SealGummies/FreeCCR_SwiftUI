# NamiColor Conversion (experimental)

Status: **experimental** — manual workflow, nothing automatic. Gated behind a
master switch (`FREECCR_NAMICOLOR`, default on for this branch). Flip off to
restore the classic v0.2.3 reference / B-W-point conversion.

## 1. Goals / Non-goals

### Goals
- Reproduce, inside FreeCCR, the DaVinci Resolve negative workflow that the
  author gets consistently better results from:
  1. RAW decode → **Adobe RGB, linear** gamma.
  2. (Manually) crop out sprocket holes / film holder.
  3. **NamiColor** node: per-channel align so the parade has the darkest point
     just under **95** and the brightest 1 % just touching **685** on a
     0–1023 (10-bit) scale (i.e. the Cineon Dmin / 90-%-white code values).
  4. Basic color tune (brightness, contrast, saturation, temperature, tint).
  5. **CST**: Rec.2020 *Cineon Film Log* → Rec.709 *Gamma 2.2*.
- Drive that pipeline from the **existing "Channel Levels" sliders** (Input
  Gain / Master Shift / Master Gain + per-channel Shift / Gain / Blackpoint),
  which already map 1:1 onto NamiColor's controls — but make them run **real
  NamiColor (log-space) math** instead of the current plain linear levels.
- Replace the negative conversion path entirely while experimenting: the
  v0.2.3 **Convert / Auto Frame / Un-convert / Set Black-White-Point** controls
  are **disabled (greyed)**. A loaded color negative is shown as a *live*
  NamiColor positive, recomputed on every slider change.

### Auto-levels (added — see §2.5)
- The conversion now **auto-fits each channel**: the darkest part of each channel
  maps to Cineon **95/1023**, the brightest part to **685/1023**, automatically,
  the moment a negative loads. The NamiColor sliders then **refine on top** (all
  sliders at 0 = the pure auto result). This replaces the manual-only neutral
  placement of the first pass.

### Non-goals (for now)
- No auto frame detection / sprocket-hole exclusion — auto-levels measures over
  the user's crop region if one is set, else the whole frame (robust percentiles
  blunt small borders; crop out the film holder for the cleanest fit).
- No log-domain scope yet (FreeCCR's histogram is computed on the final
  display image, not the pre-CST Cineon-log signal). Known limitation, see §7.
- No GPU/OpenCL kernel for the NamiColor path yet — CPU/numpy only. Preview is
  1080 px, debounced 150 ms, so this is fine.
- Per-color "Subtractive Saturations" bands are **not** applied in NamiColor
  mode for the first pass (the middle stage is the Main sliders only).

## 2. The real NamiColor math (from the DCTL)

Source: `Wavechaser/NamiColor` (`NamiColor_dev/NamiColor_dev.c`). Negatives
branch, per channel, after an optional input-colorspace matrix:

```
inputScale = 16.0 ; invScale = -1.0            # negatives
d = invScale * log10(inputScale * x)           #  = -log10(16·x)   (invert + density)
d = d * InputGain + 1.0                         # master input gain about the +1 anchor
d = d + chShift + MasterShift                   # per-channel + master shift
d = (d + chBlack) / ((1 - chGain - MasterGain) + chBlack)   # per-ch gain/black + master gain
# Fit to Cineon Base (optional, default on):
d = (d + 93/1023) / (1 + 93/1023)
# output: Rec.2020 Cineon Film Log (normalized code value, cv/1023)
```

Adobe RGB → Rec.2020 matrix (DCTL constants):
```
[0.86965940 0.08676942 0.03409159]
[0.09357638 0.90511022 0.00546303]
[0.01676546 0.06225891 0.92799144]
```

Neutral (all sliders 0): `InputGain=1, shifts=0, gains=0, blacks=0` →
`d = -log10(16·x) + 1`, then fit-to-Cineon. So the **default look is already a
valid NamiColor inversion** — the sliders only refine it.

### Slider → parameter mapping (sliders are integers in [-100, 100])
| Slider key            | NamiColor param | Mapping (env-tunable scale) |
|-----------------------|-----------------|------------------------------|
| `ch_input_gain`       | InputGain       | `2 ** (v/100)` → ×0.5…×2, 0→1 |
| `ch_master_shift`     | MasterShift     | `v/200` → ±0.5 |
| `ch_master_gain`      | MasterGain      | `v/300` → ±0.333 |
| `ch_{r,g,b}_shift`    | per-ch Shift    | `v/200` → ±0.5 (added to MasterShift) |
| `ch_{r,g,b}_gain`     | per-ch Gain     | `v/300` → ±0.333 (added to MasterGain) |
| `ch_{r,g,b}_blackpoint` | per-ch Blackpoint | `v/300` → ±0.333 |

Denominator `(1 - gain) + black` is clamped away from 0 so the divide is safe.
Scales are starting points (tunable via `FREECCR_NAMICOLOR_*`); the author tunes
by eye against the parade/histogram.

## 2.5 Auto-levels (default on, `FREECCR_NAMICOLOR_AUTO`)

Instead of the manual neutral placement above, the conversion now auto-fits each
channel's density into the Cineon range. Per channel `c`, from the raw density
`d = −log10(16·x)` (slider-independent):

```
p_lo[c] = percentile(d_c, LOW)     # LOW  = 1.0   (env FREECCR_NAMICOLOR_LOW)
p_hi[c] = percentile(d_c, HIGH)    # HIGH = 99.0  (env FREECCR_NAMICOLOR_HIGH)
d = B + (d − p_lo[c]) · (W − B) / (p_hi[c] − p_lo[c])     # B = 95/1023, W = 685/1023
```

So each channel's **darkest part → 95/1023** and **brightest 1% → 685/1023**,
automatically — which both fills the display range and neutralises the orange
mask (each channel independently spans the same code-value range). This replaces
the `·InputGain + 1` neutral anchor and the fit-to-Cineon step.

**Refinement.** The NamiColor sliders then nudge the auto-fitted `d` (all sliders
at 0 ⇒ the pure auto result):
```
if InputGain ≠ 1: d = G + (d − G)·InputGain     # master contrast about Cineon grey G = 470/1023
d = d + chShift + MasterShift
d = (d + chBlack) / ((1 − chGain − MasterGain) + chBlack)
```

**Anchors** are measured once on the preview-resolution negative (resolution-
independent; percentiles of density are ~stable across scale) and **cached** on
the `CCRImage`, keyed by `(id(resized_raw), crop_rect, crop_angle)`, so preview,
hi-res zoom, and export all use the same fit. Measured over the crop region when
a crop is set, else the whole frame. A flat/featureless channel (`p_hi==p_lo`)
falls back to mapping that channel to black (no range to fit).

## 3. CST: Rec.2020 Cineon Film Log → Rec.709 Gamma 2.2

Pure transfer + matrix + transfer (matches a Resolve CST with no tone/gamut
mapping):

```
cv  = d * 1023                                         # back to code value
# Cineon (Kodak) log → scene-linear, ng=0.6, black=95, white=685:
gain   = 1 / (1 - 10^((95-685)·0.002/0.6))
offset = gain - 1
lin2020 = gain · 10^((cv-685)·0.002/0.6) - offset      # 685→1.0, 95→~0
lin709  = clip(lin2020, 0, ·) @ M_REC2020→REC709
display = clip(lin709, 0, 1) ^ (1/2.2)
```

Note: FreeCCR's working/display buffer is treated as sRGB by the rest of the
app. Rec.709 shares sRGB primaries; the only mismatch is the transfer (pure 2.2
vs sRGB piecewise) — acceptable for the experiment and what the author asked for
("Rec.709 gamma 2.2").

## 4. Pipeline placement

`namicolor_process(img16_adobe_linear, settings)` in `ccr_processor.py`:
1. `/65535` → Adobe RGB linear float.
2. `@ M_ADOBE2REC2020` → Rec.2020 linear.
3. `namicolor_channel_transform` → Cineon-log cv/1023 (the Channel Levels
   sliders).
4. **Middle stage** — creative tune *in log space*: reuse `adjust_image` with the
   Main sliders only (temperature, tint, exposure, brightness, highlights,
   shadows, black/white point, contrast, saturation; `ch_*`=0, no bands).
5. CST → Rec.709 / 2.2 → uint16 display.

Hooked into `CCRImage.apply_adjustments` **before** the no-op early-return guard
(neutral NamiColor still must invert), so preview, hi-res zoom, and export all
flow through it. Curves / area layers / B-W collapse run after, on the display
image, unchanged.

## 5. Data model / integration points
- `ccr_processor.NAMICOLOR_EXPERIMENT` (bool, env `FREECCR_NAMICOLOR`) — master
  switch. `NAMICOLOR_FIT_CINEON` (env `FREECCR_NAMICOLOR_FIT`) — fit-to-Cineon.
- **Decode** (`ccr_image._raw_color_postprocess_kwargs`): in NamiColor mode the
  non-positive decode uses `output_color=rawpy.ColorSpace.Adobe`, `gamma=(1,1)`,
  no WB, `no_auto_scale=True`; white-level scaling still applies (linear).
- **`CCRImage.apply_adjustments`**: NamiColor branch for a non-positive,
  non-converted color image.
- **`CCRImage.update_thumbnail_and_preview`**: skip the negative preview
  auto-brightness when NamiColor is active (output is already a proper positive).
- **`ImagePreview._update_unconvert_action_state`**: grey Convert / Auto Frame /
  Un-convert and the Film B-W-Point buttons; enable the sliders panel and Export
  for any loaded negative.
- **`SlidersPanel`**: rename the "Channel Levels" section → "NamiColor" for
  clarity. Slider keys, defaults (all 0 = neutral), ranges unchanged.

## 6. Test plan
- `tests/test_namicolor.py` (pure-math, no Qt):
  - `cineon_film_log_to_linear`: 685/1023→≈1.0, 95/1023→≈0.0, monotonic.
  - `namicolor_channel_transform` neutral: monotonically **decreasing** in input
    (negative inversion — a brighter scan pixel = scene shadow = darker positive)
    and finite for x∈(0,1].
  - `namicolor_process` on a synthetic frame: valid uint16, and **inversion**
    holds (dark scan region → bright positive, bright scan region → dark).
  - Adobe→Rec.2020 of neutral grey stays ~neutral (channel spread small).
  - Slider monotonicity: raising R Gain brightens the red channel of the output.
- Manual: load a color-negative RAW, confirm the old Convert/Auto-Frame/BW
  buttons are greyed, the sliders are live, and dialing Channel Levels against
  the histogram produces a sensible positive.

## 7. Known limitations / future
- **Histogram is post-CST**, not the Cineon-log parade the author matches in
  Resolve. The 95/685 targets are dialed by eye against the display histogram for
  now. Future: an optional pre-CST log scope on `log_cv` so the 0–1023 parade and
  the 95/685 targets are directly visible (the highest-value follow-up).
- **Export colour tag**: NamiColor pixels are Rec.709 / Gamma 2.2 but the embedded
  ICC is sRGB (shared D65 primaries; only the transfer differs — pure 2.2 vs sRGB
  piecewise). Accepted per §3; a Rec.709-2.2 tag is a future nicety. Export itself
  routes through the live pipeline (`ccr_export_positive` → Adobe-linear decode →
  `apply_adjustments`), not `ccr_normalize_with_reference`.
- **Cross-session flag toggle**: flipping `FREECCR_NAMICOLOR` between runs with a
  saved catalog of v0.2.3-converted images would replay the classic conversion
  against an Adobe-linear decode. Out of scope — the flag is a developer switch,
  not a per-image mode; don't toggle it on an existing converted catalog.
- Bands/curves-in-log, Reversal / Log-to-Log NamiColor modes, GPU kernel: later.

### Resolved in the adversarial review pass
- Hi-res **zoom** worker now skips the negative auto-brightness under NamiColor
  (was washing out zoomed detail vs. the main preview).
- **Monochrome** scans are excluded from NamiColor (`_namicolor_active()` checks
  `is_monochrome`) so a `[G,G,G]` image isn't tinted by the Adobe→Rec.2020 matrix.
- The **reference-frame** hint and canvas left-drag drawing, and the **Clear White
  Point** button, are all gated off under NamiColor (dead v0.2.3 interactions).
- The backend `convert/unconvert_negative_by_index` are **UI-gated only** (greyed),
  not hard-guarded — the conversion machinery stays directly callable so catalog /
  slice / duplicate round-trip tests keep exercising the classic path.
- The `x @ M.T` matrix orientation was reviewed and **confirmed correct** (it
  reproduces the DCTL `out_R = row0·RGB` and keeps D65 grey neutral).
