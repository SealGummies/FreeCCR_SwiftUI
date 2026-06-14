# Spec: Curves (Tone Curve Control)

Status: REFINED v2
Owner: FreeCCR
Feature branch: `feature/curves`

## 1. Summary

Add a Photoshop-style tone **Curves** control to the right adjustment panel. The
control lets the user reshape image tonality by dragging an arbitrary number of
control points on a curve, per composite channel (RGB/"All") and per individual
channel (R, G, B). It lives in a new collapsible section (default collapsed),
consistent with the existing "Channel Levels" and "Subtractive Saturations"
sections in `SlidersPanel`.

## 2. Goals / Non-goals

### Goals
- A collapsible "Curves" section in the right panel, **collapsed by default**.
- An interactive Photoshop-style curve editor widget.
- 2 fixed, non-removable endpoint control points: bottom-left `(0,0)` and
  top-right `(255,255)`.
- Left-click on the curve area adds a draggable control point.
- Drag a control point to reshape the curve.
- Right-click on a control point removes it (endpoints excepted).
- Channel selector: **All / R / G / B**. Each channel keeps its own independent
  set of control points.
- A "Reset Curve" button that resets **only** the curve control points (all
  channels back to identity), nothing else.
- The curve affects the preview, the hi-res zoom detail, and the exported file,
  at every resolution (resolution independent).
- Curves persist in the catalog, participate in Undo/Redo, Copy/Paste of
  adjustments, and "Sync to All".

### Non-goals
- No on-curve histogram overlay (may be a later enhancement).
- No numeric input/output readout fields (later enhancement).
- No per-point smoothing/corner toggles (always smooth interpolation).
- No GPU/OpenCL path for curves; a CPU numpy LUT is fast enough (it is a single
  vectorized lookup over the already-downscaled preview / export array).

## 3. UX / Interaction

### 3.1 Section placement
A new `CollapsibleSection("Curves")` added to the scrollable area of
`SlidersPanel`, after the "Subtractive Saturations" section (i.e. at the bottom),
preceded by a horizontal separator, matching the existing pattern.

### 3.2 Editor widget layout (top → bottom)
1. Channel selector row: four small checkable buttons `All | R | G | B`
   (`All` selected by default; mutually exclusive, like the band swatch buttons).
2. The curve canvas — a square-ish drawing area (target ~220×220 px) with:
   - A 1px border, a faint 4×4 grid, and the diagonal identity reference.
   - The active channel's curve drawn as a smooth line.
   - Control points drawn as small filled circles (~5 px radius); the active/
     hovered point highlighted.
3. A "Reset Curve" button.

### 3.3 Coordinate system
- The curve domain is **input** tone on X (left=0 shadow … right=255 highlight)
  and **output** tone on Y (bottom=0 … top=255). Y is drawn inverted (screen
  origin top-left); the editor maps between widget pixels and curve coordinates.
- Control points are stored as `[x, y]` floats in `[0, 255]`.

### 3.4 Mouse behaviour
- **Hit area**: each control point has a square hit/"button" area of
  `POINT_HIT = 11 px` (radius ~5–6 px) around its center.
