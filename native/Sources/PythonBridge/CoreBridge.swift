// Bridge to FreeCCR's real core: core.ccr_backend + core.ccr_image, the same
// objects/call chain the Qt app's sliders_panel.py drives
// (ccr_backend.set_adjustment_by_index -> ccr_image.update_thumbnail_and_preview),
// not a hand-rolled reimplementation of the adjustment math.
//
// Phase 1 finding: PythonKit/CPython calls must be serialized (the GIL isn't
// optional). Every call in this file runs on `queue`, a single serial
// executor owned by this type (see SerialPythonExecutor — not DispatchQueue,
// see that file's doc comment for why) — nothing else in the app is allowed
// to touch PythonKit directly.

import Foundation
import PythonKit

/// Mirrors `core.ccr_processor.COLOR_BANDS`. Order matches
/// `BAND_ADJUSTMENT_KEYS`'s outer loop (not that order matters here — dict
/// keys, not positional args).
public enum ColorBand: String, CaseIterable, Sendable {
    case red, skin, yellow, green, cyan, blue, purple

    /// `theme.BAND_COLORS`' swatch colors, so the picker matches the Qt app.
    public var swatchHex: String {
        switch self {
        case .red: return "#c0392b"
        case .skin: return "#d8956b"
        case .yellow: return "#c8b900"
        case .green: return "#27ae60"
        case .cyan: return "#17a8b4"
        case .blue: return "#2f6fd0"
        case .purple: return "#8e44ad"
        }
    }
}

/// One color band's 4 sliders — mirrors `core.ccr_processor.BAND_PARAMS`
/// ("subsat", "sat", "bright", "hue"), sliders_panel.py's labels ("Sub Sat",
/// "Sat", "Brightness", "Hue").
public struct BandAdjustment: Sendable, Equatable {
    public var subSat: Double = 0
    public var sat: Double = 0
    public var brightness: Double = 0
    public var hue: Double = 0

    public init() {}
}

/// Mirrors sliders_panel.py's `ADJUSTMENT_KEYS` plus `curves` (from
/// `CurveEditor.get_curves()` — see `ccr_image.apply_adjustments`'s
/// `s.get('curves')`, the SAME `adjustment_settings` dict as everything
/// else). Field names match the Python dict keys
/// `core.ccr_backend.set_adjustment_by_index` expects, via `asPythonDict`.
public struct AdjustmentParams: Sendable, Equatable {
    public var temperature: Double = 0
    public var tint: Double = 0
    public var exposure: Double = 0
    public var brightness: Double = 0
    public var gamma: Double = 0
    public var highlights: Double = 0
    public var whitePoint: Double = 0
    public var shadows: Double = 0
    public var blackPoint: Double = 0
    public var contrast: Double = 0
    public var saturation: Double = 0
    public var subSaturation: Double = 0
    public var chInputGain: Double = 0
    public var chMasterShift: Double = 0
    public var chMasterGain: Double = 0
    public var chRShift: Double = 0
    public var chRGain: Double = 0
    public var chRBlackpoint: Double = 0
    public var chGShift: Double = 0
    public var chGGain: Double = 0
    public var chGBlackpoint: Double = 0
    public var chBShift: Double = 0
    public var chBGain: Double = 0
    public var chBBlackpoint: Double = 0
    /// Whole-image flag, not a slider — lives inside `adjustment_settings`
    /// as `cineon_log` (see `ccr_image.apply_adjustments`'s
    /// `s.get("cineon_log")`), unlike `colorProfile` below which is a
    /// separate `CCRImage.color_profile` attribute entirely.
    public var cineonLog: Bool = false

    /// Subtractive Saturations: per-color-band correction. Empty/missing
    /// bands are equivalent to all-zero (`BandAdjustment()`) — matches
    /// `ccr_image.apply_adjustments`'s `s.get(k, 0)` default. Rides the SAME
    /// `adjustment_settings` dict as everything else above (see
    /// `apply_adjustments`'s `band_settings=(s if any(...) else None)`) —
    /// there is no separate call for this, unlike `colorProfile`.
    public var bands: [ColorBand: BandAdjustment] = [:]
    /// `SLIDER_DEFAULTS["band_feather"] = 10` in sliders_panel.py — the only
    /// non-zero default among every field in this struct.
    public var bandFeather: Double = 10

