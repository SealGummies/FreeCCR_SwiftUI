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

import CoreGraphics
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

/// One manual dust-removal brush stroke — mirrors `dust_panel.py`/
/// `image_preview.py`'s `img.dust_spots` entries: `{"kind": "brush", "pts":
/// [[x,y],...], "r": r}`. `points` are normalized (0...1) over the full,
/// un-cropped image — crop-independent, same as the Qt app, though this port
/// has no crop feature yet so that distinction is moot for now. `radius` is
/// a fraction of image WIDTH (matches `DUST_BRUSH_R_MIN`/`MAX` in
/// `dust_panel.py`, 0.0005...0.2). AI-detected ("auto") spots aren't
/// supported yet — see CoreBridge's dust-removal doc comment.
public struct DustSpot: Sendable, Equatable {
    public var points: [CGPoint]
    public var radius: Double

    public init(points: [CGPoint], radius: Double) {
        self.points = points
        self.radius = radius
    }
}

public struct RGBAImage: Sendable {
    public let width: Int
    public let height: Int
    public let data: Data // width * height * 4 bytes, RGBA8

    public init(width: Int, height: Int, data: Data) {
        self.width = width
        self.height = height
        self.data = data
    }
}

/// Opaque handle to a loaded image: an index into `ccr_backend.images`, the
/// same indexing scheme the Qt app's widgets use throughout
/// (`get_preview_by_index`, `set_adjustment_by_index`, etc.).
public struct ImageHandle: Sendable, Equatable, Hashable {
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

    /// Reads `_thumb_np8` (populated by `CCRImage.__init__`'s own
    /// `update_thumbnail_and_preview()` call — no extra processing needed)
    /// rather than the `.thumbnail` property, which returns `QPixmap` and is
    /// `None` in this no-Qt environment. 156px long side, matching
    /// `update_thumbnail_and_preview`'s default `thumbnail_size`.
    public func thumbnail(handle: ImageHandle) async -> RGBAImage? {
        await withCheckedContinuation { continuation in
            queue.async { [self] in
                continuation.resume(returning: self.thumbnailOnQueue(handle: handle))
            }
        }
    }

    /// Runs the SAME call sliders_panel.py's on_slider_changed makes —
    /// `ccr_backend.set_adjustment_by_index` (sets `adjustment_settings`,
    /// then `update_thumbnail_and_preview()`, whose FIXED ~1080px-long-side
    /// cap is baked in at decode time via `CCRImage.__init__`'s own
    /// `max_long_side=1080` — nothing this function passes can raise that;
    /// that's what `hiResPreview` below is for) — then reads back the
    /// resulting preview pixels. If `handle` is nil, falls back to adjusting
    /// a synthetic gradient frame (no image loaded yet).
    public func adjustedPreview(handle: ImageHandle?, params: AdjustmentParams,
                                 colorProfile: ColorProfile = .color,
                                 dustSpots: [DustSpot] = [], dustFeather: Double = 0.25,
                                 cropRect: CGRect? = nil, cropAngle: Double = 0) async -> RGBAImage? {
        await withCheckedContinuation { continuation in
            queue.async { [self] in
                continuation.resume(returning: self.adjustedPreviewOnQueue(
                    handle: handle, params: params, colorProfile: colorProfile,
                    dustSpots: dustSpots, dustFeather: dustFeather,
                    cropRect: cropRect, cropAngle: cropAngle))
            }
        }
    }

    /// The actual "hi-res on zoom" mechanism — a port of
    /// `image_preview.py`'s `HiResDetailWorker.run()`: re-decode the source
    /// file up to `maxLongSide` (bypassing `resized_raw`'s permanent 1080px
    /// cap entirely — `CCRImage.render_hires_base` does its own fresh
    /// `read_image` call), replay any negative-conversion snapshot (a
    /// no-op — `None` — for the plain color/positive images this app loads
    /// today; becomes relevant once a Phase 3 milestone wires up B/W-point
    /// conversion), then apply the current slider settings on that
    /// higher-resolution base. `nil` on failure (falls back to whatever
    /// `adjustedPreview` already returned — see `PreviewState.runAdjustment`).
    public func hiResPreview(handle: ImageHandle, params: AdjustmentParams,
                              colorProfile: ColorProfile, maxLongSide: Int,
                              dustSpots: [DustSpot] = [], dustFeather: Double = 0.25,
                              cropRect: CGRect? = nil, cropAngle: Double = 0) async -> RGBAImage? {
        await withCheckedContinuation { continuation in
            queue.async { [self] in
                continuation.resume(returning: self.hiResPreviewOnQueue(
                    handle: handle, params: params, colorProfile: colorProfile, maxLongSide: maxLongSide,
                    dustSpots: dustSpots, dustFeather: dustFeather,
                    cropRect: cropRect, cropAngle: cropAngle))
            }
        }
    }

