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
