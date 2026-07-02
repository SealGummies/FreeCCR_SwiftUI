# Trichrome Merge Detail: Demosaic vs Single Photosite

> Status: refined (open questions resolved inline — see "Decisions" at the end).
> Extends spec/three-way-rgb-merge.md.

## Summary

A per-import choice of how each trichrome frame's colour channel is obtained
from its Bayer RAW:

- **Demosaic (full resolution)** — NEW, the DEFAULT: each source RAW is decoded
  through a normal linear demosaic and the frame's own channel is taken from
  the resulting RGB image. Full sensor resolution.
- **Single photosite (half resolution)** — the existing behaviour: the RAW
  Bayer mosaic is phase-sliced so only the wanted colour's photosites are read
  (no demosaic, guaranteed zero inter-channel crosstalk), at half-sensor
  resolution.

Selected via a dropdown in the **Settings → Color Management → Trichrome
capture** group, next to the merge checkbox. Monochrome sensors have no CFA, so
the choice is irrelevant for them (identical decode either way).

## Goals

- Dropdown "Merge detail" in the Trichrome capture group, following the group's
  staged-apply pattern (seeded at open, applied on Done via a MainWindow
  handler, persisted in QSettings under `import/rgb_merge_demosaic`).
- Default **Demosaic** (full resolution) — including for existing installs
  (the QSettings default is True).
- The choice is captured **per merged image at import** (`CCRImage.
  merge_demosaic`), like `merge_sources`: every later re-read (zoom hi-res,
  export, linear TIFF export, slice, duplicate) reproduces the same decode at
  the same canonical resolution, regardless of later toggling. Toggling affects
  the NEXT import only (same contract as the merge checkbox itself).
- The demosaic decode preserves the trichrome purity guarantee: **bilinear
  (LINEAR) demosaic interpolates each colour plane only from its own
  photosites** — no inter-channel mixing — so "each frame contributes only its
  own channel" still holds, now interpolated to full resolution.

## Non-Goals

- No per-image override UI; no re-merge of already-loaded images on toggle.
- No change to monochrome handling, triplet grouping, validation, or the
  sensor-type guards.
- No new demosaic algorithm choices (AHD etc. use cross-channel correlation,
  which would break the purity guarantee; LINEAR only).

## UX

In `_build_color_management_page`, inside the existing "Trichrome capture"
group, after the checkbox + muted text:

```
   Merge detail:  [ Demosaic (full resolution)            ▾ ]
                  [ Single photosite (half resolution)      ]
   <muted> Demosaic interpolates each frame's own channel from its own
           photosites (bilinear) at full sensor resolution. Single photosite
           reads only the raw colour sites — no interpolation at all — at half
           resolution. Applies to the next import.
```

- `self._combo_merge_detail`, entries with data `True` (demosaic, index 0) and
  `False` (photosite).
- Staged like the sibling toggles: seeded in `_init_toggles` from
  `ccr_backend.rgb_merge_demosaic`; `_apply_toggles` calls
  `self._mw.on_rgb_merge_demosaic_changed(bool)` only on change.
