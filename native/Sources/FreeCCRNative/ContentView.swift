import Metal
import SwiftUI
import UniformTypeIdentifiers

struct ContentView: View {
    @StateObject private var state: PreviewState
    @State private var isPickingFile = false

    init() {
        guard let device = MTLCreateSystemDefaultDevice() else {
            fatalError("No Metal device on this machine")
        }
        _state = StateObject(wrappedValue: PreviewState(device: device))
    }

    var body: some View {
        VStack(spacing: 0) {
            Toolbar(state: state, isPickingFile: $isPickingFile)
            Divider()
            HSplitView {
                MetalCanvasView(state: state)
                    .frame(minWidth: 512, minHeight: 384)
                SlidersPanel(state: state)
                    .frame(minWidth: 260, idealWidth: 280, maxWidth: 340)
            }
        }
        .onAppear { state.requestUpdate() }
        .fileImporter(
            isPresented: $isPickingFile,
            // FreeCCR decodes RAW via rawpy and everything else via
            // OpenCV/tifffile — .item lets any file through rather than
            // hand-maintaining a UTType list per RAW vendor extension.
            allowedContentTypes: [.item]
        ) { result in
            if case .success(let url) = result {
                state.loadImage(url: url)
            }
        }
    }
}

struct Toolbar: View {
    @ObservedObject var state: PreviewState
    @Binding var isPickingFile: Bool

    var body: some View {
        HStack {
            Button("Open Image…") { isPickingFile = true }
            if let name = state.loadedFileName {
                Text(name).foregroundStyle(.secondary)
            } else {
                Text("No file loaded — showing a synthetic test frame").foregroundStyle(.tertiary)
            }
            if let error = state.loadError {
                Text(error).foregroundStyle(.red)
            }

            Spacer()

            // Pan: two-finger scroll / scroll wheel. Zoom: pinch, or the
            // stepper buttons below. Double-click the canvas to fit.
            Text("\(state.zoomPercent)%")
                .monospacedDigit()
                .foregroundStyle(.secondary)
                .frame(minWidth: 44, alignment: .trailing)
            Button {
                state.transform.zoom(
                    by: 0.8, anchor: CGPoint(x: state.canvasViewSize.width / 2, y: state.canvasViewSize.height / 2),
                    viewSize: state.canvasViewSize, imageSize: state.currentImageSize)
            } label: {
                Image(systemName: "minus.magnifyingglass")
            }
            Button {
                state.transform.zoom(
                    by: 1.25, anchor: CGPoint(x: state.canvasViewSize.width / 2, y: state.canvasViewSize.height / 2),
                    viewSize: state.canvasViewSize, imageSize: state.currentImageSize)
            } label: {
                Image(systemName: "plus.magnifyingglass")
            }
            Button("Fit") { state.fitToView() }
        }
        .padding(8)
    }
}

/// Miniature analog of `widgets/sliders_panel.py` — four adjustment sliders
/// wired straight to `PreviewState`, which serializes every change through
/// PythonKit onto the real `core.ccr_backend`/`core.ccr_image` call chain.
struct SlidersPanel: View {
    @ObservedObject var state: PreviewState

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("Adjustments").font(.headline)

            adjustmentSlider("Temperature", $state.temperature)
            adjustmentSlider("Exposure", $state.exposure)
            adjustmentSlider("Contrast", $state.contrast)
            adjustmentSlider("Saturation", $state.saturation)

            Divider()

            HStack(spacing: 6) {
                if state.isBusy {
                    ProgressView().controlSize(.small)
                    Text("Python running…")
                } else {
                    Image(systemName: "checkmark.circle").foregroundStyle(.green)
                    Text(String(format: "adjust_image: %.1f ms", state.lastLatencyMs))
                }
            }
            .font(.caption)
            .foregroundStyle(.secondary)

            Spacer()

            Text("Every drag calls PythonKit -> core.ccr_backend/core.ccr_image on FreeCCR's real color-math module, running in an embedded CPython with zero PySide6 on sys.path.")
                .font(.caption2)
                .foregroundStyle(.tertiary)
        }
        .padding()
    }

    private func adjustmentSlider(_ label: String, _ value: Binding<Double>) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(label)
                Spacer()
                Text(String(format: "%.0f", value.wrappedValue))
                    .monospacedDigit()
                    .foregroundStyle(.secondary)
            }
            Slider(value: value, in: -100...100)
                .onChange(of: value.wrappedValue) {
                    state.requestUpdate()
                }
        }
    }
}
