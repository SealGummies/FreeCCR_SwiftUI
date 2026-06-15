# Spec: Area Editing (Local Masked Adjustment Layers)

Status: REFINED v2 (open questions resolved — see §10)
Owner: FreeCCR
Feature branch: `feature/area-editing`

## 1. Summary

Add a **local adjustment** tool: the user paints one or more masked regions on the
image, and each region carries its **own full layer of every per-pixel adjustment**
the app already offers (the 35 sliders + tone curves). Two mask kinds are supported:

- **Circle** — a *squishable* ellipse (independent X/Y radii) that can be moved,
  scaled, squished, and **rotated** on the canvas, with a soft **feather**.
- **Gradient** — a linear gradient mask (a directional ramp between two points)
  that can be moved, rotated, and lengthened, with a soft transition.

The mental model (per the feature request): **the existing global adjustment panel
is itself just an area that affects the whole picture** — an implicit, always-present
full-image layer. Selecting an area re-points the *same* adjustment panel at that
area's settings. The circle/gradient **mask picker lives on the top toolbar**
(`ImagePreview.toolbar`), and a compact **Layers list** in the right panel lets the
user select, enable/disable, and remove areas.

Areas are display-resolution-independent (they replay identically in the 1080px
preview, the hi-res zoom detail, and full-res export) and round-trip through the
catalog, Undo, Copy/Paste, and Sync-to-All — exactly the way crop and curves do.

This spec mirrors the structure of `spec/curves-tone-control.md`, which is the
canonical precedent; the curves "merge-hazard" (§4.2 there) is the single most
important pattern reused here.

## 2. Goals / Non-goals

### Goals
- A top-toolbar mask picker: **Add Circle Area** and **Add Gradient Area** buttons,
  next to the existing toolbar actions (`image_preview.py:570-665`).
- **Multiple** independent areas per image, each with its own complete
  `adjustment_settings` dict (the 35 sliders + nested `curves`).
- Interactive on-canvas overlay for the selected area — move / scale / squish /
  **rotate**, mirroring the crop tool's transform machinery
  (`_draw_crop_overlay`, `_crop_handle_at`, `begin/update/end_crop_drag`,
  `_box_transform`, `_view_scale` in `image_preview.py`).
- Per-area **feather** (soft falloff), stored normalized so it scales with resolution.
- A **Layers list** (right panel): the implicit "Whole Image" layer plus one row per
  area, each with select-to-edit, **enable/disable**, mask-kind icon, and **delete**.
- Selecting a layer re-points the entire `SlidersPanel` (and curve editor) at that
  layer's settings; the "Whole Image" layer edits the existing
  `image.adjustment_settings`.
- Resolution-independent replay through **preview, hi-res zoom, and export** via the
  single `CCRImage.apply_adjustments` chokepoint (`ccr_image.py:516`).
- Round-trip within an image: catalog persistence, Undo, and clone-on-duplicate.

### Non-goals (v1)
- **No per-area negative inversion / conversion.** Inversion is inherently
  whole-image (it is keyed to one reference crop or to global B/W anchors and is
  baked into `resized_raw` before adjustments). Areas operate on the already-converted
  positive and never re-run `ccr_normalize_*` / `apply_reference_normalization`.
- No brush / freehand / polygon masks (only ellipse + linear gradient).
- No luminosity/range masking, blend-mode menu, or per-area opacity slider separate
  from feather (areas blend at full strength inside the mask; feather controls edge).
- **Per-image only**: areas are NOT carried by Sync-to-All or Copy/Paste between
  different images (§10 Q2). Copy/Paste operates on the *active layer's* slider values
  only (the existing semantics), never on the area structure. `duplicate` clones areas
  (it copies the whole image, like it already copies adjustments/crop/curves); slices
  start with no areas.
- **B&W color profile stays whole-image** (not a per-area setting) in v1 (§10 Q1).
- No reordering UI for overlapping areas in v1 (paint order = list order; later areas
  win — see §10 Q3).
- No GPU/OpenCL path for mask rasterization or the alpha blend (CPU numpy/cv2 is
  ample; each area's *adjustment* pass still rides the existing OpenCL path). Mirrors
  the curves CPU-only decision (`curves-tone-control.md` §2).
- Slices do **not** inherit the parent's areas in v1 (coordinate-frame remap is
  out of scope — see §10 Q2).
- Area selection focus (`active_area_id`) is session state, **not** persisted.

