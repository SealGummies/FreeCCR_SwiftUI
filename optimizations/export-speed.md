# Export speed (~14 s/image)

## Where the time goes

Export routes `ccr_backend.export_image_by_index` → one of
`ccr_processor.ccr_normalize_with_reference` / `_with_bwpoint` / `_with_refparams`,
which all do, **at full resolution**:

1. **RAW decode** — `read_image(preview=False)` → `rawpy.postprocess(half_size=False)`
   (`ccr_image.py:243`, decode at `:298`/`:314`). Full-res demosaic of a 24–60 MP
   sensor. Typically **2–5 s**.
2. **Per-channel black/white-point normalize ("BWPN")** — `ccr_processor.py:678-706`.
   ~10 full-res float32 ops. ~0.3–1 s.
3. **Optical-density alignment ("ODAI")** — `:707-734`. `np.log10`, `np.power` at
   full res. ~0.5–1.5 s.
4. **Post-invert "look"** (saturation curve + shadow warmth) — `:759-816`. ~15
   full-res float32 ops including several transcendentals (`np.power`, `np.exp`,
   3× `np.dot` luminance). **~2–5 s** — the largest CPU block.
5. **User adjustments** — `apply_adjustments` at full res (`:828`). OpenCL when
   available; **CPU fallback** when band-feather is active
   (`adjust_image_opencl` → numpy), which is slow at full res.
6. **Full-res fine-rotation `warpAffine`** (`:905-920`) — ~0.3–1 s when a fine
   rotation is set.
7. **TIFF write** — `compression='deflate'` 16-bit (`:959`). Deflate of a 24 MP
   16-bit image is **~1–2 s**.
8. **`max_long_side` downscale — applied LAST** (`:943`). So for a downsized
   export, steps 1–7 all run at full res and most of the result is then thrown
   away.

> The functions already `print` per-step timings — run one export and read the
> log to confirm the split on your hardware/files.

The same patterns repeat in all three pipelines:
`read_image(preview=False)` at `ccr_processor.py:614 / :1006 / :1186`; late resize
at `:943 / :1153 / :1253`; `gc.collect()` at `:822 / :980 / :1048 / :1085 / :1166 / :1266`;
`deflate` TIFF at `:959 / :1163 / :1263`.

## [DONE] Process downsized exports at the output resolution

**Biggest lever for any non-full-size export.** New helper
`ccr_processor._load_export_source(ccr_image, output_path, max_long_side)` decides
the working resolution up front:

- Downsized (`max_long_side` set) **and no user crop** → decode small and resize
  to the target before any processing. RAW decodes at **half size**
  (`preview=True`) when the half-size long edge is still ≥ `max_long_side` (so we
  never under-deliver resolution); the reader then resizes to the target.
- A user **crop** → full decode (the crop changes which region maps to
  `max_long_side`; the existing late resize handles that case).
- **Full-size** export (`max_long_side=None`) and the in-app processing path
  (`output_path=None`) → unchanged.

Effect: for a 6000 px RAW exported at 2048 px, decode drops from full to half
(~3–4×) **and** every per-pixel block (BWPN/ODAI/look/adjustments/warp/watermark)
now runs on ~2048 px instead of 6000 px (~9× fewer pixels). Expected **3–5×**
faster downsized export. Output dimensions are unchanged (verified in
`tests/test_export.py`); pixels are visually identical (the conversion params
still come from the 1080 px reference, so the look is resolution-independent, and
this actually matches the *preview* decode — which is also half-size — more
closely than the old full-decode export did).

Wired at `ccr_processor.py` (all three pipelines) + `tests/test_export.py`
(full / downsized / cropped-downsized / JPEG dimensions, and the helper's
decode-size decisions).

## [DONE] Remove a per-export `np.power(x, 1.0)` no-op

`gamma_corrected = np.power(np.clip(rgb_norm, 0,1), 1.0)` raised to the power 1.0
— an identity that still cost a full-res transcendental pass. Replaced with the
clip alone in the reference and bwpoint pipelines. Bit-identical output.

## [DONE] Area compositing skips no-op layers

`apply_area_layers` now filters to areas that are enabled **and** have non-empty
settings before allocating any full-res mask/delta — see
[area-editing.md](area-editing.md).

## [PROPOSED] Drop / consolidate `gc.collect()`

Each export calls `gc.collect()` several times (`:822, :980, :1048, :1085, :1166,
:1266`). `gc.collect()` is a stop-the-world full-heap walk; with up to 4 parallel
export threads (`ccr_backend.py:688`) these serialize on the GIL and pause every
thread. The big arrays are already freed promptly by the explicit `del`s
(refcounting) — `gc.collect` only reclaims *reference cycles*, of which these
straight-line array pipelines have none.

Proposal: remove the intra-function `gc.collect()` calls (optionally keep a
single one at the end of a whole batch in `export_items`). Low risk; measure peak
RSS on a 4-thread batch of large RAWs to confirm memory stays bounded (it should,
since `del` already drives it).

## [PROPOSED] TIFF compression choice

`compression='deflate'` (zlib) is the slowest common TIFF codec. Options:
- `compression='lzw'` — ~2–3× faster to write, files a bit larger.
- `compression=None` — fastest, largest files.
- Expose it in the export dialog (alongside JPEG quality).

Risk: none (lossless either way); it's a speed/size trade the user should pick.

## [PROPOSED] Move the conversion look + adjustments onto OpenCL

For **full-size** exports the decode + the CPU NumPy look/normalize (steps 2–4)
dominate. `adjust_image` already has an OpenCL kernel (`ccr_processor.py:67-517`);
the inversion/look math (BWPN/ODAI/look) is equally pointwise and a strong GPU
candidate. Expected multiple-× on the per-pixel blocks where a GPU is present,
with the existing CPU path as fallback (mirror the `_initialize_opencl()` /
try-except pattern). Larger change — prototype against the printed timings.

Cheaper CPU half-measures if a GPU port is deferred:
- The look section (`:759-816`) allocates ~20 full-res temporaries. Fuse passes
  and reuse buffers (`out=` on every op, as BWPN already does) to cut allocator
  and memory-bandwidth pressure.
- Compute luminance once where the two `np.dot`s use the same input.

## [PROPOSED] Avoid the CPU fallback for band-feather at export

`adjust_image_opencl` falls back to pure NumPy when band-feather is active
(`ccr_processor.py` band path). At full res that fallback is slow. Either port
the band-feather blur to the GPU path or apply it after a downscale. Only matters
when the image uses per-band feather.

## Quick reference — expected impact

| Item | Status | Helps | Rough impact |
|---|---|---|---|
| Process downsized at output res | DONE | downsized exports | 3–5× |
| `np.power(x,1.0)` removal | DONE | all | small |
| Skip no-op area layers | DONE | images with empty areas | small–med |
| Drop `gc.collect()` | PROPOSED | parallel batches | small–med |
| TIFF `lzw`/none | PROPOSED | all (TIFF) | 0.5–1.5 s/img |
| OpenCL look/normalize | PROPOSED | full-size exports | large |
| GPU band-feather | PROPOSED | band-feather users | medium |
