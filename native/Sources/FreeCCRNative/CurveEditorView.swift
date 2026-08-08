import AppKit
import PythonBridge
import SwiftUI

/// SwiftUI composite matching `curve_editor.py`'s `CurveEditor(QWidget)`:
/// channel selector buttons + the interactive canvas + a reset button.
struct CurveEditorControl: View {
    @Binding var curves: CurveSet
    @State private var channel: CurveChannel = .rgb

    private static let channelLabels: [(CurveChannel, String)] = [
        (.rgb, "All"), (.r, "R"), (.g, "G"), (.b, "B"),
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) {
                ForEach(Self.channelLabels, id: \.0) { ch, label in
                    Button(label) { channel = ch }
                        .buttonStyle(.bordered)
                        .tint(channel == ch ? channelTint(ch) : nil)
                }
            }
            CurveCanvasView(curves: $curves, channel: channel)
                .frame(height: 220)
            Button("Reset Curve") { curves = CurveSet() }
        }
    }

    private func channelTint(_ channel: CurveChannel) -> Color {
        switch channel {
        case .rgb: return .gray
        case .r: return Color(hex: "#d06666")
        case .g: return Color(hex: "#66aa66")
        case .b: return Color(hex: "#6688d0")
        }
    }
}

/// Bridges `CurveCanvasNSView` into SwiftUI. `curves` is the FULL 4-channel
/// set (only `curves[channel]` is shown/edited at a time — the other 3
/// channels' points persist untouched, matching curve_editor.py's
/// "_points stays populated for every channel regardless of which is
/// active" design, same pattern as M3's per-color-band pages).
struct CurveCanvasView: NSViewRepresentable {
    @Binding var curves: CurveSet
    var channel: CurveChannel

    func makeNSView(context: Context) -> CurveCanvasNSView {
        let view = CurveCanvasNSView()
        view.channel = channel
        view.points = curves[channel]
        view.onChange = { ch, points in curves[ch] = points }
        return view
    }

    func updateNSView(_ nsView: CurveCanvasNSView, context: Context) {
        nsView.onChange = { ch, points in curves[ch] = points }
        nsView.channel = channel
        // Only push external state in when it actually differs — the view's
        // own mouse handlers already update `curves` (and thus re-trigger
        // this method); re-assigning the same points here is harmless but
        // pointless, so skip it rather than fight an in-progress drag.
        let external = curves[channel]
        if nsView.points != external {
            nsView.points = external
        }
    }
}

/// Direct port of `curve_editor.py`'s `CurveCanvas(QWidget)`: same hit-test
/// constants, same click-on-line-inserts-a-point / click-on-point-grabs-it /
/// right-click-deletes-an-interior-point interaction, same
/// endpoints-locked/interior-points-clamped-between-neighbors drag rule. A
/// plain `NSView` (not Metal) — this is 2D vector drawing, Core Graphics is
/// the natural fit, matching how QPainter drew the Qt version.
final class CurveCanvasNSView: NSView {
    static let pointHit: CGFloat = 11
    static let pointRadius: CGFloat = 4
    static let curveHit: CGFloat = 8

    var channel: CurveChannel = .rgb {
        didSet { if oldValue != channel { needsDisplay = true } }
    }
    var points: [CurvePoint] = CurveSet.identityPoints {
        didSet { needsDisplay = true }
    }
    var onChange: ((CurveChannel, [CurvePoint]) -> Void)?

    private var dragIndex: Int?

    override var isFlipped: Bool { true }
    override var acceptsFirstResponder: Bool { true }

    // MARK: - Geometry (mirrors _plot_rect/_to_widget/_to_curve exactly)

    private func plotRect() -> CGRect {
        let left: CGFloat = 10, top: CGFloat = 10, bottom: CGFloat = 10, right: CGFloat = 22
        return CGRect(x: left, y: top,
                      width: max(1, bounds.width - left - right),
                      height: max(1, bounds.height - top - bottom))
    }

    private func toWidget(_ x: Double, _ y: Double) -> CGPoint {
        let r = plotRect()
        let wx = r.minX + CGFloat(x / 255.0) * r.width
        let wy = r.maxY - CGFloat(y / 255.0) * r.height
        return CGPoint(x: wx, y: wy)
    }

    private func toCurve(_ pos: CGPoint) -> (x: Double, y: Double) {
        let r = plotRect()
        let x = Double((pos.x - r.minX) / r.width) * 255.0
        let y = Double((r.maxY - pos.y) / r.height) * 255.0
        return (min(255, max(0, x)), min(255, max(0, y)))
    }

    private func pointIndex(at pos: CGPoint) -> Int? {
        let half = Self.pointHit / 2
        for (i, p) in points.enumerated() {
            let w = toWidget(p.x, p.y)
            if abs(w.x - pos.x) <= half && abs(w.y - pos.y) <= half {
                return i
            }
        }
        return nil
    }

    // MARK: - Mouse (mirrors mousePressEvent/mouseMoveEvent/mouseReleaseEvent)

