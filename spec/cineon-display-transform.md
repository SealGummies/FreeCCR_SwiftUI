# Spec: Cineon Film Log → Rec.709 (γ 2.2) Display Transform

Status: REFINED v2
Owner: FreeCCR
Feature branch: `feature/color-scopes` (rides the scopes PR — the parade's
95/685 reference lines mark exactly this transform's black/white anchors)

## 1. Summary

A checkbox in the **Channel Levels** section — "Cineon Log → Rec.709
(γ 2.2)" — that, when enabled, interprets the fully-adjusted image as Cineon
printing-density film log and converts it to Rec.709 video with a plain 2.2
gamma, as the **final pipeline stage after every other adjustment**, before
display and export. This is the classic Kodak Cineon log-to-video conversion
(DaVinci's "Cineon Film Log" flavor): 10-bit code 95 = black (Dmin), code
685 = 90% white.

## 2. Goals / Non-goals

### Goals
- Per-image on/off flag, stored in `adjustment_settings` under
  `"cineon_log"` (present-and-True when on; absent when off — like the
  non-slider `curves` key).
- Applied identically to preview, hi-res zoom detail, and every export path
  (all funnel through `CCRImage.apply_adjustments`).
- Participates in undo/redo, Reset, Compare (hold shows the untransformed
  image), Copy/Paste of adjustments, Sync-to-All (rides the "Channel
  Levels" sync group), and the catalog (plain bool in the settings dict).
- Whole-image only: area layers never carry the flag (a display transform
  is not a local adjustment). The checkbox is disabled while an area layer
  is the edit target, showing the global state.

### Non-goals
- No soft-clip shoulder above code 685 (values clip to white — the classic
  video-range conversion) and no configurable black/white codes or gamma.
- No exact Rec.709 OETF (the user asked for a plain 2.2 gamma encode).
- No OpenCL port: it is a single 65536-entry LUT lookup at the very end of
  the CPU stage — negligible next to the GPU pass it follows.

## 3. Math (`ccr_processor.apply_cineon_to_rec709`)

Input 16-bit value ≡ 10-bit code `c = v / 65535 * 1023`:

```
off = 10^((95 − 685) · 0.002 / 0.6)                    # ≈ 0.0108
lin = (10^((c − 685) · 0.002 / 0.6) − off) / (1 − off) # scene-linear, 685 → 1.0
out = clip(lin, 0, 1)^(1/2.2) · 65535
```

0.002 = log₁₀ density per code value, 0.6 = film negative gamma — the
standard Kodak constants. Implemented as a cached 65536-entry uint16 LUT
(`lut[img16]`), per-channel (grayscale images pass through unchanged in
shape). Codes ≤ 95 → 0; codes ≥ 685 → 65535; monotonic.

## 4. Integration

1. **Pipeline** (`ccr_image.apply_adjustments`): after the slider pass,
   gamma, curves, area compositing and the B&W collapse — immediately
   before `return adjusted`:
   `if s.get("cineon_log"): adjusted = apply_cineon_to_rec709(adjusted)`.
   The hi-res worker snapshots the settings dict and `_hires_signature`
   hashes its items, so zoom detail re-renders on toggle automatically.
2. **UI** (`sliders_panel`): checkbox at the bottom of the Channel Levels
   section. Toggle = a discrete single-undo edit on the **global** dict
   (mirrors `_on_curve_edit_finished`: `push_undo_state`, write/pop the
   key, `update_thumbnail_and_preview`, `update_preview`, thumb refresh).
3. **Rebuild paths**: `on_slider_changed` / `_on_curve_changed` /
   `copy_adjustment_settings` rebuild the dict from sliders — a new
   `_attach_cineon` (sibling of `_attach_curves`) re-attaches the flag from
   the global dict, only when the active layer IS the global layer.
4. **Populate**: `_load_active_layer` sets the checkbox from the global
   dict (signals blocked) and disables it while an area layer is active.
5. **Paste**: strips `cineon_log` when pasting onto an area layer.
6. **Sync-to-All**: `"cineon_log"` joins the "channels" group keys; targets
   keep their own flag across the merge when that group is un-synced
   (mirrors the curves preservation).

## 5. Test plan (`tests/test_cineon_log.py`)

- LUT: everything at/below code 95 → 0; at/above 685 → 65535; monotonic;
  a mid-scale spot value matches the closed-form math.
- `apply_cineon_to_rec709`: shape/dtype preserved; neutral input stays
  neutral per channel.
- Pipeline: `CCRImage.apply_adjustments` with `{"cineon_log": True}` equals
  `apply_cineon_to_rec709` of the same call without the flag (final-stage
  property, no interleaving).
- Sync group: "channels" includes the key.
