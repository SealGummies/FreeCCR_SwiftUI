# Spec: Color Scopes Panel (RGB Parade + Vectorscope)

Status: REFINED v3
Owner: FreeCCR
Feature branch: `feature/color-scopes`

## 1. Summary

Add a collapsible **Scopes** area at the bottom of the canvas (inside
`ImagePreview`, between the graphics view and the fine-rotation slider) showing
two video-style color scopes computed from **the displayed image** — the
confirmed crop, orientation and all applied adjustments included; **zoom and
pan deliberately excluded** (zooming in must not change the scopes; slicing
produces new images, which are scoped like any other when displayed):

- **RGB Parade** — three side-by-side per-channel waveforms: x = horizontal
  position across the visible window, y = channel value on a 10-bit axis
  (0 bottom … 1023 top, DaVinci-style), brightness = pixel count.
- **Vectorscope** — chroma distribution on the CbCr plane with the standard
  R/Mg/B/Cy/G/Yl 75% targets and the skin-tone indicator line (+I axis).

While the mouse is over the canvas, a readout in the panel header shows the
RGB value under the cursor, and that value is marked with a circle on the
parade (one per channel) and on the vectorscope.

## 2. Goals / Non-goals

### Goals
- Collapsible area at the bottom of the canvas; header always visible,
  body (the scopes) shown/hidden by clicking the header toggle.
  **Collapsed by default**; state persists across sessions (QSettings).
- Scopes computed from the *displayed image*: display crop applied,
  orientation as displayed, hi-res detail when present. **Zoom/pan never
  change the scopes.** Pixels not covered by the image (fine-rotation corner
  gaps) are excluded from the statistics.
- Live updates: image switch, slider adjustments, convert/unconvert,
  rotate/flip, crop confirm/clear, slice results, hi-res detail swap-in.
  Updates are coalesced with a short debounce so drags stay fluid.
- Hover probe: RGB value readout (8-bit display values, matching what the
  scopes plot) + marker circles on both scopes. Cleared when the cursor
  leaves the canvas or the image.
- Pure-numpy scope math in a new `src/core/scopes.py`, unit-testable without
  Qt.

### Non-goals
- No waveform (luma) or histogram scope — the right panel already has a
  histogram; parade + vectorscope only.
- No viewport-based scoping (an earlier draft computed over the zoomed
  visible window; dropped — zoom must not affect the scopes).
- No configurable scope options (scale toggles, 601/709 matrix choice,
  hiding the skin-tone line / Cineon reference lines) in v1 — the indicators
  are always on.
- No GPU path; the computation is a few-ms numpy pass over a small
  (~360px-wide) downsample of the displayed image.
- No export/snapshot of scope images.

## 3. UX / Interaction

### 3.1 Placement & chrome
- New `ScopesPanel(QWidget)` inserted into `ImagePreview.layout` directly
  **after `self.view`** and before the rotation slider.
