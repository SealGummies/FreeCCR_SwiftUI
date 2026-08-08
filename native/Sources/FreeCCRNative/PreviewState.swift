import Metal
import PythonBridge
import SwiftUI

/// Miniature stand-in for the Phase 2 `ProjectState` — this is a SwiftUI
/// `ObservableObject`, not `ccr_backend`'s global singleton. Every adjustment
/// slider writes here; here is the only place that talks to PythonKit
/// (through `PythonCoreBridge`, which owns the actual GIL-safe serial queue).
@MainActor
final class PreviewState: ObservableObject {
    // Still the first-slice 4 sliders (M3 in the Phase 3 plan adds the rest
    // of sliders_panel.py's controls) — "Kelvin Shift" is now "Temperature"
    // since adjustments route through the real ccr_image.apply_adjustments
    // path, whose parameter is `temperature`, not the raw ccr_processor
    // `kelvin_shift` the earlier synthetic-frame-only version used.
    @Published var exposure: Double = 0
    @Published var contrast: Double = 0
    @Published var saturation: Double = 0
    @Published var temperature: Double = 0

    @Published private(set) var texture: MTLTexture?
    @Published private(set) var isBusy = false
    @Published private(set) var lastLatencyMs: Double = 0
    @Published private(set) var loadedFileName: String?
    @Published private(set) var loadError: String?

    let device: MTLDevice
    private var imageHandle: ImageHandle?
    private var pendingTask: Task<Void, Never>?

    init(device: MTLDevice) {
        self.device = device
    }

    func loadImage(url: URL) {
        pendingTask?.cancel()
        isBusy = true
        loadError = nil
        let path = url.path
        let device = self.device
        pendingTask = Task {
            guard let handle = await PythonCoreBridge.shared.loadImage(path: path) else {
                self.isBusy = false
                self.loadError = "Could not decode \(url.lastPathComponent) — see stderr for the Python traceback."
                return
            }
            if Task.isCancelled { return }
            self.imageHandle = handle
            self.loadedFileName = url.lastPathComponent
            await self.runAdjustment(device: device)
        }
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
        var params = AdjustmentParams()
        params.temperature = temperature
        params.exposure = exposure
        params.contrast = contrast
        params.saturation = saturation
        guard let image = await PythonCoreBridge.shared.adjustedPreview(
            handle: imageHandle, params: params) else {
            self.isBusy = false
            return
        }
        if Task.isCancelled { return }
        self.texture = Self.makeTexture(device: device, image: image)
        self.isBusy = false
        self.lastLatencyMs = Double(
            DispatchTime.now().uptimeNanoseconds - start.uptimeNanoseconds) / 1_000_000
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
}
