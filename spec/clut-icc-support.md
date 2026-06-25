# Spec: cLUT (LUT-based / A2B) ICC Support — Consume + Generate

Status: IMPLEMENTED (branch `feature/clut-icc-support`)
Owner: FreeCCR
Feature branch: `feature/clut-icc-support`

## 1. Summary

Extend FreeCCR's in-process ICC handling (`src/core/color_management.py`) from
matrix-shaper-only to also support **LUT-based (cLUT / A2B) device→PCS ICC
profiles**, in two directions:

- **CONSUME.** `InputProfile.from_bytes` currently *rejects* any profile that
  lacks `rXYZ`/`gXYZ`/`bXYZ` + `rTRC`/`gTRC`/`bTRC` (raising `UnsupportedICCError`
  — see `from_bytes` lines 405–410). A user's real LUT-based `.icm` scanner/camera
  profile is therefore unusable. We add a parser for the `A2B0`/`A2B1` device→PCS
  tags in all three ICC encodings (`mft1` lut8, `mft2` lut16, `mAB ` lutAToB),
  an N-D CLUT interpolator (tetrahedral for 3-channel input), PCS = XYZ **or**
  Lab handling, and a final PCS-XYZ(D50)→**linear Adobe RGB** step that exactly
  mirrors the matrix-shaper apply path (`InputProfile.apply`, lines 434–448) so
  the negative inversion downstream keeps reading consistent linear data.

- **GENERATE.** Add `build_clut_icc(...)` to `color_management` (mirroring
  `build_matrix_shaper_icc`) writing a valid `mft2` (lut16) RGB→XYZ cLUT, and
  extend `it8_profile.build_camera_icc` with a `mode` so the IT8 wizard can offer
  **"cLUT (higher accuracy)"** alongside the existing **"3×3 matrix"**. The cLUT
  fit is the existing well-behaved 3×3 matrix *plus a smooth scattered-data
  residual* sampled onto the grid nodes (RBF in a perceptually-even space,
  regularised, extrapolated outside the patch hull), which removes the 3×3's
  saturated-corner residuals while staying monotone and artefact-free.

Everything is **pure numpy + struct** (no Pillow / lcms / scipy / colour-science),
consistent with `color_management.py`'s module contract (lines 1–23) and the IT8
feature (spec/it8-camera-profile.md §1, §10.1).

### Why this fits FreeCCR exactly
- The CONSUME path slots into the *same* `InputProfile` apply contract: a parsed
  cLUT still emits **uint16 linear Adobe RGB** (combined through
  `M_XYZ_D50_2_ADOBE`, no sRGB OETF), so `_apply_input_icc`/`read_image` and the
  density inversion are untouched (CLAUDE.md negative-inversion note;
  `M_XYZ_D50_2_ADOBE` comment, lines 105–110).
- The decode space is already correct: `read_image` selects the canonical
  camera-native raw decode (`no_icc_default=False` ⇒ `output_color=raw`,
  `gamma=(1,1)`, `no_auto_scale=True` + manual white-level scaling) whenever an
  input ICC is active OR a bare-device profiling decode is requested
  (`ccr_image.py` lines 459–504; spec/it8-camera-profile.md §12.5). A cLUT ICC
  consumes and is fitted on this **identical** device RGB — matrix and cLUT ICC
  share one decode, no new decode plumbing.
- The GENERATE path reuses the IT8 sampling + fit machinery (`sample_patches`,
  `fit_camera_matrix`, `xyz_to_lab`/`delta_e_2000`) and only adds a residual-LUT
  builder and a second ICC writer.

## 2. Goals / Non-goals

### Goals
- Parse `A2B0` (fallback `A2B1`) in `lut8Type`/`lut16Type`/`lutAToBType`.
- N-channel input curves → CLUT interpolation (**tetrahedral** for 3-D, trilinear
  fallback) → output curves → (mAB) matrix + M-curves, in the ICC-defined order.
- PCS XYZ (`'XYZ '`) and PCS Lab (`'Lab '`) including the v2 legacy vs v4 Lab
  encodings; PCS white = D50.
- Final PCS-XYZ(D50) → **linear Adobe RGB** uint16, byte-for-byte consistent with
  the matrix-shaper apply (reuse `M_XYZ_D50_2_ADOBE`).
- `InputProfile.from_bytes` **dispatches** matrix-shaper vs A2B and stores the
  parsed representation; `apply()` branches. **Matrix-shaper path stays
  byte-identical** (regression-pinned).
- `build_clut_icc(...)` writes a valid ICC v2.4 `mft2` RGB→XYZ cLUT.
- `it8_profile.build_camera_icc(fit, desc, *, mode='matrix'|'clut', grid=...)`;
  the wizard Step 4/5 offers a matrix-vs-cLUT choice.
- A residual-corrected cLUT fit (3×3 base + RBF residual) with graceful
  extrapolation, monotone and artefact-free.
- Vectorised numpy interpolation over the **native grid** (no 65536³ blow-up).
- Tests: parse each of mft1/mft2/mAB; tetra-interp vs reference; PCS XYZ & Lab;
  reject CMYK/unsupported; generate→reparse→apply round-trip recovers patches in
  tight ΔE and beats 3×3 on a non-linear generator; the user's real `.icm` parses.