    /// `CurveEditor.get_curves()`'s result — see `curve_editor.py`'s
    /// `CURVE_CHANNELS`/`identity_curves()`. Unlike the Qt app (which omits
    /// the `curves` key entirely when every channel is identity, via
    /// `get_curves()` returning `None`), this always sends all 4 channels;
    /// `ccr_processor.apply_curves`'s own `_is_identity_curves` guard makes
    /// that a no-op, so there's no behavioral difference, just a slightly
    /// less minimal dict.
    public var curves = CurveSet()

    public init() {}

    fileprivate var asPythonDict: PythonObject {
        let pairs: [(String, Double)] = [
            ("temperature", temperature), ("tint", tint), ("exposure", exposure),
            ("brightness", brightness), ("gamma", gamma), ("highlights", highlights),
            ("white_point", whitePoint), ("shadows", shadows), ("black_point", blackPoint),
            ("contrast", contrast), ("saturation", saturation), ("sub_saturation", subSaturation),
            ("ch_input_gain", chInputGain), ("ch_master_shift", chMasterShift),
            ("ch_master_gain", chMasterGain),
            ("ch_r_shift", chRShift), ("ch_r_gain", chRGain), ("ch_r_blackpoint", chRBlackpoint),
            ("ch_g_shift", chGShift), ("ch_g_gain", chGGain), ("ch_g_blackpoint", chGBlackpoint),
            ("ch_b_shift", chBShift), ("ch_b_gain", chBGain), ("ch_b_blackpoint", chBBlackpoint),
            ("band_feather", bandFeather),
        ]
        let dict = Python.dict()
        for (key, value) in pairs {
            dict[PythonObject(key)] = PythonObject(value)
        }
        for band in ColorBand.allCases {
            let adj = bands[band] ?? BandAdjustment()
            dict[PythonObject("band_\(band.rawValue)_subsat")] = PythonObject(adj.subSat)
            dict[PythonObject("band_\(band.rawValue)_sat")] = PythonObject(adj.sat)
            dict[PythonObject("band_\(band.rawValue)_bright")] = PythonObject(adj.brightness)
            dict[PythonObject("band_\(band.rawValue)_hue")] = PythonObject(adj.hue)
        }
        if cineonLog {
            dict[PythonObject("cineon_log")] = PythonObject(true)
        }
        let curvesDict = Python.dict()
        for channel in CurveChannel.allCases {
            let points = curves[channel].map { PythonObject([PythonObject($0.x), PythonObject($0.y)]) }
            curvesDict[PythonObject(channel.rawValue)] = PythonObject(points)
        }
        dict[PythonObject("curves")] = curvesDict
        return dict
    }
}

/// Mirrors `CCRImage.color_profile` ("color"/"bw") — a per-image attribute,
/// not an `adjustment_settings` key, so it travels alongside `AdjustmentParams`
/// rather than inside it.
public enum ColorProfile: String, Sendable {
    case color
    case blackAndWhite = "bw"
}

public struct RGBAImage: Sendable {
    public let width: Int
    public let height: Int
    public let data: Data // width * height * 4 bytes, RGBA8
}

/// Opaque handle to a loaded image: an index into `ccr_backend.images`, the
/// same indexing scheme the Qt app's widgets use throughout
/// (`get_preview_by_index`, `set_adjustment_by_index`, etc.).
public struct ImageHandle: Sendable, Equatable {
    public let index: Int
}

/// `@unchecked Sendable`: every stored PythonObject below is only ever
/// touched from `queue` (a single serial executor), which is the actual
/// safety argument the GIL requires — the compiler can't see that, hence the
/// unchecked escape hatch instead of pretending PythonObject is Sendable.
public final class PythonCoreBridge: @unchecked Sendable {
    public static let shared = PythonCoreBridge()

    private let queue = SerialPythonExecutor()
    private var booted = false

    // Imported once per process, reused across calls.
    private var np: PythonObject!
    private var ccrBackend: PythonObject!

    // Synthetic-frame fallback (Phase 3 first slice) for when no real image
    // has been loaded yet — lets the canvas show *something* on launch.
    private var cv2: PythonObject!
    private var ccrProcessor: PythonObject!
    private var syntheticFrame: PythonObject!
    private var syntheticWidth = 0
    private var syntheticHeight = 0

    private init() {}

    /// Decodes `path` via `core.ccr_image.CCRImage` (the exact class the Qt
    /// app uses) and registers it with `ccr_backend`, mirroring how a real
    /// import adds to `ccr_backend.images`. Returns nil on decode failure.
    public func loadImage(path: String) async -> ImageHandle? {
        await withCheckedContinuation { continuation in
            queue.async { [self] in
                continuation.resume(returning: self.loadImageOnQueue(path: path))
            }
        }
    }

