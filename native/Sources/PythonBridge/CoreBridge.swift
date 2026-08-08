// Thin, deliberately-boring bridge to FreeCCR's core.ccr_processor.
//
// Phase 1 finding: PythonKit/CPython calls must be serialized (the GIL isn't
// optional). Every call in this file runs on `queue`, a single serial
// DispatchQueue owned by this type — nothing else in the app is allowed to
// touch PythonKit directly.

import Foundation
import PythonKit

public struct AdjustmentParams: Sendable, Equatable {
    public var exposure: Double
    public var contrast: Double
    public var saturation: Double
    public var kelvinShift: Double

    public init(exposure: Double = 0, contrast: Double = 0,
                saturation: Double = 0, kelvinShift: Double = 0) {
        self.exposure = exposure
        self.contrast = contrast
        self.saturation = saturation
        self.kelvinShift = kelvinShift
    }
}

public struct RGBAImage: Sendable {
    public let width: Int
    public let height: Int
    public let data: Data // width * height * 4 bytes, RGBA8
}

/// `@unchecked Sendable`: every stored PythonObject below is only ever
/// touched from `queue` (a single serial DispatchQueue), which is the actual
/// safety argument the GIL requires — the compiler can't see that, hence the
/// unchecked escape hatch instead of pretending PythonObject is Sendable.
public final class PythonCoreBridge: @unchecked Sendable {
    public static let shared = PythonCoreBridge()

    private let queue = SerialPythonExecutor()
    private var booted = false

    // Imported once per process, reused across calls.
    private var np: PythonObject!
    private var cv2: PythonObject!
    private var ccrProcessor: PythonObject!
    private var baseImage: PythonObject!
    private var baseWidth = 0
    private var baseHeight = 0

    private init() {}

    /// Runs `adjust_image` from `core.ccr_processor` on a synthetic test
    /// frame (see swiftui_poc's PoC for why: no real RAW fixture on hand yet)
    /// and returns straight RGBA8 bytes ready for an MTLTexture.
    public func adjustedPreview(_ params: AdjustmentParams,
                                 width: Int, height: Int) async -> RGBAImage? {
        await withCheckedContinuation { continuation in
            queue.async { [self] in
                continuation.resume(returning: self.runOnQueue(params, width: width, height: height))
            }
        }
    }

    // MARK: - Everything below only ever runs on `queue`.

    private func bootIfNeeded() {
        guard !booted else { return }
        booted = true
        PythonEnvironment.bootIfNeeded()
        np = Python.import("numpy")
        cv2 = Python.import("cv2")
        ccrProcessor = Python.import("core.ccr_processor")
    }

    private func ensureBaseImage(width: Int, height: Int) {
        if baseImage != nil, baseWidth == width, baseHeight == height { return }
        let xGrid = np.tile(np.arange(width, dtype: np.float64).reshape([1, width]), [height, 1])
            * PythonObject(100.0)
        let yGrid = np.tile(np.arange(height, dtype: np.float64).reshape([height, 1]), [1, width])
            * PythonObject(150.0)
        let bGrid = np.full([height, width], 20000.0)
        baseImage = np.stack([xGrid, yGrid, bGrid], axis: -1).astype(np.uint16)
        baseWidth = width
        baseHeight = height
    }

    private func runOnQueue(_ params: AdjustmentParams, width: Int, height: Int) -> RGBAImage? {
        bootIfNeeded()
        ensureBaseImage(width: width, height: height)

        let adjusted = ccrProcessor.adjust_image(
            baseImage,
            exposure: PythonObject(params.exposure),
            contrast: PythonObject(params.contrast),
            saturation: PythonObject(params.saturation),
            kelvin_shift: PythonObject(params.kelvinShift)
        )
        let adjusted8 = cv2.convertScaleAbs(adjusted, alpha: PythonObject(255.0 / 65535.0))
        let alpha = np.full([height, width, 1], 255, dtype: np.uint8)
        let rgba = np.ascontiguousarray(np.concatenate([adjusted8, alpha], axis: -1))

        let byteCount = width * height * 4
        guard let addr = Int(rgba.ctypes.data),
              let ptr = UnsafeRawPointer(bitPattern: addr) else {
            return nil
        }
        let data = Data(bytes: ptr, count: byteCount)
        return RGBAImage(width: width, height: height, data: data)
    }
}