- Header row (~24 px, always visible): a flat toggle button `+ Scopes` /
  `- Scopes` styled like `CollapsibleSection` in the sliders panel, a stretch,
  then the hover readout: a small color swatch + `R --- G --- B ---` label
  (monospace-ish, fixed width so it doesn't jitter).
- Body (only when expanded): fixed height ~180 px, horizontal layout:
  parade widget (stretch) + vectorscope widget (fixed square, height×height).
- Both scope widgets paint on the dark scope background with rounded corners,
  matching `HistogramWidget` chrome (`theme.Paint`-style constants).

### 3.2 Collapse behaviour
- Clicking the header toggles the body. Collapsed → no capture/compute work
  is done (a dirty flag remembers a pending refresh; expanding triggers one).
- Expanded state saved to `QSettings("FreeCCR", "FreeCCR")` under
  `scopes/expanded` (bool, default `False`).

### 3.3 Hover probe
- Moving the mouse over the canvas (any mode) samples the displayed preview
  pixel under the cursor:
  - Header readout shows `R nnn G nnn B nnn` + swatch of that color.
  - Parade: one small circle per channel cell at
    (cursor x-fraction across the displayed image, channel value).
  - Vectorscope: one circle at the value's (Cb, Cr) position.
- Cursor outside the image (canvas letterbox) or off the canvas → readout
  shows `R --- G --- B ---`, markers disappear.
- Sampling reads the *preview-resolution* displayed pixmap (the same data the
  scopes are computed from, up to resampling); it does not force a hi-res
  fetch.

## 4. Data model

No persisted per-image state. Transient state only:

- `ScopesPanel._parade` — `np.ndarray (3, 256, W) float32` counts or None.
- `ScopesPanel._vector` — `np.ndarray (S, S) float32` counts (S=128) or None.
- `ScopesPanel._probe` — `(r, g, b, x_frac)` or None.
- `ImagePreview._scope_timer` — single-shot debounce (120 ms).
- `ImagePreview._probe_qimage` — cached `QImage` of `current_pixmap` for fast
  pixel sampling; invalidated whenever `current_pixmap` changes.

## 5. Processing / math (`src/core/scopes.py`)

All functions pure numpy, no Qt imports.

### 5.1 Displayed-image capture (in `ImagePreview`, Qt side)
- Render **only the pixmap item, whole** — never through the view transform,
  so zoom/pan cannot influence the result — into an offscreen `QImage`
  (`Format_RGBA8888`, transparent-filled) of the item's scene-bounds aspect
  at `max_width = 360`:
  - `br = pixmap_item.mapToScene(boundingRect()).boundingRect()`,
  - `s = min(1.0, 360 / br.width())`; image size `round(br.size * s)`,
  - painter transform = `pixmap_item.sceneTransform() *
    QTransform.fromTranslate(-br.left, -br.top) * QTransform.fromScale(s, s)`
    (Qt composes left-to-right: item → scene → origin-shift → downscale),
  - `drawPixmap(0, 0, pixmap_item.pixmap())` with SmoothPixmapTransform.
- This bakes in flips, 90° and fine rotation, display crop (the displayed
  pixmap is pre-cropped by `update_preview`), and the hi-res prescale for
  free; slice results are separate images and scope like any other. Pixels
  not covered by the image (fine-rotation corner gaps) keep alpha 0. Overlay
  items (reference frame, crop/area/dust/slice chrome) are **not** rendered —
  only image pixels feed the scopes. In crop/area/dust/slice modes the scopes
  reflect what those modes display (the full un-cropped image).
- numpy view via `constBits()`: rows sliced to `bytesPerLine` then trimmed
  to `w*4` (RGBA8888 is byte-ordered R,G,B,A on all platforms);
  `rgb (h, w, 3) uint8`, `mask = alpha >= 250` (excludes uncovered corners
  AND antialiased image edges, which are blended toward transparent and would
  otherwise pollute the low end).

### 5.2 RGB parade
```
compute_parade(rgb, mask) -> counts (3, 256, W) float32
```
For each channel c: `counts[c][v][x]` = number of masked pixels in column x
with value v. Implemented per channel as one `np.bincount` on
`x_index * 256 + value` over the masked pixels, reshaped `(W, 256)` and
transposed.

### 5.3 Vectorscope
```
rgb_to_cbcr(rgb) -> (cb, cr)  float, BT.601 full-range:
  cb = -0.168736 R - 0.331264 G + 0.5 B          # [-127.5, 127.5]
  cr =  0.5 R - 0.418688 G - 0.081312 B
compute_vectorscope(rgb, mask, size=128) -> counts (size, size) float32
```
Bin index: `ix = clip((cb + 128) * size/256)`, `iy = clip((128 - cr) *
size/256)` (+Cr points up). One `np.bincount` over `iy * size + ix`.

Graticule targets (R/Mg/B/Cy/G/Yl) are derived at paint time from the same
`rgb_to_cbcr` at 75% intensity so the boxes always agree with plotted data.

### 5.4 Display normalisation (shared helper)
```
scale_counts(counts, pctl=98.0, gamma=0.55) -> [0,1] float32
```
Reference = the `pctl` percentile of the *nonzero* counts (robust against a
dominant flat area crushing everything, same philosophy as the histogram
widget), then `clip(counts/ref, 0, 1) ** gamma` for visibility of sparse
traces.

## 6. Rendering (`src/widgets/scopes_panel.py`)

### 6.1 `ParadeScopeWidget`
- Builds an RGB `QImage` from the scaled counts: three cells side by side
  (R, G, B), each `W` wide × 256 tall, cell pixels = channel theme color ×
  intensity; row 0 = value 255 (image is vertically flipped from the counts
  array). The QImage is regenerated on `set_data` and drawn scaled to the
  plot rect (smooth, cached) in `paintEvent`.
- **10-bit axis** (display only — the data stays 8-bit/256 bins; 8-bit 255 ≡
  10-bit 1023, so trace geometry is unchanged): a left gutter carries
  DaVinci-style code labels, with a gridline every 128 codes
  (0, 128, …, 896) plus 1023 at the top.
- **Cineon reference lines** at codes **95** (Dmin black) and **685**
  (90% white) — dashed, in the accent color (`SCOPE_REF`), drawn over the
  trace with their code labels at the right edge of the plot.
- Thin vertical separators between the three cells.
- Probe: three circles (white fill, dark outline) at
  `(cells_left + (c + x_frac) * cell_w, top + (1 - v/255) * plot_h)`.

### 6.2 `VectorscopeWidget`
- Square plot centered in the widget. Bin colors: constant-luma YCbCr→RGB of
  each bin's (cb, cr) at Y≈140/255, scaled by intensity (so the trace is
  tinted by its actual hue, Resolve-style). The `(S, S, 3)` hue LUT is
  static per size — computed once and cached at module level
  (`vector_color_lut(size)` in `core/scopes.py`, testable). Regenerated as
  a small `S×S` QImage on `set_data`, drawn scaled (smooth).