    override func mouseDown(with event: NSEvent) {
        let pos = convert(event.locationInWindow, from: nil)
        if let idx = pointIndex(at: pos) {
            dragIndex = idx
        } else {
            let (xc, _) = toCurve(pos)
            let curveY = curveValue(points: points, at: xc)
            let onLine = abs(pos.y - toWidget(xc, curveY).y) <= Self.curveHit
            guard onLine else { return }
            let x = min(254.0, max(1.0, xc))
            var insertAt = 0
            while insertAt < points.count && points[insertAt].x < x { insertAt += 1 }
            points.insert(CurvePoint(x, curveY), at: insertAt)
            dragIndex = insertAt
        }
        onChange?(channel, points)
    }

    override func mouseDragged(with event: NSEvent) {
        guard let i = dragIndex else { return }
        let pos = convert(event.locationInWindow, from: nil)
        var (x, y) = toCurve(pos)
        let last = points.count - 1
        if i == 0 {
            x = 0.0 // endpoints: X locked, Y free
        } else if i == last {
            x = 255.0
        } else {
            let lo = points[i - 1].x + 1.0
            let hi = points[i + 1].x - 1.0
            x = min(hi, max(lo, x))
        }
        points[i] = CurvePoint(x, min(255, max(0, y)))
        onChange?(channel, points)
    }

    override func mouseUp(with event: NSEvent) {
        dragIndex = nil
    }

    override func rightMouseDown(with event: NSEvent) {
        let pos = convert(event.locationInWindow, from: nil)
        guard let idx = pointIndex(at: pos), idx != 0, idx != points.count - 1 else { return }
        points.remove(at: idx)
        dragIndex = nil
        onChange?(channel, points)
    }

    // MARK: - Drawing (mirrors paintEvent)

    override func draw(_ dirtyRect: NSRect) {
        guard let ctx = NSGraphicsContext.current?.cgContext else { return }
        let r = plotRect()

        ctx.setFillColor(NSColor(hex: "#232323").cgColor)
        ctx.fill(bounds)
        ctx.setStrokeColor(NSColor(hex: "#5a5a5a").cgColor)
        ctx.stroke(r)

        ctx.setStrokeColor(NSColor(hex: "#3d3d3d").cgColor)
        for k in 1..<4 {
            let gx = r.minX + r.width * CGFloat(k) / 4.0
            let gy = r.minY + r.height * CGFloat(k) / 4.0
            ctx.move(to: CGPoint(x: gx, y: r.minY))
            ctx.addLine(to: CGPoint(x: gx, y: r.maxY))
            ctx.move(to: CGPoint(x: r.minX, y: gy))
            ctx.addLine(to: CGPoint(x: r.maxX, y: gy))
        }
        ctx.strokePath()

        ctx.saveGState()
        ctx.setStrokeColor(NSColor(hex: "#5a5a5a").cgColor)
        ctx.setLineDash(phase: 0, lengths: [4, 3])
        ctx.move(to: toWidget(0, 0))
        ctx.addLine(to: toWidget(255, 255))
        ctx.strokePath()
        ctx.restoreGState()

        let activePoints = points
        let sampleCount = max(2, Int(r.width))
        let xq = (0..<sampleCount).map { 255.0 * Double($0) / Double(sampleCount - 1) }
        let yq = monotoneCubic(xs: activePoints.map(\.x), ys: activePoints.map(\.y), xq: xq)
        let path = CGMutablePath()
        path.move(to: toWidget(xq[0], min(255, max(0, yq[0]))))
        for i in 1..<xq.count {
            path.addLine(to: toWidget(xq[i], min(255, max(0, yq[i]))))
        }
        ctx.setStrokeColor(lineColor(for: channel).cgColor)
        ctx.setLineWidth(2)
        ctx.addPath(path)
        ctx.strokePath()

        ctx.setFillColor(NSColor(hex: "#f0f0f0").cgColor)
        ctx.setStrokeColor(NSColor(hex: "#222222").cgColor)
        for p in activePoints {
            let w = toWidget(p.x, p.y)
            let rect = CGRect(x: w.x - Self.pointRadius, y: w.y - Self.pointRadius,
                              width: Self.pointRadius * 2, height: Self.pointRadius * 2)
            ctx.fillEllipse(in: rect)
            ctx.strokeEllipse(in: rect)
        }
    }

    private func lineColor(for channel: CurveChannel) -> NSColor {
        switch channel {
        case .rgb: return NSColor(hex: "#e8e8e8")
        case .r: return NSColor(hex: "#d06666")
        case .g: return NSColor(hex: "#66aa66")
        case .b: return NSColor(hex: "#6688d0")
        }
    }
}

extension NSColor {
    convenience init(hex: String) {
        var value: UInt64 = 0
        Scanner(string: hex.trimmingCharacters(in: CharacterSet(charactersIn: "#")))
            .scanHexInt64(&value)
        let r = CGFloat((value >> 16) & 0xFF) / 255
        let g = CGFloat((value >> 8) & 0xFF) / 255
        let b = CGFloat(value & 0xFF) / 255
        self.init(red: r, green: g, blue: b, alpha: 1.0)
    }
}
