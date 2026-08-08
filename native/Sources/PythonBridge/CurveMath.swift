import Foundation

/// Mirrors `core.ccr_processor`'s channel set — `identity_curves()` in
/// curve_editor.py builds one of these per channel.
public enum CurveChannel: String, CaseIterable, Sendable {
    case rgb, r, g, b
}

public struct CurvePoint: Sendable, Equatable {
    public var x: Double
    public var y: Double
    public init(_ x: Double, _ y: Double) {
        self.x = x
        self.y = y
    }
}

/// One channel's control points, 0...255 domain — matches
/// `curve_editor.py`'s `[[x, y], ...]` list-of-pairs exactly (see
/// `CoreBridge.AdjustmentParams.asPythonDict` for the conversion back).
public struct CurveSet: Sendable, Equatable {
    public static let identityPoints: [CurvePoint] = [CurvePoint(0, 0), CurvePoint(255, 255)]

    public var channels: [CurveChannel: [CurvePoint]]

    public init() {
        channels = Dictionary(uniqueKeysWithValues: CurveChannel.allCases.map { ($0, Self.identityPoints) })
    }

    public subscript(channel: CurveChannel) -> [CurvePoint] {
        get { channels[channel] ?? Self.identityPoints }
        set { channels[channel] = newValue }
    }

    public var isIdentity: Bool {
        CurveChannel.allCases.allSatisfy { self[$0] == Self.identityPoints }
    }
}

/// Direct port of `curve_editor.py`'s `_monotone_cubic` (Fritsch-Carlson
/// monotone cubic Hermite interpolation) — line-for-line, so the curve this
/// app draws and applies matches what `ccr_processor.apply_curves` (via
/// `build_channel_lut`, the same algorithm) actually does to the image.
/// `xs`/`ys` must be sorted ascending by `x` and have at least 2 points.
public func monotoneCubic(xs: [Double], ys: [Double], xq: [Double]) -> [Double] {
    let n = xs.count
    if n == 2 {
        let x0 = xs[0], x1 = xs[1]
        let y0 = ys[0], y1 = ys[1]
        let span = (x1 - x0) == 0 ? 1.0 : (x1 - x0)
        return xq.map { y0 + (y1 - y0) * ($0 - x0) / span }
    }

    var h = [Double](repeating: 0, count: n - 1)
    var delta = [Double](repeating: 0, count: n - 1)
    for i in 0..<(n - 1) {
        h[i] = xs[i + 1] - xs[i]
        delta[i] = (ys[i + 1] - ys[i]) / (h[i] == 0 ? 1.0 : h[i])
    }

    var m = [Double](repeating: 0, count: n)
    m[0] = delta[0]
    m[n - 1] = delta[n - 2]
    for i in 1..<(n - 1) {
        m[i] = (delta[i - 1] + delta[i]) / 2.0
    }
    for i in 0..<(n - 1) {
        if delta[i] == 0.0 {
            m[i] = 0.0
            m[i + 1] = 0.0
        } else {
            let a = m[i] / delta[i]
            let b = m[i + 1] / delta[i]
            let s = a * a + b * b
            if s > 9.0 {
                let t = 3.0 / s.squareRoot()
                m[i] = t * a * delta[i]
                m[i + 1] = t * b * delta[i]
            }
        }
    }

    var out = [Double]()
    out.reserveCapacity(xq.count)
    var seg = 0
    for q in xq {
        while seg < n - 2 && q > xs[seg + 1] {
            seg += 1
        }
        let x0 = xs[seg], x1 = xs[seg + 1]
        let y0 = ys[seg], y1 = ys[seg + 1]
        let hh = (x1 - x0) == 0 ? 1.0 : (x1 - x0)
        let t = (q - x0) / hh
        let t2 = t * t
        let t3 = t2 * t
        let h00 = 2 * t3 - 3 * t2 + 1
        let h10 = t3 - 2 * t2 + t
        let h01 = -2 * t3 + 3 * t2
        let h11 = t3 - t2
        out.append(h00 * y0 + h10 * hh * m[seg] + h01 * y1 + h11 * hh * m[seg + 1])
    }
    return out
}

/// `curve_editor.py`'s `CurveCanvas._curve_y_at`: the active channel's curve
/// value at a single input x.
public func curveValue(points: [CurvePoint], at x: Double) -> Double {
    monotoneCubic(xs: points.map(\.x), ys: points.map(\.y), xq: [x])[0]
}