- **Left-press on empty canvas** (not within any point's hit area): create a new
  control point at the clicked position and immediately begin dragging it.
- **Left-press on an existing point's hit area**: grab that point and drag it
  (no new point is created — this is the "button area won't create a new point"
  rule).
- **Drag**: moves the grabbed point.
  - Endpoints (`x==0` and `x==255`): X is locked; Y is draggable `[0,255]`.
  - Interior points: X clamped to stay strictly between the two neighbour points
    (with a 1-unit min gap); Y clamped to `[0,255]`.
  - Release ends the drag.
- **Right-press on an existing interior point's hit area**: remove that point.
  Endpoints cannot be removed (right-click on them is ignored).
- **Right-press on empty canvas**: ignored (does nothing).
- Points are always kept sorted by X internally.

### 3.5 Reset Curve button
Resets every channel (All/R/G/B) to its 2-point identity
`[[0,0],[255,255]]`, redraws, and triggers a single undoable preview update.
It does **not** touch sliders, crop, profile, etc.

### 3.6 Relationship to the main "Reset" button
The existing panel "Reset" button resets the whole image (all sliders) and, for
consistency, **also clears curves**. The dedicated "Reset Curve" button is the
curve-only reset.

## 4. Data model

### 4.1 Storage
Curve state is stored inside the image's existing `adjustment_settings` dict
under a single key `"curves"`:

```python
adjustment_settings["curves"] = {
    "rgb": [[0, 0], [255, 255]],   # composite / "All"
    "r":   [[0, 0], [255, 255]],
    "g":   [[0, 0], [255, 255]],
    "b":   [[0, 0], [255, 255]],
}
```

Rationale: `adjustment_settings` already round-trips through the catalog
(`json.dump`/`json.load` — lists of `[float,float]` serialize cleanly), through
Undo snapshots (`capture_undo_state` copies `adjustment_settings`), and through
the zoom worker (`HiResWorker._settings = dict(img.adjustment_settings)`). Reusing
it means curves "just work" across all those paths with no new plumbing.

Identity (all 4 channels = `[[0,0],[255,255]]`) is omitted / treated as "no
curve" so untouched images store nothing extra.

### 4.2 Invariant hazard & mitigation
`SlidersPanel` rebuilds `adjustment_settings` from the slider list on every
slider change:
```python
adjustment = {key: slider.value() for key, slider in zip(adjustment_keys, sliders)}
```
This would **drop** the `"curves"` key. Mitigation: every place that rebuilds the
dict from sliders must re-attach the live curve state from the editor (see §6).
The curve editor is the source of truth for the live curve while an image is
selected; `set_current_idx` loads the stored curves into the editor on image
switch.

Shallow copies (`dict(adjustment_settings)`) are safe because edits always
**replace** the `"curves"` value with a freshly-built dict — nested lists are
never mutated in place — so Undo snapshots and the zoom snapshot never alias a
mutating structure.

## 5. Processing / math

### 5.1 Where it applies
In `CCRImage.apply_adjustments`, **after** `adjust_image_opencl(...)` returns and
**before** the optional `_to_grayscale` step:

```python
adjusted = adjust_image_opencl(...)
curves = s.get("curves")
if curves:
    adjusted = apply_curves(adjusted, curves)   # new ccr_processor function
if profile == "bw":
    adjusted = self._to_grayscale(adjusted)
return adjusted
```

Applying before grayscale keeps curves operating in RGB (Photoshop-like) and
means B&W images still respect the composite/per-channel curve before luminance
collapse.

### 5.2 LUT construction (`ccr_processor`)
- `build_channel_lut(points) -> np.ndarray` (dtype `float32`, length 256):
  - `points`: list of `[x,y]` sorted by x, spanning x=0..255.
  - Interpolate y across integer x `0..255` using **monotone cubic** (Fritsch–
    Carlson) interpolation so the curve passes through all points without
    overshoot. With exactly 2 points this reduces to a straight line.
  - Clamp output to `[0,255]`.
- `apply_curves(img16, curves) -> np.ndarray (uint16)`:
  - Build the 256-point curve for each of rgb/r/g/b.
  - Compose: for each channel C in {r,g,b}, the effective 256-LUT is
    `lut_C[x] = channel_C( rgb( x ) )` (composite applied first, then the
    per-channel curve) — matching Photoshop.
  - Expand each 256-entry LUT to a 65536-entry uint16 LUT via `np.interp`
    (input domain `0..65535`), then index the image:
    `out[...,c] = lut16_c[img16[...,c]]`.
  - If all four channels are identity, return the input unchanged (fast path).
  - Helper `_is_identity_curves(curves)` short-circuits the whole step.

### 5.3 Performance
One `np.interp` build (65536 samples) per channel plus three fancy-index lookups
over a ≤1080px preview (or the export array). Negligible vs. the existing
pipeline. No caching needed initially; can memoize per-signature later if shown
necessary.

## 6. Integration points in `SlidersPanel`

| Location | Change |
|---|---|
| `initUI` | Build `CollapsibleSection("Curves")` with `CurveEditor`; wire `curveChanged`/`editFinished` signals. |
| `on_slider_changed` | Re-attach `curves` from editor into the rebuilt adjustment dict (non-identity only). |
| `set_current_idx` | Load `adjustment.get("curves")` into the editor (or identity if absent / no image). Disable editor when no image. |
| `on_reset_clicked` | Also reset the editor to identity (curves dropped from the rebuilt dict). |
| new `_on_curve_changed` | Build dict (sliders + curves), store on image, push undo burst, update preview (mirrors `on_slider_changed`). |
| new `_on_curve_edit_finished` | End the undo burst. |
| `on_reset_curve_clicked` | Editor reset only; single undo step + preview update. |
| `set_sliders_enabled` | Enable/disable the editor. |
| `copy_adjustment_settings` | Include `curves` in the copied dict. |
| `paste_adjustment_settings` | Apply pasted `curves` to the editor + image. |
| `on_compare_pressed/released` | Compare zeroes sliders and drops curves for the held preview, restores from `_original_adjustment` (which carries curves) on release. |
| `SYNC_GROUPS` / `_perform_sync_to_all` | Add a `("curves", "Curves", ())` group; sync the source curves to targets when selected (handled like crop/profile, not via `adjustment_keys`). |

`CurveEditor` API:
- `get_curves() -> dict | None` (None when all identity).
- `set_curves(curves: dict | None)` (None / missing → identity).
- `reset()` → identity, emits change.
- signals: `curveChanged` (live, per drag step), `editFinished` (drag/edit end).

## 7. Files touched / added

- **add** `src/widgets/curve_editor.py` — `CurveEditor` widget + curve math UI.
- **edit** `src/widgets/sliders_panel.py` — section, wiring, persistence hooks.
- **edit** `src/core/ccr_processor.py` — `build_channel_lut`, `apply_curves`,
  `_is_identity_curves`.
- **edit** `src/core/ccr_image.py` — call `apply_curves` in `apply_adjustments`.
- **add** `tests/test_curves.py` — LUT identity, monotone interp, apply round-trip,
  persistence merge invariant.

## 8. Test plan

Unit (`tests/test_curves.py`):
- Identity curves leave a 16-bit image unchanged.
- A 2-point endpoint move (e.g. lift black point to (0,64)) raises shadows as
  expected; monotonic and clamped to `[0,65535]`.
- Composite + per-channel compose in the right order.
- `apply_curves` with `None`/missing returns input unchanged.
- Adjustment-dict merge: a slider change preserves an existing `curves` entry
  (regression for the §4.2 hazard) — exercised via a `SlidersPanel`-level helper
  or a focused unit on the merge logic.

Manual:
- Collapsed by default; expands.
- Add/drag/remove points; endpoints non-removable; right-click empty = no-op.
- Channel switch keeps per-channel curves.
- Reset Curve resets only curves.
- Curve survives image switch, app restart (catalog), Undo/Redo, Copy/Paste,
  Sync to All, and is visible in zoom + export.

## 9. Refinement (v2) — resolved decisions & added detail

### 9.1 Resolved open questions
1. **Endpoint draggability**: endpoints keep X locked (`0` and `255`) but Y is
   freely draggable in `[0,255]`. This gives black-/white-point lift the same way
   Photoshop does and keeps the curve a total function over the full input range.
2. **Main "Reset" clears curves**: YES. The rebuilt all-zero adjustment dict
   simply omits `"curves"`, and `on_reset_clicked` also calls
   `curve_editor.set_curves(None)` to sync the widget. The dedicated "Reset
   Curve" button is the curve-only path.
3. **Interpolation**: **monotone cubic (Fritsch–Carlson)**. Chosen over natural
   cubic to prevent overshoot/ringing (a non-monotone tone curve would invert
   local contrast and clip oddly). Degenerates to linear for 2 points, which is
   exactly the identity we want for the default endpoints.

### 9.2 apply_adjustments early-return guard (correctness)
The existing fast path is:
```python
if not s and cb == 0 and tb == 0 and bb == 0:
    return self._to_grayscale(image) if profile == "bw" else image
```
With a `"curves"` entry, `s` is truthy even when every other setting is neutral,
so the full GPU pass would run for a curves-only edit (still correct, just not
maximally cheap). Acceptable. `apply_curves` additionally guards with
`_is_identity_curves`, so a stored-but-identity curve costs only the cheap
identity check. No change to the guard is required for correctness; we will NOT
special-case it in v1 to avoid touching the hot path.

### 9.3 Curve dict normalization (defensive)
`apply_curves` and the editor must tolerate partial/legacy dicts:
- Missing channel key → identity for that channel.
- Points not sorted / out of range → sorted and clamped before LUT build.
- A channel with `< 2` points or a malformed entry → treated as identity.
This keeps a hand-edited or older catalog from raising.

### 9.4 Exact merge points for the §4.2 invariant
The single helper used everywhere the dict is rebuilt from sliders:
```python
def _attach_curves(self, adjustment: dict) -> dict:
    curves = self.curve_editor.get_curves()   # None when identity
    if curves:
        adjustment["curves"] = curves
    return adjustment
```
Called in: `on_slider_changed`, `get_slider_values` (used by copy + sync source),
`copy_adjustment_settings`. NOT called in `on_reset_clicked` / `on_compare_pressed`
(those intentionally drop curves). `paste_adjustment_settings` and
`set_current_idx` go the other direction: they push `adjustment.get("curves")`
into the editor via `curve_editor.set_curves(...)`.

### 9.5 Sync-to-All for curves
- Add group `("curves", "Curves", ())` to `SYNC_GROUPS`.
- In `_perform_sync_to_all`: capture `src_curves = curve_editor.get_curves()`.
  For each target image, when the curves group is selected and the target's
  stored `curves` differ from `src_curves`, push an undo state and set
  `img.adjustment_settings["curves"] = deep-copied src_curves` (or delete the key
  when `src_curves is None`), then reprocess (curves change pixels, like
  adjustments/profile). Deep-copy the nested lists so synced targets don't alias
  the source's structure.
- The merged-dict rebuild in `_perform_sync_to_all` keys off `adjustment_keys`
  (which excludes `"curves"`); preserve each target's own `"curves"` across that
  rebuild unless the curves group is being synced.

### 9.6 Editor ↔ panel signal contract
- `curveChanged`: emitted on every mouse-move during a drag and on add/remove.
  → `_on_curve_changed`: rebuild dict (sliders + curves), store on image, start
  undo burst, `image_preview.update_preview(idx)` for live feedback, and queue
  the debounced heavy reprocess (reuse the panel's existing
  `_pending_adjustment`/`_debounce_timer` machinery so thumbnail/preview heavy
  work is coalesced exactly like slider drags).
- `editFinished`: emitted on mouse-release / after add/remove settles.
  → `_on_curve_edit_finished`: `end_undo_burst()` so the next discrete edit
  starts a fresh undo step (mirrors slider burst handling).

### 9.7 Widget sizing / theme
- Canvas: `setMinimumSize(200,200)`, `setFixedHeight(220)`; expands horizontally
  with the panel. Dark theme to match (`#2b2b2b` canvas, `#555` grid, `#888`
  identity diagonal, channel-tinted curve line: white for All, `#c66`/`#6a6`/
  `#66c` for R/G/B mirroring the Channel-Levels labels).
- Channel buttons reuse the small checkable style used by the band swatches.

### 9.8 Catalog note
`catalog.py:129` skips persisting some state when `adjustment_settings` is empty.
A curves-only image now has a non-empty `adjustment_settings`, so it persists —
which is the desired behaviour. Verify no inverse assumption (e.g. "empty ⇒
identity") elsewhere breaks; none found in the slider/preview paths.

### 9.9 Out-of-scope confirmations
Histogram overlay, numeric I/O fields, per-point corner toggles, and a GPU curve
path remain non-goals for this change.