- Graticule: outer circle (100%), faint inner circle (75%), crosshair, and
  six 75% target squares labeled R/Mg/B/Cy/G/Yl.
- **Skin-tone indicator line**: from the center to the 100% circle along the
  +I axis of YIQ (`scopes.skin_tone_direction()`, ≈132° in our CbCr plane —
  between the R and Yl targets, closer to R), in `SCOPE_SKIN`.
- Probe: one circle at the probe value's bin position.

### 6.3 `ScopesPanel`
- `set_frame(rgb, mask)` — computes both scopes and updates the children.
- `clear()` — empties scopes + probe (image removed).
- `set_probe(r, g, b, x_frac)` / `clear_probe()` — header readout + child
  markers.
- `is_expanded()` / toggle handling + QSettings persistence.
- Signal `expanded_changed(bool)` so `ImagePreview` can trigger the deferred
  refresh.

## 7. Integration points (`src/widgets/image_preview.py`)

1. `__init__`: create `self.scopes_panel = ScopesPanel()`, insert after the
   view; create `_scope_timer` (single-shot, 120 ms → `_update_scopes_now`);
   connect `scopes_panel.expanded_changed` → schedule. **No viewport hooks**
   (no scrollbar / zoom / resize connections) — zoom and pan must not affect
   the scopes.
2. `_schedule_scope_update()` called from exactly two choke points:
   - `update_preview` (image switch / adjustments / convert / crop
     confirm-clear / slice results — anything that swaps `current_pixmap`),
   - `apply_transformations` (rotate / flip / fine rotation, and the hi-res
     pixmap swaps, which re-run it).
   Collapsed panel → just set the dirty flag.
3. `_update_scopes_now()`: capture per §5.1; `scopes_panel.set_frame(...)`;
   None capture (no image) → `scopes_panel.clear()`.
4. `clear_preview()` → `scopes_panel.clear()`.
5. Hover: at the top of `GraphicsImageView.mouseMoveEvent`, call
   `parent_widget.probe_color_at(event.pos())` (guarded try-free, cheap);
   `leaveEvent` → `parent_widget.clear_color_probe()`.
