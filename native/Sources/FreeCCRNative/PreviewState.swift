import AppKit
import Metal
import PythonBridge
import SwiftUI

/// One entry in the thumbnail list — analog of a `ccr_backend.images[idx]`
/// row as `thumbnail_list.py` shows it (icon + filename). `originalSize` is
/// the REAL, full-resolution size of the source file
/// (`CCRImage.original_full_size`) — what "100%"/"200%" zoom are relative
/// to, not whatever resolution the current preview texture happens to be.
struct LoadedImage: Identifiable, Equatable {
    let id: ImageHandle
    var fileName: String
    var thumbnail: NSImage?
    var originalSize: CGSize

    static func == (lhs: LoadedImage, rhs: LoadedImage) -> Bool { lhs.id == rhs.id }
}

/// The three fixed zoom stops the toolbar's segmented control offers.
/// `nil` (see `PreviewState.zoomPreset`) means "custom" — the user pinched
/// or scrolled to an arbitrary zoom that doesn't match any of these.
enum ZoomPreset: CaseIterable {
    case full, oneHundred, twoHundred
}

/// Brush-radius <-> slider-step mapping, ported line-for-line from
/// `dust_panel.py`'s `slider_to_brush_r`/`brush_r_to_slider`: dust spotting
/// needs fine steps at the small end (0.05% is ~3px on a 6000px scan)
/// without giving up big scratch-covering sizes at the top, so the slider's
/// integer steps map onto the radius range LOG-scaled rather than linearly.
enum DustBrush {
    static let minRadius = 0.0005
    static let maxRadius = 0.2
    static let steps = 300
    static let defaultRadius = 0.012 // matches dust_panel.py's 1.2% default
    private static let logSpan = log(maxRadius / minRadius)

    static func radius(forSliderStep step: Int) -> Double {
        let v = Double(max(0, min(steps, step)))
        return minRadius * exp(logSpan * v / Double(steps))
    }

    static func sliderStep(forRadius r: Double) -> Int {
        let clamped = max(minRadius, min(maxRadius, r))
        return Int((Double(steps) * log(clamped / minRadius) / logSpan).rounded())
    }
}

/// Miniature stand-in for the Phase 2 `ProjectState` — this is a SwiftUI
/// `ObservableObject`, not `ccr_backend`'s global singleton. Every adjustment
/// slider writes here; here is the only place that talks to PythonKit
/// (through `PythonCoreBridge`, which owns the actual GIL-safe serial queue).
@MainActor
final class PreviewState: ObservableObject {
    /// Fallback "image size" before any real file is loaded — matches the
    /// synthetic gradient frame's fixed dimensions (see CoreBridge's
    /// `syntheticAdjusted`), so the zoom math has something sane to divide
    /// by. `nonisolated`: it's an immutable literal, not actor-isolated
    /// state, and `MetalRenderer` (not `@MainActor`) needs it as a default.
    nonisolated static let syntheticImageSize = CGSize(width: 512, height: 384)

    /// The full sliders_panel.py `ADJUSTMENT_KEYS` set plus `curves` — one
    /// struct instead of ~25 separate `@Published` fields so every slider
    /// can share a single `didSet`-triggered update. This holds the
    /// CURRENTLY SELECTED image's live settings; switching images snapshots
    /// it into `perImageState` and loads the newly-selected image's own
    /// snapshot back in (see `selectImage`), mirroring how each
    /// `ccr_backend.images[idx]` carries its own `adjustment_settings`.
    @Published var params = AdjustmentParams() {
        didSet { requestUpdate() }
    }
    @Published var colorProfile: ColorProfile = .color {
        didSet { requestUpdate() }
    }

    /// Manual dust-removal state — mirrors `CCRImage.dust_spots`/
    /// `dust_feather` (per-image, like `params`/`colorProfile` above: switching
    /// images snapshots/restores these too, see `selectImage`).
    /// `dustSpots` is committed by `appendDustStroke` (once per finished
    /// stroke, not per mouse-move sample) rather than a `didSet`, since a
    /// stroke's in-progress points live locally in `ZoomPanMTKView` until
    /// release — there's nothing to re-render until then.
    @Published private(set) var dustSpots: [DustSpot] = []
    @Published var dustFeather: Double = 0.25 {
        didSet { requestUpdate() }
    }
    /// Brush radius, normalized (fraction of image width) — a session-wide
    /// tool setting like the Qt canvas's `_dust_brush_r`, not per-image.
    @Published var dustBrushRadius: Double = DustBrush.defaultRadius
    /// Whether left-click-drag on the canvas paints dust strokes (true) or
    /// pans the image (false, the default from M2). See
    /// `MetalCanvasView`/`ZoomPanMTKView`'s `isDustMode`-gated mouse handling.
    @Published var isDustMode = false

