# NamiColor B/W-point conversion (replaces negative conversion)

Promotes the NamiColor+CST experiment (PR #44) to the real negative-conversion
path on `main`, driven by the existing **Film B/W Point** sampling. The B/W
points pin the per-channel auto-levels anchors; unset parts fall back to
percentiles. Conversion is **live/automatic** — no Convert buttons.

## 1. Goals / Non-goals
### Goals
- Replace the B/W-point conversion **calculation** with NamiColor invert + CST.
- Per channel: **black point → 95/1023**, **white point → 685/1023** (Cineon).
  If a point is unset, use the percentile for that end (low 0.5% → 95, high
  99.5% → 685). Neither set ⇒ pure percentile auto-levels (the experiment).
- **Live**: the negative always shows the converted positive; sampling a point
  re-renders immediately. Remove **Convert Current** / **Convert All**.
- The NamiColor "Channel Levels" sliders remain a neutral-by-default refinement
  on top (the app holds the auto correction; UI sliders read 0).

### Non-goals / decisions
- **Comment out** (don't delete) the old reference-frame conversion: Convert /
  Auto Frame / Un-convert toolbar actions + handlers, the reference-rectangle
  drawing, `ccr_normalize_with_reference` usage. Recoverable, per the project's
  parked-experiment style.
- Decode negatives as **Adobe RGB / linear** (faithful; the experiment's choice).
- Keep `ccr_normalize_with_bwpoint` / reference code in `ccr_processor` present
  (commented at call sites) so nothing else that imports them breaks.

## 2. Anchor mapping (the one new idea)
The B/W points are stored GLOBALLY on the backend as BGR scan values
(`black_point_bgr` = clear film base, high values; `white_point_bgr` = dense
area, low values). Per channel `c`, from density `d = −log10(16·x)` (x = the
Adobe→Rec.2020 linear scan):

```
p_lo[c] = density(black_point)[c]   if black set   else percentile(d_c, 0.5)   # → 95/1023
p_hi[c] = density(white_point)[c]   if white set   else percentile(d_c, 99.5)  # → 685/1023
```
where `density(point)` = `−log10(16 · (point_rgb/65535 @ M_ADOBE2REC2020ᵀ))`.
Clamp `p_hi ≥ p_lo + ε` per channel. Then the existing NamiColor affine maps
`[p_lo, p_hi] → [95/1023, 685/1023]` and the CST renders to Rec.709/2.2.

A clear-base black point and a dense white point are *constant across the roll*
(no_auto_bright decode), so the same global points anchor every frame; the
percentile fallback is per-image.

## 2.6 Auto gain (post-CST auto-exposure, hidden master-gain offset)

A per-image **"Auto Gain"** button reads the **post-CST** result and finds a hidden
offset added to `ch_master_gain` (the UI slider stays neutral) so the image's
highlights just touch the right clipping line — *ignoring the brightest outliers*
(sprocket holes / film holder):

```
measure over the crop region (image area); downsample for speed
target = max channel of percentile(out_c, 99.8)        # ignores the top 0.2% (holder/specular)
bisect the master-gain offset until target == 0.998     # monotonic through the CST
store CCRImage.namicolor_gain_offset                    # applied live by apply_adjustments
```

The bisection runs the real `namicolor_process` on a 256-px image (~16 iters), so
it is robust to the CST nonlinearity. The measurement region is the **crop area**
when set, else the whole image (frame auto-detection is intentionally NOT used
here). **Holder/sprocket mask**: pixels that are white-clipped in all channels at
*neutral* gain are excluded from the highlight statistic — the opaque holder maps
ABOVE the white-point reference (denser than any real scene content), so this
ignores it even with no crop. The offset is bounded below the `1/(1−gain)`
singularity; it gains UP when under-exposed and trims down when over clip. Env:
`FREECCR_NAMICOLOR_GAIN_PCT/_TARGET`. Persisted in the catalog and undo.

**Automatic, no buttons.** Auto-gain is per-image and runs as part of the
**recalculate** triggered on every anchor change (set black, set white, or clear)
— there are no manual *Auto Gain* / *Apply to All* buttons. The single **✕**
button clears BOTH the black and white points. Crop to the image first for the
best auto-exposure.

## 2.7 Frame detection (`namicolor_detect_frames`)

Finds the photographic frame(s) inside a film scan, excluding the holder, sprocket
holes, inter-frame gaps and edge markings.

**Primary — learned ONNX segmenter (`core.frame_detect`).** A tiny U-Net (0.48 M
params, ~1.9 MB ONNX, bundled in `src/models/`) segments the exposed frame region
from luma at short-side 512, on the same onnxruntime CPU session the dust model
uses (~50 ms). The box is read from the probability **projections** (row-mean →
frame band, column-mean → side-by-side split), which is robust to stray pixels and
reliably **excludes the sprocket strips** — the criterion every classical heuristic
failed. Returns `[]` when onnxruntime or the model is absent. Trained on synthetic
135 strips (real negative content composited onto synthetic base + sprockets at
known rectangles → perfect masks); see `tools/frame_training/`.

**Fallback — `_heuristic_detect_frames`.** Classical *density above the film base*
+ projection profiles (every exposed pixel is denser than the clear base; holder
removed by opacity; row/column profiles isolate and split frames). Used only when
the ONNX path is unavailable, so detection degrades gracefully.

Wired into the CLI `--auto-frame` (auto-crop). It is **not** currently used for the
in-app auto-gain region (that uses the crop area, §2.6); `namicolor_detect_frames`
remains available for explicit/headless use.

> Why learned: threshold/flood/projection heuristics each traded one failure for
> another on real scans (rebate leak, full-height sprocket inclusion, over-clamp,
> mis-split) — see [[frame-detection-physics]]. Known gaps: **color 135 only**
> (B&W / medium-format / reversal need their own synthetic recipe); a 2-up strip
> whose gap stays above threshold returns one box (a model-data refinement, not a
> brittle valley heuristic).

## 3. Pipeline placement
`namicolor_process(img_adobe_linear, settings, auto_anchors)` (ported from PR #44)
is called from `CCRImage.apply_adjustments` for every non-positive image (the
live conversion), with `auto_anchors` computed by a new
`CCRImage._namicolor_anchors()` that reads the GLOBAL `ccr_backend.black_point_bgr
/ white_point_bgr` and this image's percentiles, cached by
`(id(resized_raw), crop_rect, crop_angle, black_point_bgr, white_point_bgr)`.

- Decode: `_raw_color_postprocess_kwargs` negative branch → `ColorSpace.Adobe`.
- `update_thumbnail_and_preview`: skip the negative auto-brightness (output is a
  finished positive).
- Hi-res zoom worker: skip auto-brightness too; anchors read from the image.
- Export: route negatives through the live path (`ccr_export_positive` →
  Adobe-linear decode → `apply_adjustments`).

## 4. UI changes
- `sliders_panel`: remove **Convert Current** / **Convert All** buttons (and
  their handlers). Keep **Set Black Point** / **Set White Point** / **Clear
  White Point** — these now drive the live anchors. Sampling a point calls the
  backend setter and triggers a full re-render of all images.
- `image_preview._update_unconvert_action_state`: sliders enabled for any loaded
  negative; comment out Convert / Auto Frame / Un-convert gating.
- Comment out the reference-rectangle drawing + hint in `GraphicsImageView`.

## 5. Test plan
- Port the NamiColor math tests (Cineon anchors, inversion, matrix neutrality,
  auto-fit range fill, crop-region measurement, slider refinement).
- New: anchors-from-points — black point sets p_lo, white point sets p_hi;
  unset falls back to percentile; both set ⇒ both from points; `p_hi ≥ p_lo`
  guard. A point mapping check: a pixel equal to the black point converts to
  ~Cineon black (display ~0), a pixel equal to the white point to ~685 (bright).
- Full suite: zero new failures vs. the pre-existing baseline; update the
  negative-decode test for Adobe.

## 6. Known limitations / future
- Histogram is post-CST (no Cineon-log parade) — dial by eye, as in PR #44.
- Export ICC is tagged sRGB while pixels are Rec.709/2.2 (shared primaries).
- Reference-frame code is commented, not deleted; the now-orphaned handler
  methods (`convert_ccr` / `unconvert_ccr` / `auto_frame`, the B/W `_on_convert_*`
  handlers, AutoFrameWorker/Dialog) remain as parked dead code.
- **Old catalogs are NOT replayed**: a stored v0.2.3 `mode:"ref"`/`mode:"bw"`
  bake would be wrong against the Adobe-linear decode, so `catalog._replay_conversion`
  is skipped when NamiColor is on — the image loads unconverted and converts live.
- **Tethering**: `TetherWatchWorker._convert` no-ops under NamiColor — captures
  convert live from the global B/W points instead of baking.
- `FREECCR_NAMICOLOR=0` is debug-only: it disables the live conversion but the
  reference-frame UI is commented out, so negatives can't be converted that way.
- Anchors are measured on the preview-resolution negative and reused for export
  (intentional — keeps preview and export identical; density percentiles are
  scale-stable).
