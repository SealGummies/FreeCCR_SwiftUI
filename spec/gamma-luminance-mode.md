# Gamma: hue-preserving (luminance) mode

## Goals

- Add a global **checkbox in Settings → General** that switches how the **Gamma**
  slider (see `spec/gamma-slider.md`) is applied:
  - **Off (default): per-channel Gamma** — the current behavior. The composite
    tone curve is applied independently to R, G, and B (`apply_curves` with an
    `"rgb"` curve). Because the curve is non-linear, it distorts the R:G:B
    ratios, so non-neutral colors shift hue/saturation. This is the standard,
    filmic behavior of most tone tools and stays the default so existing edits
    render identically.
  - **On: luminance (hue-preserving) Gamma** — apply the *same* tone curve to a
    single luminance value `L`, derive a scale factor `k = L' / L`, and multiply
    all three channels by `k`. Uniform scaling leaves the RGB *direction*
    (chromaticity) untouched, so **hue and HSV-saturation are preserved**; only
    brightness changes. Equivalent to applying the Gamma curve in a "Luminosity"
    blend.
- The toggle is a **global, persisted display mode** (like **Auto gain**), not a
  per-image setting: flipping it only re-renders — no re-conversion, no catalog
  write.

## Non-goals

- **No change to the Curves editor.** This mode governs only the **Gamma**
  slider. The manual Curves ("All"/per-channel) editor stays per-channel — a
  hand-drawn curve is expected to behave per-channel, and per-channel Curves are
  the whole point of that tool.
- No change to the Gamma slider's UI, range, curve shape, or pipeline placement.
  Only the *channel application* changes.
- No new per-image state, no `SLIDER_DEFAULTS` entry, no undo interaction (it is
  a global render mode, exactly like Auto gain).
- Not a perceptual-hue guarantee. "Hue preserved" means constant RGB ratio
  (HSV/HSL hue). It is *not* constant CIE-Lab/CAM hue (the Abney effect is not
  corrected), and the guarantee holds only where **no channel clips** (see Math).

## UX / interaction

- Settings → **General** page gains a new group **"Gamma"** below the existing
  **"Exposure"** (Auto gain) group:
  - Checkbox: **"Hue-preserving Gamma (apply to luminance only)"**.
  - Muted description: *"Apply the Gamma slider to luminance and scale the colour
    channels together, so midtone adjustments don't shift hue or saturation. Off
    (default) applies Gamma per channel — the standard look, which adds a little
    saturation as it brightens/darkens. Highlights that would exceed white still
    clip."*
- **Staged**, like the other Settings toggles: changing the checkbox does nothing
  until **Done**; Escape/close discards it. Seeded from the live backend on open
  (`_init_toggles`), applied on Done (`_apply_pending`).
- On apply, a temporary hint on the sliders panel:
  *"Gamma: hue-preserving (luminance) mode."* / *"Gamma: per-channel mode."*

## Data model

- New global flag `CCRBackend.gamma_luminance: bool = False` (next to
  `auto_gain`). Not serialized per image; not in `adjustment_settings`.
- Persisted by MainWindow under QSettings key **`adjust/gamma_luminance`**
  (default `False`), restored at startup next to `adjust/auto_gain`.

## Processing / math

The per-channel path is unchanged. The luminance path reuses the **identical**
tone LUT so neutrals render bit-for-bit the same in both modes.

```
lut16  = _gamma_lut16(gamma)                 # 65536-entry uint16, built exactly
                                             # as apply_curves builds the "rgb" LUT
L      = 0.299*R + 0.587*G + 0.114*B         # Rec.601 luma (matches the weights
                                             # already used elsewhere in the file)
L'     = lut16[round(L)]                      # same tone curve, on luminance only
k      = L' / max(L, 1.0)                      # floor the denominator at 1 count
out    = clip(RGB * k, 0, 65535)              # uniform per-pixel scale, then clip
```

Key properties (each is a test):

- **Neutrals are identical to per-channel.** For `R=G=B=v`: `L=v`,
  `out = v · lut16[v]/v = lut16[v]` — exactly the per-channel result. So grays,
  the black point, and the white point match between modes.
