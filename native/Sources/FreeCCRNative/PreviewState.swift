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

    @Published private(set) var texture: MTLTexture?
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
        zoomPreset = nil
        requestUpdate()
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
            requestUpdate() // resolution needed for the current custom zoom may have changed too
        }
    }

    let device: MTLDevice
    private var imageHandle: ImageHandle? { currentIndex.flatMap { images.indices.contains($0) ? images[$0].id : nil } }
    /// Snapshot of every OTHER loaded image's settings — mirrors each
    /// `CCRImage` instance owning its own `adjustment_settings`/
    /// `color_profile` in the real app, since here `params`/`colorProfile`
    /// are shared @Published storage for whichever image is current.
    private var perImageState: [ImageHandle: (params: AdjustmentParams, colorProfile: ColorProfile)] = [:]
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
                self.perImageState[handle] = (AdjustmentParams(), ColorProfile.color)
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
            perImageState[images[previousIndex].id] = (params, colorProfile)
        }
        currentIndex = index
        let saved = perImageState[images[index].id] ?? (AdjustmentParams(), ColorProfile.color)
        params = saved.params
        colorProfile = saved.colorProfile
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
            handle: handle, params: params, colorProfile: colorProfile) else {
            self.isBusy = false
            return
        }
        if Task.isCancelled { return }

        var displayImage = fastImage
        if let handle, needsHiRes {
            let nativeLongSide = Int(max(originalImageSize.width, originalImageSize.height).rounded())
            let cap = nativeLongSide > 0 ? min(nativeLongSide, Self.hiResMaxLongSide) : Self.hiResMaxLongSide
            if let hiRes = await PythonCoreBridge.shared.hiResPreview(
                handle: handle, params: params, colorProfile: colorProfile, maxLongSide: cap) {
                displayImage = hiRes
            }
            if Task.isCancelled { return }
        }

        self.texture = Self.makeTexture(device: device, image: displayImage)
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
