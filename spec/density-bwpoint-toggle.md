# Density inversion for two-point B/W sampling

## Goal

When the user converts a negative by sampling **both** a clear (film-base) point
and a dense (exposed) point — the *two-point B/W* path — do the inversion in
**optical-density (log) space** instead of the legacy linear-in-transmittance
stretch. Expose a **"Density inversion"** checkbox in Settings → Color
Management → *Negative conversion*, **defaulting ON**.

### Why (the science)

FreeCCR decodes RAW with linear gamma / no auto-bright, so the scan value is
proportional to light transmitted through the negative: `V = k·T`. Optical
density is `D = −log10(T) = log10(base/V)` where `base` is the clear/film-base
value. Recovering density therefore *requires* the log; the legacy two-point map
`(img − dense)/(base − dense)` is affine **in transmittance**, so it hits the two
sampled endpoints but renders the tone curve between them with the wrong shape
and (because it is a per-channel *subtract*, not a *divide*) introduces
tone-dependent colour casts. The black-point-only ("default-slope") path is
already density; this change makes the two-point path consistent with it.

## Non-goals

- **No change** to the default **auto-reference percentile** conversion
  (`ccr_normalize_with_reference` / `*_refparams`) — the most common path stays
  exactly as-is. This was an explicit scope decision (avoid re-opening the parked
  density-tone experiment for the main path).
- **No change** to the **black-point-only / default-slope** mode
  (`_default_slope_invert`) — it is already density and the toggle does not gate
  it.
- No new tone/exposure rendering, no postinvert "look" (the two-point path is
  look-less today and stays so).

## UX / interaction

- New checkbox in the existing **"Negative conversion"** group box of the Color
  Management page (directly below "Positive mode"):
  *"Density inversion (recover optical density for clear+dense sampling)"*.
- **Default ON.** Persisted in `QSettings` under `conversion/density_bwpoint`,
  restored at startup into `ccr_backend.density_bwpoint` (mirrors
  `import/positive_mode` / `import/rgb_merge_mode`).
- Toggling it **reprocesses** the currently-loaded two-point B/W images in place,
  mirroring `on_positive_mode_toggled`. Per image: `reload_image()` (back to the
  raw scan; this clears `conversion_inputs`), re-stamp the saved ci with the new
  `density`, then `_reconvert_in_place` (which reads `ci["density"]` and replays
  the bw convert in the new mode), then refresh preview/thumbnails and save the
  catalog. Inherited `tint_balance_factor` is preserved for slices/duplicates
  (as in the positive-mode reprocess). Images converted by other paths
  (reference, ref_params, black-point-only, positive, unconverted) are untouched.
- A transient hint reports the new state.

## Data model

The mode is **carried per image** in `conversion_inputs` so replay (zoom,
slice, export, catalog round-trip) reproduces exactly what the preview showed,
independent of the live toggle:

```
ci = {"mode": "bw", "bw": (black_bgr, white_bgr|None), "fine_rot": int,
      "density": bool}          # NEW key
```

- New conversions stamp `"density": ccr_backend.density_bwpoint`.
- Replay/export/slice read `ci.get("density", False)` — **legacy catalogs/ci
  that predate this key were produced by the linear path, so they default to
  linear (faithful reproduction).** Only a fresh conversion or an explicit
  density-toggle reprocess flips an image to density.
- `density` is a JSON scalar, so `catalog._ci_to_json/_ci_from_json` (a `dict`
  copy) round-trip it with no change.

`ccr_backend.density_bwpoint: bool = True` holds the default for *new*
conversions and the target for the reprocess.

## Processing / maths

A single shared helper does the per-channel two-point map for **both** the
preview/export entry (`ccr_normalize_with_bwpoint`) and the resolution-
independent replay (`apply_bwpoint_normalization`). Per channel, with
`base = max(black[c],1)` (clear, HIGH scan value) and
`dense = max(white[c],1)` (dense, LOW scan value):

