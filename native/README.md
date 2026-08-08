# FreeCCRNative — Phase 3 (M1: real image loading, M2: pan/zoom, M3: full sliders, M4: curves)

A real, runnable SwiftUI app (not a CLI PoC like `swiftui_poc/`) demonstrating
the architecture's interactive loop: a Metal preview canvas plus a sliders
panel, wired live to FreeCCR's **real** `core.ccr_backend`/`core.ccr_image`
call chain through an embedded CPython via PythonKit — the same
`ccr_backend.set_adjustment_by_index` → `ccr_image.update_thumbnail_and_preview`
path `sliders_panel.py` drives, not a hand-rolled reimplementation.

This covers milestones **M1** (real image loading), **M2** (pan/zoom + the
coordinate-transform stack), **M3** (the full `sliders_panel.py` control set,
including per-color-band "Subtractive Saturations" sliders), and **M4**
(curve editing) of the Phase 3 plan
(`/Users/seal/.claude/plans/unified-foraging-candy.md`). Thumbnails, dust
removal, and the rest are later milestones (M5–M8) in that plan.

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
    `adjustedPreview(handle:params:colorProfile:)` calls
    `ccr_backend.set_adjustment_by_index` (exactly what a Qt slider does) and
    reads back `_preview_np8` directly — not the `.thumbnail`/
    `.resized_preview` properties, which return `QPixmap` and are `None` in a
    no-Qt environment (see `src/core/ccr_image.py`'s `QT_AVAILABLE` guards).
    `AdjustmentParams` now carries the full `sliders_panel.py` `ADJUSTMENT_KEYS`
    set — main sliders, all 12 Channel Levels controls, `cineonLog`, and the
    28 per-color-band keys (`bands: [ColorBand: BandAdjustment]` +
    `bandFeather`) rides the SAME `adjustment_settings` dict as everything
    else (see `apply_adjustments`'s `band_settings=(s if any(...) else
    None)` — it reads off the whole dict, there's no separate call).
    `ColorProfile` is the one exception: it's separate because
    `CCRImage.color_profile` is its own attribute, not an
    `adjustment_settings` key. `curves: CurveSet` (M4) rides the same dict
    too, under the `curves` key.
  - `CurveMath.swift`: `CurvePoint`/`CurveChannel`/`CurveSet` plus
    `monotoneCubic` — a line-for-line port of `curve_editor.py`'s
    `_monotone_cubic` (Fritsch-Carlson monotone cubic Hermite
    interpolation), verified byte-for-byte against the actual Python
    implementation's output (see `monotoneCubicMatchesThePythonReferenceImplementation`).
    Lives in `PythonBridge`, not `FreeCCRNative`, since `AdjustmentParams`
    needs these types too (same reasoning as `ColorBand`/`BandAdjustment`).
- `Sources/FreeCCRNative/` — the SwiftUI app:
  - `CanvasTransform.swift`: the screen ↔ canvas ↔ image-normalized
    coordinate stack (the Swift analog of `image_preview.py`'s
    `_display_transform`/`map_displayed_to_full`). Pure math, no AppKit/Metal
    — pan (`panOffset`) and zoom (`zoom`, a multiplier on top of the
    computed "fit" scale) live here, along with anchor-preserving zoom
    (`zoom(by:anchor:...)`, covered by unit tests). Every later feature that
    needs "where in the image did the user click" (crop, area layers, dust
    brush, black/white-point picking) should read through this rather than
    reimplementing coordinate math.
  - `MetalCanvasView` (`NSViewRepresentable` around `ZoomPanMTKView`, a
    custom `MTKView` subclass overriding `scrollWheel`/`magnify`/`mouseDown`
    for trackpad pan/pinch-zoom/double-click-to-fit — SwiftUI has no gesture
    API for these on macOS, so they're plain AppKit overrides), `Shaders.metal`
    (draws the preview texture into whatever quad `CanvasTransform` computes,
    not a fixed full-viewport quad anymore).
  - `CurveEditorView.swift`: `CurveEditorControl` (channel buttons + canvas +
    Reset, mirrors `CurveEditor(QWidget)`) and `CurveCanvasNSView`, a plain
    `NSView` (not Metal — this is 2D vector drawing, Core Graphics is the
    right tool) that is a line-for-line port of `CurveCanvas(QWidget)`: same
    hit-test constants, same click-on-line-inserts-a-point /
    click-on-point-grabs-it / right-click-deletes-an-interior-point / drag
    rules (endpoints X-locked, interior points clamped between neighbors).
  - `ContentView` (toolbar + canvas + sliders split view), `PreviewState`
    (the Phase-2-flavored `ObservableObject` standing in for `ccr_backend`'s
    role in the Qt app — holds the loaded image's `ImageHandle` and the live
    `CanvasTransform`).

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

A window opens with a toolbar ("Open Image…", zoom%, +/− buttons, Fit), a
dark canvas, and a scrollable sliders panel on the right: Color Profile,
the 12 main sliders (Temperature, Tint, Gain/Exposure, Brightness, Gamma,
Highlights, White Point, Shadows, Black Point, Contrast, Saturation,
Subtracted Sat), a collapsible "Subtractive Saturations" section (7 color
swatches — click one to select which band's Sub Sat/Sat/Brightness/Hue
sliders are shown, plus a global Feather slider), a collapsible "Channel
Levels" section (Input/Master/R/G/B gain-shift-blackpoint controls), a
Cineon Log → Rec.709 toggle, and a Reset All button. Every slider shows a
small reset arrow next to its value when it's off its default — the SwiftUI
stand-in for `ResettableSlider`'s double-click-to-reset, since a real
double-click on a ~20pt slider track isn't a reliable target. There's also a
collapsible **Curves** section: All/R/G/B channel buttons, a draggable tone
curve (click the line to add a point, drag a point to reshape, right-click
an interior point to delete it — the endpoints can't be deleted or moved off
x=0/x=255), and a Reset Curve button.

- On launch the canvas shows a synthetic coordinate-gradient test frame (no
  file loaded yet) — dragging sliders adjusts it via
  `core.ccr_processor.adjust_image` directly, same as the Phase 3 first slice.
- Click **Open Image…** and pick any file FreeCCR can decode (RAW via rawpy,
  or a PNG/TIFF/JPEG via OpenCV/tifffile) — the canvas switches to the real
  decoded image (reset to a fitted view), and the sliders now drive it
  through `core.ccr_image.CCRImage`'s actual adjustment pipeline. The toolbar
  shows the loaded filename, or a red error message if decoding failed
  (check the terminal for the Python traceback).
- **Pan**: two-finger scroll on a trackpad, or a mouse scroll wheel.
- **Zoom**: pinch on a trackpad (anchored under your fingers — the same
  image point stays put as you zoom), or the toolbar's +/− buttons (anchored
  at the canvas center).
- **Fit**: the toolbar's Fit button, or double-click the canvas.

To quit: `Cmd+Q` with the window focused, or Ctrl+C in the terminal.

## Verifying without the UI

`swift test` (needs the same `DYLD_LIBRARY_PATH`) runs:
- `loadsRealImageAndAdjustsIt`: the same `loadImage`/`adjustedPreview` path
  headlessly against a generated test PNG.
- `blackAndWhiteColorProfileGraysOutThePreview`: confirms `colorProfile`
  actually reaches `CCRImage._to_grayscale` (every returned pixel's R/G/B
  equal) rather than silently being ignored.
- `cineonLogFlagDoesNotCrashThePipeline`: `cineon_log` round-trips through
  `adjustment_settings` without error (no numeric assertion — that math
  belongs with `ccr_processor`'s own Python tests).
- `bandAdjustmentChangesTheMatchingColor`: a strong `band_red_bright` push
  visibly changes a red-dominant **gradient** test image. Written first
  against a flat solid-color image, which failed — not a product bug: a
  perfectly uniform image round-trips through
  `update_thumbnail_and_preview`'s auto-brightness stretch for un-converted
  negatives and re-normalizes to the same result regardless of the band
  push. Real finding, wrong test fixture; switched to a gradient.
- `bandFeatherDefaultsToTen`: matches `SLIDER_DEFAULTS["band_feather"]`.
- `nonIdentityCurveChangesTheOutput`: a real curve visibly changes
  `adjust_image`'s result (this one used a gradient fixture from the start —
  learned that lesson from the band-adjustment test above).
- `monotoneCubicMatchesThePythonReferenceImplementation`: the Swift port's
  output matches `curve_editor.py`'s actual `_monotone_cubic`, run on the
  same inputs, to within `1e-9`.
- `curveSetDefaultsToIdentity`: sanity check on `CurveSet`'s default state.
- `CanvasTransform`'s pure-math tests (`fitScaleFillsTheSmallerDimension`,
  `zoomAnchoredAtCenterKeepsImageCentered`,
  `zoomAnchoredAtArbitraryPointStaysUnderTheAnchor`, `zoomClampsToMinAndMax`,
  `resetToFitClearsZoomAndPan`) — no Python/DYLD_LIBRARY_PATH actually needed
  for these specifically, but they share the test target.

## Known rough edges (expected at this stage)

- Pan/zoom (M2) has no keyboard shortcuts and no on-canvas overlays yet
  (crop box, area layers, dust brush — later milestones that build on
  `CanvasTransform`).
- No debouncing beyond "cancel the previous in-flight call" — fine at
  preview resolution (~1080px long side, matches the Qt app's own preview
  size), would need real throttling for full-res/export-size frames.
- Paths in `PythonEnvironment.swift` are resolved from this source file's own
  location (`#filePath`), which works for a local dev checkout but is not
  how Phase 5's packaged app will locate its embedded Python.