    /// `CCRImage.original_full_size`: the real, full-resolution (height,
    /// width) of the source file — set once by `read_image` during
    /// `CCRImage.__init__`/`loadImage`, so this is available immediately
    /// after `loadImage` returns a handle. This is what "100%"/"200%" zoom
    /// are relative to, NOT whatever resolution the current preview texture
    /// happens to be rendered at.
    public func originalSize(handle: ImageHandle) async -> (width: Int, height: Int)? {
        await withCheckedContinuation { continuation in
            queue.async { [self] in
                continuation.resume(returning: self.originalSizeOnQueue(handle: handle))
            }
        }
    }

    /// Sets `CCRImage.crop_rect`/`crop_angle` directly — plain attributes
    /// (see `ccr_image.py`'s `__init__`), like `dust_spots`/`dust_feather`.
    /// Non-destructive and display-only in the Qt app (`resized_raw` is
    /// never modified), so this port doesn't need to re-render the preview
    /// after a crop change either — the only current consumer is
    /// `_apply_dust_removal`'s "sources must come from inside the confirmed
    /// crop" rule, which reads the attribute fresh on the next adjustment
    /// (see `adjustedPreviewOnQueue`/`hiResPreviewOnQueue`, which also set
    /// these two attributes so that rule sees the current crop). `rect` is
    /// normalized `(x1, y1, x2, y2)`, matching `crop_rect`'s own convention
    /// exactly; `nil` clears the crop (`crop_rect = None`).
    public func setCrop(handle: ImageHandle, rect: CGRect?, angle: Double) async {
        await withCheckedContinuation { continuation in
            queue.async { [self] in
                self.setCropOnQueue(handle: handle, rect: rect, angle: angle)
                continuation.resume()
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
                                         colorProfile: ColorProfile,
                                         dustSpots: [DustSpot], dustFeather: Double,
                                         cropRect: CGRect?, cropAngle: Double) -> RGBAImage? {
        bootIfNeeded()

        let previewRGB: PythonObject
        if let handle {
            let image = ccrBackend.images[handle.index]
            // Mirrors sliders_panel.py's on_color_profile_changed (set the
            // attribute, then reprocess) + set_adjustment_by_index (set
            // adjustment_settings, then reprocess) inlined together.
            image.color_profile = PythonObject(colorProfile.rawValue)
            image.adjustment_settings = params.asPythonDict
            // `dust_spots`/`dust_feather` are plain CCRImage attributes (see
            // ccr_image.py's `_apply_dust_removal`, which reads them fresh
            // via getattr on every `apply_adjustments` call) — no separate
            // "commit" call needed, unlike Qt's dust_panel.py which pushes
            // an undo snapshot first (no undo/redo in this port yet).
            image.dust_spots = dustSpotsPythonList(dustSpots)
            image.dust_feather = PythonObject(dustFeather)
            setCropAttributes(on: image, rect: cropRect, angle: cropAngle)
            image.update_thumbnail_and_preview()
            guard let preview = numpyRGB8(image._preview_np8) else { return nil }
            previewRGB = preview
        } else {
            previewRGB = syntheticAdjusted(params)
        }
        return rgbaImage(fromRGB: previewRGB)
    }

    /// Port of `HiResDetailWorker.run()`. `render_hires_base` does its own
    /// independent `read_image` call up to `maxLongSide` — entirely
    /// separate from (and not capped by) `resized_raw`'s fixed 1080px
    /// preview buffer.
    private func hiResPreviewOnQueue(handle: ImageHandle, params: AdjustmentParams,
                                      colorProfile: ColorProfile, maxLongSide: Int,
                                      dustSpots: [DustSpot], dustFeather: Double,
                                      cropRect: CGRect?, cropAngle: Double) -> RGBAImage? {
        bootIfNeeded()
        let image = ccrBackend.images[handle.index]
        image.color_profile = PythonObject(colorProfile.rawValue)
        image.adjustment_settings = params.asPythonDict
        image.dust_spots = dustSpotsPythonList(dustSpots)
        image.dust_feather = PythonObject(dustFeather)
        setCropAttributes(on: image, rect: cropRect, angle: cropAngle)

        let result = image.render_hires_base(max_long_side: PythonObject(maxLongSide))
        guard let base = numpyRGB8(result[0]) else { return nil }
        let sprocketAlpha = numpyRGB8(result[1]) // legitimately None until B/W-point conversion exists

        var display = image.apply_adjustments(base, settings: params.asPythonDict)
        let converted = Bool(image.converted) ?? false
        let positiveMode = Bool(ccrBackend.positive_mode) ?? false
        if !converted && !positiveMode {
            // Mirrors the normal preview pipeline's display-only stretch for
            // un-converted negatives (ccr_image.py's update_thumbnail_and_preview).
            display = image._auto_brightness_for_preview(display)
        }
        if let sprocketAlpha, Bool(ccrBackend.sprocket_mask_white) == true {
            display = ccrProcessor.apply_sprocket_mask(display, sprocketAlpha)
        }
        let display8 = np.ascontiguousarray(cv2.convertScaleAbs(display, alpha: PythonObject(255.0 / 65535.0)))
        return rgbaImage(fromRGB: display8)
    }

    private func setCropOnQueue(handle: ImageHandle, rect: CGRect?, angle: Double) {
        bootIfNeeded()
        setCropAttributes(on: ccrBackend.images[handle.index], rect: rect, angle: angle)
    }

    /// `crop_rect`'s convention is `(x1, y1, x2, y2)` normalized fractions
    /// (see `ccr_image.py`'s `__init__` doc comment) — a plain 4-element
    /// Python list works identically to a tuple for every consumer (`x1, y1,
    /// x2, y2 = crop_rect` unpacking, `crop_rect[i]` indexing), same as how
    /// dust spot points already ride as `[x, y]` lists.
    private func setCropAttributes(on image: PythonObject, rect: CGRect?, angle: Double) {
        if let rect {
            image.crop_rect = PythonObject([
                PythonObject(Double(rect.minX)), PythonObject(Double(rect.minY)),
                PythonObject(Double(rect.maxX)), PythonObject(Double(rect.maxY)),
            ])
        } else {
            image.crop_rect = Python.None
        }
        image.crop_angle = PythonObject(angle)
    }

    private func thumbnailOnQueue(handle: ImageHandle) -> RGBAImage? {
        bootIfNeeded()
        let image = ccrBackend.images[handle.index]
        guard let thumb = numpyRGB8(image._thumb_np8) else { return nil }
        return rgbaImage(fromRGB: thumb)
    }

    private func originalSizeOnQueue(handle: ImageHandle) -> (width: Int, height: Int)? {
        bootIfNeeded()
        let image = ccrBackend.images[handle.index]
        let size = image.original_full_size
        guard Bool(Python.isinstance(size, Python.tuple)) == true,
              let height = Int(size[0]), let width = Int(size[1]) else {
            return nil
        }
        return (width: width, height: height)
    }

    /// Mirrors the `{"kind": "brush", "pts": [[x,y],...], "r": r}` dict shape
    /// `image_preview.py`'s `dust_release` appends to `img.dust_spots`.
    private func dustSpotsPythonList(_ spots: [DustSpot]) -> PythonObject {
        let list = spots.map { spot -> PythonObject in
            let pts = spot.points.map { PythonObject([PythonObject(Double($0.x)), PythonObject(Double($0.y))]) }
            let dict = Python.dict()
            dict[PythonObject("kind")] = PythonObject("brush")
            dict[PythonObject("pts")] = PythonObject(pts)
            dict[PythonObject("r")] = PythonObject(spot.radius)
            return dict
        }
        return PythonObject(list)
    }

    /// `arr != Python.None` is NOT safe here: numpy overloads `!=` to
    /// compare element-wise, returning an array instead of a scalar bool,
    /// and PythonKit's operator crashes trying to coerce a multi-element
    /// array's truth value ("ambiguous", numpy's own error) — hit this for
    /// real while wiring M1 up. `isinstance` always returns a plain Python
    /// bool regardless of the checked type.
    private func numpyRGB8(_ arr: PythonObject) -> PythonObject? {
        Bool(Python.isinstance(arr, np.ndarray)) == true ? arr : nil
    }

    private func rgbaImage(fromRGB rgb: PythonObject) -> RGBAImage? {
        let shape = rgb.shape
        guard let height = Int(shape[0]), let width = Int(shape[1]) else { return nil }
        let alpha = np.full([height, width, 1], 255, dtype: np.uint8)
        let rgba = np.ascontiguousarray(np.concatenate([rgb, alpha], axis: -1))
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