### Non-goals
- **No PCS→device (B2A) application.** We only consume `A2B*` (device→PCS); FreeCCR
  never needs the inverse for an *input* profile.
- **No CMYK / >4-channel input, no non-RGB device space.** Reject with
  `UnsupportedICCError` (the `RGB ` data-space guard already exists, `from_bytes`
  lines 403–404; extend the message).
- **No multi-intent divergence at apply.** We read `A2B0` and fall back across
  `A2B0→A2B1` for availability only; we do not implement per-intent selection UI.
- **No DCP here.** DCP is a separate spec; this spec only notes the shared decode.
- **No floating-point `mAB` CLUT precision beyond what ICC stores** (uint8/uint16
  grid entries); the generated profile uses `mft2` 16-bit entries.
- **No change to export / output color management** (`apply_export_colorspace`,
  matrix-shaper `build_matrix_shaper_icc`, `SRGB_ICC_BYTES`, `PROPHOTO_ICC_BYTES`
  untouched).
- No GPU path; a per-decode CLUT gather is a one-time cost per image at preview /
  hi-res / export resolution.

## 3. Background (researched)

### 3.1 ICC LUT-based device→PCS tags
An ICC profile stores its transforms in tags found via the tag table parsed by
`_read_tag_table` (lines 322–335). Matrix-shaper uses `rXYZ/gXYZ/bXYZ` +
`rTRC/gTRC/bTRC`. A **LUT-based** profile instead stores a device→PCS transform
under the rendering-intent tags:
- `A2B0` — perceptual, `A2B1` — relative colorimetric, `A2B2` — saturation.

Each `A2B*` tag is one of three element types, identified by its first 4 bytes:
- `mft1` — **lut8Type** (8-bit tables, ICC v2).
- `mft2` — **lut16Type** (16-bit tables, ICC v2).
- `mAB ` — **lutAToBType** (ICC v4, the flexible A→B with optional curves +
  matrix + CLUT, offset-addressed).

PCS is given by header bytes 20–24 (`'XYZ '` or `'Lab '`, read like
`build_matrix_shaper_icc` writes at offset 20). The connection white is D50
(header PCS illuminant, offset 68, already used by the writer).

### 3.2 lut8Type (`mft1`) byte layout
Big-endian, offsets relative to the tag's start `off`:
```
off+0   : 'mft1' (4 bytes)
off+4   : 0 (reserved, 4 bytes)
off+8   : i  = number of Input  channels  (u8)
off+9   : o  = number of Output channels  (u8)
off+10  : g  = CLUT grid points per axis   (u8)
off+11  : 0 (pad, u8)
off+12  : e1..e9 = 3x3 s15Fixed16 matrix (9 * 4 = 36 bytes)  [applied to XYZ
          PCS only; identity in practice for device->PCS RGB]
off+48  : Input  tables : i * 256  u8 entries   (256 per input channel, fixed)
        : CLUT          : g^i * o  u8 entries   (output-channel-fastest)
        : Output tables : o * 256  u8 entries   (256 per output channel, fixed)
```
lut8 input/output tables are **fixed 256 entries** per channel. Device and PCS
values are u8/255 in [0,1]; for PCS XYZ the stored value is scaled so 1.0 ==
1.0 + 32767/32768 (the ICC "XYZ number" max ≈ 1.99997) — i.e. PCS-side XYZ is
encoded as `value/255 * (1 + 32767/32768)` for `mft1`/`mft2`. For PCS Lab in
`mft1`/`mft2` (legacy v2 encoding) L ∈ [0,100] maps to [0,255]/[0,65535]
(see §3.5).

### 3.3 lut16Type (`mft2`) byte layout
```
off+0   : 'mft2'
off+4   : 0
off+8   : i  (u8)  number of input channels
off+9   : o  (u8)  number of output channels
off+10  : g  (u8)  grid points per axis
off+11  : 0  (pad)
off+12  : 3x3 s15Fixed16 matrix (36 bytes)        [XYZ PCS only]
off+48  : n  = input  table entries per channel (u16)
off+50  : m  = output table entries per channel (u16)
off+52  : Input  tables : i * n  u16 entries
        : CLUT          : g^i * o  u16 entries  (output-channel-fastest)
        : Output tables : o * m  u16 entries
```
`mft2` is `mft1`'s 16-bit sibling with **variable** input/output table lengths
(`n`,`m`). u16 values are `/65535` in [0,1]; PCS XYZ uses the same
`*(1+32767/32768)` factor; PCS Lab uses the v2 legacy 16-bit encoding (§3.5).
This is the container we **generate** (§5.6) — simplest valid 3-D cLUT.