    /// Runs the SAME call chain sliders_panel.py uses —
    /// `ccr_backend.set_adjustment_by_index` (which sets
    /// `adjustment_settings` and calls `update_thumbnail_and_preview`) — then
    /// reads back the resulting preview pixels. If `handle` is nil, falls
    /// back to adjusting a synthetic gradient frame (no image loaded yet).
    public func adjustedPreview(handle: ImageHandle?, params: AdjustmentParams,
                                 colorProfile: ColorProfile = .color) async -> RGBAImage? {
        await withCheckedContinuation { continuation in
            queue.async { [self] in
                continuation.resume(returning: self.adjustedPreviewOnQueue(
                    handle: handle, params: params, colorProfile: colorProfile))
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
        ccrBackend = Python.import("core.ccr_backend").ccr_backend
    }

    private func loadImageOnQueue(path: String) -> ImageHandle? {
        bootIfNeeded()
        let ccrImageModule = Python.import("core.ccr_image")
        do {
            let image = try ccrImageModule.CCRImage.throwing.dynamicallyCall(
                withArguments: [PythonObject(path)])
            ccrBackend.images.append(image)
            let index = Int(Python.len(ccrBackend.images))! - 1
            return ImageHandle(index: index)
        } catch {
            FileHandle.standardError.write(
                "PythonCoreBridge.loadImage failed for \(path): \(error)\n".data(using: .utf8)!)
            return nil
        }
    }

    private func adjustedPreviewOnQueue(handle: ImageHandle?, params: AdjustmentParams,
                                         colorProfile: ColorProfile) -> RGBAImage? {
        bootIfNeeded()

        let previewRGB: PythonObject
        if let handle {
            let image = ccrBackend.images[handle.index]
            // Mirrors sliders_panel.py's on_color_profile_changed: set the
            // attribute, THEN reprocess (set_adjustment_by_index below does
            // the reprocess for us).
            image.color_profile = PythonObject(colorProfile.rawValue)
            ccrBackend.set_adjustment_by_index(handle.index, params.asPythonDict)
            let preview = image._preview_np8
            // NOT `preview != Python.None`: numpy overloads `!=` to compare
            // element-wise, returning an array instead of a scalar bool, and
            // PythonKit's operator crashes trying to coerce a multi-element
            // array's truth value ("ambiguous", numpy's own error) — hit this
            // for real while wiring M1 up. `isinstance` always returns a
            // plain Python bool regardless of the checked type.
            guard Bool(Python.isinstance(preview, np.ndarray)) == true else { return nil }
            previewRGB = preview
        } else {
            previewRGB = syntheticAdjusted(params)
        }

        let shape = previewRGB.shape
        guard let height = Int(shape[0]), let width = Int(shape[1]) else { return nil }

        let alpha = np.full([height, width, 1], 255, dtype: np.uint8)
        let rgba = np.ascontiguousarray(np.concatenate([previewRGB, alpha], axis: -1))
        let byteCount = width * height * 4
        guard let addr = Int(rgba.ctypes.data),
              let ptr = UnsafeRawPointer(bitPattern: addr) else {
            return nil
        }
        let data = Data(bytes: ptr, count: byteCount)
        return RGBAImage(width: width, height: height, data: data)
    }

    /// Phase 3 first-slice fallback: adjusts a synthetic coordinate-gradient
    /// frame with `core.ccr_processor.adjust_image` directly (no CCRImage
    /// instance needed) so the canvas isn't blank before a file is opened.
    private func syntheticAdjusted(_ params: AdjustmentParams) -> PythonObject {
        let width = 512, height = 384
        if syntheticFrame == nil || syntheticWidth != width || syntheticHeight != height {
            let xGrid = np.tile(np.arange(width, dtype: np.float64).reshape([1, width]), [height, 1])
                * PythonObject(100.0)
            let yGrid = np.tile(np.arange(height, dtype: np.float64).reshape([height, 1]), [1, width])
                * PythonObject(150.0)
            let bGrid = np.full([height, width], 20000.0)
            syntheticFrame = np.stack([xGrid, yGrid, bGrid], axis: -1).astype(np.uint16)
            syntheticWidth = width
            syntheticHeight = height
        }
        let adjusted = ccrProcessor.adjust_image(
            syntheticFrame,
            exposure: PythonObject(params.exposure),
            contrast: PythonObject(params.contrast),
            saturation: PythonObject(params.saturation),
            kelvin_shift: PythonObject(params.temperature)
        )
        return cv2.convertScaleAbs(adjusted, alpha: PythonObject(255.0 / 65535.0))
    }
}
