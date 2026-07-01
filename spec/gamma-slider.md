# Gamma slider (midtone tone control)

## Goals

- Add a dedicated **Gamma** slider directly below **Brightness** in the adjustment
  panel.
- It applies the classic single-anchor *midtone gamma bow*: a monotonic tone
  curve with the endpoints pinned at `(0,0)` and `(1,1)` and the midpoint bowed
  up (brighten) or down (darken). Dragging the slider conceptually moves the
  midpoint dot diagonally up/down.
- Give it a **clean, predictable, symmetric** mapping (unlike Brightness, whose
  `1 - 0.3·b/8` exponent is asymmetric and goes negative at the extremes).
- Behave as a plain **look-domain operator on the visible `[0,1]` window** — no
  interaction with the working-space highlight headroom.

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

Look-domain operator, applied **right after the Brightness block** in the tone
chain (both the CPU `adjust_image` and the OpenCL kernel), operating on the
normalized visible window:

```
gamma_exp = 2 ** (-gamma / 100)      # +100 -> 0.5, 0 -> 1.0, -100 -> 2.0
x         = clip(img / 65535, 0, 1)  # visible [0,1] window
out       = x ** gamma_exp * 65535
```

- Symmetric in log space: `+100` and `-100` are reciprocal exponents
  (`0.5` ↔ `2.0`), so equal-and-opposite slider moves are visual inverses.
- Endpoints pinned: `0^e = 0`, `1^e = 1` for any `e > 0`.
- Applied per channel with the same exponent (matches how Brightness behaves;
  slight, expected saturation interaction in deep tones).

### Pipeline placement

`... Gain → Brightness → Gamma → Highlights/Shadows → B/W point → Contrast → ...`

Because it runs on the post-clamp `[0,1]` data, it never sees or recovers
highlight headroom — satisfying "applies only to the visible window".

## Integration points

1. `src/core/ccr_processor.py`
   - `adjust_image(...)`: add `gamma: float = 0.0` (keyword, after
     `sub_saturation`); add the CPU gamma block after the Brightness block.
   - OpenCL kernel: declare `float gamma = params[25];`; add the gamma block
     after the Brightness block (per-channel `pow`).
   - `adjust_image_opencl(...)`: add `gamma` param; append it to the `params`
     array at index `25` (after the `band_active` flag at `24`, to avoid
     renumbering); pass `gamma=gamma` in both CPU fallbacks.
2. `src/core/ccr_image.py`
   - `apply_adjustments`: pass `gamma=s.get('gamma', 0)`.
   - `_adjust_for_area`: pass `gamma=s.get('gamma', 0)` (areas get their own
     gamma; base offsets stay zeroed, gamma has none anyway).
3. `src/widgets/sliders_panel.py`
   - `ADJUSTMENT_KEYS`, `slider_labels`, `create_slider("Gamma")`,
     `scroll_layout.addLayout(...)`, and the `"tone"` `SYNC_GROUPS` tuple.

Positional call-site safety: `gamma` is always passed by keyword and placed
after the last positional argument, so existing positional calls (fallbacks,
`tests/test_opencl_accuracy.py`) are unaffected.

## Test plan (`tests/test_gamma_slider.py`)

- **Identity**: `gamma=0` returns the input unchanged.
- **Lift**: `gamma=+100` maps mid-gray `0.25` → `~0.5` (increase).
- **Lower**: `gamma=-100` maps mid-gray `0.5` → `~0.25` (decrease).
- **Pinned endpoints**: pure black (`0`) stays `0` and pure white (`65535`)
  stays `65535` for `gamma ∈ {+100, -100}`.
- **Symmetry**: applying `+g` then `-g` (as reciprocal exponents) round-trips a
  mid value back to itself within tolerance.
- **CPU/OpenCL parity** (skipped if OpenCL unavailable): `adjust_image` vs
  `adjust_image_opencl` agree within tolerance for a non-zero gamma.
