# Area editing — optimizations

Review of the local-masked-adjustment-layer implementation
(`spec/area-editing.md`). The compositing core is
`ccr_processor.apply_area_layers` / `build_area_mask`; per-area adjustment is
`ccr_image._adjust_for_area`; live drag is `image_preview._area_live_refresh`.

## [DONE] Memory-lean mask rasterization

`build_circle_mask` / `build_gradient_mask` use `arange` broadcasting instead of
`np.mgrid`, so a mask is one `(h,w)` float32 array rather than two extra full-res
int64 grids. Matters most at export resolution (a 24 MP mask is ~96 MB; the old
mgrid added ~384 MB of int64 temporaries per area).

## [DONE] Skip enabled-but-unadjusted areas

`apply_area_layers` filters to `enabled and settings` before allocating any
mask/delta. A freshly created area (no slider touched yet) and any all-default
area are true no-ops and cost nothing — previously each ran a full-frame
`_adjust_for_area` plus a full-res mask + multiply-add for zero effect.

## [PROPOSED] Bounding-box area layers — the big one

`apply_area_layers` runs `_adjust_for_area` over the **entire** base for every
area, then blends with a full-frame mask — even when the mask covers 5 % of the
frame. Cost = N × (full-frame adjustment pass + full-frame mask + blend).

Proposal: compute each area's layer and mask only within the mask's **bounding
box** (+ a feather margin), and blend into that sub-rectangle:

```python
# pseudo
x0,y0,x1,y1 = mask_bbox(area, h, w, margin=feather_px + 1)
sub = base_u16[y0:y1, x0:x1]
layer = layer_fn(sub, area["settings"])          # adjust only the sub-image
m = build_area_mask_local(sub.shape, area, offset=(x0,y0))[...,None]
acc[y0:y1, x0:x1] += m * (layer.astype(f32) - sub.astype(f32))
```

- For a circle, the rotated-ellipse bbox is cheap to compute from `cx,cy,rx,ry,θ`.
- For a gradient the effect spans the whole frame (the ramp has full/zero
  plateaus), so keep it full-frame — bbox only helps the circle/ellipse.

Impact: large for small or numerous circle areas, especially at export
resolution (turns N full-frame passes into N small passes). Risk: medium — bbox
math, clamping to image bounds, and correct mask offset. Worth a focused PR with
tests comparing bbox vs full-frame output (must match within rounding).

## [PROPOSED] Live-drag: recompute only the dragged area

`image_preview._area_live_refresh` calls `update_thumbnail_and_preview`, which
re-runs the **global** adjustment pass **plus every area** every ~40 ms during a
drag — even though only the dragged area's geometry changed.

Because the blend is additive (`out = base + Σ αᵢ·δᵢ`), the other terms are
constant during a single-area geometry drag. Cache at drag start:

```
partial = base + Σ_{i≠k} αᵢ·δᵢ          # computed once in begin_area_drag
# each move (area k):
out = partial + α_k(geom)·δ_k            # only k's mask recomputed; δ_k fixed if
                                          # only geometry (not settings) changed
```

- `δ_k` (area k's adjustment delta) is fixed while dragging *geometry/feather*;
  only the mask `α_k` changes → just rasterize one mask + one blend per move.
- For a *slider* drag on area k, `δ_k` changes but `partial` and `α_k` are fixed.

Impact: large for multi-area live editing (drag stays smooth regardless of how
many other areas exist). Risk: medium — a small cache on `ImagePreview`
invalidated on layer add/remove/enable/select and image switch. Pairs naturally
with bbox layers.

## [PROPOSED] Preview-only refresh during drag

`update_thumbnail_and_preview` also rebuilds the 156 px thumbnail and the
histogram on every live-drag tick — both invisible during the drag. A
`update_preview_only()` path (preview pixmap only) would drop that work; refresh
the thumbnail/histogram once on `end_area_drag`. Impact: modest; risk: low.

## [PROPOSED] Cache masks by signature

If the same area is re-rendered without geometry change (e.g. a slider drag on
that area), its mask `α` is unchanged. Memoize the last mask per area id keyed by
`(kind, geometry, angle, feather, h, w)` to skip re-rasterization. Impact: small;
risk: low. Mostly subsumed by the drag cache above.

## Quick reference

| Item | Status | Helps | Rough impact |
|---|---|---|---|
| arange masks (vs mgrid) | DONE | export memory | memory |
| skip empty areas | DONE | unadjusted areas | small–med |
| bbox area layers | PROPOSED | small/many circle areas | large (export) |
| drag: recompute dragged area only | PROPOSED | multi-area live edit | large (UI) |
| preview-only during drag | PROPOSED | live drag | modest |
| mask memoization | PROPOSED | repeated renders | small |