- MainWindow: `on_rgb_merge_demosaic_changed(demosaic)` sets the backend flag,
  persists `import/rgb_merge_demosaic`, and shows a transient hint ("…applies
  to the next import"). Startup restore next to `import/rgb_merge_mode`.

## Data Model

- `ccr_backend.rgb_merge_demosaic: bool = True` (in `_init`).
- `CCRImage.__init__(..., merge_demosaic: bool = True)` → `self.merge_demosaic`.
  Threaded everywhere `is_merged`/`merge_sources` already are: the merge loader
  (`_load_merged_triplets` passes the backend flag), `duplicate_images_by_
  indices`, `slice_image_by_index`.
- Merged images stay out of the catalog; nothing to persist per image.

## Processing

`ccr_merge._decode_frame_plane(path, frame_pos, preview, demosaic=False)`:

- **Bayer + demosaic**: after the existing sensor-type guards,
  `raw.postprocess(output_bps=16, no_auto_bright=True, gamma=(1,1),
  user_flip=0, demosaic_algorithm=rawpy.DemosaicAlgorithm.LINEAR,
  half_size=preview, use_camera_wb=False, use_auto_wb=False,
  output_color=rawpy.ColorSpace.raw, no_auto_scale=True,
  adjust_maximum_thr=0.0, four_color_rgb=False)` — byte-for-byte the kwargs the
  monochrome path already uses, so decode conventions (linear, absolute values,
  black-subtracted by libraw, camera-native) stay uniform. The frame's channel
  is `rgb[..., bayer_channel_indices(color_desc)[frame_pos]]`. Returned
  `sensor_full` is the full sensor size; `preview` gives a fast half-size
  decode exactly like monochrome.
- **Bayer + photosite**: unchanged (mosaic phase-slice).
- **Monochrome**: unchanged (the flag is ignored; there is no CFA).

`merge_raw_channels(sources, preview=False, demosaic=False)` forwards the flag;
the canonical full size becomes `sensor_full if (any_mono or demosaic) else
merged.shape` (a demosaiced Bayer merge, like a monochrome one, may decode a
half-size preview while its full/export resolution is the full sensor).
`combine_channels` and white-level scaling are untouched — both modes emit
absolute values that it scales by `65535/white_level`.

`CCRImage._read_merged` passes `demosaic=self.merge_demosaic`;
`CCRBackend._export_merged_linear` (linear TIFF export) passes
`demosaic=getattr(image_obj, "merge_demosaic", True)` — the linear export
reproduces the image's own import-time decode.

## Integration Points

| Where | Change |
|---|---|
| `ccr_merge.py` | `demosaic` param on `_decode_frame_plane` + `merge_raw_channels`; full-size rule; module docstring note. |
| `ccr_image.py` | Ctor kwarg + attribute; `_read_merged` forwards it. |
| `ccr_backend.py` | `_init` flag; loader passes it; duplicate + slice thread it; `_export_merged_linear` forwards it. |
| `settings_dialog.py` | Dropdown + muted text in the Trichrome group; staged seed/apply. |
| `main_window.py` | Startup restore; `on_rgb_merge_demosaic_changed` handler (persist + hint). |

## Edge Cases

- **Toggling with merged images loaded** → no effect on them (per-image
  capture); hint says "next import".
- **Mixed sessions** (some images imported under each mode) → each re-reads
  correctly with its own captured flag; resolutions differ per image, which the
  pipeline already supports (monochrome vs Bayer merges differ today).
- **Monochrome triplets** → identical output in both modes.
- **Old sessions / defaults** → missing QSettings key reads True (demosaic),
  per the requested default. Note this CHANGES the default behaviour of a
  fresh trichrome import from half-res to full-res; the photosite mode remains
  one dropdown away.

## Test Plan

`tests/test_trichrome_demosaic.py`:

1. **Real-RAW integration** (uses `example_raw/DSC07096.ARW` as all three
   sources; skipped if missing): `merge_raw_channels([arw]*3, demosaic=True)`
   returns a full-sensor-size uint16 (H, W, 3) with full_size == merged shape;
   photosite mode returns the half-size merge; demosaic dims ≈ 2× photosite
   dims; `preview=True` demosaic decode is half-size while full_size stays
   full-sensor.
2. **Threading**: with `merge_raw_channels` monkeypatched to capture kwargs —
   `CCRImage(..., merge_demosaic=False)._read_merged` passes False; default
   ctor passes True; `_export_merged_linear` forwards the image's flag;
   duplicates inherit the source's flag.
3. **Loader**: `_load_merged_triplets` constructs children with the backend
   flag (monkeypatched CCRImage or attribute check on the loaded result).
4. **Settings dialog**: combo defaults to demosaic; staged apply calls the
   MainWindow handler only on change; QSettings round-trip.

## Decisions

- **LINEAR demosaic only**: bilinear interpolates each colour plane from its
  own sites — the only algorithm that keeps the "no inter-channel crosstalk"
  guarantee while adding resolution. Matches the mono/positive decode choice
  already in the codebase.
- **Per-image capture, not live-global**: a live flag would make zoom/export
  re-reads change resolution (and content) under an already-loaded image;
  capture-at-import mirrors `merge_sources` and keeps every replay identical.
- **Default demosaic** (explicit request): full-resolution RGB out of the box;
  the crosstalk-purist photosite mode is the opt-in.
- **Dropdown, not a checkbox**: the two options are modes with meaningfully
  different trade-offs (resolution vs zero-interpolation), not an on/off.
