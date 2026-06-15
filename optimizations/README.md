# FreeCCR — Optimization Notes

Performance findings and proposals, with concrete file:line references, expected
impact, risk, and status. Each item is tagged:

- **[DONE]** — implemented in this branch (with tests).
- **[PROPOSED]** — analyzed and specified, not yet implemented (needs profiling
  on a real RAW / a decision on a quality–speed trade-off).

Measure before/after with the per-step `print(... time ...)` timings the export
functions already emit (`ccr_processor.ccr_normalize_with_*`) and the
`Export completed for … in N.NNs` line in `ccr_backend.export_items`.

## Index
- [export-speed.md](export-speed.md) — the ~14 s/image export, where the time
  goes, and how to cut it (the big lever: stop processing at full resolution for
  downsized exports).
- [area-editing.md](area-editing.md) — optimizations for the area-editing
  (local masked adjustment layers) feature.

## TL;DR — highest-impact items
1. **[DONE] Downsized exports process at the output resolution.** Previously the
   whole pipeline ran at full res and the result was downscaled only at the very
   end. `_load_export_source` now decodes small (RAW half-size when safe) and
   resizes to `max_long_side` up front. For a downsized export this is the
   dominant win (often 3–5×). Full-size exports are unchanged.
2. **[PROPOSED] Move the conversion "look" + slider passes onto OpenCL.** The
   per-pixel inversion/look math (`ccr_normalize_with_*`) is pure CPU NumPy with
   several full-res transcendentals; it dominates full-resolution export time.
3. **[PROPOSED] Drop the per-export `gc.collect()` calls.** Multiple full-heap
   collections per image, run on 4 export threads at once — GIL/stop-the-world
   contention for no correctness benefit (explicit `del`s already free memory).
4. **[DONE] Skip no-op work**: removed a `np.power(x, 1.0)` identity pass per
   export; area compositing now skips enabled-but-unadjusted areas.
