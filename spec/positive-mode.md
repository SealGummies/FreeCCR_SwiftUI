# Positive Mode

## 1. Goals / Non-goals

### Goals
- Add a **"Positive mode"** checkbox above the thumbnail list. It is a single,
  global, app-wide toggle (like the input ICC profile), persisted across runs.
- When **on**:
  - RAW files decode as **normal positives** (sRGB color space, sRGB-ish gamma,
    AHD demosaic, camera white balance, auto-brightness) so they no longer look
    green/dark like the raw-sensor negative decode.
  - All **film-negative controls are disabled/greyed**: Convert, Un-convert,
    Auto Frame (toolbar), and the Film B/W Point tools (Set White/Black Point,
    Convert Current/All B/W Point). Reference-frame drawing on the canvas is
    disabled.
  - Images **do not need to be converted**. The full **adjustment** UI (sliders,
    curves, color profile, crop, slice, WB picker, local areas, layers) becomes
    available immediately on any loaded image.
  - **Export** works on un-converted images (no inversion — just adjustments,
    crop, orientation, color profile, output color space).
- Toggling the checkbox **re-decodes every already-loaded image** in the new
  mode. Per the product decision: **keep adjustments, drop conversion** — slider
  adjustments, crop, B&W profile, orientation, areas, and slices are preserved;
  any negative conversion is dropped (the image returns to `converted=False`).
- Newly loaded images (after the toggle) decode in the current mode.

### Non-goals
- No per-image positive/negative switch. The mode is global, matching the
  framing "block the parts that serve film negatives."
- No change to the negative pipeline when the mode is **off** beyond what
  positive mode itself introduced — the conversion and export paths stay
  byte-for-byte unchanged. (The negative *decode* later became conditional under
  a separate change: the no-ICC default decode is Adobe RGB + rawpy auto-scale,
  while the ICC / bare-device decode is raw primaries + absolute values + manual
  white-level scaling — see spec/color-management.md §1.1. Not a positive-mode
  concern.)
- No new export format/colorspace work; positive export reuses the existing
  `write_export_image` (colorspace + ICC + format) tail.
- Monochrome-sensor RAW decoding is left as-is (already not green; an edge case).
- The input ICC profile is treated as a negative-scanning tool and is **skipped**
  while positive mode is on (both RAW and non-RAW). Documented, not surprising:
  positive mode means "treat the decode as a ready sRGB positive."

## 2. UX / Interaction

- A `QCheckBox("Positive mode")` sits at the **top of the `ThumbnailList`**,
  above the thumbnail `QListWidget`, with a tooltip explaining it.
- Checking it:
  1. Sets the global flag and persists it.
  2. If images are loaded, shows a wait cursor and re-decodes them all
     (mirrors the input-ICC reprocess), then refreshes thumbnails, the preview,
     the toolbar/slider gating, and saves the catalog.
  3. Shows a transient hint ("Positive mode on — adjust any image directly.").
- Unchecking it does the symmetric thing (re-decode as negatives; conversions
  remain dropped — the user re-converts as usual).
- Gating while **on**:
  - Toolbar: **Convert**, **Un-convert**, **Auto Frame** disabled (greyed).
    **Export** enabled whenever ≥1 image is loaded.
  - Sliders panel: the four **Film B/W Point** buttons disabled; **all
    adjustment sliders + curves + color profile + crop/slice/WB/areas** enabled
    for the selected image regardless of `converted`.
  - Canvas: left-drag does **not** draw a reference frame; the "draw a frame…"
    hint is suppressed.
- Gating while **off**: unchanged from today (adjustments gated on `converted`).

## 3. Data model

- **Authoritative state:** `CCRBackend.positive_mode: bool` (default `False`).
  Single source of truth, referenced by the UI, decode, display, and export.
- **Persistence:** `QSettings` key `import/positive_mode` (bool), restored in
  `MainWindow.__init__` **before** any image load, and reflected in the checkbox.
- **Not** stored in the per-file catalog: the mode is global. Positive images
  serialize as ordinary *un-converted images with edits* (the catalog already
  supports this: `converted=False`, `adjustment_settings`, `crop`, `areas`,
  `color_profile`, orientation). On reopen the (persisted) global mode decides
  the decode; the stored adjustments re-apply on top.
