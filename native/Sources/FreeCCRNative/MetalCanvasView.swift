import Metal
import MetalKit
import SwiftUI

/// AppKit bridge for the SwiftUI canvas — the closest analog here to
/// `widgets/image_preview.py`'s Qt `QGraphicsView`. Owns nothing stateful
/// beyond the render pipeline; the texture to draw comes from `PreviewState`.
struct MetalCanvasView: NSViewRepresentable {
    @ObservedObject var state: PreviewState

    func makeNSView(context: Context) -> MTKView {
        let view = MTKView()
        view.device = state.device
        view.delegate = context.coordinator
        view.colorPixelFormat = .bgra8Unorm
        view.enableSetNeedsDisplay = true
        view.isPaused = true
        view.clearColor = MTLClearColorMake(0.12, 0.12, 0.12, 1.0)
        return view
    }

    func updateNSView(_ nsView: MTKView, context: Context) {
        context.coordinator.texture = state.texture
        nsView.needsDisplay = true
    }

    func makeCoordinator() -> MetalRenderer {
        MetalRenderer(device: state.device)
    }
}

final class MetalRenderer: NSObject, MTKViewDelegate {
    private let commandQueue: MTLCommandQueue
    private let pipelineState: MTLRenderPipelineState
    var texture: MTLTexture?

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
        guard let drawable = view.currentDrawable,
              let renderPassDescriptor = view.currentRenderPassDescriptor,
              let commandBuffer = commandQueue.makeCommandBuffer(),
              let encoder = commandBuffer.makeRenderCommandEncoder(descriptor: renderPassDescriptor)
        else { return }

        if let texture {
            encoder.setRenderPipelineState(pipelineState)
            encoder.setFragmentTexture(texture, index: 0)
            encoder.drawPrimitives(type: .triangleStrip, vertexStart: 0, vertexCount: 4)
        }
        encoder.endEncoding()
        commandBuffer.present(drawable)
        commandBuffer.commit()
    }
}
