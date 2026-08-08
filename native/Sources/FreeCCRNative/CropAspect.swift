import CoreGraphics

/// The Crop panel's aspect-ratio presets — ported from `crop_aspect.py`'s
/// `ASPECT_PRESETS`. `Custom…` (manual W:H spinboxes) isn't in this port yet
/// — presets only, see `CropAspect`'s doc comment for the rest of what's
/// deferred.
enum CropAspectKey: String, CaseIterable, Identifiable {
    case free, original
    case oneToOne, fiveToFour, fourToThree, sevenToFive, threeToTwo, sixteenToNine
    case academy, flat, univisium, cinemaScope, dciScope

    var id: String { rawValue }

    var label: String {
        switch self {
        case .free: return "Free"
        case .original: return "Original"
        case .oneToOne: return "1:1"
        case .fiveToFour: return "5:4"
        case .fourToThree: return "4:3"
        case .sevenToFive: return "7:5"
        case .threeToTwo: return "3:2"
        case .sixteenToNine: return "16:9"
        case .academy: return "Academy (1.37:1)"
        case .flat: return "1.85:1 (Flat)"
        case .univisium: return "2:1 (Univisium)"
        case .cinemaScope: return "2.35:1 (CinemaScope)"
        case .dciScope: return "2.39:1 (Scope)"
        }
    }

    /// Width/height ratio in LANDSCAPE orientation; `nil` for `.free` and for
    /// `.original` (resolved dynamically from the loaded image's own aspect
    /// — see `CropAspect.effectiveRatio`), matching `crop_aspect.py`'s
    /// `base_ratio` column (`None` / the `"original"` sentinel).
    var baseRatio: Double? {
        switch self {
        case .free, .original: return nil
        case .oneToOne: return 1.0
        case .fiveToFour: return 5.0 / 4.0
        case .fourToThree: return 4.0 / 3.0
        case .sevenToFive: return 7.0 / 5.0
        case .threeToTwo: return 3.0 / 2.0
        case .sixteenToNine: return 16.0 / 9.0
        case .academy: return 1.375
        case .flat: return 1.85
        case .univisium: return 2.0
        case .cinemaScope: return 2.35
        case .dciScope: return 2.39
        }
    }

    /// Keys for which the Landscape/Portrait toggle is meaningless — mirrors
    /// `crop_aspect.py`'s `ORIENTATION_FIXED_KEYS` (minus `"custom"`, not
    /// ported).
    var isOrientationFixed: Bool {
        switch self {
        case .free, .original: return true
        default: return false
        }
    }
}

/// Pure (no Python, no AppKit) geometry backing the Crop panel — ported from
/// `crop_aspect.py`. This port only covers PRESET selection (pick a ratio,
/// get a box centered on the image) — not `crop_aspect.py`'s full feature
/// set: no draggable corner/edge handles (`enforce_ratio_size`), no "fit
/// within the CURRENT box" behavior (`reshape_to_ratio`'s box_wh branch —
/// this always reseeds centered on the full image, since there's no
/// existing draggable box to fit within yet), and no fine-rotation folding
/// (`folded_crop_angle` — this port has no separate "fine rotation" concept,
/// so the Straighten slider writes `crop_angle` directly). All boxes are
/// centered; a future milestone can add drag-to-move/resize on top of this.
enum CropAspect {
    /// The actual ratio to crop to, resolving `.original` against the real
    /// image size and applying the landscape/portrait toggle (`.free` stays
    /// `nil` throughout — no crop).
    static func effectiveRatio(for key: CropAspectKey, landscape: Bool, imageSize: CGSize) -> Double? {
        switch key {
        case .free:
            return nil
        case .original:
            guard imageSize.width > 0, imageSize.height > 0 else { return nil }
            return Double(imageSize.width / imageSize.height)
        default:
            return orientedRatio(key.baseRatio, landscape: landscape)
        }
    }

    /// Applies the orientation toggle to a base (Landscape) ratio — Portrait
    /// flips it to height/width. `nil`/non-positive pass through unchanged.
    static func orientedRatio(_ ratio: Double?, landscape: Bool) -> Double? {
        guard let ratio, ratio > 0 else { return ratio }
        return landscape ? ratio : 1.0 / ratio
    }

    /// The largest box of ratio `r = w/h` that fits inside `w x h`. `r`
    /// `nil`/non-positive or degenerate bounds return `(w, h)` unchanged.
    static func fitRatioWithin(_ w: Double, _ h: Double, _ r: Double?) -> (width: Double, height: Double) {
        guard let r, r > 0, w > 0, h > 0 else { return (w, h) }
        if w / h >= r { return (h * r, h) } // height is the binding dimension
        return (w, w / r) // width is the binding dimension
    }

    /// A normalized (0...1 fractions of the image, matching
    /// `CCRImage.crop_rect`'s coordinate convention) box for `key`, centered
    /// on the image — `nil` for `.free` (no crop) or before an image is
    /// loaded.
    static func normalizedRect(for key: CropAspectKey, landscape: Bool, imageSize: CGSize) -> CGRect? {
        guard let ratio = effectiveRatio(for: key, landscape: landscape, imageSize: imageSize),
              imageSize.width > 0, imageSize.height > 0 else { return nil }
        let (boxWidth, boxHeight) = fitRatioWithin(Double(imageSize.width), Double(imageSize.height), ratio)
        let w = boxWidth / Double(imageSize.width)
        let h = boxHeight / Double(imageSize.height)
        return CGRect(x: (1 - w) / 2, y: (1 - h) / 2, width: w, height: h)
    }
}
