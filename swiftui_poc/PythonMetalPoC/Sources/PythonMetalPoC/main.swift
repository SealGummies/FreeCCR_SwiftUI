// Phase 1 feasibility PoC: embedded CPython (via PythonKit) calling FreeCCR's
// core.ccr_processor color math on a synthetic test image, with the result
// pushed through a Metal texture and written out as a PNG. This is the
// smallest slice that exercises the full proposed pipeline end to end:
//
//   Swift -> PythonKit -> embedded CPython -> numpy/opencv core module
//        -> pixels back to Swift -> MTLTexture -> PNG
//
// See /Users/seal/.claude/plans/precious-knitting-spindle.md Phase 1.

import Foundation
import Metal
import PythonKit
import CoreGraphics
import ImageIO
import UniformTypeIdentifiers

struct Stopwatch {
    private var last = DispatchTime.now()
    mutating func lap(_ label: String) {
        let now = DispatchTime.now()
        let ms = Double(now.uptimeNanoseconds - last.uptimeNanoseconds) / 1_000_000
        let padded = label.padding(toLength: 28, withPad: " ", startingAt: 0)
        let msStr = String(format: "%8.2f", ms)
        print("  [\(padded)] \(msStr) ms")
        last = now
    }
}

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(("ERROR: " + message + "\n").data(using: .utf8)!)
    exit(1)
}

var watch = Stopwatch()
let t0 = DispatchTime.now()

// --- 1. Point PythonKit at the embedded, standalone CPython 3.11 -----------
let repoRoot = "/Users/seal/FreeCCR"
let pocRoot = repoRoot + "/swiftui_poc"
let pythonHome = pocRoot + "/python"
let pythonLib = pythonHome + "/lib/libpython3.11.dylib"

guard FileManager.default.fileExists(atPath: pythonLib) else {
    fail("embedded libpython not found at \(pythonLib) — did Phase 1 setup run?")
}

setenv("PYTHONHOME", pythonHome, 1)
setenv("PYTHON_LIBRARY", pythonLib, 1)
// Keep the embedded interpreter from ever touching the user's real site
// PySide6/etc. — this must reproduce what an app-embedded interpreter would
// see, not whatever happens to be on the host's PYTHONPATH.
setenv("PYTHONNOUSERSITE", "1", 1)

print("== Phase 1 PoC: PythonKit + embedded CPython + FreeCCR core + Metal ==")
watch.lap("env setup")

// --- 2. Boot the interpreter and wire up sys.path ---------------------------
let sys = Python.import("sys")
sys.path.insert(0, pythonHome + "/lib/python3.11/site-packages")
sys.path.insert(0, repoRoot + "/src")
print("Python version reported by embedded interpreter:", sys.version)
watch.lap("interpreter boot")

// --- 3. Import FreeCCR's core (must work with ZERO PySide6 present) --------
let np = Python.import("numpy")
let cv2 = Python.import("cv2")
let rawpy = Python.import("rawpy")
let ccrProcessor = Python.import("core.ccr_processor")
let ccrImage = Python.import("core.ccr_image")
print("core.ccr_image QT_AVAILABLE (must be False in this environment):",
      ccrImage.QT_AVAILABLE)
print("rawpy loaded OK (LibRaw native module resolvable):", rawpy.__version__)
watch.lap("import core + deps")

// --- 4. Build a synthetic 16-bit RGB test frame (no real RAW file on hand,
//        this stands in for a decoded scan — see the PoC report for what a
//        real-RAW check would still need) and run it through the SAME
//        adjust_image() the Qt app's preview/export path calls. -------------
let w = 512
let h = 384
// x -> R gradient, y -> G gradient, constant B, all in 16-bit range —
// mirrors tests/test_slice.py's _coordinate_png helper so the result is
// visually/numerically checkable.
let xGrid = np.tile(np.arange(w, dtype: np.float64).reshape([1, w]), [h, 1]) * PythonObject(100.0)
let yGrid = np.tile(np.arange(h, dtype: np.float64).reshape([h, 1]), [1, w]) * PythonObject(150.0)
let bGrid = np.full([h, w], 20000.0)
let img = np.stack([xGrid, yGrid, bGrid], axis: -1).astype(np.uint16)
watch.lap("synthesize test frame")