### 3.4 lutAToBType (`mAB `) byte layout
ICC v4, offset-addressed; any of the stages may be absent (offset 0 ⇒ skip).
```
off+0   : 'mAB '
off+4   : 0
off+8   : i (u8) number of input  channels
off+9   : o (u8) number of output channels
off+10  : 0 (2 bytes pad)
off+12  : off_B    (u32)  -> "B" curves   (o curves)
off+16  : off_mat  (u32)  -> matrix (3x4: 9 s15f16 + 3 s15f16 offset = 48 bytes)
off+20  : off_M    (u32)  -> "M" curves   (o curves)
off+24  : off_CLUT (u32)  -> CLUT element
off+28  : off_A    (u32)  -> "A" curves   (i curves)
```
**Processing order for A→B (device→PCS)** is fixed by ICC as:
`A curves → CLUT → M curves → matrix → B curves`.
- **A curves**: `i` curves, applied to the device input *before* the CLUT.
- **CLUT element** at `off_CLUT`: 16 bytes of grid points (`gridPoints[16]` u8,
  one per input channel — only first `i` used), then 1 byte precision
  (`1`=u8, `2`=u16), 3 pad bytes, then `prod(gridPoints)*o` entries
  (u8 or u16), output-channel-fastest.
- **M curves**: `o` curves applied after the CLUT, before the matrix.
- **matrix**: 3×4 (a 3×3 plus a 3-vector offset), applied to the 3 M-curve
  outputs: `y = Mx + offset`.
- **B curves**: `o` curves applied last (closest to the PCS).
Each curve is a standalone `curv`/`para` element (same encodings
`_parse_trc_to_lut` already handles, lines 343–366) padded to a 4-byte boundary.
For a device→PCS RGB→XYZ profile, `o=3`. When matrix/M/B are absent the chain is
just `A curves → CLUT`.

### 3.5 PCS encodings (XYZ vs Lab, v2 vs v4)
- **PCS XYZ** (`'XYZ '`): table/CLUT values decode to XYZ via the ICC XYZ-number
  range — max representable is `1 + 32767/32768 ≈ 1.99997`. So
  `XYZ = u16/65535 * (1 + 32767/32768)` (Y of the D50 white = 1.0). After the
  interpolation we have XYZ(D50) directly.
- **PCS Lab, v4 encoding** (profile version ≥ 4, header bytes 8–12): L ∈ [0,100],
  a,b ∈ [−128,127] mapped to the full u16 range as
  `L = u16/65535 * 100`, `a = u16/65535 * 255 − 128`, `b = u16/65535 * 255 − 128`.
- **PCS Lab, v2 legacy encoding** (in `mft1`/`mft2`, and v2 `mAB`): the historical
  encoding uses the `0xFF00/0xFFFF` convention —
  `L = u16/65535 * 100 * 65535/65280`, with a,b shifted by 128 the same way; for
  `mft1` (8-bit) `L = u8/255*100`, `a=u8−128`, `b=u8−128`. We branch on the
  profile version byte (header offset 8, top byte ≥ 4 ⇒ v4) to pick the Lab
  scale. Lab is then converted XYZ via `it8_profile.lab_to_xyz` against D50
  (white `_D50_W100`, scaled to Y=1).
PCS XYZ/Lab are **always D50** for an ICC PCS (no chromatic adaptation needed
before `M_XYZ_D50_2_ADOBE`).

### 3.6 The user's real profile
A LUT-based `.icm` scanner profile that today raises `UnsupportedICCError`.
Almost certainly `mft2` (the common v2 scanner output of Argyll/vendor tools),
3-channel RGB input, PCS Lab or XYZ. This is the canonical CONSUME test fixture
(§8 "real .icm parses").

### 3.7 Generate: why a residual cLUT beats a 3×3
A single 3×3 matrix is globally well-behaved (the IT8 fit, spec/it8 §3.4: avg
ΔE2000 ≈ 1–3) but cannot bend the saturated corners of the gamut where a real
sensor's response is non-linear — leaving systematic residuals on the most
saturated patches. A cLUT can store an arbitrary device→PCS map, so encoding
`matrix + smooth_residual` removes those residuals while the smooth/regularised
residual keeps the LUT monotone and free of the chroma-noise amplification a raw
per-node fit would introduce in sparsely-sampled regions.

## 4. Data model & files

### 4.1 `color_management.py` additions (parse)
New internal representation + dispatch in `InputProfile`:
```python
class UnsupportedICCError(Exception): ...   # existing; extend messages

# parsed cLUT representation (all numpy, precomputed for fast apply)
class _CLUT:
    n_in: int; n_out: int                   # 3, 3 for our use
    grid: tuple[int, ...]                    # per-axis gridPoints (len n_in)
    table: np.ndarray                        # (g0, g1, g2, n_out) float32 in PCS-decoded units (XYZ D50, Y=1)
    in_luts:  list[np.ndarray]               # n_in device->[0,1] LUTs (len 65536 each, for direct uint16 index)
    out_curves: list[np.ndarray] | None      # post-CLUT/output curves already folded into `table` when possible
    # mAB extras (None for mft1/mft2):
    mab_matrix: np.ndarray | None            # (3,4) M-curve-output -> ... ; None = identity/absent
    # PCS already decoded to XYZ(D50, Y=1) inside `table`, so apply() is uniform.

class InputProfile:
    # existing matrix-shaper fields:
    #   self._matrix (3,3 float32), self._luts (3 x 65536), self.description
    # new:
    #   self._kind: str   # 'matrix' | 'clut'
    #   self._clut: _CLUT | None
```
`from_bytes` dispatches: if matrix-shaper tags present → build today's
representation (`_kind='matrix'`, **unchanged bytes**); elif an `A2B*` tag present
→ parse cLUT (`_kind='clut'`); else raise `UnsupportedICCError`.