    /// Crop state — mirrors `CCRImage.crop_rect`/`crop_angle` (per-image,
    /// like the dust/adjustment state above). Non-destructive in the Qt app
    /// (`resized_raw` is never modified) — the preview stays full-frame; a
    /// `cropRect` here only drives `CropOverlayView`'s on-canvas box and,
    /// via `pushCropToPython`, `_apply_dust_removal`'s "sources must come
    /// from inside the confirmed crop" rule. So unlike every other
    /// `@Published` field above, changing these does NOT call
    /// `requestUpdate()` — there's no new preview to render, just a
    /// different overlay box and an attribute push.
    @Published var cropAspectKey: CropAspectKey = .free {
        didSet { recomputeCropRect() }
    }
    @Published var cropLandscape = true {
        didSet { recomputeCropRect() }
    }
    @Published var cropAngle: Double = 0 {
        didSet { pushCropToPython() }
    }
    /// Normalized (0...1) crop box, or `nil` for Free/no image —
    /// `CropOverlayView` reads this directly.
    @Published private(set) var cropRect: CGRect?

    @Published private(set) var texture: MTLTexture?
    /// Computed from the fast ~1080px preview (not whatever hi-res buffer
    /// might also be on screen) every adjustment — matches
    /// `histogram_widget.py` reflecting the whole adjusted image, not a
    /// zoomed-in detail crop. See `Histogram.compute`.
    @Published private(set) var histogram: Histogram?
    @Published private(set) var isBusy = false
    @Published private(set) var lastLatencyMs: Double = 0
    @Published private(set) var loadError: String?

    /// M5: the thumbnail list (`thumbnail_list.py`'s analog) and which entry
    /// is currently shown on the canvas / edited by the sliders.
    @Published private(set) var images: [LoadedImage] = []
    @Published private(set) var currentIndex: Int?

    var loadedFileName: String? {
        currentIndex.flatMap { images.indices.contains($0) ? images[$0].fileName : nil }
    }

    // Pan/zoom state (CanvasTransform.swift) + the sizes it needs to turn
    // "zoom" into an actual on-screen rect. canvasViewSize is kept in sync by
    // MetalCanvasView (its NSView's bounds, in points) via
    // `updateCanvasViewSize`. `originalImageSize` — NOT the current preview
    // texture's pixel size — is the basis for all zoom math: "100%" means
    // one real source pixel per screen point, "200%" means two. The texture
    // actually on screen can be lower-resolution (a fast ~1080px render for
    // "Full") or full native resolution (for "100%"/"200%") — see
    // `computeRequestedPreviewSize` — but the GEOMETRY (where the quad sits,
    // how big it is) is always computed from the true photo size so it
    // doesn't jump around as the requested render resolution changes.
    @Published var transform = CanvasTransform()
    @Published private(set) var canvasViewSize: CGSize = .zero
    /// Which of the three toolbar zoom stops is active, if any. `nil` means
    /// the user pinched/scrolled to a custom zoom — the segmented control
    /// shows no highlight in that state.
    @Published private(set) var zoomPreset: ZoomPreset? = .full

    var originalImageSize: CGSize {
        if let idx = currentIndex, images.indices.contains(idx), images[idx].originalSize != .zero {
            return images[idx].originalSize
        }
        return Self.syntheticImageSize
    }

    var zoomPercent: Int {
        let scale = transform.effectiveScale(viewSize: canvasViewSize, imageSize: originalImageSize)
        return Int((scale * 100).rounded())
    }

    /// Snaps to one of the three fixed stops, recentering (matches
    /// `image_preview.py`'s "new image: back to the fitted view" for Full,
    /// and just feels right for 100%/200% too — no stale pan offset from a
    /// previous zoom level).
    func selectZoomPreset(_ preset: ZoomPreset) {
        let fit = transform.fitScale(viewSize: canvasViewSize, imageSize: originalImageSize)
        guard fit > 0 else { return }
        switch preset {
        case .full: transform.zoom = 1.0
        case .oneHundred: transform.zoom = 1.0 / fit
        case .twoHundred: transform.zoom = 2.0 / fit
        }
        transform.panOffset = .zero
        zoomPreset = preset
        requestUpdate()
    }