## 3. UX / Interaction

### 3.1 Top toolbar — the mask picker
Two new `QAction`s on `ImagePreview.toolbar` (built `image_preview.py:570-665`),
placed after the mirror actions / before Convert, each with an icon and tooltip:
- **Add Circle Area** → `add_area("circle")`
- **Add Gradient Area** → `add_area("gradient")`

`add_area(kind)`:
1. Requires a **converted** image (areas presuppose a positive — same gating as the
   sliders, `image_preview.py:1166` / `set_sliders_enabled`). If not converted, show a
   hint and no-op.
2. Appends a new area (default-centered geometry, identity `adjustment_settings`,
   `enabled=True`) to `img.area_layers`, sets `img.active_area_id` to it, and **enters
   area-edit mode** showing that area's overlay.
3. The Layers list and the adjustment panel both refresh to the new (empty) area.

The toolbar buttons are disabled when no converted image is selected.

### 3.2 The Layers list (right panel)
A compact list at the **top of `SlidersPanel`**, above the existing controls
(the panel is the "editor for the selected layer"). Rows, top → bottom:

1. **Whole Image** (always present, first, non-removable, always enabled) — selecting
   it edits `image.adjustment_settings` (today's global behavior).
2. One row per area in `img.area_layers`, each showing:
   - a **kind icon** (circle ⬭ / gradient ▢▤),
   - an auto name ("Circle 1", "Gradient 2", …),
   - an **enable/disable** checkbox (requirement 6 — disabled areas are skipped in
     processing but kept in the model),
   - a **delete** button (✕) removing the area (with Undo).
   - The **selected** row is highlighted; clicking a row selects that layer (points
     the panel at it) and, for an area, enters area-edit mode + draws its overlay.

Selecting "Whole Image" exits area-edit mode and removes any area overlay.

### 3.3 The on-canvas overlay (mirrors crop)
Area editing is a new **interaction mode** parallel to `crop_mode` / `slice_mode` /
`bwpoint_mode` / `wb_pick_mode` (all mutually exclusive; entering one cancels the
others — `image_preview.py:1916-1952`, `:1169-1189`). It uses the crop tool's exact
architecture:

- **No `paintEvent`.** Overlays are scene items (`QGraphicsEllipseItem`,
  `QGraphicsLineItem`, `QGraphicsPathItem`, `QGraphicsRectItem` handles) carrying a
  `combined = box_t * base` transform, where `base = _base_transform()`
  (`:1898-1914`, coarse flip/rotation only — fine rotation suppressed during editing,
  matching crop, `:1035-1039`) and `box_t` is a `_box_transform`-style rotation about
  the mask center (`:1967-1974`).
- **No `grabMouse`.** A drag-state dict (the area analog of `_crop_drag`) is non-None
  between press and release; `setMouseTracking(True)` drives hover cursors
  (`:126-322`). Dispatch adds an area-edit branch alongside crop in
  `GraphicsImageView.mousePress/Move/ReleaseEvent`.
- **Handle glyphs** are sized `HANDLE_DRAW_PX / _view_scale()` and hit-tested with
  `HANDLE_VIEW_PX / _view_scale()` so they stay constant on screen; the overlay is
  **redrawn on every zoom/resize/transform** (hooks at `:995`, `:1284`, `:1065`) so
  drawn glyphs and clickable zones never drift.
- Teardown wraps `scene.removeItem` in `try/except RuntimeError` (`:2189-2201`).

#### 3.3.1 Circle (squishable ellipse) overlay
- Visuals: the ellipse outline + a fainter **feather ring** (inner edge of the
  ramp), drawn with a `QPainterPath`/`OddEvenFill` dim outside the mask (optional, to
  preview coverage like crop's hole-punch dim, `:2227-2239`).
- Handles (reuse crop's set, hit-tested on the *un-rotated* frame):
  - **center** → move (`_drag_move_box` analog; clamp center to image).
  - **4 corner / 4 edge** → resize each radius independently = the *squish*
    (`_drag_corner` / `_drag_edge` analog; min radius enforced).
  - **rotate knob** above the top edge → rotate (`_drag_rotate_box` analog: `atan2`
    delta about center, snap to 0° within ~0.75°).
- A "circle" defaults to on-screen-circular: `rx*W == ry*H` (see §5.1 aspect note).

#### 3.3.2 Gradient (linear) overlay
- Visuals: a line from `p0` (effect start, α=0) to `p1` (effect full, α=1), with a
  perpendicular band hint showing the ramp direction.
- Handles:
  - **p0** and **p1** endpoint handles → move each end (sets direction + length +
    position together).
  - **mid handle** → translate the whole gradient.
  - **rotate** is implicit in moving the endpoints; an optional rotate knob may pivot
    both endpoints about the midpoint for fine control.

#### 3.3.3 Feather control
- Primary control: a **Feather slider** shown in a small header above the sliders
  whenever an *area* layer is selected (range 0–100, default ~25). It maps to the
  normalized feather fraction (§4.1). Hidden when "Whole Image" is selected.
- Secondary (nice-to-have): for the circle, an on-overlay feather-ring drag handle
  that writes the same value. Spec it but it may land after v1.
- Note: this per-area feather is **distinct** from the existing `band_feather`
  slider (a spatial low-pass of the per-band color correction, `:498-500`); keep the
  two clearly separated in UI and data.

### 3.4 Live feedback, Undo, debounce
Area edits (slider changes **and** mask move/scale/rotate/feather drags) reuse the
panel's existing coalescing machinery:
- **Undo burst**: one snapshot per gesture via `_begin_undo_burst(img)` /
  `end_undo_burst` (`sliders_panel.py:832-847`); these key off the `CCRImage`, not the
  active layer, so they work unchanged. Mask-geometry commits push a snapshot on
  drag-release, mirroring crop's pre-assign `push_undo_state()` (`:2314-2319`).
- **Debounced heavy reprocess** via `_debounce_timer` / `_pending_adjustment`
  (`:762-827`) — area compositing is heavier than a global edit, so coalescing matters.

### 3.5 Keyboard / mode exit
- **Esc** exits area-edit mode (keeps the area; like `cancel_crop_mode`).
- **Delete/Backspace** while an area is selected removes that area (with Undo).
- Switching images, sliders that trigger an external `update_preview`, or entering
  crop/slice mode exits area-edit mode (guarded like crop via a `_rerender` flag so
  the mode's own entry re-render doesn't kick it out — `:818-822`).

## 4. Data model

### 4.1 Storage — a dedicated `CCRImage.area_layers` list (NOT nested in `adjustment_settings`)

Add two new `CCRImage` fields (`ccr_image.py:30-142`), parallel to `crop_rect`:

```python
self.area_layers: list[dict] = list(areas) if areas else []   # persisted
self.active_area_id: str | None = None                        # session-only, NOT persisted
```

Each area is a plain, JSON-friendly dict:

```python
{
  "id": "a1f3…hex",            # uuid4().hex — stable id for selection; mirrors slice ids
  "kind": "circle",            # "circle" | "gradient"
  "enabled": True,             # requirement 6 (disable without removing)
  "feather": 0.25,             # normalized 0..1 (fraction of mask radius / ramp), requirement 4
  "angle": 0.0,                # degrees, Qt clockwise-positive (matches crop_angle convention)
  "geometry": {                # ALL normalized fractions of the un-rotated/un-flipped image
     # circle/ellipse:
     "cx": 0.5, "cy": 0.5, "rx": 0.30, "ry": 0.20
     # gradient (linear) instead:
     # "x0": 0.20, "y0": 0.50, "x1": 0.80, "y1": 0.50
  },
  "settings": { … }            # a FULL adjustment_settings dict (35 sliders + nested "curves"),
                               # identical shape to img.adjustment_settings (requirement 2)
}
```

**Why a separate attribute and not `adjustment_settings["areas"]`** (the alternative
considered): `SlidersPanel` rebuilds `adjustment_settings` from the slider list on
every change via the positional zip
`{k: s.value() for k, s in zip(adjustment_keys, sliders)}`
(5 sites: `:734, :768, :933, :1135, :830`). Anything not a slider key is **silently
dropped** — this is the curves §4.2 hazard. Curves survive only via `_attach_curves`
re-merge (`:753-760`). Nesting the *whole* area structure inside `adjustment_settings`
would force the same re-attach AND overload that dict as both "the global layer" and
"the container of all layers", which is confusing when an area is the active edit
target. Keeping `area_layers` as its own field makes the rebuild only ever target the
**active layer's** `settings` sub-dict (global → `adjustment_settings`; area →
`area_layers[i]["settings"]`), and the area list itself can never be dropped by a
slider tick. The cost is explicit plumbing at the same round-trip sites `crop_rect`
already touches (§6) — a known, bounded set.

Note the per-area `settings` dict still contains a nested `"curves"`, so the
`_attach_curves` pattern is reused **within** whichever layer is active (the curve
editor is the live source of truth for the active layer's curves).

### 4.2 Active-layer routing (the central seam)

`get_adjustment_by_index` / `set_adjustment_by_index` (`ccr_backend.py:273-291`)
currently hard-read/write `images[idx].adjustment_settings`. They are called from
`set_current_idx`, `on_reset_clicked`, compare, and paste — all of which must become
**layer-aware**. Add backend accessors that resolve the active layer:

```python
def _active_settings(self, idx):           # returns the dict to read/write
    img = self.images[idx]
    if img.active_area_id is None:
        return img.adjustment_settings
    for a in img.area_layers:
        if a["id"] == img.active_area_id:
            return a["settings"]
    return img.adjustment_settings          # fallback: stale id → global
```

`SlidersPanel` reads/writes through this resolved target. "Whole Image" selected ⇒
behaves exactly as today.

### 4.3 Identity / empty handling
- An area with `enabled=False` is skipped at processing time.
- An area whose `settings` is all-default and (optionally) whose `curves` is identity
  is still kept (the user created it deliberately); it simply composites a no-op
  (alpha-blend of identical arrays). We do **not** auto-prune empty areas.
- `area_layers == []` ⇒ the image behaves exactly as it does today (zero overhead).

### 4.4 Aliasing rule (undo/zoom/sync safety)
Every snapshot/clone of `area_layers` must be a **deep copy** (`copy.deepcopy`),
because each area nests `settings` (and `settings["curves"]`) and a geometry dict.
Shallow `list(area_layers)` is insufficient. Edits must **replace** sub-values rather
than mutate them in place (same invariant curves relies on). Applies to
`capture_undo_state`, the zoom worker snapshot, duplicate, and sync.

## 5. Processing / math

### 5.1 Mask rasterization (normalized geometry → float alpha at any resolution)

New functions in `ccr_processor.py`, next to `apply_curves` (`:2667`) and
`apply_crop_to_image` (`:1277`). They take **normalized** geometry and the *current*
array shape, so the same area renders correctly at 1080px preview, hi-res zoom, and
full-res export — the exact resolution-independence contract `apply_crop_to_image`
and `compute_reference_norm_params`/`apply_reference_normalization` already honor.

```python
def _smoothstep(a):                       # cubic ease, a in [0,1]
    a = np.clip(a, 0.0, 1.0)
    return a * a * (3.0 - 2.0 * a)

def build_circle_mask(h, w, g, angle_deg, feather):
    """Rotated, squishable ellipse → float32 alpha[h,w] in [0,1], feathered inward."""
    cx, cy = g["cx"] * w, g["cy"] * h
    rx, ry = max(g["rx"] * w, 1e-3), max(g["ry"] * h, 1e-3)
    t = np.deg2rad(angle_deg); cs, sn = np.cos(t), np.sin(t)
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    dx, dy = xs - cx, ys - cy
    xr =  cs * dx + sn * dy               # rotate into ellipse-local frame
    yr = -sn * dx + cs * dy
    d = np.sqrt((xr / rx) ** 2 + (yr / ry) ** 2)   # 1.0 == ellipse boundary
    f = max(feather, 1e-4)                # ramp from d=(1-f) (alpha 1) to d=1 (alpha 0)
    return _smoothstep((1.0 - d) / f).astype(np.float32)

def build_gradient_mask(h, w, g, feather):
    """Linear ramp: alpha 0 at p0, alpha 1 at p1, smooth across (feather softens ends)."""
    ax, ay = g["x0"] * w, g["y0"] * h
    bx, by = g["x1"] * w, g["y1"] * h
    vx, vy = bx - ax, by - ay
    L2 = max(vx * vx + vy * vy, 1e-6)
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    t = ((xs - ax) * vx + (ys - ay) * vy) / L2     # projection param along the axis
    return _smoothstep(t).astype(np.float32)       # clip+ease handles 0..1 band
```

Notes:
- Geometry is fractions of **width** (x/rx) and **height** (y/ry) — same convention as
  `crop_rect` (`ccr_image.py:98-103`). A nominal "circle" is `rx*w == ry*h`; the
  overlay sets the circle-tool default radii accordingly so it looks circular
  on-screen, while "squish" lets `rx`/`ry` diverge.
- `feather` reuses the established fraction-of-extent idiom (cf. `_BAND_FEATHER_MAX_FRAC`,
  `ccr_processor.py:2281`).
- Mask geometry lives in **un-rotated, un-flipped, un-cropped** image space because
  `apply_adjustments` always runs *before* crop/flip/rotate in every path (preview,
  zoom base, export `:828/:1093/:1199` then crop `:836`). No orientation bookkeeping in
  the mask math; the downstream crop/rotate transform image and masked result together.

### 5.2 Compositing — order and where it hooks in

The base each area composites onto is the output of the **global** adjustment pass —
i.e. the existing `adjust_image_opencl(...) + apply_curves(...)` result, which *is* the
implicit "Whole Image" layer. Area adjustments run on that converted, globally-adjusted
base (not the raw scan), so a neutral area is a true no-op and resolution-independence
is inherited for free.

**Additive (delta-accumulation) blend (§10 Q3).** Each enabled area's adjustment is
computed against the *same* base, yielding a per-area delta `δ_i = layer_i − base`. The
masked deltas are **summed** onto the base:
`out = base + Σ_i α_i · δ_i`, then clipped. Properties:
- Where two areas overlap, their contributions **accumulate** (natural for soft
  dodge/burn), rather than one overriding the other.
- It is **order-independent** (commutative) — so no layer-reordering UI is needed.
- A single area reduces to `base·(1−α) + layer·α` (identical to simple over-compositing),
  so non-overlapping areas behave exactly as expected.
- Because each `layer_i` is computed from `base` (not the running result), the deltas
  are independent; this is what makes the sum well-defined and order-free.

Insert at the **end of `CCRImage.apply_adjustments`** (`ccr_image.py:516-571`), after
global sliders + curves and **before** the `profile=="bw"` collapse:

```python
adjusted = adjust_image_opencl(...)                 # global / "whole image" layer
curves = s.get("curves")
if curves:
    adjusted = apply_curves(adjusted, curves)
areas = self.area_layers if areas_override is None else areas_override
if any(a.get("enabled") for a in areas):
    adjusted = apply_area_layers(adjusted, areas, self)   # NEW
if profile == "bw":
    adjusted = self._to_grayscale(adjusted)
return adjusted
```

```python
def apply_area_layers(base_u16, areas, img):
    h, w = base_u16.shape[:2]
    base = base_u16.astype(np.float32)
    acc = base.copy()                       # accumulate masked deltas onto the base
    for a in areas:
        if not a.get("enabled"):
            continue
        # Each area's OWN full adjustment layer, computed against the SAME base.
        # Bases (contrast_base/temperature_base/brightness_base) are GLOBAL-look
        # offsets and are NOT re-applied per area (pass 0) — else effects double up.
        layer = img._adjust_for_area(base_u16, a["settings"]).astype(np.float32)
        if a["kind"] == "circle":
            m = build_circle_mask(h, w, a["geometry"], a.get("angle", 0.0), a.get("feather", 0.25))
        else:
            m = build_gradient_mask(h, w, a["geometry"], a.get("feather", 0.25))
        acc += m[..., None] * (layer - base)        # additive: Σ αᵢ·δᵢ
    return np.clip(acc, 0, 65535).astype(np.uint16)
```

- **Overlap**: additive — `out = base + Σ αᵢ·δᵢ` (see above). Order-independent; no
  reordering UI (§10 Q3). Each `layerᵢ` is computed from the same `base`.
- `_adjust_for_area` reuses the existing per-pixel math with the area's settings and
  zeroed bases — **no new color math** is introduced. The "full layer of every
  adjustment" requirement is satisfied by reusing the `adjustment_settings` schema and
  the existing `adjust_image_opencl`/`apply_curves` functions.

### 5.3 Early-return guard
`apply_adjustments` fast-path (`ccr_image.py:527`, `if not s and bases==0: return …`)
must **not** short-circuit when `area_layers` has enabled members, or area-only edits
won't render. Extend the guard to also check for enabled areas (same class as
`curves-tone-control.md` §9.2).

### 5.4 Resolution-independent replay (the three paths)
All three render paths funnel through `apply_adjustments`, so a single insertion covers
them — *provided* `area_layers` reaches each:
- **Preview** (`ccr_image.py:439`) — uses `self.area_layers` directly.
- **Hi-res zoom** — `HiResDetailWorker.__init__` (`image_preview.py:2506-2521`)
  snapshots GUI-thread state; add `self._areas = copy.deepcopy(img_obj.area_layers)`
  beside `self._settings`, and pass it as `apply_adjustments(base, settings=…,
  areas_override=self._areas, …)` at `:2537`. Masks rasterize against the hi-res
  `base` shape automatically (normalized geometry).
- **Export** (`ccr_processor.py:828 / :1093 / :1199`) — all three converters call
  `apply_adjustments`, which now applies areas before `apply_crop_to_image` (`:836`).
  Nothing else in the export order changes.

**Zoom cache invalidation**: `_current_adj_sig` (`image_preview.py:1387-1394`,
`tuple(sorted(adjustment_settings.items()))` + bases + profile) must incorporate an
`area_layers` digest, or editing an area won't invalidate stale hi-res detail. Use a
hashable digest, e.g. per area `(id, kind, enabled, angle, feather,
tuple(sorted(geometry.items())), tuple(sorted(settings.items())))`. Verify values
compare by equality (lists/dicts of primitives — no unhashable-but-unequal objects).

### 5.5 Performance
- Mask + blend are cheap vectorized numpy over a ≤1080px (preview) or full-res
  (export) array — no GPU needed (mirrors curves §5.3). Each area's *adjustment* pass
  rides the existing OpenCL/numpy path.
- The cost driver is **N full adjustment passes per render** (one per enabled area).
  **Recommended optimization**: compute each area's layer only within the mask's
  bounding box (+ feather margin), then blend into that sub-rect — bounds cost by mask
  coverage rather than full frame. Spec this for export especially (full-res × N).
- No hard cap on area count in v1; if a soft ceiling is wanted, surface it as a hint
  (§10 Q4). Log/inform if a render is expensive rather than silently truncating.

## 6. Integration points

| Location | Change |
|---|---|
| `ccr_image.py:30-142` (`__init__`) | Add `self.area_layers` (param + field) and `self.active_area_id=None`. `import copy`. |
| `ccr_image.py:516-571` (`apply_adjustments`) | Add `areas_override` kwarg; after global sliders+curves and before `_to_grayscale`, call `apply_area_layers`. Add `_adjust_for_area(base, settings)` helper (adjust_image_opencl+apply_curves, zeroed bases). |
| `ccr_image.py:527` (early-return) | Don't short-circuit when enabled `area_layers` exist. |
| `ccr_image.py:631-669` (`capture_undo_state`/`pop_undo_state`) | Include `area_layers` (deep-copied in, `.get(...,[])` out). `active_area_id` is session-only — not snapshotted. |
| `ccr_processor.py` (near `:1277`/`:2667`) | Add `build_circle_mask`, `build_gradient_mask`, `_smoothstep`, `apply_area_layers`. |
| `catalog.py:100-123` (`serialize_image`) | Add `"areas": _areas_to_json(img.area_layers)` after `adjustment_settings`. New `_areas_to_json` (tuples→lists, deep copy) mirroring `_ci_to_json` (`:65-76`). |
| `catalog.py:304-340` (`_restore_image`) | `img.area_layers = _areas_from_json(state.get("areas"))` (default `[]`). New `_areas_from_json` mirroring `_ci_from_json` (`:79-90`). No catalog-version bump (old catalogs load via the `[]` default, like `crop_angle` did). |
| `catalog.py:126-134` (`_is_pristine`) | Add `and not state.get("areas")` so an areas-only image isn't judged pristine and dropped on a failed-restore merge. |
| `ccr_backend.py:273-291` (`get/set_adjustment_by_index`) | Make layer-aware via `_active_settings(idx)`; add area CRUD `*_by_index` (`add_area`, `remove_area`, `set_area_enabled`, `set_active_area`, geometry setters) each calling `update_thumbnail_and_preview()`. |
| `ccr_backend.py:663-724` (`duplicate_images_by_indices`) | `dup.area_layers = copy.deepcopy(img.area_layers)` (`import copy`). |
| `ccr_backend.py:749-892, 925-1045` (slice create / reset) | Slices get `area_layers = []` in v1 (do not inherit — §10 Q2). |
| `sliders_panel.py` (the 5 dict rebuilds `:734,:768,:933,:1135,:830`) | Re-point the rebuild at the **active layer's** settings via the backend accessor; reuse `_attach_curves` on that sub-dict. |
| `sliders_panel.py:679-726` (`set_current_idx`) | Add a "load active layer" path: populate sliders + curve editor from the active layer's settings; refresh the Layers list + feather header. |
| `sliders_panel.py:660-674` (`set_sliders_enabled`) | Extend gating to "a layer is selected" (always true; "Whole Image" default). Enable/disable toolbar area buttons + Layers list. |
| `sliders_panel.py:15-33, 922-993` (`SYNC_GROUPS`/`_perform_sync_to_all`) | **No change** — areas are per-image only (§10 Q2); they are not a sync group and `_perform_sync_to_all` must **preserve** each target's own `area_layers` untouched when syncing other groups. |
| `sliders_panel.py:873-899` (compare) | Compare bypasses all area layers (shows global-only unadjusted), restores on release. This is within-image display, not cross-image. |
| `sliders_panel.py:1129-1175` (copy/paste) | Operates on the live panel = the **active layer's** slider values only (existing semantics); it does **not** copy the area structure between images (§10 Q2). Copying while an area is active copies that area's settings; pasting writes to whatever layer is active on the target. |
| `image_preview.py:570-665` (toolbar) | Add **Add Circle Area** / **Add Gradient Area** actions; enable only for converted images. |
| `image_preview.py:126-322` (`GraphicsImageView` events) | Add an area-edit branch (mutually exclusive with crop/slice/bw/wb), gated on an area drag-state dict; early-return to swallow events. |
| `image_preview.py` (new, mirroring `:1916-2363`) | `enter_area_mode`/`exit_area_mode`, `_draw_area_overlay`, `_area_handle_at`, `begin/update/end_area_drag`, ellipse/gradient drag math reusing `_base_transform`/`_box_transform`/`_view_scale`. Redraw on zoom/resize hooks (`:995,:1284,:1065`). |
| `image_preview.py:2506-2521, :2537` (`HiResDetailWorker`) | Snapshot `self._areas = copy.deepcopy(img_obj.area_layers)`; pass `areas_override=` into `apply_adjustments`. |
| `image_preview.py:1387-1394` (`_current_adj_sig`) | Fold an `area_layers` digest into the signature. |
| `image_preview.py` overlay-vs-crop-display | When a confirmed crop is displayed, map full-image-normalized area geometry through `map_displayed_to_full`/`_crop_display_transform` and skip drawing under a rotated crop (precedent `:906-924`). |
| `main_window.py:198-227` (undo) | No change beyond `pop_undo_state` now restoring `area_layers`; ensure preview/thumbnail refresh (existing). |

## 7. Files touched / added

- **add** `tests/test_area_editing.py` — mask math, compositing, persistence, undo,
  sync, signature, layer routing (see §8).
- **edit** `src/core/ccr_processor.py` — mask + composite functions.
- **edit** `src/core/ccr_image.py` — model fields, `apply_adjustments` hook,
  `_adjust_for_area`, undo.
- **edit** `src/core/ccr_backend.py` — layer-aware accessors, area CRUD, duplicate.
- **edit** `src/core/catalog.py` — serialize/restore areas, pristine clause.
- **edit** `src/widgets/sliders_panel.py` — Layers list, feather header, active-layer
  routing, sync group.
- **edit** `src/widgets/image_preview.py` — toolbar buttons, area-edit mode + overlay,
  zoom snapshot + signature.
- **add** `src/icons/` — circle/gradient toolbar icons (PNG, matching existing icon
  style; `QIcon.fromTheme` fallback like the other toolbar actions).

## 8. Test plan

### Unit (`tests/test_area_editing.py`)
- `build_circle_mask`: center alpha == 1; outside ellipse == 0; rotation by θ matches a
  reference; squish (rx≠ry) produces an ellipse; feather monotonic 1→0 across the ramp.
- `build_gradient_mask`: alpha 0 at p0, 1 at p1, monotonic between; rotation-invariant
  under endpoint swap symmetry.
- Resolution independence: same area def rasterized at 270/540/1080px produces the same
  *normalized* coverage (within tolerance) — the crop-style invariant.
- `apply_area_layers`: empty/all-disabled list returns input unchanged (identity fast
  path); a single neutral-settings area is a no-op; a strong exposure area changes only
  masked pixels; two overlapping areas **accumulate additively** (δ-sum) and the result
  is **order-independent** (swapping the two areas yields the same pixels).
- `apply_adjustments` early-return: an areas-only image (no sliders) still composites.
- Persistence round-trip: `serialize_image`→`_restore_image` reproduces `area_layers`
  (geometry, feather, enabled, settings incl. curves); legacy catalog without `"areas"`
  loads as `[]`; `_is_pristine` false for an areas-only state.
- Undo: capturing then editing an area's settings/geometry, then `pop_undo_state`,
  restores the prior areas without aliasing (mutating the restored area doesn't touch
  the snapshot).
- Layer routing: with an area active, a slider rebuild writes the area's `settings`
  and leaves `img.adjustment_settings` untouched (and vice-versa for "Whole Image").
- Adjustment signature: editing/adding/removing/toggling an area changes
  `_current_adj_sig`.
- Per-image isolation: `_perform_sync_to_all` (any group) leaves every target's
  `area_layers` untouched; Copy/Paste does not transfer the area structure.
- Duplicate: a duplicated image's `area_layers` is an independent deep copy; mutating
  the duplicate's areas doesn't affect the source.
- Slice: a created slice has `area_layers == []`.

### Manual
- Add Circle / Add Gradient from the toolbar (only enabled when converted); new area
  selected + overlay shown.
- Move / squish (independent radii) / rotate the ellipse; move / rotate / lengthen the
  gradient; handles stay constant-size across zoom; feather slider softens the edge.
- Multiple areas; per-row enable/disable (disabled = no effect, kept); delete.
- Select "Whole Image" vs an area → the panel edits the right layer; sliders + curves
  follow; feather header only for areas.
- Area survives: image switch, app restart (catalog), Undo, and duplicate; visible
  identically in **preview, hi-res zoom, and export**. Sync-to-All on another image
  does not add/alter this image's areas.
- Crop + area: confirm an area drawn before/after a crop stays aligned through export;
  area-edit and crop modes are mutually exclusive.
- Performance sanity: several areas on a large export complete in reasonable time.

## 9. Notes / precedents reused
- Crop is the architectural template for the overlay (transforms, hit-test on the
  un-rotated frame, drag-state-dict, no `grabMouse`, constant-size handles, redraw on
  zoom). Rotated-box AABB degeneracy at 45° → map **all four** corners through
  `map_displayed_to_full` (precedent `:354-364`, `:429-434`).
- Curves is the template for "extra state that rides `apply_adjustments` and must
  survive the slider-rebuild" (`_attach_curves`, deep-copy-on-snapshot, signature).
- Conversion replay (`compute_reference_norm_params`/`apply_reference_normalization`)
  is the template for resolution-independent normalized params.

## 10. Resolved decisions (v2)

1. **Per-area adjustment scope — RESOLVED: full per-pixel set; B&W stays global.** A
   per-area layer carries the full 35 sliders + `curves`. The color profile (B&W vs
   color) remains a whole-image setting and is applied after area compositing
   (`_to_grayscale`, `ccr_image.py:569`). No sliders are excluded — all are
   per-pixel-meaningful locally.
2. **Cross-image semantics — RESOLVED: per-image only.** Areas are NOT a Sync-to-All
   group and are NOT transferred by Copy/Paste between images. `_perform_sync_to_all`
   must leave each target's `area_layers` untouched. `duplicate_images_by_indices`
   *does* deep-copy areas (it clones the whole image, consistent with adjustments/crop/
   curves). Slices start with `area_layers = []` (no parent inheritance / coordinate
   remap).
3. **Overlap — RESOLVED: additive blend.** `out = base + Σ αᵢ·δᵢ` with
   `δᵢ = layerᵢ − base` (§5.2). Overlapping areas accumulate; the operation is
   order-independent, so there is **no** reordering UI.
4. **Area count / performance — RESOLVED: no hard cap.** Recommend the bounding-box
   layer optimization (§5.5) to bound export cost; surface an informational hint if a
   render is heavy rather than capping. Revisit only if profiling shows a problem.
5. **Crop coexistence — RESOLVED: mutually exclusive modes.** Area-edit, crop, slice,
   B/W-point, and WB-pick are mutually exclusive interaction modes; entering one cancels
   the others (existing pattern, `image_preview.py:1916-1952`, `:1169-1189`).
6. **Feather control surface — RESOLVED: panel slider primary.** A Feather slider in
   the panel header (shown only when an area layer is selected) is the primary control;
   an on-overlay feather-ring drag for the circle is a post-v1 nicety.