- **Hue/chroma preserved (pre-clip).** All three channels are scaled by the same
  `k`, so the R:G:B ratios (hence HSV hue and saturation) are unchanged wherever
  `RGB·k` stays within `[0, 65535]`.
- **Clipping is the documented exception.** A saturated near-white pixel with a
  brightening Gamma can drive its top channel past white; the clip then breaks
  the ratio and reintroduces a hue shift. This is inherent to any brighten-then-
  clip and is called out in the UI copy.
- **Division-by-zero guard.** The denominator is floored at 1 count. Pure black
  (`L=0`) stays black (`L'=0 → out=0`); this avoids amplifying shadow chroma
  noise the way an unclamped `L'/L` would.

`_gamma_lut16(gamma)` factors out the LUT expansion already inlined in
`apply_curves` (build the 256-point composite curve via
`build_channel_lut(gamma_curve_points(gamma))`, then `np.interp` it up to a
16-bit LUT), so both paths share one definition of the curve.

### Pipeline placement

Unchanged. Still applied in `apply_adjustments` / `_adjust_for_area` after the
slider pass and before manual `curves`. Only the internal branch differs.

## Integration points

1. `src/core/ccr_processor.py`
   - Add `luminance: bool = False` kwarg to `apply_gamma_curve`. When true and
     `gamma != 0`, dispatch to a new `_apply_gamma_luminance(img16, gamma)`;
     otherwise the existing `apply_curves({"rgb": ...})` path. Default `False`
     keeps every current caller/test unchanged.
   - Add `_gamma_lut16(gamma)` (shared LUT builder) and
     `_apply_gamma_luminance(img16, gamma)` (the L·k scale above).
2. `src/core/ccr_backend.py`
   - Add `self.gamma_luminance: bool = False`.
3. `src/core/ccr_image.py`
   - Both `apply_gamma_curve` call sites pass
     `luminance=ccr_backend.gamma_luminance`. The whole-image path already has a
     deferred `ccr_backend` import in scope (the Auto-gain block); the area path
     adds the same deferred import.
4. `src/ui/main_window.py`
   - Restore `ccr_backend.gamma_luminance` from `adjust/gamma_luminance` at init
     (beside `adjust/auto_gain`).
   - Add `on_gamma_mode_toggled(checked)`: set flag, persist, and re-render all
     loaded images (`_release_hires`, `update_all_thumbnails`, `update_preview`)
     with a hint — a copy of `on_auto_gain_toggled` (display-only; no reconvert,
     no catalog write).
5. `src/widgets/settings_dialog.py`
   - New "Gamma" group + `self._cb_gamma_lum` checkbox on the General page.
   - Add it to `_init_toggles` (seed from `ccr_backend.gamma_luminance`) and
     `_apply_pending` (on change, call `on_gamma_mode_toggled`).

## Test plan

`tests/test_gamma_slider.py` (extend):
- **Luminance identity at 0**: `apply_gamma_curve(img, 0, luminance=True)` returns
  the input unchanged.
- **Neutrals match per-channel**: for several grays and gammas,
  `apply_gamma_curve(gray, g, luminance=True) == apply_gamma_curve(gray, g)`
  (== `lut` value), incl. black→0 and white→65535.
- **Hue/ratio preserved**: a non-clipping colored pixel (e.g. `(120,80,40)`-ish
  in 16-bit) keeps its channel ratios after luminance-mode Gamma (within LUT
  quantization), and equals `round(rgb · k)`.
- **Per-channel *shifts* the ratio**: the same pixel through the default path
  changes the ratio (documents the difference the feature addresses).
- **Clipping caveat**: a bright saturated pixel with large `+gamma` clips its top
  channel at 65535 in luminance mode (ratio no longer preserved) — asserted so
  the limitation is pinned, not a surprise.

`tests/test_settings_dialog.py` (extend):
- The **General** page exposes `_cb_gamma_lum`; it seeds from
  `ccr_backend.gamma_luminance`.
- Toggling then **Done** calls `on_gamma_mode_toggled` and flips the backend flag;
  closing without Done leaves it unchanged (staged/discard behavior, mirroring the
  existing Auto-gain / Positive toggle tests).