New module-private helpers (mirroring the existing `_parse_*`):
```python
def _parse_a2b(icc, off, pcs, version) -> _CLUT          # dispatch on element sig
def _parse_lut8 (icc, off, pcs, version) -> _CLUT        # 'mft1'
def _parse_lut16(icc, off, pcs, version) -> _CLUT        # 'mft2'
def _parse_lutAToB(icc, off, pcs, version) -> _CLUT      # 'mAB '
def _pcs_decode(values01, pcs, version) -> np.ndarray    # ->XYZ(D50,Y=1)
def _clut_interp_tetra(d01, clut) -> np.ndarray          # (...,3)->(...,3)
def _clut_interp_trilinear(d01, clut) -> np.ndarray      # fallback / >3D path
```

### 4.2 `color_management.py` additions (generate)
```python
def build_clut_icc(desc: str,
                   clut_xyz: np.ndarray,        # (g,g,g,3) XYZ D50 (Y=1), input-R-slowest
                   grid: int,
                   wtpt=D50_XYZ,
                   copyright_text=...) -> bytes
    # mft2 lut16: identity 3x3 matrix, identity input/output 16-bit tables,
    # the 3D CLUT carries everything. PCS = 'XYZ '.
```
Plus a small `_lut16_type(...)` byte builder alongside `_xyz_type`/`_para_type3`/
`_text_type` (lines 150–173).

### 4.3 `it8_profile.py` additions (fit the cLUT)
```python
def build_residual_clut(fit: CameraFit,
                        samples: Dict[str, PatchSample],
                        ref: IT8Reference,
                        grid: int = 17) -> np.ndarray
    # returns (grid,grid,grid,3) XYZ D50 (Y=1) cLUT = matrix(node) + RBF residual

def build_camera_icc(fit: CameraFit, desc: str, *,
                     mode: str = "matrix", grid: int = 17,
                     samples=None, ref=None,
                     copyright_text=...) -> bytes
    # mode='matrix' -> existing build_matrix_shaper_icc path (unchanged default)
    # mode='clut'   -> build_residual_clut(...) -> color_management.build_clut_icc(...)
```
`mode='matrix'` keeps the **current signature behaviour** (default arg), so
existing callers/tests are unaffected. `mode='clut'` requires `samples`+`ref`
(the residual needs per-patch device RGB + reference XYZ).

### 4.4 Files touched / added
- **edit** `src/core/color_management.py` — cLUT parse + dispatch in
  `InputProfile`; `_CLUT`; interpolators; `build_clut_icc` + `_lut16_type`.
- **edit** `src/core/it8_profile.py` — `build_residual_clut`, `build_camera_icc`
  `mode`/`grid` params, RBF helpers.
- **edit** `src/widgets/it8_profile_dialog.py` — Step 4/5 "3×3 matrix" vs
  "cLUT (higher accuracy)" selector; pass `mode`/`grid`/`samples`/`ref` to
  `build_camera_icc` (call site line 837).
- **add** `tests/test_clut_icc.py` — parse mft1/mft2/mAB, interp, PCS XYZ/Lab,
  reject CMYK, round-trip, non-linear-generator advantage, real `.icm` parse.
- **edit** `src/ui/main_window.py` — relax the "Unsupported ICC" guidance text
  (lines 401–405) now that cLUT is supported (CMYK / B2A-only still rejected).

No new dependency. No change to `build_matrix_shaper_icc`, the export path, or
`ccr_backend.set_input_icc`/`load_input_icc_from_storage` (a cLUT profile flows
through them unchanged — they only call `load_input_profile`/`from_bytes`).

## 5. Processing / math

### 5.1 CONSUME — apply pipeline (`InputProfile.apply`, cLUT branch)
Input is `HxWx3 uint16` camera-native device RGB (the §1 decode). Steps:
1. **Input curves**: `lin[...,c] = self._clut.in_luts[c][rgb_u16[...,c]]` — the
   in_luts are length-65536 so a uint16 indexes directly (identical pattern to
   the matrix path, lines 442–445). Gives device values in [0,1] in the CLUT's
   input domain.
2. **CLUT interpolation** → PCS-decoded XYZ(D50, Y=1) (the table is pre-decoded at
   parse time, §5.3), via **tetrahedral** interpolation (§5.2).
3. **mAB only**: if `mab_matrix`/M-curves/B-curves were present they are folded
   so the table already yields XYZ(D50) (§5.4) — apply() stays uniform.
4. **XYZ(D50) → linear Adobe RGB**: `adobe_lin = xyz @ M_XYZ_D50_2_ADOBE.T`
   (reuse the existing constant, lines 105–110), `clip[0,1]`, `*65535`, `rint`,
   `uint16` — byte-identical tail to the matrix path (lines 446–448). **No sRGB
   OETF**, per the negative-inversion contract.

Result: a cLUT input profile is indistinguishable downstream from a matrix one —
same linear-Adobe uint16 output that `_apply_input_icc` and the inversion expect.

