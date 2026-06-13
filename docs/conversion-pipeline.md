# Negative conversion pipeline

How FreeCCR turns a scanned/photographed film negative into a positive.

## Decode (shared by every path)

RAW frames are decoded **linear**, with no white balance and in the raw colour
space (`ccr_image.read_image` → rawpy `gamma=(1,1)`, `use_camera_wb=False`,
`output_color=raw`, `no_auto_bright=True`). The decoded value is therefore
proportional to the negative's **transmittance** — the correct physical
starting point for an inversion.

## Inversion — optical-density (log) space

FreeCCR inverts in **optical density**, because film records density as a
roughly linear function of *log* exposure (the Hurter–Driffield characteristic
curve). The earlier linear `out = 65535 - v` method (and its saturation/shadow
"look") has been **removed** — density is the only inversion the app uses.

Per channel:

1. `D = -log10(transmittance)` — optical density.
2. Subtract the **film base `Dmin`** (the orange mask) as a log **offset**.
3. Divide by a per-channel **gamma** (`FREECCR_DENSITY_GAMMA`, default 0.55 —
   the C-41 taking gamma — times a per-channel balance term).
4. `H = 10^((D - Dmin)/gamma)` recovers scene-linear exposure; a luminance
   level-stretch sets black/white, then sRGB encode → display-referred
   positive. No saturation boost or shadow tint (pure faithful).

Shared core: `_density_params_from_ref` (compute per-channel params) +
`_density_apply` (apply them). `FREECCR_DENSITY_GAMMA` tunes contrast
(≈0.45 punchier, ≈0.75 flatter).

## Two entry points

| Path | Functions | Dmin / gamma source |
|------|-----------|---------------------|
| **B/W-point tool** (primary) | `ccr_normalize_with_bwpoint` / `apply_bwpoint_normalization` / `_density_invert_points` | clear-film point = Dmin; dense point fixes per-channel gamma (both endpoints neutralised) |
| Auto reference frame | `ccr_normalize_with_reference` / `compute_reference_norm_params` + `apply_reference_normalization` | Dmin from the clear-film percentile; gamma from gray-world mean of the reference frame |

Both use the same density core, so preview, hi-res zoom, and export match.
`compute_reference_norm_params` returns `(base_density, gamma_ch)`, which
sliced children persist (conversion_inputs `mode: "ref_params"`).

## Offline check

```bash
python experiments/density_compare.py NEG.tif --black 60000,42000,30000 --white 3500,2600,1900
python experiments/density_compare.py --synthetic
```
