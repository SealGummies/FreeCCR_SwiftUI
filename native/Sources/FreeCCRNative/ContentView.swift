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
                ThumbnailListView(state: state)
                    .frame(minWidth: 140, idealWidth: 160, maxWidth: 220)
                ZStack {
                    MetalCanvasView(state: state)
                    CropOverlayView(state: state)
                }
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
            allowedContentTypes: [.item],
            allowsMultipleSelection: true
        ) { result in
            if case .success(let urls) = result {
                state.loadImages(urls: urls)
            }
        }
    }
}

/// Analog of `widgets/thumbnail_list.py`: a scrollable list of every loaded
/// image's thumbnail + filename, selecting which one the canvas/sliders
/// show. Thumbnails come from `_thumb_np8` via `PythonCoreBridge.thumbnail`
/// (see `PreviewState.loadImages`) — plain `NSImage`s, not `MTLTexture`s;
/// there's no reason to involve Metal/GPU for a static ~156px icon.
struct ThumbnailListView: View {
    @ObservedObject var state: PreviewState

    var body: some View {
        ScrollView {
            LazyVStack(spacing: 4) {
                ForEach(Array(state.images.enumerated()), id: \.element.id) { index, image in
                    row(index: index, image: image)
                }
            }
            .padding(6)
        }
        .background(Color(nsColor: .underPageBackgroundColor))
    }

    private func row(index: Int, image: LoadedImage) -> some View {
        let isSelected = state.currentIndex == index
        return VStack(spacing: 4) {
            Group {
                if let thumbnail = image.thumbnail {
                    Image(nsImage: thumbnail).resizable().aspectRatio(contentMode: .fit)
                } else {
                    Color.gray.opacity(0.3)
                }
            }
            .frame(height: 90)
            .clipShape(RoundedRectangle(cornerRadius: 4))
            Text(image.fileName)
                .font(.caption2)
                .lineLimit(1)
                .truncationMode(.middle)
        }
        .padding(6)
        .background(
            RoundedRectangle(cornerRadius: 6)
                .fill(isSelected ? Color.accentColor.opacity(0.25) : Color.clear)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 6)
                .stroke(isSelected ? Color.accentColor : .clear, lineWidth: 2)
        )
        .contentShape(Rectangle())
        .onTapGesture { state.selectImage(at: index) }
    }
}

struct Toolbar: View {
    @ObservedObject var state: PreviewState
    @Binding var isPickingFile: Bool

    var body: some View {
        HStack {
            Button("Open Images…") { isPickingFile = true }
            if let name = state.loadedFileName {
                Text(name).foregroundStyle(.secondary)
            } else {
                Text("No file loaded — showing a synthetic test frame").foregroundStyle(.tertiary)
            }
            if !state.images.isEmpty {
                Text("(\(state.images.count) loaded)").foregroundStyle(.tertiary)
            }
            if let error = state.loadError {
                Text(error).foregroundStyle(.red)
            }

            Spacer()

            // Pan: two-finger scroll / scroll wheel (or drag the trackpad).
            // Zoom: pinch (deselects the preset below, since pinch lands on
            // an arbitrary ratio), or tap a preset. Double-click the canvas
            // also snaps to Full.
            Text("\(state.zoomPercent)%")
                .monospacedDigit()
                .foregroundStyle(.secondary)
                .frame(minWidth: 44, alignment: .trailing)
            ZoomPresetControl(selected: state.zoomPreset) { state.selectZoomPreset($0) }
        }
        .padding(8)
    }
}

/// The zoom% is always `canvasSize / originalImageSize` (see
/// `PreviewState.zoomPercent`/`selectZoomPreset`) — "Full" fits the image to
/// whatever the canvas currently measures, "100%"/"200%" are relative to the
/// image's real, full-resolution size (not the current preview render's
/// resolution, which can be smaller — see `PreviewState.computeRequestedPreviewSize`,
/// the mechanism that makes 100%/200% actually sharp instead of an upscaled
/// low-res preview). A `matchedGeometryEffect` highlight slides between
/// whichever segment is active; `selected == nil` (pinched/scrolled to a
/// custom ratio) shows no highlight at all.
struct ZoomPresetControl: View {
    let selected: ZoomPreset?
    let onSelect: (ZoomPreset) -> Void
    @Namespace private var highlightNamespace

    private static let labels: [(ZoomPreset, String)] = [
        (.full, "Full"), (.oneHundred, "100%"), (.twoHundred, "200%"),
    ]

