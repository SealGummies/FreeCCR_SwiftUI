# Spec: Crop Panel (aspect ratios + straighten)

Status: REFINED v1
Owner: FreeCCR
Feature branch: `feature/crop-panel-aspect-ratios`
Issue: #39 — "Feature Request: Aspect Ratios in Cropping & Selection Menus".

## 1. Summary

Replace the crop tool's "just a button + canvas handles" UX with a **dedicated
Crop panel** that covers the right-hand sliders panel while in crop mode — the
exact pattern used by Dust Removal (`DustRemovalPanel`). The panel exposes "all
the options" for cropping:

- **Aspect-ratio presets** (Free, Original, 1:1, 5:4, 4:3, 7:5, 3:2, 16:9, plus a
  cinematic group — Academy 1.37, 1.85 Flat, 2:1 Univisium, 2.35 CinemaScope,
  2.39 Scope) with a **portrait/landscape** toggle and a **custom W:H** field.
  A locked ratio
  constrains every on-canvas drag (draw, corner, edge) so the box can only ever
  be that shape. The selected ratio **persists across images and sessions**
  (QSettings) so a whole catalogue can be cropped to the same dimensions — the
  core request in #39.
- **Straighten** slider (−45°..+45°) that drives the crop box's rotation, kept in
  sync with the existing on-canvas rotate knob.
- **Reset** (clear the pending crop + straighten) and **Done** (commit) buttons.
  Enter/Esc/right-click on the canvas keep working exactly as today.

When entering crop mode, any existing **micro-rotation** (image-level
`fine_rotation_angle`, the straighten slider under the canvas) is **folded into
the crop straighten**: the leveling is preserved but re-expressed as the crop
box's angle, and the image-level micro-rotation is cleared on commit so the two
rotations never stack. Cancelling leaves the original micro-rotation untouched.

## 2. Goals / Non-goals

### Goals
- A **Crop panel** (sibling of `sliders_panel`/`dust_panel`, fixed 300px) shown in
  place of the sliders while crop mode is active; restored on exit — mirroring
  `MainWindow.toggle_dust_removal`.
- Aspect-ratio **presets + orientation toggle + custom W:H**, persisted in
  QSettings (`crop/aspect_key`, `crop/custom_w`, `crop/custom_h`,
  `crop/orientation`) so the choice is sticky across images and app restarts.
- A locked ratio constrains **all three** drag interactions (new selection, corner
  resize, edge resize) and reshapes the current box immediately when chosen.
- A **Straighten** slider in the panel, two-way-synced with the canvas rotate
  knob (both edit the single `_pending_crop_angle`).
- **Fold micro-rotation into crop straighten** on entry; clear
  `fine_rotation_angle` on commit (undoable in one snapshot); preserve it on
  cancel. The canvas micro-rotation slider is disabled while in crop mode.
- **Reset** and **Done** buttons; Enter = Done, Esc = cancel, right-click = clear,
  Crop button again = exit — unchanged.
- No change to the export/preview crop math: the panel only changes how
  `crop_rect` / `crop_angle` are *produced*. `apply_crop_to_image`,
  `_extract_rotated_crop`, histogram crop, and all three export paths are
  untouched.

### Non-goals
- **No aspect ratios for the "selection" tools yet** (reference frame, Slice, Area
  editing). #39 mentions "selection menus"; that is a follow-up. This change is
  crop-only. (Documented so the issue can be partially closed.)
- **No fixed pixel-dimension output** (e.g. force exactly 3000×2000 px). Only the
  *ratio* is constrained; output resolution is still driven by the existing
  resize/export logic.
- **No re-architecture of crop to display the live fine-rotation.** Crop mode keeps
  showing the un-fine-rotated image and rotates the *box* (`_pending_crop_angle`),
  as today. Straighten is the same variable, surfaced as a slider.
- No grid/thirds overlay changes beyond what already exists.

## 3. UX / interaction

### 3.1 Entering / leaving
- The **Crop** button in `sliders_panel` now routes through
  `MainWindow.toggle_crop_panel()` (was: directly `enter_crop_mode()`):
  - On enter: `image_preview.enter_crop_mode()`, hide `sliders_panel`, `bind` and
    show `crop_panel`.
  - The single canvas exit chokepoint `_exit_crop_mode()` calls back to
    `MainWindow` (`on_crop_panel_closed()`) to restore the sliders panel — so
    **every** exit path (Done, Enter, Esc, right-click clear, Crop-toggle-off,
    image switch, entering dust mode) restores the panel with no special-casing.
