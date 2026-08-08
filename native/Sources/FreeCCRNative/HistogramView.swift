import PythonBridge
import SwiftUI

/// Per-channel pixel-value counts (0...255), computed straight from the
/// already-decoded `RGBAImage` bytes — unlike everything else in this app,
/// this needs NO PythonKit call: `histogram_widget.py` itself is pure
/// presentation over a `(3, 256)` numpy array that `ccr_image.py` computes
/// from data Swift already has in hand (the fast ~1080px preview buffer),
/// so recomputing it here avoids a round trip for no reason.
struct Histogram: Equatable {
    var red: [Int]
    var green: [Int]
    var blue: [Int]

    static let empty = Histogram(
        red: Array(repeating: 0, count: 256),
        green: Array(repeating: 0, count: 256),
        blue: Array(repeating: 0, count: 256))

    /// Reads the RGBA8 buffer directly rather than going through
    /// `CGImage`/`NSImage` — this runs once per adjustment, so a tight byte
    /// loop matters more than convenience here.
    static func compute(from image: RGBAImage) -> Histogram {
        var red = [Int](repeating: 0, count: 256)
        var green = [Int](repeating: 0, count: 256)
        var blue = [Int](repeating: 0, count: 256)
        image.data.withUnsafeBytes { (raw: UnsafeRawBufferPointer) in
            guard let base = raw.bindMemory(to: UInt8.self).baseAddress else { return }
            let pixelCount = image.width * image.height
            var offset = 0
            for _ in 0..<pixelCount {
                red[Int(base[offset])] += 1
                green[Int(base[offset + 1])] += 1
                blue[Int(base[offset + 2])] += 1
                offset += 4
            }
        }
        return Histogram(red: red, green: green, blue: blue)
    }

    /// A high percentile of the non-zero bin counts, used as the chart's
    /// vertical scale instead of the raw max — a handful of saturated bins
    /// (pure black/white clipping) would otherwise flatten every other bin
    /// to near-invisible. Not `histogram_widget.py`'s exact smoothing/scale
    /// algorithm, but the same idea: percentile-clipped, not max-clipped.
    fileprivate static func percentileScale(_ channels: [[Int]], percentile: Double = 0.99) -> Int {
        let counts = channels.flatMap { $0 }.filter { $0 > 0 }.sorted()
        guard !counts.isEmpty else { return 1 }
        let index = min(counts.count - 1, Int(Double(counts.count) * percentile))
        return max(1, counts[index])
    }
}

/// Analog of `histogram_widget.py`'s self-painting RGB histogram — an
/// additive-blended (`.plusLighter`, matching Qt's Plus composition mode)
/// overlay of the three channel distributions, percentile-scaled so clipped
/// pixels don't crush the rest of the chart flat.
struct HistogramView: View {
    let histogram: Histogram?

    var body: some View {
        Canvas { context, size in
            guard let histogram else { return }
            let scale = Histogram.percentileScale([histogram.red, histogram.green, histogram.blue])
            let barWidth = size.width / 256
            for (channel, color) in [
                (histogram.red, Color.red), (histogram.green, Color.green), (histogram.blue, Color.blue),
            ] {
                var path = Path()
                for bin in 0..<256 {
                    let h = min(size.height, size.height * CGFloat(channel[bin]) / CGFloat(scale))
                    guard h > 0 else { continue }
                    path.addRect(CGRect(
                        x: CGFloat(bin) * barWidth, y: size.height - h,
                        width: max(1, barWidth), height: h))
                }
                context.blendMode = .plusLighter
                context.fill(path, with: .color(color.opacity(0.85)))
            }
        }
        .frame(height: 90)
        .background(Color.black.opacity(0.85))
        .clipShape(RoundedRectangle(cornerRadius: 4))
    }
}
