import AppKit
import Metal
import PythonBridge
import SwiftUI

/// One entry in the thumbnail list — analog of a `ccr_backend.images[idx]`
/// row as `thumbnail_list.py` shows it (icon + filename).
struct LoadedImage: Identifiable, Equatable {
    let id: ImageHandle
    var fileName: String
    var thumbnail: NSImage?

    static func == (lhs: LoadedImage, rhs: LoadedImage) -> Bool { lhs.id == rhs.id }
}

/// Miniature stand-in for the Phase 2 `ProjectState` — this is a SwiftUI
/// `ObservableObject`, not `ccr_backend`'s global singleton. Every adjustment
/// slider writes here; here is the only place that talks to PythonKit
/// (through `PythonCoreBridge`, which owns the actual GIL-safe serial queue).
@MainActor
final class PreviewState: ObservableObject {
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

    // M2: pan/zoom state (CanvasTransform.swift) + the sizes it needs to turn
    // "zoom" into an actual on-screen rect. canvasViewSize is kept in sync by
    // MetalCanvasView (its NSView's bounds, in points); currentImageSize
    // comes straight from the live texture, so both stay correct across
    // window resizes and image reloads without any extra plumbing.
    @Published var transform = CanvasTransform()
    @Published var canvasViewSize: CGSize = .zero

    var currentImageSize: CGSize {
        guard let texture else { return .zero }
        return CGSize(width: texture.width, height: texture.height)
    }

    var zoomPercent: Int {
        let scale = transform.effectiveScale(viewSize: canvasViewSize, imageSize: currentImageSize)
        return Int((scale * 100).rounded())
    }

    func fitToView() {
        transform.resetToFit()
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
                self.images.append(LoadedImage(id: handle, fileName: url.lastPathComponent, thumbnail: thumbnail))
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
        transform.resetToFit() // new image: back to a fitted view (mirrors image_preview.py)
        requestUpdate()
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

    private func runAdjustment(device: MTLDevice) async {
        let start = DispatchTime.now()
        let handle = imageHandle
        guard let image = await PythonCoreBridge.shared.adjustedPreview(
            handle: handle, params: params, colorProfile: colorProfile) else {
            self.isBusy = false
            return
        }
        if Task.isCancelled { return }
        self.texture = Self.makeTexture(device: device, image: image)
        self.isBusy = false
        self.lastLatencyMs = Double(
            DispatchTime.now().uptimeNanoseconds - start.uptimeNanoseconds) / 1_000_000

        // Bug fix: the thumbnail list was frozen at whatever the image
        // looked like on load, because nothing ever re-read _thumb_np8 after
        // a slider/curve/color-profile change — even though
        // set_adjustment_by_index (called by adjustedPreview above) already
        // refreshes it on the Python side for free. Look the row up by
        // handle, not currentIndex, in case the selection changed while this
        // was in flight.
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