- The panel header is "Crop". Layout top→bottom: Aspect ratio (combo),
  orientation radios, custom W:H row, a separator, Straighten slider, a separator,
  then a stretch and a button row **Reset | ✓ Done** (Done = primary, like dust).

### 3.2 Aspect ratio
- **Combo** lists: Free, Original, 1:1, 5:4, 4:3, 7:5, 3:2, 16:9, Academy (1.37:1),
  1.85:1 (Flat), 2:1 (Univisium), 2.35:1 (CinemaScope), 2.39:1 (Scope), Custom…
- **Orientation** (Landscape / Portrait) radios; disabled and ignored for Free,
  Original, and 1:1 (orientation is meaningless there). Toggling swaps the active
  ratio `r ↔ 1/r`.
- **Custom W:H**: two spin boxes (1..9999). Editing them selects "Custom" and uses
  `r = w/h`. Hidden unless Custom is selected (kept compact).
- Selecting a ratio (or toggling orientation, or editing custom) **immediately
  reshapes** the current pending box to that ratio (preserving its center and
  straighten angle; see §5.3) and redraws. With **Free**, nothing is constrained.
- The selection is **remembered** and re-applied the next time crop mode opens, on
  the same or a different image (QSettings).

### 3.3 Straighten
- Slider −45.0°..+45.0° (0.1° steps), centered, double-click → 0 (reuse
  `CenteringSlider`). Drives `_pending_crop_angle` directly (deg). Dragging it
  redraws the box overlay.
- Dragging the canvas rotate knob updates `_pending_crop_angle`; the panel slider
  re-syncs from `image_preview` via `on_crop_geometry_changed()` after each drag.
- Drawing a *brand-new* box resets the angle to 0 (existing behavior — a fresh box
  is axis-aligned); the panel slider re-syncs to 0 accordingly.

### 3.4 Buttons / keys
- **Done** → `MainWindow.toggle_crop_panel(False)` → `image_preview.confirm_crop()`
  (commit) → panel restored. Same as pressing Enter.
- **Reset** → clear the *pending* crop box + straighten (set `_pending_crop_local`
  = None, `_pending_crop_angle` = 0) and redraw; does not commit. (Pressing Done
  afterward with the whole image and angle 0 clears any stored crop, as today.)
- **Esc** → `cancel_crop_mode()` (no change committed). **Right-click** on canvas →
  `clear_crop()` (remove stored crop). Both restore the panel via the chokepoint.

## 4. Data model

No new persistent field on `CCRImage`. The committed result is still exactly
`crop_rect` (normalized `(x1,y1,x2,y2)` in un-rotated/un-flipped space) and
`crop_angle` (deg). The chosen **aspect ratio is panel/session state**, persisted
in QSettings (not per image) so it is consistent across the catalogue:

- `crop/aspect_key`: one of
  `free|original|1:1|5:4|4:3|7:5|3:2|16:9|academy|1.85:1|2:1|2.35:1|2.39:1|custom`.
- `crop/custom_w`, `crop/custom_h`: ints for the custom ratio.
- `crop/orientation`: `landscape|portrait`.

Transient crop-session state on `ImagePreview` (already present, reused):
`_pending_crop_local` (QRectF, pixel coords), `_pending_crop_angle` (deg). New:
`_crop_fold_fine` (bool) — was a nonzero `fine_rotation_angle` folded at entry?

## 5. Processing / math

Downstream crop application is **unchanged**. New math lives in a pure,
Qt-free, unit-tested helper module `src/core/crop_aspect.py`; the drag handlers
call it.

### 5.1 Ratio definition & coarse-rotation compensation
Ratio `r = boxwidth / boxheight` is measured in the box's own frame, i.e. on
`_pending_crop_local` which is in **un-rotated image pixel space**. But the user
sees the box through `_base_transform` (coarse 90/180/270 rotation + flips). For
the on-screen box to actually look like the chosen ratio:

```
effective_r = r            if current_rotation in {0, 180}
              1 / r        if current_rotation in {90, 270}
```

Flips do not change a rectangle's aspect, so only 90/270 invert it. All
constraint math below uses `effective_r`.

