# FreeCCRNative — Phase 3 (M1: real image loading)

A real, runnable SwiftUI app (not a CLI PoC like `swiftui_poc/`) demonstrating
the architecture's interactive loop: a Metal preview canvas plus a sliders
panel, wired live to FreeCCR's **real** `core.ccr_backend`/`core.ccr_image`
call chain through an embedded CPython via PythonKit — the same
`ccr_backend.set_adjustment_by_index` → `ccr_image.update_thumbnail_and_preview`
path `sliders_panel.py` drives, not a hand-rolled reimplementation.

This is milestone **M1** of the Phase 3 plan
(`/Users/seal/.claude/plans/unified-foraging-candy.md`): open a real image
file and adjust it. Canvas pan/zoom, the rest of `sliders_panel.py`'s
controls, curve editing, thumbnails, dust removal, etc. are later milestones
(M2–M8) in that plan.

## Layout

- `Sources/PythonBridge/` — embedded-Python bootstrap + the single serial
  execution context every PythonKit call must go through.
  - `SerialPythonExecutor.swift`: a dedicated `Thread` with a 16MB stack, not
    `DispatchQueue`. **This matters**: `import numpy`'s OpenBLAS self-check
    segfaults with a stack overflow on GCD's pooled worker threads (~512KB
    stack) — confirmed via lldb while building this. Any future PythonKit
    integration point must reuse this executor rather than a fresh
    `DispatchQueue`.
  - `CoreBridge.swift`: `loadImage(path:)` decodes via the real
    `core.ccr_image.CCRImage` and registers it on `ccr_backend.images`;
    `adjustedPreview(handle:params:)` calls
    `ccr_backend.set_adjustment_by_index` (exactly what a Qt slider does) and
    reads back `_preview_np8` directly — not the `.thumbnail`/
    `.resized_preview` properties, which return `QPixmap` and are `None` in a
    no-Qt environment (see `src/core/ccr_image.py`'s `QT_AVAILABLE` guards).
- `Sources/FreeCCRNative/` — the SwiftUI app: `ContentView` (toolbar + canvas
  + sliders split view), `MetalCanvasView` (`NSViewRepresentable` around
  `MTKView`), `Shaders.metal` (trivial textured-quad blit), `PreviewState`
  (the Phase-2-flavored `ObservableObject` standing in for `ccr_backend`'s
  role in the Qt app, holding the loaded image's `ImageHandle`).

## Running it

Requires the embedded Python from Phase 1's PoC (shared, not duplicated —
saves re-downloading ~360MB):

```bash
cd /Users/seal/FreeCCR/swiftui_poc
./setup_embedded_python.sh   # skip if swiftui_poc/python already exists
```

Then:

```bash
cd /Users/seal/FreeCCR/native
swift build
DYLD_LIBRARY_PATH="$(pwd)/../swiftui_poc/python/lib" .build/debug/FreeCCRNative
```

A window opens with a toolbar ("Open Image…"), a dark canvas, and four
sliders (Temperature, Exposure, Contrast, Saturation) on the right:

- On launch the canvas shows a synthetic coordinate-gradient test frame (no
  file loaded yet) — dragging sliders adjusts it via
  `core.ccr_processor.adjust_image` directly, same as the Phase 3 first slice.
- Click **Open Image…** and pick any file FreeCCR can decode (RAW via rawpy,
  or a PNG/TIFF/JPEG via OpenCV/tifffile) — the canvas switches to the real
  decoded image, and the sliders now drive it through
  `core.ccr_image.CCRImage`'s actual adjustment pipeline. The toolbar shows
  the loaded filename, or a red error message if decoding failed (check the
  terminal for the Python traceback).

To quit: `Cmd+Q` with the window focused, or Ctrl+C in the terminal.

## Verifying without the UI

`swift test --filter loadsRealImageAndAdjustsIt` (needs the same
`DYLD_LIBRARY_PATH`) exercises the same `loadImage`/`adjustedPreview` path
headlessly against a generated test PNG — useful for confirming the Python
bridge itself works before touching the UI.

## Known rough edges (expected at this stage)

- No pan/zoom yet (M2) — the canvas always fits the current preview size.
- Only 4 of `sliders_panel.py`'s ~30 controls are wired up (M3 adds the rest;
  `AdjustmentParams` in `CoreBridge.swift` already carries the full parameter
  set the UI will eventually expose).
- No debouncing beyond "cancel the previous in-flight call" — fine at
  preview resolution (~1080px long side, matches the Qt app's own preview
  size), would need real throttling for full-res/export-size frames.
- Paths in `PythonEnvironment.swift` are resolved from this source file's own
  location (`#filePath`), which works for a local dev checkout but is not
  how Phase 5's packaged app will locate its embedded Python.