    func fitToView() {
        selectZoomPreset(.full)
    }

    /// Pinch-to-zoom (see `MetalCanvasView`'s `onMagnify`): an arbitrary,
    /// continuous zoom change, so it deselects whichever preset was active.
    func applyManualZoom(by factor: CGFloat, anchor: CGPoint) {
        transform.zoom(by: factor, anchor: anchor, viewSize: canvasViewSize, imageSize: originalImageSize)
        transform.clampPan(viewSize: canvasViewSize, imageSize: originalImageSize)
        zoomPreset = nil
        requestUpdate()
    }

    /// Left-click-drag and two-finger-scroll panning (see
    /// `MetalCanvasView`'s `onPan`) share this single entry point so both
    /// respect the same bounds: `clampPan` pins `panOffset` to 0 on any axis
    /// where the image doesn't exceed the viewport (which is always true at
    /// "Full" zoom, by definition of fit-to-view — so dragging is
    /// automatically a no-op there, no special-casing needed), and otherwise
    /// keeps the image's near edge from crossing the viewport edge. Pure
    /// geometry — no PythonKit call needed, since panning never changes what
    /// pixels the current texture holds, only where it's drawn.
    func applyPan(by delta: CGSize) {
        transform.pan(by: delta)
        transform.clampPan(viewSize: canvasViewSize, imageSize: originalImageSize)
    }

    /// Called by `MetalCanvasView` whenever the NSView's bounds change
    /// (window resize, split-view drag, ...). If a fixed zoom stop is
    /// active, re-snap to it: `fitScale` depends on `canvasViewSize`, so
    /// "100%" would silently drift away from true 1:1 as the window resizes
    /// unless `zoom` is recomputed for the new size.
    func updateCanvasViewSize(_ newSize: CGSize) {
        guard canvasViewSize != newSize else { return }
        canvasViewSize = newSize
        if let preset = zoomPreset {
            selectZoomPreset(preset)
        } else {
            transform.clampPan(viewSize: canvasViewSize, imageSize: originalImageSize)
            requestUpdate() // resolution needed for the current custom zoom may have changed too
        }
    }

    let device: MTLDevice
    private var imageHandle: ImageHandle? { currentIndex.flatMap { images.indices.contains($0) ? images[$0].id : nil } }
    /// Snapshot of every OTHER loaded image's settings — mirrors each
    /// `CCRImage` instance owning its own `adjustment_settings`/
    /// `color_profile` in the real app, since here `params`/`colorProfile`
    /// are shared @Published storage for whichever image is current.
    private var perImageState: [ImageHandle: (
        params: AdjustmentParams, colorProfile: ColorProfile,
        dustSpots: [DustSpot], dustFeather: Double,
        cropAspectKey: CropAspectKey, cropLandscape: Bool, cropAngle: Double)] = [:]
    private var pendingTask: Task<Void, Never>?

    init(device: MTLDevice) {
        self.device = device
    }

    /// Adds every URL as a new entry (does not replace what's already
    /// loaded — mirrors "Open Files" adding to `ccr_backend.images` rather
    /// than starting over), decoding + thumbnailing sequentially, then
    /// selects the last one added.
    func loadImages(urls: [URL]) {
        pendingTask?.cancel()
        isBusy = true
        loadError = nil
        pendingTask = Task {
            var lastGoodIndex: Int?
            for url in urls {
                if Task.isCancelled { return }
                guard let handle = await PythonCoreBridge.shared.loadImage(path: url.path) else {
                    self.loadError = "Could not decode \(url.lastPathComponent) — see stderr for the Python traceback."
                    continue
                }
                let thumbnail = await PythonCoreBridge.shared.thumbnail(handle: handle)
                    .flatMap(Self.makeNSImage)
                let originalSize = await PythonCoreBridge.shared.originalSize(handle: handle)
                    .map { CGSize(width: $0.width, height: $0.height) } ?? .zero
                self.images.append(LoadedImage(
                    id: handle, fileName: url.lastPathComponent, thumbnail: thumbnail, originalSize: originalSize))
                self.perImageState[handle] = (AdjustmentParams(), ColorProfile.color, [], 0.25, .free, true, 0)
                lastGoodIndex = self.images.count - 1
            }
            if let lastGoodIndex {
                self.selectImage(at: lastGoodIndex)
            } else {
                self.isBusy = false
            }
        }
    }

