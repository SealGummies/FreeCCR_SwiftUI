# FreeCCRNative — Phase 3 (M1: real image loading, M2: pan/zoom + hi-res-on-zoom, M3: full sliders, M4: curves, M5: thumbnail list)

A real, runnable SwiftUI app (not a CLI PoC like `swiftui_poc/`) demonstrating
the architecture's interactive loop: a Metal preview canvas plus a sliders
panel, wired live to FreeCCR's **real** `core.ccr_backend`/`core.ccr_image`
call chain through an embedded CPython via PythonKit — the same
`ccr_backend.set_adjustment_by_index` → `ccr_image.update_thumbnail_and_preview`
path `sliders_panel.py` drives, not a hand-rolled reimplementation.

This covers milestones **M1** (real image loading), **M2** (pan/zoom + the
coordinate-transform stack), **M3** (the full `sliders_panel.py` control set,
including per-color-band "Subtractive Saturations" sliders), **M4** (curve
editing), and **M5** (the thumbnail list, multi-image support) of the
Phase 3 plan (`/Users/seal/.claude/plans/unified-foraging-candy.md`). Dust
removal and the rest are later milestones (M6–M8) in that plan.

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
    `thumbnail(handle:)` (M5) reads `_thumb_np8` the same way, at its
    already-smaller 156px-long-side size (`CCRImage.__init__` populates both
    on construction, no extra processing needed).
    `hiResPreview(handle:params:colorProfile:maxLongSide:)` is a separate
    call — a port of `image_preview.py`'s `HiResDetailWorker.run()`
    (`render_hires_base` + `apply_adjustments` + the un-converted-negative
    auto-brightness stretch + sprocket mask). This has to be a genuinely
    different Python call, not just a bigger number passed to
    `adjustedPreview`: `resized_raw` (what `_preview_np8` is derived from) is
    permanently capped at ~1080px long side *at decode time* by
    `CCRImage.__init__`, so no `preview_size` argument to
    `update_thumbnail_and_preview` can ever exceed it —
    `render_hires_base` does its own independent `read_image` call instead.
    Found this the hard way mid-implementation after an initial "just
    request a bigger preview_size" attempt silently kept returning 1080px
    data; see `PreviewState.needsHiRes`/`runAdjustment` for where the two
    calls are combined.
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
    reimplementing coordinate math. **Important**: `imageSize` passed into
    this type's functions must always be `PreviewState.originalImageSize`
    (the real photo's dimensions, from `CCRImage.original_full_size`) —
    never the current preview texture's pixel size, which varies (~1080px
    fast path vs. up to 4500px hi-res path, see below). Geometry has to be
    computed from a stable size or the on-screen quad would jump around as
    the requested render resolution changes with zoom.
  - `MetalCanvasView` (`NSViewRepresentable` around `ZoomPanMTKView`, a
    custom `MTKView` subclass overriding `scrollWheel`/`magnify`/`mouseDown`
    for trackpad pan/pinch-zoom/double-click-to-fit — SwiftUI has no gesture
    API for these on macOS, so they're plain AppKit overrides), `Shaders.metal`
    (draws the preview texture into whatever quad `CanvasTransform` computes,
    not a fixed full-viewport quad anymore). Note this canvas has no manual
    tiling/viewport-cropping logic anywhere, unlike `image_preview.py`'s
    `HiResDetailWorker` — Metal's viewport already clips an arbitrarily large
    texture to whatever's visible, so a bigger hi-res texture "just works"
    with the exact same quad/pan/zoom math as the small fast-path one.
  - `CurveEditorView.swift`: `CurveEditorControl` (channel buttons + canvas +
    Reset, mirrors `CurveEditor(QWidget)`) and `CurveCanvasNSView`, a plain
    `NSView` (not Metal — this is 2D vector drawing, Core Graphics is the
    right tool) that is a line-for-line port of `CurveCanvas(QWidget)`: same
    hit-test constants, same click-on-line-inserts-a-point /
    click-on-point-grabs-it / right-click-deletes-an-interior-point / drag
    rules (endpoints X-locked, interior points clamped between neighbors).
  - `ThumbnailListView` (in `ContentView.swift`): analog of
    `widgets/thumbnail_list.py` — a scrollable list of every loaded image's
    thumbnail + filename (plain `NSImage`s from `_thumb_np8`, not `MTLTexture`s
    — no reason to involve the GPU for a static ~156px icon), selecting which
    one the canvas/sliders show.
  - `ContentView` (toolbar + thumbnail list + canvas + sliders, in one
    `HSplitView`), `PreviewState` (the Phase-2-flavored `ObservableObject`
    standing in for `ccr_backend`'s role in the Qt app). M5 changed its
    shape: `images: [LoadedImage]` + `currentIndex` replace the old single
    `imageHandle`, and `params`/`colorProfile` are snapshotted into
    `perImageState` on every selection change and restored for whichever
    image becomes current — the Swift-side equivalent of each `CCRImage`
    instance owning its own `adjustment_settings`/`color_profile` (verified
    by `perImageAdjustmentsPersistIndependently`).
  - **Zoom model (M2 follow-up)**: `ZoomPreset` (`.full`/`.oneHundred`/
    `.twoHundred`) plus `PreviewState.selectZoomPreset(_:)`. "100%"/"200%"
    are defined relative to `originalImageSize` — `zoom = N /
    fitScale(canvasViewSize, originalImageSize)` — so they mean "one/two
    real source pixels per screen point", not "N% of whatever the preview
    texture's resolution happens to be". `updateCanvasViewSize` re-snaps the
    active preset's `zoom` whenever the window resizes, since `fitScale`
    (and therefore what `zoom` value "100%" corresponds to) depends on the
    canvas size. Pinching (`applyManualZoom`) deselects the preset (`nil` —
    the toolbar control shows no highlight) since it lands on an arbitrary
    ratio.
    `PreviewState.needsHiRes` (effective scale > ~1.0, i.e. past "100%") and
    `runAdjustment` decide whether to also call `hiResPreview` — see
    `CoreBridge.swift`'s note above on why that's a separate Python call
    from the fast ~1080px `adjustedPreview` path, not just a bigger number.
    Both are always run when hi-res is needed (fast path first, to keep the
    thumbnail fresh — see M5's bug fix above — then hi-res overwrites the
    displayed texture if it succeeds), matching how the real app also keeps
    its normal preview update and `HiResDetailWorker` running independently.

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

A window opens with a toolbar ("Open Images…", zoom%, a Full/100%/200%
segmented control), a thumbnail list on the left, a dark canvas, and a
scrollable sliders panel on the right: Color Profile,
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
- Click **Open Images…** and pick one or more files FreeCCR can decode (RAW
  via rawpy, or PNG/TIFF/JPEG via OpenCV/tifffile — multi-select works) —
  each gets thumbnailed and added to the list on the left, and the last one
  picked becomes current (canvas + sliders switch to it, reset to a fitted
  view). Click any thumbnail to switch — each image remembers its own
  slider/curve/color-profile settings independently. The toolbar shows the
  current filename, how many images are loaded, and a red error message for
  any file that failed to decode (check the terminal for the Python
  traceback).
- **Pan**: two-finger scroll on a trackpad, a mouse scroll wheel, or left-click
  and drag directly on the canvas (the image follows the cursor 1:1). All
  three share the same bounds clamp: you can never drag/scroll so far that
  empty space shows up inside the canvas, and at **Full** zoom the image
  already fits the canvas on both axes, so panning is a no-op there — not a
  special case, just what the clamp resolves to.
- **Zoom**: pinch on a trackpad (anchored under your fingers — the same
  image point stays put as you zoom; this deselects the segmented control,
  since a pinch lands on an arbitrary ratio), or tap **Full**/**100%**/**200%**
  in the toolbar — a highlight slides between them with a short animation to
  show which is active. **100%**/**200%** are relative to the photo's real
  resolution and trigger a genuinely higher-resolution render (see "Zoom
  model" above) — they're not just the same ~1080px preview stretched
  bigger. Double-click the canvas also snaps to Full.

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
- `thumbnailIsSmallerThanThePreview`: `thumbnail(handle:)` actually returns
  the smaller `_thumb_np8` (≤156px long side), not the same buffer as
  `adjustedPreview`'s ~1080px preview.
- `perImageAdjustmentsPersistIndependently`: loads two images, sets
  different `exposure` on each via `selectImage`, and confirms switching
  back and forth doesn't bleed one image's settings into the other. Polls
  `PreviewState`'s published state to converge (`waitUntil`) rather than
  awaiting a Task, since `loadImages`/`selectImage` are deliberately
  fire-and-forget for SwiftUI's sake.
- `originalSizeReportsTheRealSourceDimensions`: `originalSize(handle:)`
  returns the actual source file dimensions, not the ~1080px-capped
  `resized_raw`/preview size — what the zoom model's ratios depend on.
- `previewTextureIsFullPreviewResolutionNotThumbnailSized`: the fast path
  (canvas view never laid out, so `needsHiRes` is correctly `false`) is
  exactly ~1080px, confirming the "Full" default doesn't accidentally
  trigger the (expensive) hi-res path.
- `oneHundredPercentZoomTriggersHiResRender`: with a real `canvasViewSize`
  set, selecting the "100%" preset produces a texture well past 1080px (and
  no larger than the source) — the actual fix for the original "looks like
  a blown-up thumbnail" report.
- `CanvasTransform`'s pure-math tests (`fitScaleFillsTheSmallerDimension`,
  `zoomAnchoredAtCenterKeepsImageCentered`,
  `zoomAnchoredAtArbitraryPointStaysUnderTheAnchor`, `zoomClampsToMinAndMax`,
  `resetToFitClearsZoomAndPan`) — no Python/DYLD_LIBRARY_PATH actually needed
  for these specifically, but they share the test target.
- `clampPanLocksToZeroAtFullZoom`: at Full zoom (image fits both axes) any
  attempted `panOffset` is forced back to `.zero`.
- `clampPanKeepsImageCoveringTheViewportWhenZoomedIn`: zoomed in past fit,
  an extreme attempted pan in either direction still leaves the image's
  quad covering the whole viewport (no gap on either edge).
- `clampPanLocksOnlyTheAxisThatFits`: a wide/short image zoomed so only its
  width exceeds the viewport — the height axis locks to 0 while width still
  allows (clamped) panning.

## Known rough edges (expected at this stage)

- The hi-res path re-renders the WHOLE frame at up to 4500px, not just the
  visible region — `image_preview.py`'s `HiResDetailWorker` only decodes a
  cropped tile of the visible area, which is cheaper. Metal's viewport-clips-
  a-large-texture approach made a tile system unnecessary for *drawing*, but
  the *Python-side render* still costs more than the real app's tile-only
  approach would. Fine for the source sizes tested here; could matter for
  very large (45MP+) RAWs on every slider drag while zoomed in — no
  debouncing beyond "cancel the previous in-flight call" (see below) is
  built on top of that yet either.
- The thumbnail list (M5) has no remove/reorder/rename — only add and select.
  No async/background thumbnail loading either (each file is decoded and
  thumbnailed sequentially in `loadImages`' loop before the next starts).
- Pan/zoom (M2) has no keyboard shortcuts and no on-canvas overlays yet
  (crop box, area layers, dust brush — later milestones that build on
  `CanvasTransform`).
- No debouncing beyond "cancel the previous in-flight call" — fine at
  preview resolution (~1080px long side, matches the Qt app's own preview
  size), would need real throttling for full-res/export-size/hi-res frames.
- Paths in `PythonEnvironment.swift` are resolved from this source file's own
  location (`#filePath`), which works for a local dev checkout but is not
  how Phase 5's packaged app will locate its embedded Python.
