import Metal
import SwiftUI

struct ContentView: View {
    @StateObject private var state: PreviewState

    init() {
        guard let device = MTLCreateSystemDefaultDevice() else {
            fatalError("No Metal device on this machine")
        }
        _state = StateObject(wrappedValue: PreviewState(device: device))
    }

    var body: some View {
        HSplitView {
            MetalCanvasView(state: state)
                .frame(minWidth: 512, minHeight: 384)
            SlidersPanel(state: state)
                .frame(minWidth: 260, idealWidth: 280, maxWidth: 340)
        }
        .onAppear { state.requestUpdate() }
    }
}

/// Miniature analog of `widgets/sliders_panel.py` — four adjustment sliders
/// wired straight to `PreviewState`, which serializes every change through
/// PythonKit onto `core.ccr_processor.adjust_image`.
struct SlidersPanel: View {
    @ObservedObject var state: PreviewState

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("Adjustments").font(.headline)

            adjustmentSlider("Exposure", $state.exposure)
            adjustmentSlider("Contrast", $state.contrast)
            adjustmentSlider("Saturation", $state.saturation)
            adjustmentSlider("Kelvin Shift", $state.kelvinShift)

            Divider()

            HStack(spacing: 6) {
                if state.isBusy {
                    ProgressView().controlSize(.small)
                    Text("adjust_image running…")
                } else {
                    Image(systemName: "checkmark.circle").foregroundStyle(.green)
                    Text(String(format: "adjust_image: %.1f ms", state.lastLatencyMs))
                }
            }
            .font(.caption)
            .foregroundStyle(.secondary)

            Spacer()

            Text("Every drag calls PythonKit -> core.ccr_processor.adjust_image on FreeCCR's real color-math module, running in an embedded CPython with zero PySide6 on sys.path.")
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