`Original` resolves to `current_pixmap.width() / current_pixmap.height()` (the
un-cropped image shown in crop mode = the native un-rotated aspect); orientation
is not applied to Original.

### 5.2 Constrained drag (pure helpers in `crop_aspect.py`)
- `enforce_ratio_size(adx, ady, r) -> (bw, bh)`: given non-negative desired
  extents, return the smallest box of ratio `r` that **covers** both:
  `if adx >= ady*r: (adx, adx/r) else: (ady*r, ady)`. Used by new-selection and
  corner drags (the box grows to include the pointer while holding ratio).
- `fit_ratio_within(w, h, r) -> (bw, bh)`: largest box of ratio `r` that **fits**
  inside `w×h`: `if w/h >= r: (h*r, h) else: (w, w/r)`. Used to clamp a drawn box
  to the image and to reshape on preset change.

Handler wiring (all keep the existing anchor/clamp behavior, just adding ratio):
- **New selection** (`_update_new_selection`): build the axis-aligned rect from
  `p0→p1`; if locked, replace `(w,h)` with `enforce_ratio_size(w, h, effective_r)`
  keeping the `p0` anchor and drag direction; clamp the result into the image with
  `fit_ratio_within` (scaling about the anchor) so it never spills out.
- **Corner** (`_drag_corner`): after computing the free `(adx, ady)` in box frame,
  apply `enforce_ratio_size` before rebuilding the rect (opposite corner stays
  fixed).
- **Edge** (`_drag_edge`): the dragged dimension is set from the pointer as today;
  if locked, derive the perpendicular dimension from `effective_r` symmetric about
  the box center (so the box keeps its position on the cross axis and the opposite
  edge fixed on the drag axis). Horizontal edges (`l`/`r`) set width→height;
  vertical edges (`t`/`b`) set height→width.

### 5.3 Reshape-to-ratio on preset change
`apply_ratio_to_pending(box | None, effective_r, img_w, img_h)`:
- If a box exists: keep its center; `bw,bh = fit_ratio_within(box.w, box.h,
  effective_r)` (shrink to fit *within* the current selection so it never grows
  past what the user had); return a centered QRectF, clamped into the image.
- If no box yet: center on the image; `bw,bh = fit_ratio_within(img_w, img_h,
  effective_r)`; return the centered max box.
- The straighten `_pending_crop_angle` is preserved.

### 5.4 Straighten fold-in
Equivalence (derived from the pipeline): `fine_rotation_angle` exports via
`getRotationMatrix2D(center, -fine_angle)` → positive `fine` rotates content
**clockwise**; `crop_angle` de-rotates the box → positive rotates content
**counter-clockwise**. So the same leveling is `crop_angle = -fine_angle`.

- `folded_crop_angle(crop_angle, fine_rotation_angle) = crop_angle -
  fine_rotation_angle/100.0` (pure helper, tested).
- `enter_crop_mode`: set `_pending_crop_angle = folded_crop_angle(img.crop_angle,
  img.fine_rotation_angle)`; `_crop_fold_fine = img.fine_rotation_angle != 0`. If
  folding and `_pending_crop_local is None`, seed a **full-frame** pending box so
  the folded angle is visible/committable. Do **not** mutate the image yet
  (cancel stays clean).
- `confirm_crop`: when committing an applied crop, if `_crop_fold_fine`, also set
  `img.fine_rotation_angle = 0` inside the same `push_undo_state()` snapshot — the
  crop result already reflects the un-fine-rotated display, so this is WYSIWYG and
  one Ctrl+Z restores both crop and the original micro-rotation.
- `cancel_crop_mode` / `clear_crop`: never touch `fine_rotation_angle`.
- The canvas `rotation_slider` is disabled on `enter_crop_mode` and re-enabled by
  the normal `update_preview` path on exit (it already re-reads the value).

## 6. Integration points

- `src/core/crop_aspect.py` — NEW: presets list + pure helpers (§5). No Qt.
- `src/widgets/crop_panel.py` — NEW: `CropPanel(QWidget)` mirroring
  `DustRemovalPanel` (header, controls, `bind_image()`,
  `on_crop_geometry_changed()`, Reset/Done). Persists ratio via QSettings.
- `src/ui/main_window.py` — build/insert `crop_panel` as a 300px sibling
  (after `dust_panel`); add `toggle_crop_panel(on)` and `on_crop_panel_closed()`;
  call `image_preview.set_crop_panel(crop_panel)`.