6. `probe_color_at(vp_pos)`: viewport → scene → inverse
   `_display_transform()` → preview-pixmap pixel; sample the cached
   `_probe_qimage`, built lazily from `current_pixmap` and keyed on
   `current_pixmap.cacheKey()` (so slider/convert refreshes that swap the
   pixmap invalidate it automatically); out of bounds →
   `clear_color_probe()`. x_frac = fraction of the cursor's scene x across
   the pixmap item's scene bounding rect (the parade's x-domain).
   The probe hook runs at the very top of `mouseMoveEvent` (before the
   mid-pan / mode dispatch early-returns) so it works in every mode.
7. Theme: new `theme.Paint` constants — `SCOPE_BG`, `SCOPE_GRID`,
   `SCOPE_LABEL`, `SCOPE_MARKER`, `SCOPE_REF` (Cineon 95/685 lines),
   `SCOPE_SKIN` (skin-tone line); reuse `HIST_R/G/B` for parade tints.

## 8. Performance

- Capture ≈ 360×~240 RGBA render + one numpy conversion; parade = 3
  bincounts over ≤ ~86k masked pixels; vectorscope = 1 bincount + a 128×128
  colorize. Total well under 10 ms on the target machines; debounced at
  120 ms so rotate/slider drags never queue more than ~8 updates/s. Zoom and
  pan trigger no work at all.
- Probe sampling is O(1) per mouse-move (one cached-QImage pixel read +
  two tiny widget `update()`s that only repaint markers).

## 9. Test plan (`tests/test_scopes.py`)

Pure math (no Qt beyond an offscreen QApplication where widgets are smoked):

1. `compute_parade`: synthetic 4×4 image with known per-column values → the
   expected (channel, value, column) bins hold the expected counts; masked
   pixels are excluded; total counts per channel = mask sum.
2. `compute_vectorscope`: pure gray frame → all mass in the center bin;
   pure red / pure blue → mass at the expected quadrant (sign checks on
   cb/cr); masked-out pixels excluded.
3. `rgb_to_cbcr`: gray → (0, 0); red → (cb<0, cr>0); blue → (cb>0, cr<0);
   green → (cb<0, cr<0); yellow ≈ opposite of blue.
4. `scale_counts`: output in [0,1]; a dominant pile does not crush a sparse
   trace to 0 (percentile reference); all-zero input → all-zero output.
5. Widget smoke: construct `ScopesPanel`, feed a synthetic frame via
   `set_frame`, toggle expand, set/clear probe, `grab()` renders non-empty
   without errors (offscreen platform, same pattern as
   `test_histogram_widget.py`).
6. Integration-ish: `ImagePreview` capture helper returns None with no
   image (no crash), and a probe outside the pixmap clears the readout —
   only if constructing `ImagePreview` headless is already done in existing
   tests; otherwise keep to widget level.

## 10. Resolved questions (v2)

- Future scope types: the header stays a dumb toggle; anything more waits
  for a real second scope.
- Cursor over letterbox / off-image: **no markers, dashed readout** — there
  is no pixel value to represent.
- Expanded body height: **180 px** (vectorscope 180×180, parade gets the
  rest of the width).
- Readout format: **8-bit values only** — matches the scope axes; the
  swatch conveys the color at a glance.
- Readout stays live while collapsed (the header is visible either way and
  the probe is O(1)); only capture/compute is skipped when collapsed.

## 11. Amendments (v3)

- **Zoom/pan independence** (user feedback): scopes are computed over the
  whole displayed image, not the zoomed visible window. Crop and slice DO
  affect them (the displayed pixmap is pre-cropped; slices are new images);
  zoom/pan do NOT. Capture renders the item through `sceneTransform` only.
- **10-bit parade axis**: DaVinci-style labels 0–1023 in a left gutter, a
  gridline every 128 codes plus 1023; Cineon reference lines at 95 and 685
  (dashed, `SCOPE_REF` amber, labels at the right edge).
- **Skin-tone line**: vectorscope draws the +I-axis line from the center to
  the 100% circle (`scopes.skin_tone_direction()`, `SCOPE_SKIN`).
