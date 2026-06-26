# Spec: Camera-profile colour correctness (ICC / DCP standards compliance)

Status: IMPLEMENTED
Branch: `fix/camera-profile-color-standard`

## 1. Problem

User-generated camera profiles wrecked images: the **DCP rendered pure white**
(~99% of pixels clipped) and the **ICC came out heavily red-cast / blown**, both in
FreeCCR and in RawTherapee. Reproduced on `example_raw/DSC07099.ARW` by decoding
camera-native and applying the user's saved profiles.

## 2. Root cause (confirmed vs DNG spec 1.6, ArgyllCMS, DCamProf, colour-hdri)

Camera matrix profiles — ICC matrix-shaper **and** DNG ForwardMatrix — operate on
**white-balanced** data by universal convention (DCamProf: *"Both DCPs and ICCs make
corrections on white-balanced data"*). FreeCCR instead:

1. **Built the matrix to consume *un*-white-balanced raw** (it fit, then folded WB
   back in) → wrong in RawTherapee, which feeds white-balanced data.
2. **Baked the chart's absolute exposure into the matrix gain** (scaled M so the
   chart neutral reproduced its absolute Y) → huge gain (det≈29) → blows any image
   not at the chart's exposure.
3. **`apply_dcp` multiplied by the raw, un-normalized `camera_whitebalance`**
   (`[2366,1024,1640]`) → ×~2000 → pure white.
4. **`ColorMatrix = inv(ForwardMatrix)`** — wrong per DNG; they differ by the
   reference-neutral white-balance diagonal, so the camera-neutral external apps
   derive was corrupted.

## 3. The standard recipe (implemented)

Notation: `D50 = [0.9642, 1.0, 0.8249]`; `wb` = green-normalised white-balance
multipliers (raw→balanced, `wb[1]=1`); `M` = fitted matrix.

**Fit** (`it8_profile.fit_camera_matrix`) — `M` maps **white-balanced** device →
XYZ D50, **white-relative**:
- `wb = neutral_green / neutral_raw` (green-normalised).
- `bN = (device · wb) / neutral_green` → neutral patch → `(1,1,1)` (exposure out).
- Least-squares `bN → XYZ`; then pin `M = diag(D50 / (M·1)) · M` so **`M·(1,1,1) =
  D50`** exactly. `CameraFit` gains a `wb_mult` field.

**ICC** (`build_camera_icc` + `InputProfile.apply`) — `M`'s columns are the
colorants (balanced device → XYZ D50). `apply(rgb, as_shot_wb)` **white-balances the
raw** (green-normalised as-shot neutral) before the matrix → linear Adobe. The raw
decode threads the frame's `camera_whitebalance` through (`ccr_image._apply_input_icc`).
RawTherapee feeds its own white-balanced data → renders correctly.

**DCP** (`build_camera_dcp`) — `ForwardMatrix1 = M` (so `FM·(1,1,1)=D50`);
`ColorMatrix1 = inv(M · diag(wb))` (camera neutral `CM·D50 = 1/wb`, green-normalised
raw neutral). `apply_dcp` green-normalises `as_shot_wb` before the WB diagonal.

**cLUT** — built in balanced device space (`M/neutral_green` base, WB'd sample
points); `_apply_clut` white-balances + clamps into the `[0,1]` grid. The linear base
stays exposure-robust; the residual fades to zero outside the sampled hull, so
off-scale inputs degrade to the safe matrix.

## 4. Validation

- **Synthetic round-trip** (`scratch/validate_fix.py`): a known camera is recovered
  exactly — `M·(1,1,1)=D50`, `wb` recovered, ICC output == DCP output (colour exact,
  exposure-relative), `CM·D50 = 1/wb`, `FM·(1,1,1)=D50`.
- **Real ARW** (`scratch/render_fixed.py`): building a profile from LibRaw's trusted
  matrix and rendering `DSC07099.ARW` → DCP and ICC produce an **identical, sane**
  negative scan, **1.6% blown** (was 98.6%).
- Tests: `test_it8_profile`/`test_dcp_profile`/`test_clut_icc` (68) + the DCP/ICC
  decode-wiring tests updated to the new convention; all green (only the unrelated
  pre-existing `exposure_base` `TestPositiveExport` failures remain).

## 5. Follow-ups (not in this change)
- **Sanity bound**: compare the fitted matrix against LibRaw's `raw.rgb_xyz_matrix`
  (the no-ICC baseline) and warn / fall back when the neutral axis or gain diverges
  wildly — would have caught the historical red-highest / det-29 matrices. The
  no-ICC decode already *is* that baseline, so it's the natural fallback.
- cLUT high-saturation handling (clamped balanced samples) could be refined.
