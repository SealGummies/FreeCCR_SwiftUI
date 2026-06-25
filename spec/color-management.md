# Spec: Color Management — Export Color Space + Input ICC Profile

Status: REFINED v2
Owner: FreeCCR
Feature branch: `feature/color-management`

## 1. Summary

Two related color-management features:

1. **Export color space** — an "Color space" dropdown in the Export dialog with
   **sRGB** (default) and **ProPhoto RGB**. sRGB exports the current result
   unchanged (today's behaviour) but now **tags** it with an sRGB ICC profile.
   ProPhoto re-encodes the result into the ProPhoto RGB (ROMM) color space and
   **embeds the ProPhoto ICC profile** so a color-managed viewer interprets it
   correctly.

2. **Input ICC profile** — a global, persistent **File menu** action "Set Input
   ICC Profile…" / "Clear Input ICC Profile". The chosen `.icc`/`.icm` is copied
   into the app-data folder and remembered across sessions; it is applied to
   **every** imported image **at decode time, before negative conversion and
   adjustments** ("burn in"). It converts the decoded pixels from the assigned
   profile into the pipeline's working encoding (sRGB), so all downstream math is
   consistent.

### 1.1 Working-space framing (important honesty note)

FreeCCR's internal pipeline is **not** a characterized color space. RAW is
decoded `gamma=(1,1)`, no white balance; non-RAW files are read as-is with no
profile interpretation; the v0.2.3 negative inversion + look then runs on those
values. There is no ICC that describes this space.

The negative RAW decode is chosen at decode time (`_raw_color_postprocess_kwargs`
/ `read_image`). When an external input ICC will be burned in afterwards
(`_input_icc_will_apply()` — an input matrix profile is active), or a caller
asked for bare device RGB (IT8 profiling, `apply_input_icc=False`):
`output_color=ColorSpace.raw` with `no_auto_scale=True` keeps absolute sensor
values, and `read_image`'s uniform `*65535/white_level` scaling brings them to
full range, so the ICC + inversion see consistent values. Otherwise (the no-ICC
default decode of an unprofiled scan): `output_color=ColorSpace.Adobe` (Adobe RGB
— a defined, camera-independent working space) with `no_auto_scale=False` lets
rawpy auto-scale the decode to full range, and `read_image` skips its manual
white-level scaling for this path (re-scaling would blow highlights). Note rawpy
auto-scale also applies the camera's default (daylight) WB multipliers, so the
no-ICC default decode carries a per-channel cast the absolute-value path does
not. DNG is treated like any other RAW here — the input ICC applies to it as
well (no special-casing).

Consequence for both features: we treat the **on-screen / exported result** as
**sRGB-encoded display RGB** — which is exactly how every viewer already
interprets today's untagged output. Therefore:

- "Export to ProPhoto" is **not** a colorimetric conversion from a characterized
  source; it is a **re-encoding of the displayed sRGB result** into ProPhoto's
  primaries + gamma, with the ICC embedded so a color-managed viewer shows the
  *same colors*. The honest benefit is a **wider container** for downstream
  editing, not "more accurate" color. The spec/UI must not overclaim.
- "Input ICC" means: **assign** profile P to the decoded pixels, then **convert**
  P → sRGB working encoding. Most meaningful for already-encoded standard imports
  (TIFF/JPEG/PNG scans). For RAW it is applied to the linear camera-native decode
  as-is — an advanced/at-your-own-risk reinterpretation, documented as such.

## 2. Goals / Non-goals

### Goals
- Export dialog "Color space" dropdown: **sRGB** | **ProPhoto RGB** (extensible
  to Adobe RGB later via the same matrix machinery).
- ProPhoto export: correct 16-bit sRGB→ProPhoto transform + embedded ProPhoto ICC
  for **both** TIFF (16-bit) and JPEG (8-bit) outputs.
- sRGB export: pixel-identical to today, now with an embedded sRGB ICC tag.
- Resolution independence: the export transform runs at the chosen output
  resolution in the single pre-write chokepoint, so full-size and downsized
  exports are consistent.
- Input ICC: global, persistent (survives restart), copied into app data, shown
  by name in the menu, applied to every decode (preview, hi-res zoom, export) so
  it is automatically resolution-independent and lands before conversion.
- Setting/changing/clearing the input ICC re-decodes + re-converts the currently
  loaded images so the change takes effect immediately.
- **Zero new runtime dependencies.** Pure numpy color math + tifffile `iccprofile`
  + a small JPEG APP2 ICC injector. (Pillow's `ImageCms` is **8-bit-only for RGB**
  — useless for the 16-bit path — so we do not adopt it.)

### Non-goals
- No colorimetric calibration of the internal pipeline (it stays uncalibrated;
  we only re-encode the assumed-sRGB result on the way out).
- No support for **LUT-based / cLUT / CMYK** input ICC profiles in v1 — only
  **matrix-shaper** profiles (colorant + TRC tags). LUT profiles get a clear,
  non-fatal error. (A real CMM at 16-bit, e.g. `cmm-16bit`/`pylcms2`, would be a
  future dependency-adding enhancement.)
- No per-image input-ICC overrides (it is a single global setting, by explicit
  user request).
- No Adobe RGB / Rec.2020 export targets in v1 (architecture leaves room).
- No PNG export (the app has no PNG export path today).
- No rendering-intent UI; input-ICC conversion uses relative-colorimetric-style
  matrix math (matrix-shaper profiles have no intent tables anyway).

## 3. UX / Interaction

### 3.1 Export color space dropdown
- New `QComboBox` `self.colorspace_combo` in `ExportSettingsDialog._build_ui`,
  added to the `QFormLayout` immediately **after** the Format row
  (`export_dialog.py:131`). Items:
  - `"sRGB"` → data `"srgb"` (default, current index 0)
  - `"ProPhoto RGB (wide gamut)"` → data `"prophoto"`
- A small grey hint label under it when ProPhoto is selected (JPEG only):
  *"8-bit ProPhoto JPEG can band; prefer 16-bit TIFF for wide gamut."* Shown/
  hidden in `_on_format_changed`/a new `_on_colorspace_changed`.
- Persisted/restored via `QSettings` key `export/colorspace` in `_save_settings`
  (`:220-235`) and `_restore_settings` (`:189-218`), mirroring the other combos.
- Carried into `ExportPlan` (new field `output_colorspace: str = "srgb"`) in
  `_on_export_clicked` (`:391-399`).

### 3.2 Input ICC File-menu actions
In `main_window.py` `_create_menus`/wherever "Open"/"Export" actions are built
(near `:240`), add to the **File** menu (after the open/export group, before
Exit):
- **"Set Input ICC Profile…"** → `QFileDialog.getOpenFileName` filtered to
  `"ICC Profiles (*.icc *.icm);;All Files (*)"`. On accept: validate + copy the
  file into app data (see §5.2), persist its path, re-process loaded images, show
  a status hint with the profile's description name.
- **"Clear Input ICC Profile"** → removes the setting + working copy, re-processes
  loaded images. Disabled (greyed) when no profile is set.
- The "Set…" action text reflects the active profile, e.g.
  **"Input ICC: <name>…"** (the embedded `desc` or the filename) so the user can
  see at a glance what is active. Updated whenever it changes.
- On a parse failure / unsupported (LUT) profile, a `QMessageBox.warning`
  explains it and the setting is **not** changed.

### 3.3 Feedback when input ICC changes
Changing/clearing the profile triggers a (possibly slow) re-decode + re-convert
of all loaded images. Reuse the existing busy affordance: run it through the same
path the app already uses for batch reprocessing (a progress dialog is desirable
but a simple blocking reprocess with a wait cursor is acceptable for v1, matching
how Sync/Auto-frame already reprocess). Document this in the test plan as a manual
check.

## 4. Data model & persistence

### 4.1 Export color space
- `ExportPlan.output_colorspace: str` (`"srgb"` | `"prophoto"`). Pure transient
  plumbing; not catalog-persisted (it's an export-time choice), but the **default**
  is remembered in `QSettings` like the other export options.

### 4.2 Input ICC profile (global)
- **QSettings** (`"FreeCCR"/"FreeCCR"`), keys:
  - `import/input_icc_path` (str): absolute path to the **working copy** inside
    app data, or empty when none.
- **Working copy**: copied to `<APPDATA>/FreeCCR/input_profile.icc` (the same
  folder as `catalog.json`, via `catalog.default_catalog_path()`'s dir). Copying
  decouples us from the user moving/deleting the original.
- **Not** stored per-image in the catalog. It is a global pipeline setting by
  design. Documented caveat (intended behaviour): images converted under profile
  A, reopened after switching to B, are re-decoded under B. This matches "one
  remembered profile for the rest of the app's use".
- A new global accessor lives on the backend/singleton (e.g.
  `ccr_backend.input_icc` holding parsed transform state, see §6.3) so every
  decode can reach it without per-image plumbing.

## 5. Processing / math

All new color math lives in a new module **`src/core/color_management.py`**
(pure numpy + struct), imported by `ccr_processor.py` (export) and `ccr_image.py`
(input). Keeping it standalone keeps the hot processing files lean and makes it
unit-testable in isolation.

### 5.1 sRGB ↔ linear, and the combined sRGB→ProPhoto matrix

```python
# float64 math, values normalized to [0,1]
def srgb_decode(v):   # sRGB EOTF (encoded -> linear)
    a = 0.055
    return np.where(v <= 0.04045, v/12.92, ((v+a)/(1+a))**2.4)

def srgb_encode(x):   # linear -> sRGB (for the input-ICC target encode)
    a = 0.055
    return np.where(x <= 0.0031308, x*12.92, (1+a)*np.power(np.clip(x,0,1),1/2.4) - a)
```

Canonical matrices (Lindbloom / ninedegreesbelow / colour-science), float64:

```
M_SRGB2XYZ (D65) =
[0.4123908 0.3575843 0.1804808]
[0.2126390 0.7151687 0.0721923]
[0.0193308 0.1191948 0.9505322]

M_BRADFORD (D65->D50) =
[ 1.0478112 0.0228866 -0.0501270]
[ 0.0295424 0.9904844 -0.0170491]
[-0.0092345 0.0150436  0.7521316]

M_XYZ2PROPHOTO (D50) =
[ 1.3459433 -0.2556075 -0.0511118]
[-0.5445989  1.5081673  0.0205351]
[ 0.0000000  0.0000000  1.2118128]

M_SRGB2PROPHOTO = M_XYZ2PROPHOTO @ M_BRADFORD @ M_SRGB2XYZ   # precompute once
```

ProPhoto (ROMM) gamma encode — γ 1.8 with the 1/512 linear toe (slope 16):

```python
def romm_encode(x):           # linear ProPhoto -> ProPhoto-encoded, x in [0,1]
    Et = 1.0/512.0
    return np.where(x < Et, 16.0*x, np.power(np.clip(x,0,1), 1.0/1.8))
```

### 5.2 The export transform (`apply_export_colorspace`)

```python
def apply_export_colorspace(rgb_u16, target):  # rgb: HxWx3 uint16 RGB, sRGB-encoded
    """Return (out_u16, icc_bytes). For 'srgb' returns the input unchanged
    plus sRGB ICC bytes (pixel-identical to today). For 'prophoto' re-encodes."""
    if target == "srgb":
        return rgb_u16, SRGB_ICC_BYTES
    lin   = srgb_decode(rgb_u16.astype(np.float64)/65535.0)
    pro   = np.clip(lin @ M_SRGB2PROPHOTO.T, 0.0, 1.0)   # clip imaginary/overflow
    enc   = romm_encode(pro)
    out   = np.rint(enc*65535.0).astype(np.uint16)
    return out, PROPHOTO_ICC_BYTES
```

Notes:
- Done in float64; clip negatives (ProPhoto's huge gamut produces tiny negatives
  from rounding) and >1.
- For an **8-bit JPEG** ProPhoto export the transform runs on the 16-bit array
  **before** `to_8bit`, so the encode is computed at full precision and only the
  final container is 8-bit.

### 5.3 ICC profile synthesis (`build_matrix_shaper_icc`)

Self-contained, license-clean, ~80 lines. Builds a valid ICC v2.4 `mntr/RGB/XYZ`
matrix-shaper profile from colorants + a parametric TRC. **Verified working**:
the output embeds byte-identically via `tifffile(iccprofile=...)`, round-trips
through `TiffFile` tag 34675, and parses + builds a transform under lcms (Pillow
`ImageCms`).

Header: 128 bytes (`size, 'lcms', 0x02400000 v2.4, 'mntr','RGB ','XYZ ', 'acsp',
… , D50 PCS illuminant at offset 68`). Tags: `desc, wtpt(D50), rXYZ, gXYZ, bXYZ,
rTRC, gTRC, bTRC, cprt`. Tag types: `XYZ ` (`s15Fixed16` ×3), `para` parametric
curve **function type 3** (`Y=(aX+b)^g for X>=d; Y=cX for X<d`), `desc`
(textDescriptionType), `text` (copyright). 4-byte tag alignment; the three TRC
tags share one offset.

Two profiles, generated once at module load:
- **sRGB** (`SRGB_ICC_BYTES`): sRGB primaries colorants (D50-adapted), TRC
  para type 3 `g=2.4, a=1/1.055, b=0.055/1.055, c=1/12.92, d=0.04045`.
- **ProPhoto/ROMM** (`PROPHOTO_ICC_BYTES`): colorants = columns of
  `M_XYZ2PROPHOTO⁻¹`'s source (i.e. `M_PROPHOTO2XYZ` columns):
  `rXYZ=(0.7976749,0.2880402,0)`, `gXYZ=(0.1351917,0.7118741,0)`,
  `bXYZ=(0.0313534,0.0000857,0.8252100)`; TRC para type 3
  `g=1.8, a=1, b=0, c=0.0625, d=0.03125`. Named generically ("FreeCCR ROMM
  (ProPhoto)") — "ProPhoto" is a Kodak trademark; a self-generated profile avoids
  trademark + redistribution issues.

For sRGB colorants, derive from `M_SRGB2XYZ` then Bradford-adapt D65→D50, or use
the well-known D50-adapted sRGB colorant XYZs; store as constants with a comment.

### 5.4 Embedding on write (`write_export_image`)

The three `ccr_normalize_*` functions currently duplicate the
resize→jpg/tiff write block (`ccr_processor.py:975-996`, `:1184-1196`,
`:1281-1293`). **Refactor** them to call one shared helper:

```python
def write_export_image(ccr_image, rgb_u16, output_path, jpg_out, jpg_quality,
                       max_long_side, output_colorspace):
    if max_long_side:
        rgb_u16 = ccr_image.resize_image_to_max_pixel(rgb_u16, max_long_side)
    rgb_u16, icc = apply_export_colorspace(rgb_u16, output_colorspace)
    if jpg_out:
        out = os.path.splitext(output_path)[0] + ".jpg"
        img8 = cv2.cvtColor(to_8bit(rgb_u16), cv2.COLOR_RGB2BGR)
        ok, buf = cv2.imencode(".jpg", img8, [cv2.IMWRITE_JPEG_QUALITY, int(jpg_quality)])
        if ok: buf = inject_jpeg_icc(buf.tobytes(), icc)   # APP2 ICC_PROFILE
        # write buf via the existing unicode-safe raw write
    else:
        out = os.path.splitext(output_path)[0] + ".tiff"
        safe_tifffile_imwrite(out, rgb_u16, photometric="rgb",
                              compression="deflate", iccprofile=icc)
```

- `safe_tifffile_imwrite` already forwards `**kwargs`, so `photometric=` /
  `iccprofile=` need no signature change.
- `inject_jpeg_icc(jpeg_bytes, icc)` (in `color_management.py`): splits the ICC
  into ≤65519-byte chunks, builds `FFE2` APP2 segments each prefixed
  `b"ICC_PROFILE\\x00" + seq + count + chunk`, and inserts them right after the
  SOI (`FFD8`). **Verified**: Pillow reads the injected profile back identically.
- Unicode-safe JPEG write: encode→inject→write bytes via the same
  `open(path,'wb')` fallback `safe_cv2_imwrite` already uses (factor a tiny
  `_write_bytes_unicode(path, data)` helper, or extend `safe_cv2_imwrite` to take
  pre-encoded bytes).

This dedups three near-identical blocks and gives one color-managed chokepoint.

### 5.5 Input ICC engine (`InputProfile`)

Parse a matrix-shaper ICC into numpy and apply it to a 16-bit RGB array:

```python
class InputProfile:
    # parsed from the .icc: device->PCS via TRC linearization + colorant matrix
    #   linRGB = TRC_each(device/clip)        # 'curv'/'para' per channel
    #   XYZ_D50 = M_colorants @ linRGB        # columns = rXYZ/gXYZ/bXYZ
    #   linRGB_srgb = M_XYZ2SRGB_D50 @ XYZ_D50
    #   out = srgb_encode(clip(linRGB_srgb))  # back to sRGB-encoded working space
    @classmethod
    def from_bytes(cls, icc_bytes) -> "InputProfile": ...   # raises UnsupportedICCError on LUT/CMYK
    def apply(self, rgb_u16) -> np.ndarray: ...             # HxWx3 uint16 -> uint16
```

Details:
- Minimal ICC parser: read header (assert `acsp`, `RGB ` data space), tag table,
  require `rXYZ/gXYZ/bXYZ` + `rTRC/gTRC/bTRC`. If `A2B0`/`mft1`/`mft2`/`mAB `
  (LUT) tags are present and colorants/TRC absent → raise `UnsupportedICCError`.
- TRC parse: `curv` (count 0 → identity gamma 1.0; count 1 → gamma = u8Fixed8;
  count N → sampled LUT, interpolate) and `para` (function types 0–4). Build a
  256/1024-entry float LUT per channel (or closed-form) for speed.
- Colorant matrix `M_colorants` columns from the three XYZ tags. PCS is D50.
- Target = sRGB working encoding: precompute `M_XYZ2SRGB_D50` (sRGB matrix with a
  D50→D65 adaptation folded in, or simply `M_SRGB2XYZ_D65⁻¹ @ M_BRADFORD(D50→D65)`).
- `apply()`: float32, `device/65535 → TRC → @M.T → clip → srgb_encode → *65535 →
  uint16`. Resolution-independent point op; the parsed LUTs/matrix are cached on
  the instance.

## 6. Integration points

### 6.1 Export dropdown plumbing (thread `output_colorspace` through)
| Location | Change |
|---|---|
| `export_dialog.py:24-34` (`ExportPlan`) | add `output_colorspace: str = "srgb"` |
| `export_dialog.py:_build_ui` (~`:131`) | add `colorspace_combo` + hint |
| `export_dialog.py:_save/_restore_settings` | persist `export/colorspace` |
| `export_dialog.py:_on_export_clicked` (`:391`) | set `output_colorspace=` on plan |
| `image_preview.py:ExportItemsWorker.run` (`:3304`) | pass `output_colorspace=self.plan.output_colorspace` |
| `ccr_backend.py:export_items` (`:644`) | add param; forward to `export_image_by_index` |
| `ccr_backend.py:export_image_by_index` (`:601`) | add param; forward to all three `ccr_normalize_*` |
| `ccr_processor.py:ccr_normalize_with_reference/_bwpoint/_refparams` | add `output_colorspace="srgb"` kwarg; pass to `write_export_image` |

### 6.2 Export write refactor
- New `write_export_image(...)` in `ccr_processor.py` (or `color_management.py`),
  replacing the three duplicated resize+write blocks. Preserve current behaviour
  exactly for `output_colorspace="srgb"` **except** the added sRGB ICC tag.
- `apply_export_colorspace`, `inject_jpeg_icc`, `SRGB_ICC_BYTES`,
  `PROPHOTO_ICC_BYTES` from `color_management.py`.

### 6.3 Input ICC application (decode-time)
- **`CCRBackend`**: add `self.input_icc_path` + `self.input_profile`
  (`InputProfile | None`), loaded from `QSettings` at startup
  (`set_input_icc(path)`, `clear_input_icc()`, `_load_input_icc_from_settings()`).
  `set_input_icc` copies the file to app data, parses it (catching
  `UnsupportedICCError`), stores QSettings, and returns the profile description /
  raises for the UI to show.
- **`CCRImage.read_image`**: at the **single** return-point pre-resize, apply the
  global profile to the decoded uint16 RGB:
  - RAW branch: after white-level scaling, before `return rgb` (`ccr_image.py:345-349`).
  - Non-RAW branch: after RGB/uint16 normalization, before `return img` (`:428-435`).
  - Implementation: a tiny `self._apply_input_icc(arr)` that calls
    `ccr_backend.input_profile.apply(arr)` when set. (Import the singleton lazily
    to avoid a cycle; `ccr_image` already imports from `ccr_processor`, and the
    backend imports `ccr_image` — read the profile via a module-level getter to
    sidestep the cycle.)
  - Apply **before** `_apply_source_ops`+resize so slicing/zoom/export all inherit
    color-managed pixels. Since it's a per-pixel op the result is identical at any
    resolution.
- **Slice parent decode**: `ccr_backend.slice_image_by_index` (`:983`) shares a
  parent decode passed as `preloaded_img` (bypasses `read_image`). Ensure that
  shared decode also went through `read_image` (it does — the parent was loaded
  via `read_image`), so its pixels are already color-managed; the slice's own
  `read_image` calls (zoom/export, via `source_ops`) re-apply the same profile.
  Audit the exact decode source in `slice_image_by_index` and apply
  `_apply_input_icc` there if it decodes raw bytes directly.
- **Reprocess on change**: `set_input_icc`/`clear_input_icc` must, for every
  loaded image: `reload_image()` (re-decodes via `read_image` → re-applies ICC),
  then re-run its conversion replay (`_replay_conversion` via stored
  `conversion_inputs`) and `update_thumbnail_and_preview()`. Provide
  `ccr_backend.reprocess_all_for_input_icc_change()` and call it from the menu
  slots; refresh thumbnails + current preview afterward (mirror Auto-frame's
  finish path, `image_preview.py:3014-3023`).

### 6.4 Menu wiring (`main_window.py`)
- Build the two `QAction`s near the existing File-menu actions (~`:240`); store
  refs to update text/enabled state. Slots call the backend methods, show
  hints/warnings, and trigger the reprocess.

## 7. Files touched / added

- **add** `src/core/color_management.py` — matrices, `srgb_decode/encode`,
  `romm_encode`, `apply_export_colorspace`, `build_matrix_shaper_icc`,
  `SRGB_ICC_BYTES`, `PROPHOTO_ICC_BYTES`, `inject_jpeg_icc`, `InputProfile`,
  `UnsupportedICCError`.
- **edit** `src/widgets/export_dialog.py` — dropdown, persistence, plan field.
- **edit** `src/widgets/image_preview.py` — worker passes `output_colorspace`.
- **edit** `src/core/ccr_backend.py` — export plumbing; input-ICC state + load/
  set/clear/reprocess; slice-decode audit.
- **edit** `src/core/ccr_processor.py` — `write_export_image` refactor; thread
  `output_colorspace` into the three `ccr_normalize_*`.
- **edit** `src/core/ccr_image.py` — `_apply_input_icc` at the decode return
  points.
- **edit** `src/ui/main_window.py` — File-menu actions + slots.
- **add** `tests/test_color_management.py` — see §8.

## 8. Test plan

### Unit (`tests/test_color_management.py`, numpy-only, no Qt)
1. **sRGB passthrough**: `apply_export_colorspace(img,'srgb')` returns pixels
   unchanged and non-empty `SRGB_ICC_BYTES`.
2. **ProPhoto round-trip sanity**: a mid-grey sRGB value → ProPhoto → (decode via
   inverse matrices) ≈ original within tolerance; pure white (65535) stays at the
   ProPhoto max; black stays black; output dtype uint16, clipped `[0,65535]`.
3. **ROMM toe continuity**: `romm_encode` continuous at `x=1/512` (`16·Et ==
   Et^(1/1.8)`); monotonic.
4. **ICC validity**: `PROPHOTO_ICC_BYTES`/`SRGB_ICC_BYTES` start with a valid
   128-byte header (`acsp` at 36), parse via the in-house parser, and — when
   Pillow is importable — `ImageCms.getOpenProfile(BytesIO(bytes))` +
   `buildTransform` succeed. (Pillow check skipped if absent.)
5. **TIFF embed**: write a uint16 array with `iccprofile=PROPHOTO_ICC_BYTES`, read
   back with `tifffile`, assert tag 34675 bytes equal the profile.
6. **JPEG inject**: `inject_jpeg_icc` output decodes as a valid JPEG (cv2.imdecode
   not None) and, when Pillow present, `Image.open(...).info['icc_profile']`
   equals the injected bytes; multi-chunk path exercised with a >65519-byte dummy.
7. **InputProfile matrix-shaper**: build a known matrix-shaper ICC (reuse
   `build_matrix_shaper_icc` for, e.g., an Adobe-RGB-like profile), parse via
   `InputProfile.from_bytes`, apply to a test image; assert it changes pixels in
   the expected direction and preserves dtype/shape. Identity sRGB-in profile →
   ~unchanged within rounding.
8. **InputProfile rejects LUT**: a constructed (or fixture) cLUT/`A2B0` profile
   raises `UnsupportedICCError`.

### Integration / regression
9. **Default export unchanged**: with `output_colorspace="srgb"`, the written TIFF
   pixels are identical to a write through the old path (guard the refactor) — the
   only diff is the new ICC tag.
10. **Existing suite green**: `python tests/run_tests.py` / `pytest tests/ -v`.

### Manual
- Export a converted image as sRGB TIFF and ProPhoto TIFF; open both in a
  color-managed viewer (e.g. Photoshop/Affinity/Firefox) — colors match; ProPhoto
  file reports the ProPhoto/ROMM profile; sRGB reports sRGB.
- Export ProPhoto JPEG; confirm the viewer reads the embedded profile.
- File → Set Input ICC Profile…: pick a matrix-shaper `.icc`; menu shows its name;
  loaded images re-decode + re-convert and the look shifts accordingly; restart
  the app → the profile is still active. Clear it → reverts. Pick a LUT profile →
  friendly "unsupported profile type" warning, setting unchanged.
- Unicode export paths still work (JPEG inject + TIFF).

## 9. Refinement (v2) — resolved decisions

1. **No Pillow/lcms dependency.** Pillow `ImageCms` RGB transforms are 8-bit only
   (Pillow #880/#8007); using it would silently posterize the 16-bit pipeline.
   All color math is numpy; ICC bytes are synthesized; embedding uses tifffile +
   manual JPEG APP2. (Both mechanisms prototyped and verified in-repo.)
2. **Input ICC scope = matrix-shaper only** in v1; LUT/CMYK profiles raise a
   handled `UnsupportedICCError`. A 16-bit CMM (`cmm-16bit`/`pylcms2`) is the
   future path if arbitrary profiles are needed.
3. **Input ICC is global + persistent + copied into app data** (File menu), not
   per-image. Re-decoding loaded images on change keeps the session consistent.
   Reproducibility caveat (convert under A, reopen under B → B) is intended.
4. **sRGB export tags the file** (adds sRGB ICC) but is otherwise pixel-identical
   to today — verified by regression test #9.
5. **8-bit ProPhoto JPEG is allowed** (with a UI caution); the transform is
   computed at 16-bit precision before the 8-bit downconvert, and the ICC is
   embedded so it is at least correct, if not ideal.
6. **Single write chokepoint**: the three duplicated `ccr_normalize_*` write tails
   are refactored into `write_export_image`, which is the only place the output
   transform + embed happen — guaranteeing TIFF and JPEG, full and downsized, all
   color-managed identically.
7. **ProPhoto profile is self-generated and generically named** to avoid the
   Kodak "ProPhoto" trademark and any redistribution encumbrance; the UI label
   may still say "ProPhoto RGB" (descriptive use) while the embedded profile's
   internal `desc` is "FreeCCR ROMM (ProPhoto)".
8. **Decode-time hook for input ICC** lives in `read_image` (the single decode
   path shared by preview/zoom/export), guaranteeing resolution independence and
   that conversion/adjustments see color-managed pixels. The slice preloaded-decode
   path is audited to inherit the same.
9. **RAW caveat documented**: input ICC is applied to RAW's linear camera-native
   decode as-is; it is principled only for already-encoded standard imports. No
   special-casing in v1 beyond the documentation + the general matrix-shaper math.
```
