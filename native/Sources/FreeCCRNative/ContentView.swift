import Metal
import PythonBridge
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
                    .frame(minWidth: 280, idealWidth: 300, maxWidth: 360)
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

/// Analog of `widgets/sliders_panel.py`'s main controls: the full
/// `ADJUSTMENT_KEYS` slider set (minus per-color-band/curves — later
/// milestones), Color Profile, and the Cineon toggle. Every field writes
/// straight into `PreviewState.params`/`.colorProfile`, whose `didSet`
/// serializes the resulting PythonKit call — no per-slider plumbing needed
/// here beyond the `Binding`.
struct SlidersPanel: View {
    @ObservedObject var state: PreviewState

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                header

                colorProfileRow

                Group {
                    adjustmentSlider("Temperature", $state.params.temperature)
                    adjustmentSlider("Tint", $state.params.tint)
                    adjustmentSlider("Gain (Exposure)", $state.params.exposure, range: -200...200)
                    adjustmentSlider("Brightness", $state.params.brightness)
                    adjustmentSlider("Gamma", $state.params.gamma)
                    adjustmentSlider("Highlights", $state.params.highlights)
                    adjustmentSlider("White Point", $state.params.whitePoint)
                    adjustmentSlider("Shadows", $state.params.shadows)
                    adjustmentSlider("Black Point", $state.params.blackPoint)
                    adjustmentSlider("Contrast", $state.params.contrast)
                    adjustmentSlider("Saturation", $state.params.saturation)
                    adjustmentSlider("Subtracted Sat", $state.params.subSaturation)
                }

                DisclosureGroup("Curves") {
                    CurveEditorControl(curves: $state.params.curves)
                        .padding(.top, 8)
                }

                DisclosureGroup("Subtractive Saturations") {
                    BandSaturationsSection(state: state)
                        .padding(.top, 8)
                }

                DisclosureGroup("Channel Levels") {
                    VStack(alignment: .leading, spacing: 16) {
                        adjustmentSlider("Input Gain", $state.params.chInputGain)
                        adjustmentSlider("Master Shift", $state.params.chMasterShift)
                        adjustmentSlider("Master Gain", $state.params.chMasterGain)
                        adjustmentSlider("R Shift", $state.params.chRShift)
                        adjustmentSlider("R Gain", $state.params.chRGain)
                        adjustmentSlider("R Blackpoint", $state.params.chRBlackpoint)
                        adjustmentSlider("G Shift", $state.params.chGShift)
                        adjustmentSlider("G Gain", $state.params.chGGain)
                        adjustmentSlider("G Blackpoint", $state.params.chGBlackpoint)
                        adjustmentSlider("B Shift", $state.params.chBShift)
                        adjustmentSlider("B Gain", $state.params.chBGain)
                        adjustmentSlider("B Blackpoint", $state.params.chBBlackpoint)
                    }
                    .padding(.top, 8)
                }

                Toggle("Cineon Log → Rec.709 (γ 2.2)", isOn: $state.params.cineonLog)
                    .toggleStyle(.checkbox)

                Button("Reset All") { state.resetAdjustments() }

                Divider()

                statusLine

                Text("Every change writes into PreviewState.params, whose didSet calls PythonKit -> core.ccr_backend/core.ccr_image on FreeCCR's real color-math module, running in an embedded CPython with zero PySide6 on sys.path.")
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            }
            .padding()
        }
    }

    private var header: some View {
        Text("Adjustments").font(.headline)
    }

    private var colorProfileRow: some View {
        Picker("Color Profile", selection: $state.colorProfile) {
            Text("Color").tag(ColorProfile.color)
            Text("Black & White").tag(ColorProfile.blackAndWhite)
        }
        .pickerStyle(.menu)
    }

    private var statusLine: some View {
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
    }

    private func adjustmentSlider(_ label: String, _ value: Binding<Double>,
                                   range: ClosedRange<Double> = -100...100) -> some View {
        AdjustmentSliderRow(label: label, value: value, range: range)
    }
}

/// One labeled slider row, shared by `SlidersPanel` and
/// `BandSaturationsSection` — value readout, a reset-to-default arrow when
/// off `defaultValue` (SwiftUI stand-in for `ResettableSlider`'s
/// double-click-to-reset, unreliable to hit on a ~20pt slider track), and
/// the slider itself.
struct AdjustmentSliderRow: View {
    let label: String
    let value: Binding<Double>
    var range: ClosedRange<Double> = -100...100
    var defaultValue: Double = 0

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(label)
                Spacer()
                Text(String(format: "%.0f", value.wrappedValue))
                    .monospacedDigit()
                    .foregroundStyle(.secondary)
                if value.wrappedValue != defaultValue {
                    Button {
                        value.wrappedValue = defaultValue
                    } label: {
                        Image(systemName: "arrow.uturn.backward.circle")
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(.secondary)
                }
            }
            Slider(value: value, in: range)
        }
    }
}

/// Analog of sliders_panel.py's "Subtractive Saturations" section: a swatch
/// button per `ColorBand` selects which band's 4 sliders (Sub Sat, Sat,
/// Brightness, Hue) are shown — all 7 bands' values exist and feed
/// `AdjustmentParams.bands` regardless of which page is visible, mirroring
/// the Qt app's "_band_pages stay populated, only visibility toggles"
/// design. The Feather slider is global (not per-band), created last to
/// match `ADJUSTMENT_KEYS`' trailing `band_feather`.
struct BandSaturationsSection: View {
    @ObservedObject var state: PreviewState
    @State private var selectedBand: ColorBand = .red

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(spacing: 6) {
                ForEach(ColorBand.allCases, id: \.self) { band in
                    swatch(for: band)
                }
                Spacer()
            }

            AdjustmentSliderRow(label: "Sub Sat", value: binding(\.subSat))
            AdjustmentSliderRow(label: "Sat", value: binding(\.sat))
            AdjustmentSliderRow(label: "Brightness", value: binding(\.brightness))
            AdjustmentSliderRow(label: "Hue", value: binding(\.hue))

            AdjustmentSliderRow(
                label: "Feather", value: $state.params.bandFeather,
                range: 0...100, defaultValue: 10)
        }
    }

    private func swatch(for band: ColorBand) -> some View {
        Circle()
            .fill(Color(hex: band.swatchHex))
            .frame(width: 22, height: 22)
            .overlay(
                Circle().stroke(Color.accentColor, lineWidth: selectedBand == band ? 2 : 0)
            )
            .help(band.rawValue.capitalized)
            .onTapGesture { selectedBand = band }
    }

    private func binding(_ keyPath: WritableKeyPath<BandAdjustment, Double>) -> Binding<Double> {
        Binding(
            get: { state.params.bands[selectedBand, default: BandAdjustment()][keyPath: keyPath] },
            set: { newValue in
                var adjustment = state.params.bands[selectedBand, default: BandAdjustment()]
                adjustment[keyPath: keyPath] = newValue
                state.params.bands[selectedBand] = adjustment
            })
    }
}

extension Color {
    /// Parses a `"#rrggbb"` string — `theme.BAND_COLORS`' format exactly, so
    /// the swatch picker matches the Qt app's colors.
    init(hex: String) {
        var value: UInt64 = 0
        Scanner(string: hex.trimmingCharacters(in: CharacterSet(charactersIn: "#")))
            .scanHexInt64(&value)
        let r = Double((value >> 16) & 0xFF) / 255
        let g = Double((value >> 8) & 0xFF) / 255
        let b = Double(value & 0xFF) / 255
        self.init(red: r, green: g, blue: b)
    }
}