    /// Switches which image the canvas/sliders show, snapshotting the
    /// outgoing image's live settings and restoring the incoming one's —
    /// the Swift-side equivalent of the Qt app reading a different
    /// `CCRImage.adjustment_settings` when the thumbnail selection changes.
    func selectImage(at index: Int) {
        guard images.indices.contains(index) else { return }
        if let previousIndex = currentIndex, images.indices.contains(previousIndex) {
            perImageState[images[previousIndex].id] = (
                params, colorProfile, dustSpots, dustFeather,
                cropAspectKey, cropLandscape, cropAngle)
        }
        currentIndex = index
        let saved = perImageState[images[index].id]
            ?? (AdjustmentParams(), ColorProfile.color, [], 0.25, .free, true, 0)
        params = saved.params
        colorProfile = saved.colorProfile
        dustSpots = saved.dustSpots
        dustFeather = saved.dustFeather
        cropAspectKey = saved.cropAspectKey
        cropLandscape = saved.cropLandscape
        cropAngle = saved.cropAngle
        fitToView() // new image: back to a fitted view (mirrors image_preview.py); also calls requestUpdate()
    }

    /// Resets every slider (and Color Profile) to its default — mirrors
    /// sliders_panel.py's on_reset_clicked (minus curves/crop/areas, not
    /// wired up yet). Curves ARE part of `AdjustmentParams` (M4), so
    /// `AdjustmentParams()` already resets those too.
    func resetAdjustments() {
        params = AdjustmentParams()
        colorProfile = .color
    }

    /// Commits one finished brush stroke — called by `MetalCanvasView` on
    /// mouse-up while `isDustMode` is set, mirroring `image_preview.py`'s
    /// `dust_release` appending a `{"kind": "brush", ...}` dict to
    /// `img.dust_spots`. `points` are already normalized (0...1) image
    /// coordinates (see `CanvasTransform.imageNormalizedPoint`).
    func appendDustStroke(points: [CGPoint], radius: Double) {
        guard !points.isEmpty else { return }
        dustSpots.append(DustSpot(points: points, radius: radius))
        requestUpdate()
    }

    /// Mirrors dust_panel.py's "Undo last spot" button.
    func undoLastDustSpot() {
        guard !dustSpots.isEmpty else { return }
        dustSpots.removeLast()
        requestUpdate()
    }

    /// Mirrors dust_panel.py's "Clear all" button.
    func clearDustSpots() {
        guard !dustSpots.isEmpty else { return }
        dustSpots.removeAll()
        requestUpdate()
    }

    /// Recomputes `cropRect` from the current preset/orientation and image
    /// size (see `CropAspect.normalizedRect`) — called whenever either
    /// input changes. Always re-centers rather than trying to fit within
    /// whatever box was already there (this port has no draggable box to
    /// preserve yet — see `CropAspect`'s doc comment).
    private func recomputeCropRect() {
        cropRect = CropAspect.normalizedRect(
            for: cropAspectKey, landscape: cropLandscape, imageSize: originalImageSize)
        pushCropToPython()
    }

    /// Crop is non-destructive display/metadata (see the `cropRect` doc
    /// comment above), so this just forwards the current box + angle to the
    /// loaded `CCRImage` for `_apply_dust_removal`'s crop-awareness — no
    /// `requestUpdate()`, there's no new preview pixels to fetch.
    private func pushCropToPython() {
        guard let handle = imageHandle else { return }
        let rect = cropRect
        let angle = cropAngle
        Task { await PythonCoreBridge.shared.setCrop(handle: handle, rect: rect, angle: angle) }
    }

    /// Mirrors crop_panel.py's Reset button.
    func resetCrop() {
        cropAspectKey = .free
        cropLandscape = true
        cropAngle = 0
    }

    func requestUpdate() {
        pendingTask?.cancel()
        isBusy = true
        let device = self.device
        pendingTask = Task {
            await self.runAdjustment(device: device)
        }
    }

    /// Matches `image_preview.py`'s `HiResDetailWorker.HIRES_MAX_LONG_SIDE`
    /// constant (also 4500) — a fixed generous cap, not something computed
    /// per-zoom-level; `render_hires_base`/`resize_image_to_max_pixel` never
    /// upscale, so requesting more than the source has just returns the
    /// source's own native size.
    private static let hiResMaxLongSide = 4500