### 5.2 Tetrahedral interpolation (3-D input)
Vectorise over all H·W pixels. For normalised device `d ∈ [0,1]^3` and per-axis
grid sizes `(g0,g1,g2)`:
- Scale to grid coords `t = d * (g−1)`; base index `i0 = floor(t)` clamped to
  `g−2`; fractional `f = t − i0 ∈ [0,1]^3` per axis.
- **Tetrahedral**: decompose the unit cube into 6 tetrahedra by the ordering of
  `(fr, fg, fb)`. The interpolated value is
  `V = V000 + sum over the 3 ordered steps of (w_k * (V_{next} − V_prev))`,
  where the three barycentric weights are the sorted differences of `f`. Concretely
  (Argyll/standard formulation), with `c000 = table[i0]` and the 7 other cube
  corners gathered, the 6 cases select which 2 mid-corners enter:
  - `fr≥fg≥fb`: `c000 + fr(c100−c000) + fg(c110−c100) + fb(c111−c110)`
  - `fr≥fb≥fg`: `c000 + fr(c100−c000) + fb(c101−c100) + fg(c111−c101)`
  - `fb≥fr≥fg`: `c000 + fb(c001−c000) + fr(c101−c001) + fg(c111−c101)`
  - `fb≥fg≥fr`: `c000 + fb(c001−c000) + fg(c011−c001) + fr(c111−c011)`
  - `fg≥fb≥fr`: `c000 + fg(c010−c000) + fb(c011−c010) + fr(c111−c011)`
  - `fg≥fr≥fb`: `c000 + fg(c010−c000) + fr(c110−c010) + fb(c111−c110)`
  Implemented as `np.select` over the 6 boolean masks, each branch a vectorised
  sum of gathered corner planes. Tetrahedral avoids the trilinear "diagonal
  desaturation" on neutrals and is the de-facto standard for ICC cLUT apply.
- **Gather**: flatten the table to `(g0*g1*g2, 3)`; compute the 8 corner linear
  indices once (`i0` and the +1 neighbours combined by strides), gather all 8
  corner planes as `(N,3)` arrays, then combine per the selected tetra case.
- **Trilinear fallback** (`_clut_interp_trilinear`): the full 8-corner weighted
  sum `Σ V_corner * Πaxis (f or 1−f)`. Used when `n_in != 3` (defensive) and as
  the reference oracle in tests.

### 5.3 PCS decode at parse time (`_pcs_decode`)
Decode the **whole CLUT once** at parse (not per pixel): convert the raw
u8/u16 grid entries to [0,1], then:
- PCS XYZ → `XYZ = v01 * (1 + 32767/32768)`.
- PCS Lab → Lab via the version-correct scale (§3.5), then
  `it8_profile.lab_to_xyz(lab) / 100` (D50, Y=1). (Import `lab_to_xyz` lazily to
  avoid a core import cycle, the same way `it8_profile` imports `color_management`.)
Store the decoded `(g0,g1,g2,3)` XYZ(D50,Y=1) float32 table on `_CLUT.table`.
This keeps `apply()` a pure gather+combine+matmul with no per-pixel branching on
PCS type.

### 5.4 mAB stage folding
For `mAB ` with `i=o=3`:
- Bake **A curves** into `in_luts` (length-65536, like `_parse_trc_to_lut`).
- After CLUT interpolation, apply **M curves** (3 LUTs), then **matrix** (3×4:
  `y = M3x3·x + offset`), then **B curves** (3 LUTs), all in PCS-connected order,
  *before* `_pcs_decode`. To keep `apply()` uniform we fold M/matrix/B into the
  **decoded table** at parse time: run every grid node value through
  `M-curve → matrix → B-curve → _pcs_decode` once, storing the result in
  `table`. Then `apply()` is identical for mft1/mft2/mAB. (Curves are 1-D LUTs;
  the matrix is a single `(g0*g1*g2,3) @ (3,3).T + offset`.)

### 5.5 Performance / memory (CONSUME)
- Table memory: a 3-D grid of `g` per axis is `g³ * 3 * 4` bytes. The
  prohibitive idea is a 65536-per-axis dense LUT (`2.8e14` entries) — impossible.
  Native-grid tables are tiny: `g=33` ⇒ ~431 KB, `g=17` ⇒ ~59 KB. We interpolate
  on the native grid; **never** materialise a per-channel 65536³ LUT.
- Per-decode cost: input curves are three 65536-LUT gathers (cheap, same as
  matrix path); tetra interp is ~8 gathers of `(N,3)` + a handful of fused
  adds, fully vectorised. At export full resolution this is one pass over the
  export array (float32), comparable to the existing matrix matmul — acceptable.
  In_luts are precomputed; the table is precomputed; nothing is rebuilt per call.

### 5.6 GENERATE — residual cLUT fit (`build_residual_clut`)
Inputs: the fitted `CameraFit.matrix M` (device-norm RGB[0,1] → XYZ D50, Y≈1),
the per-patch device RGB (`samples[id].rgb/65535`) and reference XYZ
(`ref.xyz(id)/100`). Output: `(grid,grid,grid,3)` XYZ(D50,Y=1) cLUT.
1. **Base.** For each grid node `n ∈ [0,1]^3` (device RGB), base prediction
   `B(n) = M @ n` — the well-behaved global map.