- A CCRImage reads the global flag lazily (`from core.ccr_backend import
  ccr_backend`) inside the two methods that need it (`read_image`,
  `update_thumbnail_and_preview`); no per-image copy is kept (avoids a stale flag
  after a toggle, and avoids threading a parameter through every call site).

## 4. Processing / decode / export

### 4.1 RAW decode (`CCRImage.read_image`, RAW branch, non-monochrome)
A small pure helper returns the rawpy `postprocess` kwargs for the active mode so
the choice is unit-testable and the negative path is provably unchanged:

- **Negative (mode off):** `gamma=(1,1)`, `no_auto_bright=True`,
  `use_camera_wb=False`, `use_auto_wb=False`, AHD, `half_size=preview`. Output
  space + scaling are conditional (separate change, see
  spec/color-management.md §1.1): `output_color=raw` + `no_auto_scale=True`
  (absolute sensor values + manual white-level scaling) when an input ICC will be
  burned in afterwards or a caller wants bare device RGB (`apply_input_icc=False`,
  IT8 profiling); otherwise the no-ICC default decode is `output_color=Adobe`
  (Adobe RGB) + `no_auto_scale=False` (rawpy auto-scales to full range).
- **Positive (mode on):** `output_color=sRGB`, `gamma=(2.222, 4.5)`,
  `use_camera_wb=True`, AHD, `half_size=preview`, **`no_auto_bright=True`**.
  Auto-brightness is OFF on purpose: rawpy's auto-bright scales until ~1% of the
  brightest pixels saturate, which CLIPS highlights to white. With it off,
  rawpy's auto-scale (`no_auto_scale` left absent/off) still maps the sensor
  white level to full range — a proper, non-clipping exposure that preserves
  highlight headroom (the user raises Exposure to taste). (No `no_auto_scale`.)

Two follow-on steps are gated on the mode:
- **White-level scaling** (`rgb *= 65535/white_level`) is applied on the
  absolute-value negative decode (ICC / bare-device). The positive decode **and**
  the no-ICC default negative decode are already rawpy-auto-scaled to full range;
  re-scaling would blow out highlights — so it is skipped for both.
- **Input ICC** (`_apply_input_icc`) is skipped in positive mode (RAW and
  non-RAW). In negative mode it is applied exactly as today.

Monochrome RAW and non-RAW decoding are otherwise unchanged (non-RAW just also
skips input ICC in positive mode).

### 4.2 Preview/thumbnail brightness (`update_thumbnail_and_preview`)
The display-only auto-brightness stretch exists to make the dark linear negative
legible. A positive decode is already correctly exposed, so:
```
display_img = adjusted if (self.converted or positive_mode) else auto_brightness(adjusted)
```
Same change in the hi-res zoom worker (`HiResDetailWorker.run`) so zoomed detail
matches the preview (capture `positive_mode` at request time for thread safety).

### 4.2a Neutral look baseline (no negative-look offset on positives)
Every CCRImage carries a non-destructive `brightness_base = -8` — part of the
film-NEGATIVE look. Because it is non-zero, `apply_adjustments` runs
`adjust_image` even with no user sliders, applying a gamma-1.3 darkening that
crushes shadows. On a positive that is an unwanted "extra step" between decode
and preview/output (it also rides into export). So positives use a **neutral
baseline `brightness_base = 0`** (set in `CCRImage.__init__` and `reload_image`
based on the mode; `contrast_base`/`temperature_base` are already 0). With the
neutral baseline and no user sliders, `apply_adjustments` short-circuits to an
identity, so a fresh positive is exactly: decode → (user adjustments) → output.
`tint_balance_factor` is left computed from the decoded pixels (it only affects
the Tint slider/WB-picker strength and never clips).

### 4.3 Export (`ccr_processor.ccr_export_positive`)
A new function mirroring the **bwpoint export tail** but with **no
normalization/inversion**:
1. `img = _load_export_source(...)` (positive decode, downsize-aware).
2. `apply_adjustments(img)` (handles sliders, curves, areas, B&W profile).
3. `apply_crop_to_image(crop_rect, crop_angle)`.
4. flips → 90° rotation → corner-anchored watermark (if unpaid) → fine rotation
   (with canvas expansion) → `write_export_image` (colorspace + ICC + format).
- `output_path is None` returns the adjusted in-memory array (parity with the
  other normalize functions; used by tests).