let adjusted = ccrProcessor.adjust_image(
    img,
    exposure: PythonObject(15.0),
    contrast: PythonObject(10.0),
    saturation: PythonObject(20.0),
    kelvin_shift: PythonObject(-8.0)
)
watch.lap("ccr_processor.adjust_image")

// 16-bit -> 8-bit RGBA the same way ccr_image.to_8bit does (convertScaleAbs),
// then pad an opaque alpha channel for Metal.
let adjusted8 = cv2.convertScaleAbs(adjusted, alpha: PythonObject(255.0 / 65535.0))
let alpha = np.full([h, w, 1], 255, dtype: np.uint8)
let rgba = np.concatenate([adjusted8, alpha], axis: -1)
let rgbaContig = np.ascontiguousarray(rgba)
watch.lap("8-bit + RGBA pack (numpy/cv2)")

// --- 5. Pull the pixel bytes back into Swift --------------------------------
// Zero-copy: read the numpy array's own buffer address (ctypes.data) rather
// than marshalling element-by-element through PythonKit — this is the same
// technique real PythonKit<->numpy bridging would need for interactive
// preview performance, so it doubles as a first read on that cost.
let expectedByteCount = w * h * 4
guard let dataAddr = Int(rgbaContig.ctypes.data),
      let dataPtr = UnsafeRawPointer(bitPattern: dataAddr) else {
    fail("could not resolve numpy array's buffer address via ctypes.data")
}
let pixelData = Data(bytes: dataPtr, count: expectedByteCount)
watch.lap("numpy -> Swift Data (zero-copy read)")

// --- 6. Upload to a Metal texture (the real app's live-preview canvas) -----
guard let device = MTLCreateSystemDefaultDevice() else {
    fail("no Metal device available on this machine")
}
print("Metal device:", device.name)

let textureDescriptor = MTLTextureDescriptor.texture2DDescriptor(
    pixelFormat: .rgba8Unorm, width: w, height: h, mipmapped: false)
textureDescriptor.usage = [.shaderRead, .shaderWrite]
guard let texture = device.makeTexture(descriptor: textureDescriptor) else {
    fail("failed to create MTLTexture")
}
pixelData.withUnsafeBytes { raw in
    texture.replace(
        region: MTLRegionMake2D(0, 0, w, h),
        mipmapLevel: 0,
        withBytes: raw.baseAddress!,
        bytesPerRow: w * 4)
}
watch.lap("upload to MTLTexture")

// --- 7. Read the texture back and write a PNG, proving the round trip ------
var readback = [UInt8](repeating: 0, count: expectedByteCount)
readback.withUnsafeMutableBytes { raw in
    texture.getBytes(
        raw.baseAddress!, bytesPerRow: w * 4,
        from: MTLRegionMake2D(0, 0, w, h), mipmapLevel: 0)
}
watch.lap("read back from MTLTexture")

let outputPath = pocRoot + "/output_preview.png"
guard let colorSpace = CGColorSpace(name: CGColorSpace.sRGB),
      let provider = CGDataProvider(data: Data(readback) as CFData) else {
    fail("could not build CGDataProvider for PNG export")
}
guard let cgImage = CGImage(
    width: w, height: h, bitsPerComponent: 8, bitsPerPixel: 32,
    bytesPerRow: w * 4, space: colorSpace,
    bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.premultipliedLast.rawValue),
    provider: provider, decode: nil, shouldInterpolate: false,
    intent: .defaultIntent) else {
    fail("could not build CGImage from Metal readback")
}
guard let destination = CGImageDestinationCreateWithURL(
    URL(fileURLWithPath: outputPath) as CFURL, UTType.png.identifier as CFString, 1, nil) else {
    fail("could not create PNG destination at \(outputPath)")
}
CGImageDestinationAddImage(destination, cgImage, nil)
guard CGImageDestinationFinalize(destination) else {
    fail("failed to finalize PNG at \(outputPath)")
}
watch.lap("MTLTexture -> PNG on disk")

let totalMs = Double(DispatchTime.now().uptimeNanoseconds - t0.uptimeNanoseconds) / 1_000_000
print(String(format: "\nTOTAL end-to-end: %.2f ms", totalMs))
print("Wrote:", outputPath)
print("\nOK: PythonKit -> embedded CPython -> core.ccr_processor -> Metal texture -> PNG round trip succeeded.")
