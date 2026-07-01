# Gamma slider (midtone tone control)

## Goals

- Add a dedicated **Gamma** slider directly below **Brightness** in the adjustment
  panel.
- It is a **single control point on the composite ("rgb") tone curve** that
  moves diagonally, perpendicular to the identity line, from the center
  `(127.5, 127.5)`: toward the **top-left to brighten** (+) or the
  **bottom-right to darken** (−). Endpoints stay pinned at `(0,0)`/`(255,255)`.
- It renders through the **exact same monotone-cubic interpolation as the Curves
  editor** (`apply_curves` → `build_channel_lut` → `_monotone_cubic`), so the
  result is identical to dragging that midpoint by hand. This is the key
  requirement: the shape must match the curve editor, **not** a global `x^γ`
  power law (which bows differently).
- Behave as a plain **look-domain operator on the visible `[0,1]` window** —
  endpoints pinned means no interaction with the working-space highlight
  headroom.

## Non-goals

- No visual curve graph beside the slider (the existing **Curves** section
  already offers a full editor). Just the standard label + slider + value row.
- No change to Brightness (it stays as-is, quirks and all) or to Gain/Exposure.
- No new base offset — Gamma defaults to identity (slider `0`), no per-image
  baked default like `brightness_base`.

## Naming rationale

The curve is a **gamma** curve (endpoints pinned, midpoint bends = the slope of a
film characteristic curve). It is *not* "density": a density change is a uniform
multiply (in log space, an additive offset) that moves the whites too — that is
what **Gain/Exposure** already do. Gamma is the pinned-endpoint midtone bend, so
the slider is named **Gamma**.

## UX / interaction

- Row: `Gamma` label, horizontal slider, value label — identical to every other
  slider (`create_slider`). Range `[-100, 100]`, default `0`.
- `+` values **lift** midtones (brighter), `-` values **lower** them (darker).
- Double-click resets to `0` (inherited `ResettableSlider` behavior).
- Participates in Reset, Compare, Copy/Paste, Undo bursts, and per-area layers
  automatically (all driven by the positional `ADJUSTMENT_KEYS` ↔ `sliders`
  zip).

## Data model

- New adjustment key: `"gamma"` in `SlidersPanel.ADJUSTMENT_KEYS`, inserted
  **immediately after `"brightness"`** so the positional zip with the created
  sliders stays aligned. `create_slider("Gamma")` is inserted at the matching
  position, and its layout added to `scroll_layout` right after Brightness.
- Default is `0`; no entry in `SLIDER_DEFAULTS`.
- Lives inside `adjustment_settings`, which is already serialized wholesale by
  `catalog.py` and snapshotted wholesale by the zoom hi-res worker — so
  persistence and resolution-independent replay come for free.
- Added to the `"tone"` group in `SYNC_GROUPS` so *Sync to All → Tone* copies it.

## Processing / math

The slider value is turned into a 3-point control-point list in the `0..255`
domain (the domain the Curves editor / `apply_curves` use), and that curve is
applied via `apply_curves` — the same monotone-cubic path as a hand-drawn curve:

```
offset = (gamma / 100) * GAMMA_MAX_OFFSET      # GAMMA_MAX_OFFSET = 63.75
points = [[0, 0], [127.5 - offset, 127.5 + offset], [255, 255]]
out    = apply_curves(img, {"rgb": points})
```

- **Perpendicular movement through center**: the middle point keeps
  `cx + cy = 255`, i.e. it slides along the anti-diagonal — `+gamma` up-left
  (brighten), `−gamma` down-right (darken). This matches observed editor drags
  (sample points move along a slope ≈ −1 line through `(127.5, 127.5)`).
- **Strength**: `GAMMA_MAX_OFFSET = 63.75` (`= 255/4`) puts the control point at
  `(63.75, 191.25)` at `+100`. Chosen to fit real drag samples
  (`g≈20/47/75` reproduce `[115.8,141.5]`, `[97.75,158.1]`, `[80.75,175.95]`).
- **Endpoints pinned** by construction, so the effect stays in the visible
  window; no highlight-headroom interaction.

### Pipeline placement

Applied in `apply_adjustments` **after the slider pass**, immediately **before**
the user's manual `curves` (so the Gamma curve and any hand-drawn curve compose
predictably), and before the B&W luminance collapse — the same stage where
`apply_curves` already runs. It is *not* part of the per-pixel `adjust_image` /
OpenCL slider pass (a monotone-cubic LUT is a CPU curve op, like the editor's).

## Integration points

1. `src/core/ccr_processor.py`
   - Add `gamma_curve_points(gamma)` (3-point control-point builder) and
     `apply_gamma_curve(img16, gamma)` (delegates to `apply_curves`), next to
     `apply_curves`. `adjust_image` / the OpenCL kernel are **not** touched.
2. `src/core/ccr_image.py`
   - Import `apply_gamma_curve`.
   - `apply_adjustments` and `_adjust_for_area`: after the slider pass, if
     `s.get('gamma')` is non-zero, `adjusted = apply_gamma_curve(adjusted, g)`
     before the existing `apply_curves(adjusted, curves)` call.
3. `src/widgets/sliders_panel.py`
   - `ADJUSTMENT_KEYS`, `slider_labels`, `create_slider("Gamma")`,
     `scroll_layout.addLayout(...)`, and the `"tone"` `SYNC_GROUPS` tuple.

## Test plan (`tests/test_gamma_slider.py`)

- **Points identity**: `gamma_curve_points(0)` is the identity diagonal.
- **Perpendicular movement**: control point keeps `cx + cy == 255`, endpoints
  pinned, `cx/cy = 127.5 ∓ offset`, across several slider values.
- **Direction**: `+` puts the point above the diagonal (brighten), `−` below.
- **Sign symmetry**: `+g` and `−g` mirror the point (`cx/cy` swap).
- **Identity render**: `apply_gamma_curve(img, 0)` returns the input unchanged.
- **Same path as editor**: `apply_gamma_curve(img, g)` equals
  `apply_curves(img, {"rgb": gamma_curve_points(g)})`.
- **Lift/lower**: mid-gray increases for `+g`, decreases for `−g`.
- **Control point maps to target**: a value at `cx` maps ~onto `cy` (the curve
  passes through the node), within LUT quantization.
- **Pinned endpoints**: black stays `0`, white stays `65535` for any gamma.