    /// "Zoomed in enough" to justify paying for a hi-res render: past the
    /// point where one source pixel maps to more than one screen pixel.
    /// Mirrors `image_preview.py`'s `_zoomed_in_enough()` gate in spirit
    /// (exact threshold differs — that one also accounts for already having
    /// a cached hi-res tile; this app always re-renders on demand instead).
    private var needsHiRes: Bool {
        // Before the canvas has been laid out even once, canvasViewSize is
        // .zero and CanvasTransform.fitScale's degenerate-size guard
        // returns a meaningless fallback of 1 — exclude that case
        // explicitly rather than letting it look like ">= 100% zoom".
        guard canvasViewSize.width > 0, canvasViewSize.height > 0 else { return false }
        // >= 1.0 in spirit, but "100%" itself must trigger this (it's the
        // whole point of that preset), and 1/fit*fit isn't always exactly
        // 1.0 in floating point — a small tolerance below 1.0 covers it.
        return transform.effectiveScale(viewSize: canvasViewSize, imageSize: originalImageSize) > 0.999
    }

    private func runAdjustment(device: MTLDevice) async {
        let start = DispatchTime.now()
        let handle = imageHandle
        // Always run the fast ~1080px path first: it's what keeps
        // _thumb_np8 fresh (see below) and gives an immediate result even
        // when the hi-res render (next) is slow. Matches the real app's own
        // architecture — sliders_panel's normal update path and
        // HiResDetailWorker both run, independently, when zoomed in.
        guard let fastImage = await PythonCoreBridge.shared.adjustedPreview(
            handle: handle, params: params, colorProfile: colorProfile,
            dustSpots: dustSpots, dustFeather: dustFeather,
            cropRect: cropRect, cropAngle: cropAngle) else {
            self.isBusy = false
            return
        }
        if Task.isCancelled { return }

        var displayImage = fastImage
        if let handle, needsHiRes {
            let nativeLongSide = Int(max(originalImageSize.width, originalImageSize.height).rounded())
            let cap = nativeLongSide > 0 ? min(nativeLongSide, Self.hiResMaxLongSide) : Self.hiResMaxLongSide
            if let hiRes = await PythonCoreBridge.shared.hiResPreview(
                handle: handle, params: params, colorProfile: colorProfile, maxLongSide: cap,
                dustSpots: dustSpots, dustFeather: dustFeather,
                cropRect: cropRect, cropAngle: cropAngle) {
                displayImage = hiRes
            }
            if Task.isCancelled { return }
        }

        self.texture = Self.makeTexture(device: device, image: displayImage)
        self.histogram = Histogram.compute(from: fastImage)
        self.isBusy = false
        self.lastLatencyMs = Double(
            DispatchTime.now().uptimeNanoseconds - start.uptimeNanoseconds) / 1_000_000

        // Re-read _thumb_np8 too: update_thumbnail_and_preview (called by
        // the fast adjustedPreview above) refreshes it on the Python side
        // for free, but nothing here re-fetches it into the matching
        // LoadedImage without this. Look the row up by handle, not
        // currentIndex, in case the selection changed while this was in flight.
        guard let handle else { return }
        guard let thumbnailImage = await PythonCoreBridge.shared.thumbnail(handle: handle) else { return }
        if Task.isCancelled { return }
        guard let nsImage = Self.makeNSImage(thumbnailImage) else { return }
        guard let row = images.firstIndex(where: { $0.id == handle }) else { return }
        images[row].thumbnail = nsImage
    }

    private static func makeTexture(device: MTLDevice, image: RGBAImage) -> MTLTexture? {
        let descriptor = MTLTextureDescriptor.texture2DDescriptor(
            pixelFormat: .rgba8Unorm, width: image.width, height: image.height,
            mipmapped: false)
        descriptor.usage = [.shaderRead]
        guard let texture = device.makeTexture(descriptor: descriptor) else { return nil }
        image.data.withUnsafeBytes { raw in
            texture.replace(
                region: MTLRegionMake2D(0, 0, image.width, image.height),
                mipmapLevel: 0, withBytes: raw.baseAddress!, bytesPerRow: image.width * 4)
        }
        return texture
    }

    private static func makeNSImage(_ image: RGBAImage) -> NSImage? {
        guard let provider = CGDataProvider(data: image.data as CFData),
              let cgImage = CGImage(
                width: image.width, height: image.height, bitsPerComponent: 8, bitsPerPixel: 32,
                bytesPerRow: image.width * 4, space: CGColorSpaceCreateDeviceRGB(),
                bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.premultipliedLast.rawValue),
                provider: provider, decode: nil, shouldInterpolate: true, intent: .defaultIntent)
        else { return nil }
        return NSImage(cgImage: cgImage, size: NSSize(width: image.width, height: image.height))
    }
}
