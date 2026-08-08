import CoreGraphics
import Foundation
import ImageIO
import Metal
import PythonBridge
import Testing
import UniformTypeIdentifiers
@testable import FreeCCRNative

/// `.serialized`: every test in this suite either shares the
/// `PythonCoreBridge.shared` singleton (its embedded CPython, its
/// `ccr_backend.images` list) or a `PreviewState` that talks to it. Swift
/// Testing runs tests in parallel by default, and letting these race each
/// other for real caused a spurious crash ("Fatal error: Index out of
/// range") that had nothing to do with product code — confirmed by running
/// with `swift test --no-parallel`, where every test passes. `.serialized`
/// makes that the default here instead of relying on everyone remembering
/// the flag.
@Suite(.serialized)
struct FreeCCRNativeTests {

    // MARK: - M5: thumbnail_list.py analog

    /// `thumbnail(handle:)` must read `_thumb_np8` (the 156px-long-side
    /// thumbnail `CCRImage.__init__` already populates), not the full preview —
    /// confirms it's actually smaller than `adjustedPreview`'s ~1080px-long-side
    /// result rather than accidentally returning the same buffer twice.
    @Test func thumbnailIsSmallerThanThePreview() async throws {
        let testPNGPath = NSTemporaryDirectory() + "freeccr_native_test_scan_thumb.png"
        try makeTestPNG(at: testPNGPath, width: 2000, height: 1500)
        let handle = try #require(await PythonCoreBridge.shared.loadImage(path: testPNGPath))

        let thumbnail = try #require(await PythonCoreBridge.shared.thumbnail(handle: handle))
        let preview = try #require(await PythonCoreBridge.shared.adjustedPreview(
            handle: handle, params: AdjustmentParams()))

        #expect(max(thumbnail.width, thumbnail.height) <= 156)
        #expect(max(preview.width, preview.height) > max(thumbnail.width, thumbnail.height))
    }

    /// `originalSize` must report the REAL source dimensions
    /// (`CCRImage.original_full_size`), not whatever the ~1080px-capped
    /// `resized_raw`/preview happens to be — this is what the Full/100%/200%
    /// zoom model's ratios are computed against.
    @Test func originalSizeReportsTheRealSourceDimensions() async throws {
        let testPNGPath = NSTemporaryDirectory() + "freeccr_native_test_origsize.png"
        try makeTestPNG(at: testPNGPath, width: 3200, height: 1800)
        let handle = try #require(await PythonCoreBridge.shared.loadImage(path: testPNGPath))

        let size = try #require(await PythonCoreBridge.shared.originalSize(handle: handle))
        #expect(size.width == 3200)
        #expect(size.height == 1800)
    }

    /// User-reported bug: the thumbnail list stayed frozen at whatever the
    /// image looked like on load, because runAdjustment only ever updated the
    /// canvas texture — nothing re-read _thumb_np8 after a slider change, even
    /// though set_adjustment_by_index (called by adjustedPreview) already
    /// refreshes it on the Python side for free.
    @MainActor
    @Test func thumbnailRefreshesAfterAnAdjustment() async throws {
        guard let device = MTLCreateSystemDefaultDevice() else {
            Issue.record("no Metal device on this machine")
            return
        }
        let state = PreviewState(device: device)
        let path = NSTemporaryDirectory() + "freeccr_native_test_thumb_refresh.png"
        try makeTestPNG(at: path, width: 300, height: 200)

        state.loadImages(urls: [URL(fileURLWithPath: path)])
        try await waitUntil { !state.isBusy && state.images.first?.thumbnail != nil }
        let before = state.images[0].thumbnail?.tiffRepresentation
        #expect(before != nil)

        state.params.exposure = 90
        state.params.contrast = 90
        try await waitUntil(timeout: 5) { state.images[0].thumbnail?.tiffRepresentation != before }

        #expect(state.images[0].thumbnail?.tiffRepresentation != before,
                "thumbnail should reflect the new exposure/contrast, not stay frozen at load-time state")
    }

    /// M5 regression: switching the selected image must not bleed one image's
    /// slider values into another — each `LoadedImage` keeps its own
    /// `AdjustmentParams`/`ColorProfile`, mirroring every `CCRImage` instance
    /// owning its own `adjustment_settings` in the real app.
    @MainActor
    @Test func perImageAdjustmentsPersistIndependently() async throws {
        guard let device = MTLCreateSystemDefaultDevice() else {
            Issue.record("no Metal device on this machine")
            return
        }
        let state = PreviewState(device: device)

        let path1 = NSTemporaryDirectory() + "freeccr_native_test_multi_1.png"
        let path2 = NSTemporaryDirectory() + "freeccr_native_test_multi_2.png"
        try makeTestPNG(at: path1, width: 48, height: 32)
        try makeTestPNG(at: path2, width: 48, height: 32)

        state.loadImages(urls: [URL(fileURLWithPath: path1), URL(fileURLWithPath: path2)])
        try await waitUntil { state.images.count == 2 }

        // Image 0 (selected by loadImages, being the last one added... actually
        // both get added then the LAST is selected — select 0 explicitly first).
        state.selectImage(at: 0)
        try await waitUntil { !state.isBusy }
        state.params.exposure = 42

        state.selectImage(at: 1)
        try await waitUntil { !state.isBusy }
        #expect(state.params.exposure == 0, "image 1 should start at its own default, not image 0's exposure")
        state.params.exposure = -17

        state.selectImage(at: 0)
        try await waitUntil { !state.isBusy }
        #expect(state.params.exposure == 42, "switching back to image 0 should restore its own exposure")

        state.selectImage(at: 1)
        try await waitUntil { !state.isBusy }
        #expect(state.params.exposure == -17, "image 1's own exposure should have survived the round trip")
    }

    /// Regression for a user-reported "preview looks like a blown-up thumbnail"
    /// report: confirms the full PreviewState pipeline (not just CoreBridge)
    /// puts a properly ~1080px-long-side MTLTexture on the canvas for a large
    /// source image, not something thumbnail-sized. It's genuinely 1080px, by
    /// design (matches the Qt app's own default preview resolution) — the
    /// visible softness the report described is a missing feature
    /// (image_preview.py's HiResDetailWorker, which swaps in a higher-res tile
    /// once zoomed in past a threshold — not built yet, tracked as a follow-up
    /// to M2), not wrong data. This test exists to keep that distinction
    /// pinned down: if this ever starts failing, the DATA pipeline broke, which
    /// is a different bug from "no hi-res zoom yet".
    @MainActor
    @Test func previewTextureIsFullPreviewResolutionNotThumbnailSized() async throws {
        guard let device = MTLCreateSystemDefaultDevice() else {
            Issue.record("no Metal device on this machine")
            return
        }
        let state = PreviewState(device: device)
        let path = NSTemporaryDirectory() + "freeccr_native_test_large.png"
        try makeTestPNG(at: path, width: 3000, height: 2000)

        state.loadImages(urls: [URL(fileURLWithPath: path)])
        try await waitUntil { !state.isBusy && state.texture != nil }

        let texture = try #require(state.texture)
        #expect(max(texture.width, texture.height) == 1080, "expected the ~1080px-long-side preview, not thumbnail-sized data")
        let thumbnail = try #require(state.images.first?.thumbnail)
        #expect(max(thumbnail.size.width, thumbnail.size.height) <= 156)
    }

    /// The actual fix for the "looks like a blown-up thumbnail" report:
    /// selecting the "100%" zoom preset must trigger the hi-res path
    /// (`PythonCoreBridge.hiResPreview`, a port of `HiResDetailWorker`) and
    /// deliver a texture well past the fast path's fixed 1080px cap — this
    /// is what `resized_raw`'s permanent decode-time cap made impossible
    /// with the earlier (abandoned) "just ask for a bigger preview_size"
    /// approach; see the CoreBridge.swift doc comments on `adjustedPreview`
    /// vs `hiResPreview` for why those are two different Python-side calls.
    @MainActor
    @Test func oneHundredPercentZoomTriggersHiResRender() async throws {
        guard let device = MTLCreateSystemDefaultDevice() else {
            Issue.record("no Metal device on this machine")
            return
        }
        let state = PreviewState(device: device)
        let path = NSTemporaryDirectory() + "freeccr_native_test_hires.png"
        try makeTestPNG(at: path, width: 3000, height: 2000)

        state.loadImages(urls: [URL(fileURLWithPath: path)])
        try await waitUntil { !state.isBusy && state.texture != nil }

        state.updateCanvasViewSize(CGSize(width: 900, height: 600))
        try await waitUntil { !state.isBusy }

        state.selectZoomPreset(.oneHundred)
        try await waitUntil(timeout: 10) { !state.isBusy }

        let texture = try #require(state.texture)
        #expect(max(texture.width, texture.height) > 1080,
                "100% zoom should trigger the hi-res path and exceed the fast path's 1080px cap")
        #expect(max(texture.width, texture.height) <= 3000)
    }

    // MARK: - M4: monotoneCubic vs. curve_editor.py's _monotone_cubic

    /// Reference values generated by running curve_editor.py's actual
    /// `_monotone_cubic` (the Fritsch-Carlson implementation this file ports) on
    /// the same inputs — see the M4 implementation notes for the exact command.
    /// If this ever fails after touching CurveMath.swift, the port has drifted
    /// from what ccr_processor.apply_curves actually does to pixels, which won't
    /// show up as a crash — it'll show up as curves editor previews vs. exports
    /// simply looking different, silently.
    @Test func monotoneCubicMatchesThePythonReferenceImplementation() {
        let cases: [(xs: [Double], ys: [Double], xq: [Double], expected: [Double])] = [
            ([0, 255], [0, 255], [0, 64, 127.5, 191, 255],
             [0.0, 64.0, 127.5, 191.0, 255.0]),
            ([0, 127.5, 255], [0, 180, 255], [0, 32, 64, 96, 127.5, 160, 200, 255],
             [0.0, 47.65350521292716, 96.94107515209082, 142.88270303277022,
              180.0, 206.54714250175275, 228.2021620643644, 255.0]),
            ([0, 50, 150, 255], [0, 10, 240, 255], [0, 25, 50, 75, 100, 150, 200, 255],
             [0.0, 1.8895620094623782, 10.0, 52.27370286845988, 127.08493150380912,
              240.0, 252.18346598917535, 255.0]),
        ]
        for testCase in cases {
            let actual = monotoneCubic(xs: testCase.xs, ys: testCase.ys, xq: testCase.xq)
            #expect(actual.count == testCase.expected.count)
            for (a, e) in zip(actual, testCase.expected) {
                #expect(abs(a - e) < 1e-9, "got \(a), expected \(e) for xq in \(testCase.xq)")
            }
        }
    }

    @Test func curveSetDefaultsToIdentity() {
        #expect(CurveSet().isIdentity)
        var curves = CurveSet()
        curves[.r] = [CurvePoint(0, 0), CurvePoint(128, 100), CurvePoint(255, 255)]
        #expect(!curves.isIdentity)
    }

    /// M4 regression: a real (non-identity) curve on the "rgb" channel must
    /// visibly change adjust_image's output — proves AdjustmentParams.curves
    /// actually reaches ccr_processor.apply_curves via the same
    /// adjustment_settings dict (`s.get('curves')`), not silently dropped like
    /// the earlier band-adjustment test almost mistook for a real bug.
    @Test func nonIdentityCurveChangesTheOutput() async throws {
        let testPNGPath = NSTemporaryDirectory() + "freeccr_native_test_scan_curve.png"
        try makeTestPNG(at: testPNGPath, width: 64, height: 48)
        let handle = await PythonCoreBridge.shared.loadImage(path: testPNGPath)
        #expect(handle != nil)

        let identity = await PythonCoreBridge.shared.adjustedPreview(
            handle: handle, params: AdjustmentParams())
        #expect(identity != nil)

        var curved = AdjustmentParams()
        // A strong S-curve-ish lift: pulls shadows down, pushes highlights up.
        curved.curves[.rgb] = [CurvePoint(0, 0), CurvePoint(64, 10), CurvePoint(192, 245), CurvePoint(255, 255)]
        let curvedImage = await PythonCoreBridge.shared.adjustedPreview(handle: handle, params: curved)
        #expect(curvedImage != nil)

        guard let identity, let curvedImage else { return }
        #expect(identity.data != curvedImage.data,
                "a non-identity rgb curve should change adjust_image's output")
    }

    /// End-to-end regression check for M1: real image load through
    /// core.ccr_image.CCRImage (not the synthetic-frame fallback), via the same
    /// ccr_backend.set_adjustment_by_index call chain sliders_panel.py uses.
    /// Requires swiftui_poc/python (see swiftui_poc/setup_embedded_python.sh);
    /// run with the DYLD_LIBRARY_PATH documented in native/README.md.
    @Test func loadsRealImageAndAdjustsIt() async throws {
        let testPNGPath = NSTemporaryDirectory() + "freeccr_native_test_scan.png"
        try makeTestPNG(at: testPNGPath, width: 300, height: 200)

        let handle = await PythonCoreBridge.shared.loadImage(path: testPNGPath)
        #expect(handle != nil, "CCRImage should decode a plain 8-bit RGB PNG")

        var params = AdjustmentParams()
        params.exposure = 20
        params.contrast = 10
        let image = await PythonCoreBridge.shared.adjustedPreview(handle: handle, params: params)
        #expect(image != nil)
        if let image {
            #expect(image.width == 300 && image.height == 200)
            #expect(image.data.count == image.width * image.height * 4)
        }
    }

    /// M3 (Subtractive Saturations) regression: a big band_red_bright push on a
    /// red-dominant test image must visibly change the result — proves
    /// AdjustmentParams.bands actually reaches ccr_processor's band_settings
    /// (routed through the SAME adjustment_settings dict as every other slider,
    /// gated by ccr_image.apply_adjustments' `any(s.get(k, 0) for k in
    /// BAND_ADJUSTMENT_KEYS)`), not silently dropped.
    ///
    /// Deliberately a GRADIENT (makeTestPNG), not a solid color: a flat
    /// single-color image round-trips through
    /// update_thumbnail_and_preview's un-converted-negative auto-brightness
    /// stretch (a legitimate feature — see ccr_image.py's
    /// `_auto_brightness_for_preview`) and comes out bit-identical regardless of
    /// the band push, since a percentile-based stretch on a perfectly uniform
    /// image just re-normalizes to the same result either way. Hit this for
    /// real while writing this test; false failure, not a product bug.
    @Test func bandAdjustmentChangesTheMatchingColor() async throws {
        let testPNGPath = NSTemporaryDirectory() + "freeccr_native_test_scan_red.png"
        try makeTestPNG(at: testPNGPath, width: 64, height: 48)
        let handle = await PythonCoreBridge.shared.loadImage(path: testPNGPath)
        #expect(handle != nil)

        let baseline = await PythonCoreBridge.shared.adjustedPreview(
            handle: handle, params: AdjustmentParams())
        #expect(baseline != nil)

        var redBand = BandAdjustment()
        redBand.brightness = 80
        var banded = AdjustmentParams()
        banded.bands[.red] = redBand
        let bandedImage = await PythonCoreBridge.shared.adjustedPreview(handle: handle, params: banded)
        #expect(bandedImage != nil)

        guard let baseline, let bandedImage else { return }
        #expect(baseline.data != bandedImage.data,
                "a strong band_red_bright push should visibly change a red-dominant image")
    }

    @Test func bandFeatherDefaultsToTen() {
        #expect(AdjustmentParams().bandFeather == 10)
    }

    /// M3 regression: colorProfile is a separate CCRImage attribute, not an
    /// adjustment_settings key (see CoreBridge.swift's doc comment on
    /// ColorProfile) — verify it actually reaches core.ccr_image._to_grayscale
    /// by checking the returned pixels are colorless (R == G == B everywhere),
    /// which a plain color adjustment on this red/green test gradient would not
    /// produce.
    @Test func blackAndWhiteColorProfileGraysOutThePreview() async throws {
        let testPNGPath = NSTemporaryDirectory() + "freeccr_native_test_scan_bw.png"
        try makeTestPNG(at: testPNGPath, width: 64, height: 48)
        let handle = await PythonCoreBridge.shared.loadImage(path: testPNGPath)
        #expect(handle != nil)

        let image = await PythonCoreBridge.shared.adjustedPreview(
            handle: handle, params: AdjustmentParams(), colorProfile: .blackAndWhite)
        #expect(image != nil)
        guard let image else { return }
        var allNeutral = true
        image.data.withUnsafeBytes { (raw: UnsafeRawBufferPointer) in
            let bytes = raw.bindMemory(to: UInt8.self)
            for pixel in stride(from: 0, to: bytes.count, by: 4) {
                if bytes[pixel] != bytes[pixel + 1] || bytes[pixel + 1] != bytes[pixel + 2] {
                    allNeutral = false
                    break
                }
            }
        }
        #expect(allNeutral, "Black & White color profile should make every pixel's R/G/B equal")
    }

    /// cineonLog just needs to not crash the pipeline (it's a boolean flag inside
    /// adjustment_settings, not a separate call path) — a real numeric assertion
    /// on the Cineon->Rec.709 curve belongs with ccr_processor's own Python
    /// tests, not here.
    @Test func cineonLogFlagDoesNotCrashThePipeline() async throws {
        let testPNGPath = NSTemporaryDirectory() + "freeccr_native_test_scan_cineon.png"
        try makeTestPNG(at: testPNGPath, width: 64, height: 48)
        let handle = await PythonCoreBridge.shared.loadImage(path: testPNGPath)
        #expect(handle != nil)

        var params = AdjustmentParams()
        params.cineonLog = true
        let image = await PythonCoreBridge.shared.adjustedPreview(handle: handle, params: params)
        #expect(image != nil)
    }

    // MARK: - M2: CanvasTransform coordinate-stack checks (pure math, no Python)

    @Test func fitScaleFillsTheSmallerDimension() {
        let t = CanvasTransform()
        // A 500x500 image in a 1000x500 view: fit scale is limited by height.
        let scale = t.fitScale(viewSize: CGSize(width: 1000, height: 500),
                                imageSize: CGSize(width: 500, height: 500))
        #expect(scale == 1.0)
        let rect = t.quadRect(viewSize: CGSize(width: 1000, height: 500),
                               imageSize: CGSize(width: 500, height: 500))
        #expect(rect == CGRect(x: 250, y: 0, width: 500, height: 500))
    }

    @Test func zoomAnchoredAtCenterKeepsImageCentered() {
        var t = CanvasTransform()
        let viewSize = CGSize(width: 1000, height: 500)
        let imageSize = CGSize(width: 500, height: 500)
        let center = CGPoint(x: viewSize.width / 2, y: viewSize.height / 2)
        t.zoom(by: 2, anchor: center, viewSize: viewSize, imageSize: imageSize)
        let rect = t.quadRect(viewSize: viewSize, imageSize: imageSize)
        #expect(abs(rect.midX - center.x) < 0.001)
        #expect(abs(rect.midY - center.y) < 0.001)
        #expect(abs(rect.width - 1000) < 0.001) // fitScale(1) * zoom(2) * 500
    }

    /// The actual point of scroll-to-zoom: whatever image location was under the
    /// cursor before zooming must still be under the cursor after — otherwise
    /// every zoom gesture would visibly "slide" the image out from under you.
    @Test func zoomAnchoredAtArbitraryPointStaysUnderTheAnchor() {
        var t = CanvasTransform()
        let viewSize = CGSize(width: 800, height: 600)
        let imageSize = CGSize(width: 400, height: 300)
        let anchor = CGPoint(x: 300, y: 150) // an arbitrary point, not the center
        let before = t.imageNormalizedPoint(screen: anchor, viewSize: viewSize, imageSize: imageSize)
        #expect(before != nil)

        t.zoom(by: 3.7, anchor: anchor, viewSize: viewSize, imageSize: imageSize)
        let after = t.imageNormalizedPoint(screen: anchor, viewSize: viewSize, imageSize: imageSize)
        #expect(after != nil)
        if let before, let after {
            #expect(abs(before.x - after.x) < 0.0001)
            #expect(abs(before.y - after.y) < 0.0001)
        }
    }

    @Test func zoomClampsToMinAndMax() {
        var t = CanvasTransform()
        let viewSize = CGSize(width: 800, height: 600)
        let imageSize = CGSize(width: 400, height: 300)
        let anchor = CGPoint(x: 400, y: 300)
        t.zoom(by: 0.0001, anchor: anchor, viewSize: viewSize, imageSize: imageSize)
        #expect(t.zoom == CanvasTransform.minZoom)
        t.zoom = 1
        t.zoom(by: 10_000, anchor: anchor, viewSize: viewSize, imageSize: imageSize)
        #expect(t.zoom == CanvasTransform.maxZoom)
    }

    @Test func resetToFitClearsZoomAndPan() {
        var t = CanvasTransform()
        t.zoom = 5
        t.panOffset = CGSize(width: 123, height: -45)
        t.resetToFit()
        #expect(t.zoom == 1.0)
        #expect(t.panOffset == .zero)
    }

    /// At "Full" zoom the image never exceeds the viewport on either axis
    /// (that's what fit-to-view means), so `clampPan` must force any
    /// attempted pan back to zero — this is the mechanism behind "can't drag
    /// at Full zoom" from the product requirement, not a special case.
    @Test func clampPanLocksToZeroAtFullZoom() {
        var t = CanvasTransform()
        let viewSize = CGSize(width: 800, height: 600)
        let imageSize = CGSize(width: 400, height: 300) // fits exactly at zoom 1
        t.panOffset = CGSize(width: 999, height: -999)
        t.clampPan(viewSize: viewSize, imageSize: imageSize)
        #expect(t.panOffset == .zero)
    }

    /// Once zoomed in past fit, the image is larger than the viewport on
    /// both axes — panning should be allowed, but clamped so the image's
    /// near edge never crosses the viewport edge (no gap ever shows).
    @Test func clampPanKeepsImageCoveringTheViewportWhenZoomedIn() {
        var t = CanvasTransform()
        let viewSize = CGSize(width: 800, height: 600)
        let imageSize = CGSize(width: 400, height: 300)
        t.zoom = 4 // effective scale 4x fit -> rendered image is 1600x1200, bigger than the view

        // Try to drag far past any reasonable limit.
        t.panOffset = CGSize(width: 100_000, height: 100_000)
        t.clampPan(viewSize: viewSize, imageSize: imageSize)
        let rect = t.quadRect(viewSize: viewSize, imageSize: imageSize)
        #expect(rect.minX <= 0)
        #expect(rect.minY <= 0)

        t.panOffset = CGSize(width: -100_000, height: -100_000)
        t.clampPan(viewSize: viewSize, imageSize: imageSize)
        let rect2 = t.quadRect(viewSize: viewSize, imageSize: imageSize)
        #expect(rect2.maxX >= viewSize.width)
        #expect(rect2.maxY >= viewSize.height)
    }

    /// Mixed case: a wide/short image zoomed so its width exceeds the
    /// viewport but its height still doesn't — the height axis should lock
    /// to centered (0) while the width axis still allows panning within its
    /// own bounds.
    @Test func clampPanLocksOnlyTheAxisThatFits() {
        var t = CanvasTransform()
        let viewSize = CGSize(width: 800, height: 600)
        let imageSize = CGSize(width: 400, height: 100) // wide, short
        // fitScale = min(800/400, 600/100) = 2 -> at zoom 2, effective scale
        // 4: rendered 1600x400 — wider than the 800pt view, shorter than
        // the 600pt view.
        t.zoom = 2
        t.panOffset = CGSize(width: 100_000, height: 100_000)
        t.clampPan(viewSize: viewSize, imageSize: imageSize)
        #expect(t.panOffset.height == 0)
        #expect(t.panOffset.width > 0) // clamped, but still allowed to move
        let rect = t.quadRect(viewSize: viewSize, imageSize: imageSize)
        #expect(rect.minX <= 0) // clamped so no gap opens up on the width axis
    }
}