2. **Per-patch residual.** For each used patch `i` with device `d_i`, reference
   `X_i`: residual `r_i = X_i − M @ d_i` (in XYZ, Y=1). To keep the correction
   perceptually even (avoid over-weighting bright patches) fit the residual in a
   **scaled space**: work on `r_i` directly in XYZ but distance-weight in device
   RGB (smooth, monotone in the device domain we interpolate over).
3. **RBF scatter→grid (numpy, no scipy).** Thin-plate / Gaussian RBF over the
   device-RGB sample points `{d_i}`:
   - Kernel: Gaussian `φ(r) = exp(−(r/σ)²)` with `σ` ≈ mean nearest-neighbour
     device distance (smooth, compact-ish), OR thin-plate `φ(r)=r²·log r`. Default
     Gaussian (no singularity, easy regularisation).
   - Solve per XYZ channel: `(Φ + λI) w = r` where `Φ_jk = φ(||d_j − d_k||)`,
     `λ` a small smoothness/regularisation term (e.g. `λ = 1e-3 * trace(Φ)/N`).
     A low-order polynomial term (`1, R, G, B`) is appended for affine
     reproduction and unbiased extrapolation (the standard RBF+polynomial
     augmented system), solved with `np.linalg.lstsq`.
   - Evaluate at every grid node: `residual(n) = Σ_i w_i φ(||n − d_i||) + poly(n)`.
4. **Extrapolation guard (monotone, artefact-free).** Outside the convex hull of
   the patch device points the RBF can diverge. Mitigate by:
   - the polynomial term keeping far-field behaviour affine (bounded),
   - **fading the residual to zero** by a smooth factor of the distance from the
     patch hull / nearest sample (so nodes far outside fall back to the pure
     matrix `B(n)`), and
   - clamping `|residual|` to a fraction of the local node value.
   This guarantees the LUT degrades to the safe 3×3 in unsampled corners rather
   than ringing.
5. **Assemble + bounded-ringing safeguard.** `table[n] = clip(B(n) + corr(n), 0, ~2)`.
   The kernel is deliberately **broad** (σ ≈ 3× the mean nearest-neighbour device
   spacing) with a firm `λ`, which captures the systematic saturated-corner bias
   with little high-frequency overshoot — empirically both lower ΔE *and* far less
   non-monotonicity than a tight kernel. A safeguard then bounds *gross* ringing:
   if the correction introduces a per-axis reversal larger than `RINGING_TOL`
   (0.12 of white) where the linear base is non-decreasing, the whole correction is
   shrunk toward the base and rechecked (strength 0 = pure 3×3, always safe, so it
   terminates). **The cLUT is NOT guaranteed strictly monotone** — small reversals
   are the cost of the bias correction; the 3×3 matrix mode is the strictly-safe
   default. Tests pin the delivered bound (no reversal > `RINGING_TOL`) and that
   the cLUT still markedly beats the 3×3 on a non-affine camera.

### 5.7 GENERATE — grid size choice
- **9³ = 729 nodes** vs **17³ = 4913 nodes**. The IT8 fit has ~288 patches
  (fewer after clip rejection), so a finer grid is *interpolated*, not
  independently solved — the RBF is the smoother. `17³` gives ample resolution
  for smooth saturated-corner correction while the `mft2` table stays ~59 KB and
  the residual solve is a `~280×280` linear system (instant in numpy). **Default
  `grid=17`** (justified: smooth, small, more than enough nodes between
  sparse patches); `9` available as a lighter option. The grid is uniform per
  axis (identity input curves), so no extra grid-spacing metadata is needed.

### 5.8 GENERATE — ICC writer (`build_clut_icc` → `mft2`)
Choose **`mft2` (lut16)** over `mAB `: it is the simplest valid 3-D cLUT
container our existing `mft1/mft2/mAB` *reader* round-trips, needs no
offset-table bookkeeping, and a v2.4 header matches `build_matrix_shaper_icc`'s
proven header (lines 226–237). Layout written:
- header: copy `build_matrix_shaper_icc`'s header but **PCS = `'XYZ '`** (already
  what the matrix writer uses), device space `'RGB '`, class `mntr`, v2.4, D50
  PCS illuminant.
- tag table: `desc`, `wtpt`, `A2B0`, `cprt` (and optionally `A2B1`=`A2B0` alias
  via the dedup the writer already does, lines 206–219).
- `A2B0` element bytes (`mft2`):
  - `'mft2'`, 0, `i=3`, `o=3`, `g=grid`, pad.
  - 3×3 **identity** matrix (9 s15Fixed16) — XYZ PCS, identity so the CLUT carries
    everything.
  - `n=2`, `m=2` (minimal identity input/output tables: 2 entries `[0, 65535]`
    per channel = linear ramp; the CLUT alone defines the transform).
  - Input tables: `3 * 2` u16 = `[0,65535]×3`.
  - CLUT: `grid³ * 3` u16, **encoding XYZ → u16** via
    `u16 = round(clip(XYZ / (1+32767/32768), 0, 1) * 65535)`, **output-channel-
    fastest, input-R-slowest** (ICC order: last input channel varies fastest;
    we lay R slowest, G, B fastest to match `_parse_lut16`'s gather strides —
    documented and unit-pinned against the parser).
  - Output tables: `3 * 2` u16 = `[0,65535]×3`.
