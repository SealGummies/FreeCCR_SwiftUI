import AppKit
import Metal
import MetalKit
import SwiftUI

/// AppKit bridge for the SwiftUI canvas — the closest analog here to
/// `widgets/image_preview.py`'s `GraphicsImageView(QGraphicsView)`. Owns the
/// render pipeline; pan/zoom state lives in `PreviewState.transform`
/// (`CanvasTransform`), gestures here just mutate it.
struct MetalCanvasView: NSViewRepresentable {
    @ObservedObject var state: PreviewState

    func makeNSView(context: Context) -> ZoomPanMTKView {
        let view = ZoomPanMTKView()
        view.device = state.device
        view.delegate = context.coordinator
        view.colorPixelFormat = .bgra8Unorm
        view.enableSetNeedsDisplay = true
        view.isPaused = true
        view.clearColor = MTLClearColorMake(0.12, 0.12, 0.12, 1.0)

        view.onPan = { [weak state] delta in
            state?.applyPan(by: delta)
        }
        view.onMagnify = { [weak state] magnification, anchor in
            state?.applyManualZoom(by: 1 + magnification, anchor: anchor)
        }
        view.onDoubleClick = { [weak state] in
            state?.fitToView()
        }
        view.onBoundsChange = { [weak state] size in
            state?.updateCanvasViewSize(size)
        }

        // Dust-mode brush painting: the view reports raw screen-point
        // samples (it has no notion of image geometry), and this closure —
        // which DOES have `state`, hence `CanvasTransform` — converts each
        // to a normalized image point and buffers it until the stroke ends.
        // Points outside the image quad are dropped (`imageNormalizedPoint`
        // returns nil there), matching a brush that only paints on the photo.
        var strokePoints: [CGPoint] = []
        view.onDustStrokePoint = { [weak state] location in
            guard let state,
                  let norm = state.transform.imageNormalizedPoint(
                    screen: location, viewSize: state.canvasViewSize, imageSize: state.originalImageSize)
            else { return }
            strokePoints.append(norm)
        }
        view.onDustStrokeEnd = { [weak state] in
            guard let state else { return }
            state.appendDustStroke(points: strokePoints, radius: state.dustBrushRadius)
            strokePoints = []
        }
        return view
    }

    func updateNSView(_ nsView: ZoomPanMTKView, context: Context) {
        context.coordinator.texture = state.texture
        context.coordinator.transform = state.transform
        context.coordinator.originalImageSize = state.originalImageSize
        nsView.isDustMode = state.isDustMode
        if state.canvasViewSize != nsView.bounds.size {
            // First layout / SwiftUI-driven resize the AppKit callback might
            // not have observed yet.
            DispatchQueue.main.async { state.updateCanvasViewSize(nsView.bounds.size) }
        }
        nsView.needsDisplay = true
    }

    func makeCoordinator() -> MetalRenderer {
        MetalRenderer(device: state.device)
    }
}

/// MTKView subclass owning the AppKit event overrides SwiftUI has no gesture
/// API for: trackpad/scroll-wheel pan, left-click-drag pan, and pinch-to-zoom
/// anchored at the cursor. `isFlipped == true` so its coordinate space
/// (origin top-left, y down) matches `CanvasTransform`'s convention and
/// ordinary screen reasoning about images.
final class ZoomPanMTKView: MTKView {
    /// Fires for BOTH two-finger scroll and left-click-drag — both are "pan
    /// by this many view points", and `PreviewState.applyPan` clamps either
    /// source identically (see its doc comment for why that's also what
    /// makes "Full zoom can't be dragged" fall out for free).
    var onPan: ((CGSize) -> Void)?
    var onMagnify: ((CGFloat, CGPoint) -> Void)?
    var onDoubleClick: (() -> Void)?
    var onBoundsChange: ((CGSize) -> Void)?

    /// Kept in sync from `PreviewState.isDustMode` each `updateNSView` —
    /// while true, left-click-drag paints a dust-removal brush stroke
    /// instead of panning the canvas (see `mouseDown`/`mouseDragged`).
    var isDustMode = false
    /// Fires once per mouse-move sample while painting a stroke, with the
    /// raw view-point location (this view has no image-geometry knowledge —
    /// `MetalCanvasView` converts to normalized image coordinates).
    var onDustStrokePoint: ((CGPoint) -> Void)?
    /// Fires once when a stroke's mouse button is released, after its last
    /// `onDustStrokePoint` — the cue to commit the accumulated points as one
    /// spot (mirrors `image_preview.py`'s `dust_release`).
    var onDustStrokeEnd: (() -> Void)?

    /// Tracks the pan drag in progress; `nil` when the left button isn't
    /// down or a dust stroke is in progress instead. Set in `mouseDown`,
    /// updated/consumed in `mouseDragged`, cleared in `mouseUp`.
    private var lastDragLocation: CGPoint?
    /// Whether the CURRENT mouse-down/drag/up sequence is painting a dust
    /// stroke — decided once in `mouseDown` from `isDustMode`, so toggling
    /// dust mode mid-drag can't switch behavior underneath an active
    /// gesture.
    private var isPaintingStroke = false

