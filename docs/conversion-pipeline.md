# Negative conversion pipeline

This documents how FreeCCR turns a scanned/photographed film negative into a
positive, and the experimental **density-space** inversion on this branch.

## Decode (shared by every path)

RAW frames are decoded **linear**, with no white balance and in the raw colour
space (`ccr_image.read_image` → rawpy `gamma=(1,1)`, `use_camera_wb=False`,
`output_color=raw`, `no_auto_bright=True`). The decoded value is therefore
proportional to the negative's **transmittance** — the correct physical
starting point for an inversion.

## Two conversion entry points

| Path | Function | Status |
|------|----------|--------|
| **B/W-point tool** (user samples a clear-film point and a dense point) | `ccr_normalize_with_bwpoint` / `apply_bwpoint_normalization` | **Active — primary tool** |
| Auto reference frame (percentile + OD mean-equalisation) | `ccr_normalize_with_reference` / `apply_reference_normalization` | **Legacy** |

> The auto reference path is **legacy**. New work targets the B/W-point tool,
> where the user explicitly samples the film base (clear film) and the densest
> area — exactly the anchors a physically-based inversion needs.

## Inversion algorithm (selectable)

Selected at runtime by the module flag `USE_DENSITY_INVERSION`
(`core.ccr_processor`), settable via environment variable:

```
FREECCR_DENSITY_INVERT=1     # use the density-space inversion (default: off)
FREECCR_DENSITY_GAMMA=0.55   # film contrast used by the density path
```

### `standard` (default) — linear inversion

Per-channel black/white-point normalisation in linear light, an optical-density
mean-equalisation for cast balance, then a **linear `out = 65535 - v`**
inversion, followed by a saturation boost + shadow-warmth "look".

This is pleasing but not colorimetrically faithful: film density is ~linear in
*log* exposure (the Hurter–Driffield characteristic curve), so a linear
inversion bends the tonal transfer into a non-film curve, and the orange mask is
removed by density *scaling* rather than a base *offset*.

### `density` (experimental) — Cineon / negadoctor-style

Inverts in optical-density (log) space, the way the film curve encodes the
scene:

1. `D = -log10(transmittance)` — per-channel optical density.
2. Subtract the per-channel **film base `Dmin`** (the orange mask) as a log
   **offset**. In the B/W-point tool this comes directly from the sampled
   clear-film point; in the legacy auto path it is estimated from a high
   percentile of the reference frame.
3. Divide by a per-channel **gamma** (`FREECCR_DENSITY_GAMMA` × a per-channel
   balance term). In the B/W-point tool the balance comes from the sampled
   dense point, so the clear point lands on neutral black and the dense point on
   neutral white — the mask is cancelled across the whole tonal range with no
   gray-world guess.
4. `H = 10^((D - Dmin)/gamma)` recovers scene-linear exposure; the result is
   sRGB-encoded for display.

On this branch the density path is **pure faithful**: the saturation boost and
shadow-warmth styling are **not** applied (they would re-introduce a colour
cast). Tune contrast with `FREECCR_DENSITY_GAMMA` (≈0.45 punchier, ≈0.75
flatter).

#### Known limitation

The B/W-point tool is exact in density mode at every resolution (preview, zoom,
export all go through `_density_invert_points`). The **legacy auto path's**
hi-res replay (`apply_reference_normalization`) still uses the linear inversion
in density mode — only its styling is dropped — so auto + density is approximate
on zoom/export. This is intentional: the auto path is legacy.

## Trying it

```bash
# Live in the app (your normal workflow, B/W-point tool):
FREECCR_DENSITY_INVERT=1 python src/main.py        # PowerShell: $env:FREECCR_DENSITY_INVERT=1

# Offline A/B on a single file:
python experiments/density_compare.py NEG.tif --black 60000,42000,30000 --white 3500,2600,1900
python experiments/density_compare.py NEG.tif --ref 120,90,300,260     # legacy auto path
python experiments/density_compare.py --synthetic                       # no file needed
```