- `CCRBackend.export_image_by_index` routes to `ccr_export_positive` **first**
  when `self.positive_mode`, bypassing the reference/bwpoint routing entirely
  (a leftover `reference_frame` must not trigger an inversion).

### 4.4 Mode toggle (`CCRBackend.reprocess_all_for_positive_mode_change`)
Mirrors `reprocess_all_for_input_icc_change`, but **drops conversion** instead of
replaying it:
```
for img in images:
    inherited_tbf = bool(img.source_ops) or img.is_duplicate
    tbf = img.tint_balance_factor
    img.converted = False
    img.conversion_inputs = None
    img.reload_image()              # re-decode in the new global mode; keeps adjustment_settings
    if inherited_tbf: img.tint_balance_factor = tbf
    img.update_thumbnail_and_preview()
```
`reload_image()` keeps `adjustment_settings`/`crop`/`areas`/orientation and resets
the internal base offsets to defaults (correct: the conversion that set them is
gone). `reference_frame` is intentionally **kept** so toggling back to negative
restores the user's frame.

## 5. Integration points (every `converted` gate that positive must unblock)

`src/widgets/image_preview.py`:
- Store `self.auto_frame_action` / `self.convert_action` (were locals) to gate.
- `_update_unconvert_action_state`: in positive mode →
  `convert`/`auto_frame`/`unconvert` disabled; `export` enabled if any image
  loaded; sliders enabled if an image is selected; B/W-point buttons disabled.
- `update_preview`: crop **display** gate (`…and converted`) → `(converted or
  positive)`; area-mode **exit** gate (`not converted`) → `not (converted or
  positive)`; "draw a frame" hint suppressed in positive mode.
- `_crop_wants_hires` and `_get_display_pixmap` crop-active gate → allow positive.
- `add_area`: allow when `converted or positive`.
- `GraphicsImageView.mousePressEvent`: the reference-draw branch gated with
  `and not ccr_backend.positive_mode`.

`src/widgets/sliders_panel.py`:
- `set_negative_controls_enabled(enabled)` enables/disables the four Film B/W
  Point buttons (called from `_update_unconvert_action_state`).

`src/widgets/export_dialog.py`:
- "exportable" predicate = `img.converted or ccr_backend.positive_mode` for the
  all/current/selected scope sets; "All converted images" label → "All images"
  in positive mode.

`src/widgets/thumbnail_list.py`:
- The checkbox; wires to `MainWindow.on_positive_mode_toggled`.

`src/ui/main_window.py`:
- Restore the persisted flag pre-load; `on_positive_mode_toggled` (persist +
  reprocess + refresh + save catalog); set the checkbox initial state.

`src/core/ccr_backend.py`:
- `positive_mode` field, `reprocess_all_for_positive_mode_change`, export routing.

`src/core/ccr_image.py`:
- `_raw_color_postprocess_kwargs(positive, preview, no_icc_default=False)`,
  decode/ICC/white-level gating, display-brightness gating.

`src/core/ccr_processor.py`:
- `ccr_export_positive`.

## 6. Test plan (`tests/test_positive_mode.py`)
- `_raw_color_postprocess_kwargs(positive=True)` uses `output_color=sRGB`,
  `gamma=(2.222,4.5)`, `use_camera_wb=True`, `no_auto_bright=False`, and has **no**
  `no_auto_scale`; `positive=False` is the exact negative kwargs (regression
  guard, incl. `output_color=raw`, `gamma=(1,1)`). The `no_icc_default` flag
  picks the negative decode: default (`no_icc_default=False`) = `output_color=raw`
  + `no_auto_scale=True`; `no_icc_default=True` = `output_color=Adobe` +
  `no_auto_scale=False` (covered in the decode-wiring tests under a separate
  change).
- `ccr_export_positive(stub, output_path=None)` with identity adjustments
  **returns the input unchanged** (proves no inversion) and applies adjustments
  (e.g. B&W profile → neutral grayscale; a real adjustment changes pixels).
- `CCRBackend.positive_mode` defaults `False`; `export_image_by_index` routes to
  `ccr_export_positive` when on (monkeypatched recorder), and to the negative
  path when off.
- `reprocess_all_for_positive_mode_change` (stub images, monkeypatched
  `reload_image`) sets `converted=False`, clears `conversion_inputs`, **keeps**
  `adjustment_settings`, and preserves an inherited `tint_balance_factor` for
  slices/duplicates.