    override var isFlipped: Bool { true }
    override var acceptsFirstResponder: Bool { true }

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        postsBoundsChangedNotifications = true
        NotificationCenter.default.addObserver(
            self, selector: #selector(boundsDidChange),
            name: NSView.boundsDidChangeNotification, object: self)
        onBoundsChange?(bounds.size)
    }

    @objc private func boundsDidChange() {
        onBoundsChange?(bounds.size)
    }

    override func scrollWheel(with event: NSEvent) {
        onPan?(CGSize(width: event.scrollingDeltaX, height: event.scrollingDeltaY))
    }

    override func magnify(with event: NSEvent) {
        let location = convert(event.locationInWindow, from: nil)
        onMagnify?(event.magnification, location)
    }

    override func mouseDown(with event: NSEvent) {
        if event.clickCount >= 2 {
            onDoubleClick?()
        }
        isPaintingStroke = isDustMode
        let location = convert(event.locationInWindow, from: nil)
        if isPaintingStroke {
            onDustStrokePoint?(location)
        } else {
            lastDragLocation = location
        }
    }

    override func mouseDragged(with event: NSEvent) {
        let location = convert(event.locationInWindow, from: nil)
        if isPaintingStroke {
            onDustStrokePoint?(location)
            return
        }
        if let last = lastDragLocation {
            // Direct manipulation, not "scroll" semantics: the image follows
            // the cursor 1:1, which is what `CanvasTransform.pan`'s
            // `panOffset += delta` already does when fed the raw cursor
            // movement (no sign flip needed, unlike NSEvent's own
            // scrollingDelta convention).
            onPan?(CGSize(width: location.x - last.x, height: location.y - last.y))
        }
        lastDragLocation = location
    }

    override func mouseUp(with event: NSEvent) {
        if isPaintingStroke {
            onDustStrokeEnd?()
        }
        isPaintingStroke = false
        lastDragLocation = nil
    }
}

final class MetalRenderer: NSObject, MTKViewDelegate {
    private let commandQueue: MTLCommandQueue
    private let pipelineState: MTLRenderPipelineState
    var texture: MTLTexture?
    var transform = CanvasTransform()
    /// The real photo's dimensions — NOT `texture.width/height`, which can
    /// be a lower-res render (see `PreviewState.computeRequestedPreviewSize`).
    /// Geometry must be based on the true size so the quad doesn't jump
    /// around as the requested render resolution changes with zoom.
    var originalImageSize = PreviewState.syntheticImageSize

    init(device: MTLDevice) {
        guard let queue = device.makeCommandQueue() else {
            fatalError("Metal device cannot create a command queue")
        }
        commandQueue = queue

        guard let library = try? device.makeDefaultLibrary(bundle: Bundle.module) else {
            fatalError("Shaders.metal did not compile into the module's default library")
        }
        let descriptor = MTLRenderPipelineDescriptor()
        descriptor.vertexFunction = library.makeFunction(name: "vertex_main")
        descriptor.fragmentFunction = library.makeFunction(name: "fragment_main")
        descriptor.colorAttachments[0].pixelFormat = .bgra8Unorm
        do {
            pipelineState = try device.makeRenderPipelineState(descriptor: descriptor)
        } catch {
            fatalError("Failed to build render pipeline state: \(error)")
        }
        super.init()
    }

    func mtkView(_ view: MTKView, drawableSizeWillChange size: CGSize) {}

    func draw(in view: MTKView) {
        guard let texture,
              let drawable = view.currentDrawable,
              let renderPassDescriptor = view.currentRenderPassDescriptor,
              let commandBuffer = commandQueue.makeCommandBuffer(),
              let encoder = commandBuffer.makeRenderCommandEncoder(descriptor: renderPassDescriptor)
        else { return }

        let viewSize = view.bounds.size // points — see MetalCanvasView's doc comment
        let rect = transform.quadRect(viewSize: viewSize, imageSize: originalImageSize)

        // rect is in view points (top-left origin, y down); convert to NDC
        // (x: -1...1 left-to-right, y: -1...1 bottom-to-top). Derivation is
        // in Shaders.metal's doc comment.
        var uniforms = QuadUniforms(
            origin: SIMD2<Float>(
                Float(rect.minX / viewSize.width * 2 - 1),
                Float(1 - rect.minY / viewSize.height * 2)),
            size: SIMD2<Float>(
                Float(rect.width / viewSize.width * 2),
                Float(-rect.height / viewSize.height * 2)))

        encoder.setRenderPipelineState(pipelineState)
        encoder.setVertexBytes(&uniforms, length: MemoryLayout<QuadUniforms>.stride, index: 0)
        encoder.setFragmentTexture(texture, index: 0)
        encoder.drawPrimitives(type: .triangleStrip, vertexStart: 0, vertexCount: 4)
        encoder.endEncoding()
        commandBuffer.present(drawable)
        commandBuffer.commit()
    }
}

/// Layout must match Shaders.metal's `QuadUniforms` exactly.
private struct QuadUniforms {
    var origin: SIMD2<Float>
    var size: SIMD2<Float>
}