- `src/widgets/image_preview.py`:
  - `set_crop_panel(panel)`; store `self._crop_panel`.
  - `enter_crop_mode`: fold-in (§5.4); disable `rotation_slider`; read the active
    ratio from the panel and reshape if locked; bind panel.
  - `_update_new_selection` / `_drag_corner` / `_drag_edge`: apply ratio via
    `crop_aspect` using the panel's active `effective_r` (None = Free).
  - `update_crop_drag` / `end_crop_drag`: after redraw, call
    `self._crop_panel.on_crop_geometry_changed()` so the straighten slider stays
    in sync.
  - `_exit_crop_mode`: call `self.window().on_crop_panel_closed()` (guarded).
  - `confirm_crop`: fold-fine commit (§5.4); the `_crop_fold_fine` reset.
  - A small accessor for the active ratio: the handlers ask
    `self._crop_panel.current_effective_ratio(self.current_rotation, pixmap)`.
- `src/widgets/sliders_panel.py` — `_on_crop_clicked` calls
  `mw.toggle_crop_panel()` (toggle) instead of `enter_crop_mode()` directly; keep
  the hint. Update the Crop tooltip to mention the panel + aspect ratios.

## 7. Edge cases

- **No current image / unconverted**: Crop is already gated by
  `set_sliders_enabled`; `toggle_crop_panel` no-ops when `current_idx is None`
  (like dust).
- **90/270 coarse rotation**: ratio inverted via `effective_r` so the on-screen
  box matches the chosen ratio (§5.1). Confirmed result is stored in un-rotated
  space; downstream coarse rotation is applied after crop (unchanged), giving the
  expected final orientation.
- **Custom 0 / degenerate ratio**: guard `h>=1, w>=1`; ignore ratios that produce
  `< 10px` boxes (min size already enforced by handlers).
- **Reshape spilling outside image** with a rotated box: `fit_ratio_within` uses
  the box's own w×h, and confirm already intersects (angle 0) or keeps (angle≠0)
  as today; no new clamp needed for the rotated case.
- **Entering dust mode from crop**: `enter_dust_mode` already calls
  `_exit_crop_mode`, which restores the sliders panel; `toggle_dust_removal` then
  swaps to the dust panel. One transient, no breakage.
- **Switching images while in crop mode**: existing behavior unchanged; the exit
  chokepoint restores the panel.
- **Panel/QSettings missing** (tests construct `ImagePreview` without a real
  panel): `set_crop_panel` defaults `_crop_panel=None`; the ratio accessor returns
  Free and the sync calls are guarded — crop works exactly as before.

## 8. Test plan

Pure helpers (no Qt) in `tests/test_crop_aspect.py`:
- `enforce_ratio_size`: covers both branches; ratio held exactly; covers the
  pointer; 1:1 and wide/tall ratios.
- `fit_ratio_within`: fits within bounds for width-bound and height-bound cases;
  ratio held; never exceeds.
- `apply_ratio_to_pending`: shrinks within an existing box; centers in the image
  when none; preserves center.
- `folded_crop_angle`: sign/scale (`fine=+200` → `-2.0` added); zero is a no-op;
  combines with an existing crop_angle.
- `effective_ratio` helper: inverted for 90/270, unchanged for 0/180; Original
  from pixmap dims; orientation swap.

GUI-level (offscreen Qt) in `tests/test_crop_panel.py` (mirror
`test_crop_overlay.py`'s stub host):
- Locked ratio constrains a simulated `_update_new_selection` /
  `_drag_corner` (box ratio within tolerance).
- Fold-in: set `fine_rotation_angle`, `enter_crop_mode`, assert
  `_pending_crop_angle == folded` and `_crop_fold_fine` True and a full-frame
  pending box seeded; `confirm_crop` zeroes `fine_rotation_angle`;
  `cancel_crop_mode` leaves it.
- `toggle_crop_panel` swaps panel visibility and restores on exit.
- Existing `test_crop_overlay.py`, `test_histogram_crop.py`, `test_crop_hires.py`
  still pass (no downstream change).

Regression: run the crop/rotation/export-touching suites
(`tests/run_tests.py`) — pre-existing unrelated failures noted in repo memory are
not introduced by this change.