- Reuse the writer's 4-byte alignment + offset/dedup loop and the header size
  field. A `build_clut_icc` round-trips through `_parse_lut16`.

### 5.9 ID/dispatch correctness
`from_bytes` (extended):
```python
tags = _read_tag_table(icc)
if icc[16:20] != b'RGB ':
    raise UnsupportedICCError("input profile is not an RGB profile")
ms = all(t in tags for t in (b'rXYZ',b'gXYZ',b'bXYZ',b'rTRC',b'gTRC',b'bTRC'))
if ms:
    ... existing matrix-shaper path, UNCHANGED ...      # _kind='matrix'
a2b = next((t for t in (b'A2B0', b'A2B1') if t in tags), None)
if a2b is not None:
    pcs = icc[20:24]
    if pcs not in (b'XYZ ', b'Lab '):
        raise UnsupportedICCError(f"unsupported PCS {pcs!r}")
    version = icc[8]                                     # top byte of version
    clut = _parse_a2b(icc, tags[a2b][0], pcs, version)
    return cls._from_clut(clut, _read_desc(icc, tags))  # _kind='clut'
raise UnsupportedICCError("only RGB matrix-shaper or A2B cLUT profiles are supported (CMYK / B2A-only not)")
```
The `RGB ` guard already rejects CMYK device space. A profile with only `B2A*`
(no `A2B*`) and no matrix-shaper tags falls through to the final raise.

## 6. Integration points

### 6.1 `InputProfile` (color_management.py)
- `from_bytes` dispatches (§5.9). `__init__` unchanged for matrix; add
  `_from_clut` classmethod / `_kind`/`_clut` fields.
- `apply()` branches on `self._kind`; **matrix branch is the current code verbatim**
  (regression test asserts byte-identical output for a known matrix profile).
- `description` read unchanged via `_read_desc` (lines 421–432).

### 6.2 `ccr_backend` / `ccr_image` — no change
`set_input_icc`/`load_input_icc_from_storage` (lines 592–622) call
`load_input_profile`→`from_bytes`; a cLUT profile now parses instead of raising,
so it activates through the *exact same* path. `read_image`'s decode already
produces the camera-native device RGB a cLUT consumes (lines 459–504); `apply()`
emits linear Adobe RGB so `_apply_input_icc` (lines 271–287) and the inversion are
untouched. `_input_icc_will_apply` (lines 289–296) stays correct (any active
profile ⇒ camera-native decode).

### 6.3 IT8 wizard (it8_profile_dialog.py)
- Step 4/5 gains a profile-type selector (radio or combo): **"3×3 matrix"** /
  **"cLUT (higher accuracy)"** (default matrix to preserve current behaviour; a
  tooltip notes cLUT needs the full patch set and fixes saturated corners).
- The build call site (line 837) becomes
  `it8.build_camera_icc(self._fit, desc, mode=chosen_mode, grid=17,
   samples=self._all_samples, ref=self._ref)`.
- `saved_path`/`apply_now` flow (lines 845–846) and
  `main_window.create_camera_profile_from_it8` (lines 418–436) unchanged — they
  consume whatever ICC bytes were written.

### 6.4 Reuse, not reinvent
- ICC tag table / XYZ / curve parsing ⇒ `_read_tag_table`, `_parse_xyz`,
  `_parse_trc_to_lut`, `_eval_para` (lines 322–387) reused for mAB sub-curves.
- XYZ→Adobe ⇒ `M_XYZ_D50_2_ADOBE` (lines 105–110), shared tail with matrix apply.
- Lab→XYZ ⇒ `it8_profile.lab_to_xyz` (lines 647–657), D50 white `_D50_W100`.
- ICC header/tag writer scaffolding ⇒ `build_matrix_shaper_icc`'s alignment/
  dedup/offset loop (lines 199–237), `_s15f16`, `_xyz_type`, `_text_type`,
  `_desc_type` (lines 145–173).
- Fit + quality ⇒ `fit_camera_matrix`, `xyz_to_lab`, `delta_e_2000` (it8_profile).

## 7. UX

cLUT support is **invisible** on the CONSUME side: a user simply selects their
LUT-based `.icm` via the existing **File ▸ Set Input ICC Profile…** picker
(`set_input_icc_profile`, main_window lines 383–391) and it now loads instead of
showing "Unsupported ICC Profile". The warning text (lines 401–405) is relaxed to
"Only RGB matrix-shaper and cLUT (A2B) profiles are supported (CMYK and
B2A-only profiles are not)."

On the GENERATE side the only new control is the **profile-type selector** on IT8
Step 4/5 (§6.3): "3×3 matrix" (default) vs "cLUT (higher accuracy)". The fit
review (ΔE chip + worst-list, dialog lines 450–456) is shown for the chosen mode
so the user sees the cLUT's lower residuals before saving. No other UI change.

## 8. Test plan