    var body: some View {
        HStack(spacing: 0) {
            ForEach(Self.labels, id: \.0) { preset, label in
                Text(label)
                    .font(.callout)
                    .foregroundStyle(selected == preset ? Color.white : Color.primary)
                    .frame(width: 52, height: 22)
                    .background {
                        if selected == preset {
                            RoundedRectangle(cornerRadius: 5)
                                .fill(Color.accentColor)
                                .matchedGeometryEffect(id: "zoomHighlight", in: highlightNamespace)
                        }
                    }
                    .contentShape(Rectangle())
                    .onTapGesture {
                        withAnimation(.easeInOut(duration: 0.18)) {
                            onSelect(preset)
                        }
                    }
            }
        }
        .background(RoundedRectangle(cornerRadius: 6).fill(Color.gray.opacity(0.18)))
        .padding(2)
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

                HistogramView(histogram: state.histogram)

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

                DisclosureGroup("Dust Removal") {
                    DustRemovalSection(state: state)
                        .padding(.top, 8)
                }

                DisclosureGroup("Crop") {
                    CropSection(state: state)
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

/// Analog of `dust_panel.py`'s manual-brush section (AI detection isn't
/// ported yet — see native/README.md's known rough edges). "Paint Mode"
/// repurposes left-click-drag on the canvas from panning to brush strokes
/// (`MetalCanvasView`/`ZoomPanMTKView`); the brush slider is log-scaled via
/// `DustBrush`, matching `dust_panel.py`'s fine-steps-at-the-small-end
/// rationale exactly. Feather and spots are per-image (`PreviewState`
/// snapshots/restores them in `selectImage`, like `params`); brush size is a
/// session-wide tool setting, not per-image.
struct DustRemovalSection: View {
    @ObservedObject var state: PreviewState

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Toggle("Paint Mode (left-drag on canvas)", isOn: $state.isDustMode)
                .toggleStyle(.checkbox)

            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    Text("Brush Size")
                    Spacer()
                    Text(String(format: "%.2f%%", state.dustBrushRadius * 100))
                        .monospacedDigit()
                        .foregroundStyle(.secondary)
                }
                Slider(
                    value: Binding(
                        get: { Double(DustBrush.sliderStep(forRadius: state.dustBrushRadius)) },
                        set: { state.dustBrushRadius = DustBrush.radius(forSliderStep: Int($0.rounded())) }),
                    in: 0...Double(DustBrush.steps))
            }

            AdjustmentSliderRow(
                label: "Feather",
                value: Binding(
                    get: { state.dustFeather * 100 },
                    set: { state.dustFeather = $0 / 100 }),
                range: 0...100, defaultValue: 25)

            HStack(spacing: 8) {
                Button("Undo Last Spot") { state.undoLastDustSpot() }
                    .disabled(state.dustSpots.isEmpty)
                Button("Clear All") { state.clearDustSpots() }
                    .disabled(state.dustSpots.isEmpty)
            }

            if !state.dustSpots.isEmpty {
                Text("\(state.dustSpots.count) spot\(state.dustSpots.count == 1 ? "" : "s")")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }
}

/// Analog of `crop_panel.py`'s aspect/straighten controls (see
/// `CropAspect`'s doc comment for what's NOT ported: no draggable
/// corner/edge handles, presets only). Picking a preset or flipping
/// Landscape/Portrait re-centers a box of that ratio on the full image;
/// Straighten writes `crop_angle` directly (no separate "fine rotation" to
/// fold against, unlike the Qt app). The box itself renders in
/// `CropOverlayView`, layered over `MetalCanvasView` in `ContentView`.
struct CropSection: View {
    @ObservedObject var state: PreviewState

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Picker("Aspect", selection: $state.cropAspectKey) {
                ForEach(CropAspectKey.allCases) { key in
                    Text(key.label).tag(key)
                }
            }
            .pickerStyle(.menu)

            Picker("Orientation", selection: $state.cropLandscape) {
                Text("Landscape").tag(true)
                Text("Portrait").tag(false)
            }
            .pickerStyle(.segmented)
            .disabled(state.cropAspectKey.isOrientationFixed)

            AdjustmentSliderRow(
                label: "Straighten", value: $state.cropAngle,
                range: -45...45, defaultValue: 0)

            Button("Reset") { state.resetCrop() }
        }
    }
}

/// Draws the current crop box (see `PreviewState.cropRect`) over the Metal
/// canvas: a dashed outline plus a dimmed surround, matching the "you see
/// the whole photo, the box marks what a future export would keep" model —
/// non-destructive, no pixels are actually removed here (see
/// `CoreBridge.setCrop`'s doc comment). Doesn't render `cropAngle` (the box
/// stays axis-aligned) — a known limitation, see native/README.md.
struct CropOverlayView: View {
    @ObservedObject var state: PreviewState

    var body: some View {
        Canvas { context, size in
            guard let cropRect = state.cropRect else { return }
            let imageRect = state.transform.quadRect(viewSize: size, imageSize: state.originalImageSize)
            let boxRect = CGRect(
                x: imageRect.minX + cropRect.minX * imageRect.width,
                y: imageRect.minY + cropRect.minY * imageRect.height,
                width: cropRect.width * imageRect.width,
                height: cropRect.height * imageRect.height)

            var dimmed = Path(CGRect(origin: .zero, size: size))
            dimmed.addRect(boxRect)
            context.fill(dimmed, with: .color(.black.opacity(0.35)), style: FillStyle(eoFill: true))

            context.stroke(
                Path(boxRect), with: .color(.white),
                style: StrokeStyle(lineWidth: 1.5, dash: [6, 4]))
        }
        .allowsHitTesting(false)
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
