import CoreGraphics
import Foundation
import ImageIO
import PythonBridge
import Testing
import UniformTypeIdentifiers
@testable import FreeCCRNative

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