**density = True** (default):
```
Dmax = log10(base/dense)              # require base > dense, else channel → 0 (black)
n    = log10(base / clip(img, floor)) / Dmax
out  = clip(n, 0, 1) * 65535          # already a positive: clear→0, dense→65535
```
No separate `65535 − x` invert (the density normalisation is already oriented).
`floor = _DENSITY_FLOOR (1.0)` keeps the log finite for near-black pixels;
`n<0` (pixels brighter than base) is clipped to 0.

**density = False** (legacy, bit-identical to today):
```
norm = clip((img − dense)/(base − dense), …) * 65535     # |base−dense|<1 → channel 0
out  = clip(65535 − norm, 0, 65535)
```

Both endpoints agree for either mode: **clear → black (0), dense → white
(65535)**; only the curve between them (and per-channel colour consistency)
differs. The black-point-only branch (`white is None`) is unchanged and ignores
`density`.

## Integration points

| Site | File | Change |
|---|---|---|
| `_twopoint_invert` helper | ccr_processor.py | NEW — density/linear per-channel map |
| `ccr_normalize_with_bwpoint(..., density=True)` | ccr_processor.py | two-point branch calls helper |
| `apply_bwpoint_normalization(..., density=True)` | ccr_processor.py | two-point branch calls helper |
| `density_bwpoint = True` | ccr_backend.py `__init__` | new default flag |
| `_reconvert_in_place` | ccr_backend.py | pass `density=ci.get("density", False)` |
| `export_image_by_index` (ci bw / legacy) | ccr_backend.py | `ci.get("density", False)` / `self.density_bwpoint` |
| slice child build | ccr_backend.py | inherit `parent_ci` density into call + child_ci |
| reset-slice parent rebuild | ccr_backend.py | carry template-ci density into call + ci |
| `apply_bwpoint_to_all_images` | ccr_backend.py | call with `self.density_bwpoint`; stamp ci |
| `reprocess_all_for_density_bwpoint_change` | ccr_backend.py | NEW — re-convert two-point bw imgs |
| `_read_converted` bw replay | ccr_image.py | `ci.get("density", False)` |
| `_replay_conversion` bw (catalog) | catalog.py | `ci.get("density", False)` |
| `_cb_density` checkbox + `_on_density` | settings_dialog.py | NEW in Negative conversion group |
| startup restore + `on_density_bwpoint_toggled` | main_window.py | NEW persistence + reprocess |

## Edge cases

- `base ≤ dense` (degenerate / inverted pick): no usable density range →
  channel output 0 (black). Legacy linear maps the same degenerate case to white
  (`norm=0 → 65535`); the difference only affects an invalid pick and is
  acceptable.
- `img == 0` or negative: floored to `_DENSITY_FLOOR` before the log.
- `white is None` (black-point-only): `density` is irrelevant; the call still
  passes a value but the None branch ignores it.
- Old catalog without `"density"`: replays linear (faithful).
- Mixed library (some ref, some bw, some bw-density): only two-point bw images
  react to the toggle.

## Test plan

Pure / rawpy-free unit tests (mirroring `test_three_way_merge.py` style):

- `_twopoint_invert` density vs linear:
  - endpoints: clear→0, dense→65535 in **both** modes.
  - density midtone differs from linear midtone (curve shape).
  - density is a true log: a pixel at the geometric mean of base/dense lands at
    ~0.5·65535; the linear map does not.
  - per-channel independence; degenerate `base≤dense` → 0 (density) vs 255*…
    (linear) sanity.
  - `img` floor (0 input doesn't NaN/inf).
- `apply_bwpoint_normalization(density=False)` is **bit-identical** to the prior
  output for a sample array (refactor-safety).
- `ci.get("density", False)` default: a legacy ci (no key) replays linear.
- Settings dialog: `_cb_density` defaults to backend value, toggling calls
  `on_density_bwpoint_toggled`, refresh reflects backend (mirror the rgb_merge
  tests).
- Backend: `reprocess_all_for_density_bwpoint_change` flips the stored ci
  `density` for two-point bw images and leaves ref / black-point-only untouched
  (monkeypatch `ccr_normalize_with_bwpoint` to record the density it is called
  with).

`merge_raw_channels`-style note: the full GUI reprocess + zoom replay need a Qt
loop / real decode and are covered by the existing manual-verification path; the
maths and wiring above are unit-tested.