/// Polls a condition on the main actor with a short timeout — `PreviewState`
/// is deliberately fire-and-forget (`Task { ... }` internally) to stay
/// SwiftUI-friendly, so tests observe its published state converging rather
/// than awaiting a returned Task.
@MainActor
private func waitUntil(timeout: TimeInterval = 5, _ condition: () -> Bool) async throws {
    let deadline = Date().addingTimeInterval(timeout)
    while !condition() {
        if Date() > deadline {
            Issue.record("condition did not become true within \(timeout)s")
            return
        }
        try await Task.sleep(nanoseconds: 10_000_000)
    }
}

/// A plain 8-bit RGB gradient PNG, written with CoreGraphics/ImageIO —
/// no Python/numpy needed just to produce a decodable fixture.
private func makeTestPNG(at path: String, width: Int, height: Int) throws {
    let colorSpace = CGColorSpaceCreateDeviceRGB()
    guard let context = CGContext(
        data: nil, width: width, height: height, bitsPerComponent: 8,
        bytesPerRow: width * 4, space: colorSpace,
        bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue
    ) else {
        throw TestFixtureError.contextCreationFailed
    }
    for y in 0..<height {
        for x in 0..<width {
            context.setFillColor(
                red: CGFloat(x) / CGFloat(width), green: CGFloat(y) / CGFloat(height),
                blue: 0.5, alpha: 1.0)
            context.fill(CGRect(x: x, y: y, width: 1, height: 1))
        }
    }
    guard let cgImage = context.makeImage() else { throw TestFixtureError.imageCreationFailed }
    try writePNG(cgImage, to: path)
}

private func writePNG(_ cgImage: CGImage, to path: String) throws {
    guard let destination = CGImageDestinationCreateWithURL(
        URL(fileURLWithPath: path) as CFURL, UTType.png.identifier as CFString, 1, nil) else {
        throw TestFixtureError.destinationCreationFailed
    }
    CGImageDestinationAddImage(destination, cgImage, nil)
    guard CGImageDestinationFinalize(destination) else { throw TestFixtureError.writeFailed }
}

private enum TestFixtureError: Error {
    case contextCreationFailed, imageCreationFailed, destinationCreationFailed, writeFailed
}
