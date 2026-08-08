import CoreGraphics

/// The screen ↔ canvas ↔ image-normalized coordinate stack, in one place —
/// the Swift analog of `image_preview.py`'s `_display_transform` /
/// `map_displayed_to_full`. Every later interactive feature that needs to
/// know "where in the image did the user click" (crop handles, area layers,
/// dust brush, black/white-point picking, reference-frame selection) reads
/// through this, so it's worth getting right once here rather than
/// reimplementing per feature.
///
/// Units: `panOffset` and every `CGSize`/`CGPoint` taken as "screen"/"view"
/// coordinates are in the view's own points (NOT backing-store pixels — see
/// `MetalCanvasView`'s note on why that's fine for the NDC math), with the
/// origin at the view's top-left and y increasing downward (the view is
/// `isFlipped == true`, see `ZoomPanMTKView`).
struct CanvasTransform: Equatable {
    /// User zoom multiplier on top of the "fit" scale. 1.0 == fit-to-view.
    var zoom: CGFloat = 1.0
    /// Additional pan, in view points, beyond the centered "fit" position.
    var panOffset: CGSize = .zero

    static let minZoom: CGFloat = 0.05
    static let maxZoom: CGFloat = 32.0

    /// The scale that makes the image fill the view without cropping
    /// (`QGraphicsView.fitInView`'s equivalent), ignoring `zoom`/`panOffset`.
    func fitScale(viewSize: CGSize, imageSize: CGSize) -> CGFloat {
        guard imageSize.width > 0, imageSize.height > 0,
              viewSize.width > 0, viewSize.height > 0 else { return 1 }
        return min(viewSize.width / imageSize.width, viewSize.height / imageSize.height)
    }

    func effectiveScale(viewSize: CGSize, imageSize: CGSize) -> CGFloat {
        fitScale(viewSize: viewSize, imageSize: imageSize) * zoom
    }

    /// Where the image quad currently sits, in view points (origin top-left,
    /// same space as mouse/trackpad event locations).
    func quadRect(viewSize: CGSize, imageSize: CGSize) -> CGRect {
        let scale = effectiveScale(viewSize: viewSize, imageSize: imageSize)
        let w = imageSize.width * scale
        let h = imageSize.height * scale
        let centerX = viewSize.width / 2 + panOffset.width
        let centerY = viewSize.height / 2 + panOffset.height
        return CGRect(x: centerX - w / 2, y: centerY - h / 2, width: w, height: h)
    }

    /// Screen point -> normalized image coordinates (0...1, origin top-left
    /// of the image). `nil` when the point falls outside the image quad.
    func imageNormalizedPoint(screen: CGPoint, viewSize: CGSize, imageSize: CGSize) -> CGPoint? {
        let rect = quadRect(viewSize: viewSize, imageSize: imageSize)
        guard rect.width > 0, rect.height > 0 else { return nil }
        let nx = (screen.x - rect.minX) / rect.width
        let ny = (screen.y - rect.minY) / rect.height
        guard (0...1).contains(nx), (0...1).contains(ny) else { return nil }
        return CGPoint(x: nx, y: ny)
    }

    /// Multiplies `zoom` by `factor`, adjusting `panOffset` so that `anchor`
    /// (a screen point — typically the cursor/pinch center) stays over the
    /// same image location before and after. This is what makes
    /// scroll-to-zoom feel anchored instead of always zooming toward center.
    mutating func zoom(by factor: CGFloat, anchor: CGPoint, viewSize: CGSize, imageSize: CGSize) {
        let before = quadRect(viewSize: viewSize, imageSize: imageSize)
        guard before.width > 0, before.height > 0 else { return }
        let relX = (anchor.x - before.minX) / before.width
        let relY = (anchor.y - before.minY) / before.height

        zoom = min(max(zoom * factor, Self.minZoom), Self.maxZoom)

        // Recompute the quad with the new zoom but panOffset unchanged, then
        // shift panOffset so `anchor` lands back on the same relative point.
        let after = quadRect(viewSize: viewSize, imageSize: imageSize)
        let desiredMinX = anchor.x - relX * after.width
        let desiredMinY = anchor.y - relY * after.height
        let desiredCenterX = desiredMinX + after.width / 2
        let desiredCenterY = desiredMinY + after.height / 2
        panOffset = CGSize(
            width: desiredCenterX - viewSize.width / 2,
            height: desiredCenterY - viewSize.height / 2)
    }

    mutating func pan(by delta: CGSize) {
        panOffset.width += delta.width
        panOffset.height += delta.height
    }

    /// Keeps the image quad from leaving a gap inside the viewport: on any
    /// axis where the rendered image is smaller than the view, `panOffset`
    /// is pinned to 0 (centered — no room to drag, which is exactly the
    /// "Full zoom" case since fit-to-view guarantees both axes are <= the
    /// view). On axes where the image is larger than the view, `panOffset`
    /// is clamped so the image's near edge never crosses the view's edge.
    mutating func clampPan(viewSize: CGSize, imageSize: CGSize) {
        guard viewSize.width > 0, viewSize.height > 0,
              imageSize.width > 0, imageSize.height > 0 else {
            panOffset = .zero
            return
        }
        let scale = effectiveScale(viewSize: viewSize, imageSize: imageSize)
        let extraWidth = imageSize.width * scale - viewSize.width
        let extraHeight = imageSize.height * scale - viewSize.height

        if extraWidth > 0 {
            let limit = extraWidth / 2
            panOffset.width = min(max(panOffset.width, -limit), limit)
        } else {
            panOffset.width = 0
        }

        if extraHeight > 0 {
            let limit = extraHeight / 2
            panOffset.height = min(max(panOffset.height, -limit), limit)
        } else {
            panOffset.height = 0
        }
    }

    mutating func resetToFit() {
        zoom = 1.0
        panOffset = .zero
    }
}
