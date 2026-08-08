import Metal
import PythonBridge
import SwiftUI

/// Miniature stand-in for the Phase 2 `ProjectState` — this is a SwiftUI
/// `ObservableObject`, not `ccr_backend`'s global singleton. Every adjustment
/// slider writes here; here is the only place that talks to PythonKit
/// (through `PythonCoreBridge`, which owns the actual GIL-safe serial queue).
@MainActor
final class PreviewState: ObservableObject {
    static let previewWidth = 512
    static let previewHeight = 384

    @Published var exposure: Double = 0
    @Published var contrast: Double = 0
    @Published var saturation: Double = 0
    @Published var kelvinShift: Double = 0

    @Published private(set) var texture: MTLTexture?
    @Published private(set) var isBusy = false
    @Published private(set) var lastLatencyMs: Double = 0

    let device: MTLDevice
    private var pendingTask: Task<Void, Never>?

    init(device: MTLDevice) {
        self.device = device
    }

    func requestUpdate() {
        pendingTask?.cancel()
        let params = AdjustmentParams(
            exposure: exposure, contrast: contrast,
            saturation: saturation, kelvinShift: kelvinShift)
        isBusy = true
        let width = Self.previewWidth
        let height = Self.previewHeight
        let device = self.device
        pendingTask = Task {
            let start = DispatchTime.now()
            guard let image = await PythonCoreBridge.shared.adjustedPreview(
                params, width: width, height: height) else {
                self.isBusy = false
                return
            }
            if Task.isCancelled { return }
            let tex = Self.makeTexture(device: device, image: image)
            self.texture = tex
            self.isBusy = false
            self.lastLatencyMs = Double(
                DispatchTime.now().uptimeNanoseconds - start.uptimeNanoseconds) / 1_000_000
        }
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
