# FreeCCRNative — Phase 3 first slice

A real, runnable SwiftUI app (not a CLI PoC like `swiftui_poc/`) demonstrating
the architecture's interactive loop: a Metal preview canvas plus a sliders
panel, both wired live to FreeCCR's actual `core.ccr_processor` through an
embedded CPython via PythonKit.

This is a **first slice** of the migration plan's Phase 3, not a full port —
it covers the `image_preview.py` canvas + `sliders_panel.py` pattern in
miniature (4 sliders, a synthetic test frame, no RAW loading / thumbnails /
crop / dust yet). See
`/Users/seal/.claude/plans/precious-knitting-spindle.md` for the full phase
breakdown.

## Layout

- `Sources/PythonBridge/` — embedded-Python bootstrap + the single serial
  execution context every PythonKit call must go through.
  - `SerialPythonExecutor.swift`: a dedicated `Thread` with a 16MB stack, not
    `DispatchQueue`. **This matters**: `import numpy`'s OpenBLAS self-check
    segfaults with a stack overflow on GCD's pooled worker threads (~512KB
    stack) — confirmed via lldb while building this. Any future PythonKit
    integration point must reuse this executor rather than a fresh
    `DispatchQueue`.
- `Sources/FreeCCRNative/` — the SwiftUI app: `ContentView` (canvas + sliders
  split view), `MetalCanvasView` (`NSViewRepresentable` around `MTKView`),
  `Shaders.metal` (trivial textured-quad blit), `PreviewState` (the Phase-2-
  flavored `ObservableObject` standing in for `ccr_backend`).

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

A window titled "FreeCCR Native — Phase 3 PoC" opens: a dark canvas on the
left showing the current synthetic test frame, four sliders (Exposure,
Contrast, Saturation, Kelvin Shift) on the right. Drag any slider — each
change calls `core.ccr_processor.adjust_image` in the embedded interpreter
and repaints the canvas; the panel's bottom line shows the last call's
latency.

To quit: `Cmd+Q` with the window focused, or Ctrl+C in the terminal.

## Known rough edges (expected at this stage)

- The "image" is a synthetic coordinate gradient, not a loaded RAW/scan —
  there's no file picker or thumbnail list yet (those are later in the
  Phase 3 widget list).
- No debouncing beyond "cancel the previous in-flight call" — fine at this
  frame size (~12ms/call), would need real throttling for full-res frames.
- Paths in `PythonEnvironment.swift` are resolved from this source file's own
  location (`#filePath`), which works for a local dev checkout but is not
  how Phase 5's packaged app will locate its embedded Python.