Unit (`tests/test_clut_icc.py`), all pure (no Qt):
- **Parse mft1**: hand-built `mft1` bytes (i=o=3, small grid, identity tables,
  a known CLUT) parse; `_CLUT.grid`/`table` shapes and decoded XYZ correct.
- **Parse mft2**: same for 16-bit, variable `n`/`m` input/output tables; PCS XYZ.
- **Parse mAB**: hand-built `mAB ` with A-curves + CLUT + M-curves + 3×4 matrix +
  B-curves; assert the folded `table` equals a reference manual evaluation.
- **Tetra interp vs reference**: a CLUT that is an exact affine map `V = A·d`
  must be reproduced by `_clut_interp_tetra` to ~float precision (affine is exact
  for tetra); a random smooth CLUT matches `_clut_interp_trilinear` within the
  known tetra-vs-trilinear bound on a dense query set; neutral axis (`d=(t,t,t)`)
  stays neutral (no trilinear desaturation).
- **PCS XYZ and Lab**: a profile whose CLUT encodes a known XYZ map, and the same
  map via Lab (v2 and v4 encodings), both decode to the same XYZ(D50) within a
  tight tolerance; the version byte selects the right Lab scale.
- **Reject**: CMYK device space (`icc[16:20] != b'RGB '`) → `UnsupportedICCError`;
  a profile with only `B2A0` and no matrix-shaper tags → `UnsupportedICCError`;
  unsupported PCS → `UnsupportedICCError`.
- **Matrix path unchanged (regression)**: `build_matrix_shaper_icc(...)` →
  `from_bytes` → `apply` on a fixed image returns **byte-identical** output to the
  pre-change implementation (golden array).
- **Generate round-trip**: `build_residual_clut` → `build_clut_icc` →
  `InputProfile.from_bytes` → `apply` on the patch device RGB recovers the
  reference patches within a tight ΔE2000 (≪ the matrix-only avg), and a
  **synthetic non-linear generator** (a known smooth non-affine device→XYZ map)
  is recovered by the cLUT with markedly lower max/avg ΔE than the 3×3
  (`build_camera_icc(mode='clut')` beats `mode='matrix'`).
- **Bounded ringing / extrapolation**: the generated CLUT adds no per-axis reversal
  larger than `RINGING_TOL` where the linear base is non-decreasing
  (`_residual_monotone`); nodes far outside the patch hull equal the pure-matrix
  prediction (residual faded to ~0).
- **Build→reparse byte sanity**: `build_clut_icc` output has the `mft2` `A2B0`
  element, correct `i/o/g`, identity in/out tables, and the header is
  RGB/`acsp`/v2.4 with PCS `'XYZ '`.

Integration / real-data:
- **Real `.icm` parses**: the user's actual LUT-based scanner/camera `.icm`
  loads via `from_bytes` (no `UnsupportedICCError`); `apply` on a neutral patch
  yields a near-neutral linear-Adobe result and on a saturated patch a plausible
  saturated result (sanity, not exact — no reference for a third-party profile).
- **End-to-end (manual)**: set the real `.icm` as input profile via the menu →
  loaded scans reprocess (`reprocess_all_for_input_icc_change`) without error;
  IT8 wizard → choose cLUT → save → "apply now" → reprocess; the saved cLUT
  re-loads on restart through the existing storage path
  (`load_input_icc_from_storage`).
- **fit-space == apply-space** stays pinned: a cLUT generated from IT8 samples and
  then applied via the input-profile decode operates on the *same* camera-native
  device RGB (the existing IT8 decode-space test extended to the cLUT path).

## 9. Risks & mitigations
- **CLUT channel order / strides wrong** (the classic ICC footgun) → the writer
  and `_parse_lut16` are pinned against each other by the generate→reparse
  round-trip test; layout (R slowest, B fastest, output-channel grouping)
  documented in §5.8 and asserted.
- **Lab v2-vs-v4 encoding mismatch** → version-byte branch (§3.5) with explicit
  tests for both encodings against the same reference XYZ.
- **mAB stage-order error** (A→CLUT→M→matrix→B is non-obvious) → folded at parse
  with a hand-evaluated reference test (§8 "Parse mAB").
- **RBF over-fitting / ringing LUT** (chroma-noise amplification, the parked
  density-inversion failure mode) → a broad kernel + firm regularisation `λ` +
  polynomial term + hull-fade to the safe 3×3, and a bounded-ringing safeguard
  (shrink the correction toward the base until no per-axis reversal exceeds
  `RINGING_TOL`). Not strict monotonicity — matrix mode is the strictly-safe option
  (§5.6). Default `grid=17` keeps the LUT smooth.
- **Performance at full-res export** → native-grid interpolation only (never a
  65536³ LUT); precomputed in_luts + decoded table; one vectorised pass — on par
  with the matrix matmul (§5.5).
- **Breaking the matrix-shaper path** → dispatch only *adds* a branch; the matrix
  branch is verbatim and guarded by a byte-identical golden regression test.
- **Tetrahedral edge cases** (degenerate `f` ties) → ties resolved consistently by
  the `>=` ordering in the 6-way `np.select`; affine-exactness test catches a
  wrong branch.
