from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QToolBar, QMessageBox, QSlider, QGraphicsView, QGraphicsScene,
    QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsPathItem, QStyleOptionSlider, QDialog,
    QLabel, QPushButton, QStyle, QSizePolicy
)
from PySide6.QtGui import (QPixmap, QIcon, QTransform, QPen, QColor, QAction, QPainter,
                           QCursor, QDesktopServices, QPainterPath, QBrush)
from PySide6.QtCore import Qt, QSize, Signal, QRect, QRectF, QPointF, QThread, QTimer, QUrl
from core.ccr_backend import ccr_backend
from widgets.export_dialog import ExportSettingsDialog
import sys
import os

_eyedropper_cursor_cache = None

def _eyedropper_cursor():
    """Small painter-drawn eyedropper cursor, hotspot at the tip (bottom-left)."""
    global _eyedropper_cursor_cache
    if _eyedropper_cursor_cache is None:
        pm = QPixmap(24, 24)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        # White underlay then dark dropper on top, so it reads on any image.
        for color, body_w, bulb_w in ((QColor(255, 255, 255), 5, 7),
                                      (QColor(25, 25, 25), 3, 5)):
            pen = QPen(color, body_w, Qt.SolidLine, Qt.RoundCap)
            p.setPen(pen)
            p.drawLine(4, 20, 13, 11)        # body, tip toward bottom-left
            pen.setWidth(bulb_w)
            p.setPen(pen)
            p.drawLine(11, 7, 17, 13)        # bulb across the top end
        p.end()
        _eyedropper_cursor_cache = QCursor(pm, 3, 21)
    return _eyedropper_cursor_cache

def resource_path(relative_path):
    """
    Get absolute path to resource, works for dev and for Nuitka bundle.
    - In bundle: icons are in Contents/MacOS/icons/
    - In dev: icons are in src/icons/
    """
    # If running as a bundled app (Nuitka, PyInstaller, etc.)
    if getattr(sys, 'frozen', False):
        base_path = os.path.dirname(sys.executable)
        abs_path = os.path.join(base_path, relative_path)
        if os.path.exists(abs_path):
            return abs_path
    # In development: look for src/icons/...
    dev_base = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "icons"))
    dev_path = os.path.join(dev_base, os.path.basename(relative_path))
    if os.path.exists(dev_path):
        return dev_path
    # Fallback: try relative to current file (for edge cases)
    fallback_path = os.path.join(os.path.dirname(__file__), relative_path)
    if os.path.exists(fallback_path):
        return fallback_path
    # Not found, return as-is (QIcon will fail gracefully)
    return relative_path


class GraphicsImageView(QGraphicsView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setMouseTracking(True)
        self.drawing_reference = False
        self._drag_start = None
        self._drag_end = None
        self.reference_rect_item = None
        self.parent_widget = parent

        self.bwpoint_mode = None   # None | "black" | "white"
        self._bw_drag_start = None
        self._bw_drag_end = None
        self._bw_rect_item = None

        self.wb_pick_mode = False  # eyedropper: click a neutral point for auto temp/tint

        self._crop_drag_start = None  # scene pos where a crop-mode drag began

        # Disable scrollbars
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    def mousePressEvent(self, event):
        if self.parent_widget.crop_mode and self.parent_widget.pixmap_item is not None:
            if event.button() == Qt.LeftButton:
                self._crop_drag_start = self.mapToScene(event.pos())
            elif event.button() == Qt.RightButton:
                self.parent_widget.clear_crop()
            return
        if event.button() == Qt.LeftButton and self.wb_pick_mode and self.parent_widget.pixmap_item is not None:
            scene_pos = self.mapToScene(event.pos())
            self.wb_pick_mode = False
            self.setCursor(Qt.ArrowCursor)
            self._sample_wb_point(scene_pos)
            return
        if event.button() == Qt.LeftButton and self.bwpoint_mode and self.parent_widget.pixmap_item is not None:
            self._bw_drag_start = self.mapToScene(event.pos())
            self._bw_drag_end = self._bw_drag_start
            if self._bw_rect_item:
                self.scene().removeItem(self._bw_rect_item)
                self._bw_rect_item = None
            self.viewport().update()
        elif event.button() == Qt.LeftButton and self.parent_widget.pixmap_item is not None:
            self.drawing_reference = True
            self._drag_start = self.mapToScene(event.pos())
            self._drag_end = self._drag_start
            if self.reference_rect_item:
                self.scene().removeItem(self.reference_rect_item)
                self.reference_rect_item = None
            self.viewport().update()
        elif event.button() == Qt.RightButton:
            # Remove reference frame on right click
            if self.reference_rect_item:
                self.scene().removeItem(self.reference_rect_item)
                self.reference_rect_item = None
            # Also clear in parent widget
            self.parent_widget.reference_rect_item = None
            # Clear in backend
            ccr_backend.set_reference_frame_by_index(self.parent_widget.current_idx, None)
            self.drawing_reference = False
            self.viewport().update()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.parent_widget.crop_mode:
            if self._crop_drag_start is not None:
                self.parent_widget.update_crop_selection(
                    self._crop_drag_start, self.mapToScene(event.pos()))
            return
        if self.bwpoint_mode and self._bw_drag_start is not None:
            self._bw_drag_end = self.mapToScene(event.pos())
            self._update_bw_rect(self._bw_drag_start, self._bw_drag_end)
        elif self.drawing_reference:
            self._drag_end = self.mapToScene(event.pos())
            self.viewport().update()
            self.parent_widget.update_reference_rect(self._drag_start, self._drag_end)

    def _update_bw_rect(self, start, end):
        """Draw the B/W point selection rect on the canvas."""
        pw = self.parent_widget
        cx = pw.current_pixmap.width() / 2
        cy = pw.current_pixmap.height() / 2
        base_transform = QTransform()
        base_transform.translate(cx, cy)
        if pw.current_vertical_flip:
            base_transform.scale(1, -1)
        if pw.current_horizontal_flip:
            base_transform.scale(-1, 1)
        if pw.current_rotation:
            base_transform.rotate(pw.current_rotation)
        base_transform.translate(-cx, -cy)

        if not base_transform.isInvertible():
            return
        inv = base_transform.inverted()[0]
        s = inv.map(start)
        e = inv.map(end)
        rect = QRectF(min(s.x(), e.x()), min(s.y(), e.y()),
                      abs(e.x() - s.x()), abs(e.y() - s.y()))
        color = QColor(0, 100, 255, 140) if self.bwpoint_mode == "white" else QColor(255, 140, 0, 140)
        if self._bw_rect_item is None:
            self._bw_rect_item = QGraphicsRectItem(rect)
            pen = QPen(color, 2, Qt.DashLine)
            self._bw_rect_item.setPen(pen)
            self.scene().addItem(self._bw_rect_item)
        else:
            self._bw_rect_item.setPen(QPen(color, 2, Qt.DashLine))
            self._bw_rect_item.setRect(rect)
        self._bw_rect_item.setTransform(base_transform)

    def _sample_wb_point(self, scene_pos):
        """Sample a small neighborhood at the clicked point and auto-set the
        temperature/tint sliders so that point becomes neutral."""
        pw = self.parent_widget
        cx = pw.current_pixmap.width() / 2
        cy = pw.current_pixmap.height() / 2
        base_transform = QTransform()
        base_transform.translate(cx, cy)
        if pw.current_vertical_flip:
            base_transform.scale(1, -1)
        if pw.current_horizontal_flip:
            base_transform.scale(-1, 1)
        if pw.current_rotation:
            base_transform.rotate(pw.current_rotation)
        base_transform.translate(-cx, -cy)
        if not base_transform.isInvertible():
            return
        local = base_transform.inverted()[0].map(scene_pos)
        # Displayed pixmap may be a crop of the full image — map back to
        # full-image coordinates before indexing resized_raw.
        dx, dy = pw.displayed_crop_offset()
        x, y = int(local.x()) + dx, int(local.y()) + dy

        img_obj = ccr_backend.get_image_by_index(pw.current_idx)
        if img_obj is None or img_obj.resized_raw is None:
            return
        data = img_obj.resized_raw
        h, w = data.shape[:2]
        if not (0 <= x < w and 0 <= y < h):
            return
        import numpy as np
        rad = 3
        crop = data[max(0, y - rad):min(h, y + rad + 1),
                    max(0, x - rad):min(w, x + rad + 1), :]
        means = np.mean(crop.reshape(-1, 3), axis=0)

        from core.ccr_processor import compute_neutral_temp_tint
        temp, tint = compute_neutral_temp_tint(
            means[0], means[1], means[2],
            getattr(img_obj, 'tint_balance_factor', 1.0))
        try:
            pw.parent().parent().sliders_panel.on_wb_sampled(temp, tint)
        except AttributeError:
            pass

    def mouseReleaseEvent(self, event):
        if self.parent_widget.crop_mode:
            if event.button() == Qt.LeftButton and self._crop_drag_start is not None:
                self.parent_widget.update_crop_selection(
                    self._crop_drag_start, self.mapToScene(event.pos()))
                self._crop_drag_start = None
            return
        if self.bwpoint_mode and event.button() == Qt.LeftButton and self._bw_drag_start is not None:
            drag_start_scene = self._bw_drag_start
            drag_end_scene = self.mapToScene(event.pos())

            pw = self.parent_widget
            cx = pw.current_pixmap.width() / 2
            cy = pw.current_pixmap.height() / 2
            base_transform = QTransform()
            base_transform.translate(cx, cy)
            if pw.current_vertical_flip:
                base_transform.scale(1, -1)
            if pw.current_horizontal_flip:
                base_transform.scale(-1, 1)
            if pw.current_rotation:
                base_transform.rotate(pw.current_rotation)
            base_transform.translate(-cx, -cy)

            mode = self.bwpoint_mode
            self.bwpoint_mode = None
            self._bw_drag_start = None
            self._bw_drag_end = None
            self.setCursor(Qt.ArrowCursor)

            if base_transform.isInvertible():
                inv = base_transform.inverted()[0]
                s = inv.map(drag_start_scene)
                e = inv.map(drag_end_scene)
                # Map from displayed (possibly cropped) coords to full-image coords
                dx, dy = pw.displayed_crop_offset()
                x1 = int(min(s.x(), e.x())) + dx
                y1 = int(min(s.y(), e.y())) + dy
                x2 = int(max(s.x(), e.x())) + dx
                y2 = int(max(s.y(), e.y())) + dy

                img_obj = ccr_backend.get_image_by_index(pw.current_idx)
                if img_obj is not None:
                    import numpy as np
                    # Always sample from original scan data, not processed data
                    if img_obj.converted:
                        raw_data = img_obj.read_image(img_obj.file_path)
                        if raw_data is not None:
                            raw_data = img_obj.resize_image_to_max_pixel(raw_data, 1080)
                    else:
                        raw_data = img_obj.resized_raw
                    if raw_data is not None:
                        h, w = raw_data.shape[:2]
                        x1 = max(0, min(x1, w - 1))
                        y1 = max(0, min(y1, h - 1))
                        x2 = max(0, min(x2, w))
                        y2 = max(0, min(y2, h))
                        if (x2 - x1) >= 5 and (y2 - y1) >= 5:
                            crop = raw_data[y1:y2, x1:x2, :]
                            means = np.mean(crop.reshape(-1, 3), axis=0)
                            bgr_tuple = (float(means[0]), float(means[1]), float(means[2]))
                            if mode == "black":
                                ccr_backend.set_black_point(bgr_tuple)
                            else:
                                ccr_backend.set_white_point(bgr_tuple)
                            try:
                                pw.parent().parent().sliders_panel.on_bwpoint_sampled(mode)
                            except AttributeError:
                                pass
            return

        if self.drawing_reference and event.button() == Qt.LeftButton:
            self._drag_end = self.mapToScene(event.pos())
            self.drawing_reference = False

            # Build the base (coarse) transform (same as in update_reference_rect)
            cx = self.parent_widget.current_pixmap.width() / 2
            cy = self.parent_widget.current_pixmap.height() / 2
            base_transform = QTransform()
            base_transform.translate(cx, cy)

            if self.parent_widget.current_vertical_flip:
                base_transform.scale(1, -1)
            if self.parent_widget.current_horizontal_flip:
                base_transform.scale(-1, 1)
            if self.parent_widget.current_rotation:
                base_transform.rotate(self.parent_widget.current_rotation)
            base_transform.translate(-cx, -cy)

            if not base_transform.isInvertible():
                return
            inv_transform = base_transform.inverted()[0]
            start_local = inv_transform.map(self._drag_start)
            end_local = inv_transform.map(self._drag_end)

            x1, y1 = start_local.x(), start_local.y()
            x2, y2 = end_local.x(), end_local.y()
            x, y = min(x1, x2), min(y1, y2)
            x2, y2 = max(x1, x2), max(y1, y2)
            w, h = x2 - x, y2 - y
            if w > 20 and h > 20:
                # Map from displayed (possibly cropped) coords to full-image coords
                dx, dy = self.parent_widget.displayed_crop_offset()
                ccr_backend.set_reference_frame_by_index(
                    self.parent_widget.current_idx,
                    (int(x) + dx, int(y) + dy, int(x2) + dx, int(y2) + dy)
                )
                self.parent_widget.parent().parent().sliders_panel.set_temporary_hint(
                    "<b>Hint:</b><br>Reference frame set! Click the Convert button to view the updates.",
                    duration=5000
                )
                print("Set reference frame:", (int(x), int(y), int(x2), int(y2)))
        self.parent_widget.update_preview(self.parent_widget.current_idx)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Let the parent widget handle the fitting to avoid conflicts
        if self.parent_widget and self.parent_widget.pixmap_item:
            self.parent_widget._fit_view_to_content()

    def drawForeground(self, painter, rect):
        super().drawForeground(painter, rect)
        if hasattr(self.parent_widget, "show_grid") and self.parent_widget.show_grid:
            painter.save()
            painter.setRenderHint(QPainter.Antialiasing, False)  # <-- Use QPainter.Antialiasing
            pen = QPen(QColor(0, 80, 0, 80), 2)
            painter.setPen(pen)

            # Draw grid in scene (canvas) coordinates, so it stays stable
            scene_rect = self.scene().sceneRect()
            left, top, width, height = scene_rect.left(), scene_rect.top(), scene_rect.width(), scene_rect.height()

            # Draw vertical lines
            for i in range(0, 13):
                x = left + i * width / 12.0
                painter.drawLine(QPointF(x, top), QPointF(x, top + height))
            # Draw horizontal lines
            for i in range(0, 13):
                y = top + i * height / 12.0
                painter.drawLine(QPointF(left, y), QPointF(left + width, y))

            painter.restore()

    def wheelEvent(self, event):
        # Disable mouse wheel zoom/scroll
        event.ignore()

    def keyPressEvent(self, event):
        # Disable up/down/left/right keys from scrolling the view
        if event.key() in (Qt.Key_Up, Qt.Key_Down, Qt.Key_Left, Qt.Key_Right):
            event.ignore()
        else:
            super().keyPressEvent(event)

class CenteringSlider(QSlider):
    def mouseDoubleClickEvent(self, event):
        super().mouseDoubleClickEvent(event)
        self.setValue(0)

    def mousePressEvent(self, event):
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        handle_rect = self.style().subControlRect(
            QStyle.CC_Slider,  # <-- Use QStyle.CC_Slider
            opt,
            QStyle.SC_SliderHandle,  # <-- Use QStyle.SC_SliderHandle
            self
        )
        if handle_rect.contains(event.pos()):
            super().mousePressEvent(event)
        else:
            event.ignore()

class ImagePreview(QWidget):
    ccr_converted = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout = QVBoxLayout()
        self.layout.setContentsMargins(40, 0, 40, 0)

        # Toolbar
        self.toolbar = QToolBar()
        self.toolbar.setIconSize(QSize(24, 24))
        self.toolbar.setStyleSheet("""
    QToolButton { color: red; }
    QToolButton:enabled { color: black; }
    QToolButton:!hover { color: black; }
    QToolButton:disabled { color: gray; }
""")

        def add_spacer(width=5):
            spacer = QWidget()
            spacer.setFixedWidth(width)
            spacer.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
            self.toolbar.addWidget(spacer)

        auto_icon = QIcon(resource_path("icons/auto.png"))
        if auto_icon.isNull():
            auto_icon = QIcon.fromTheme("view-refresh")
        auto_frame_action = QAction(auto_icon, "Auto Frame", self)
        auto_frame_action.triggered.connect(self.auto_frame)
        self.toolbar.addAction(auto_frame_action)
        add_spacer()

        rotate_left_icon = QIcon(resource_path("icons/rotate-left-icon-size_512.png"))
        if rotate_left_icon.isNull():
            rotate_left_icon = QIcon.fromTheme("view-refresh")
        rotate_left_action = QAction(rotate_left_icon, "Rotate Left", self)
        rotate_left_action.triggered.connect(self.rotate_left)
        self.toolbar.addAction(rotate_left_action)
        add_spacer()

        rotate_right_icon = QIcon(resource_path("icons/rotate-right-icon-size_512.png"))
        if rotate_right_icon.isNull():
            rotate_right_icon = QIcon.fromTheme("view-refresh")
        rotate_right_action = QAction(rotate_right_icon, "Rotate Right", self)
        rotate_right_action.triggered.connect(self.rotate_right)
        self.toolbar.addAction(rotate_right_action)
        add_spacer()

        mirror_v_icon = QIcon(resource_path("icons/vertical-mirror-icon.png"))
        if mirror_v_icon.isNull():
            mirror_v_icon = QIcon.fromTheme("view-refresh")
        mirror_v_action = QAction(mirror_v_icon, "Mirror Vertical", self)
        mirror_v_action.triggered.connect(self.mirror_vertical)
        self.toolbar.addAction(mirror_v_action)
        add_spacer()

        mirror_h_icon = QIcon(resource_path("icons/horizontal-mirror-icon.png"))
        if mirror_h_icon.isNull():
            mirror_h_icon = QIcon.fromTheme("view-refresh")
        mirror_h_action = QAction(mirror_h_icon, "Mirror Horizontal", self)
        mirror_h_action.triggered.connect(self.mirror_horizontal)
        self.toolbar.addAction(mirror_h_action)
        add_spacer()

        convert_action = QAction("Convert", self)
        convert_action.triggered.connect(self.convert_ccr)
        self.toolbar.addAction(convert_action)
        add_spacer()

        self.unconvert_action = QAction("Un-convert", self)
        self.unconvert_action.triggered.connect(self.unconvert_ccr)
        self.toolbar.addAction(self.unconvert_action)
        add_spacer()

        self.export_action = QAction("Export…", self)
        self.export_action.triggered.connect(self.open_export_dialog)
        self.toolbar.addAction(self.export_action)

        self.layout.addWidget(self.toolbar)

        # Graphics Scene/View
        self.scene = QGraphicsScene(self)
        self.view = GraphicsImageView(self)
        self.view.setScene(self.scene)
        self.layout.addWidget(self.view)

        # Fine rotation slider
        self.rotation_slider = CenteringSlider(Qt.Horizontal)
        self.rotation_slider.setMinimum(-4500)
        self.rotation_slider.setMaximum(4500)
        self.rotation_slider.setValue(0)
        self.rotation_slider.setTickInterval(450)

        self.rotation_slider.setTickPosition(QSlider.TicksBelow)
        self.rotation_slider.valueChanged.connect(self._on_slider_rotate)
        self.rotation_slider.setEnabled(False)
        self.rotation_slider.sliderPressed.connect(self._on_slider_pressed)
        self.rotation_slider.sliderReleased.connect(self._on_slider_released)
        self.layout.addWidget(self.rotation_slider)

        self.setLayout(self.layout)

        # State
        self.current_pixmap = None
        self.current_idx = None
        self.current_converted = False
        self.current_fine_rotation = 0
        self.current_rotation = 0
        self.current_vertical_flip = False
        self.current_horizontal_flip = False

        self.pixmap_item = None
        self.reference_rect_item = None
        self.group_item = None

        # Crop state. crop_mode shows the full image with a selection overlay;
        # outside crop mode the confirmed crop is applied to the displayed
        # pixmap only (display-level — no reprocessing/re-conversion).
        self.crop_mode = False
        self._crop_rerender = False          # guards update_preview while entering crop mode
        self._pending_crop_local = None      # QRectF selection in full-pixmap coords
        self._crop_overlay_item = None
        self._crop_display_offset = (0, 0)   # px offset of displayed crop within full image

        # Coalesce a fine-rotation drag into a single undo step
        self._fine_rot_burst_active = False
        self._fine_rot_burst_timer = QTimer(self)
        self._fine_rot_burst_timer.setSingleShot(True)
        self._fine_rot_burst_timer.timeout.connect(self._end_fine_rot_burst)

        self._update_unconvert_action_state()

        # --- Add hotkey support ---
        self._install_shortcuts()
        # --- End hotkey support ---

    def _install_shortcuts(self):
        from PySide6.QtGui import QShortcut, QKeySequence
        # Left bracket: rotate left
        QShortcut(QKeySequence("["), self, self.rotate_left)
        # Right bracket: rotate right
        QShortcut(QKeySequence("]"), self, self.rotate_right)
        # Enter: confirm crop while in crop mode, otherwise convert image
        QShortcut(QKeySequence(Qt.Key_Return), self, self._on_enter_key)
        QShortcut(QKeySequence(Qt.Key_Enter), self, self._on_enter_key)
        # Esc: leave crop mode without changing the crop
        QShortcut(QKeySequence(Qt.Key_Escape), self, self._on_escape_key)

    def _on_enter_key(self):
        if self.crop_mode:
            # Confirming a crop is display-level only — never re-converts.
            self.confirm_crop()
        else:
            self.convert_ccr()

    def _on_escape_key(self):
        if self.crop_mode:
            self.cancel_crop_mode()

    def update_preview(self, idx):
        ''' Update the UI image based on the backend, using the index from the thumbnail list. '''
        if idx is None or not (0 <= idx < len(ccr_backend.images)):
            print("Invalid index for update_preview:", idx)
            return
        # Any external refresh while picking a crop abandons crop mode
        # (image switch, slider change, conversion, ...).
        if self.crop_mode and not self._crop_rerender:
            self._exit_crop_mode()

        preview_img = ccr_backend.get_preview_by_index(idx)
        self.current_idx = idx

        # Display-level crop: show only the cropped region, except in crop
        # mode where the full image is shown so the user can adjust/regret.
        self._crop_display_offset = (0, 0)
        crop = getattr(ccr_backend.images[idx], "crop_rect", None)
        if (preview_img is not None and not preview_img.isNull()
                and crop is not None and not self.crop_mode):
            px_rect = self._crop_rect_to_pixels(crop, preview_img)
            if px_rect is not None:
                preview_img = preview_img.copy(px_rect)
                self._crop_display_offset = (px_rect.x(), px_rect.y())

        self.current_pixmap = preview_img
        self.current_fine_rotation = ccr_backend.get_image_fine_rotation_by_index(idx)
        self.rotation_slider.setValue(self.current_fine_rotation)
        self.current_rotation = ccr_backend.get_image_rotation_by_index(idx)
        self.current_vertical_flip = ccr_backend.get_image_vertical_flip_by_index(idx)
        self.current_horizontal_flip = ccr_backend.get_image_horizontal_flip_by_index(idx)

        self.scene.clear()
        self.pixmap_item = None
        self.reference_rect_item = None
        self.view._bw_rect_item = None
        self._crop_overlay_item = None

        self.parent().parent().sliders_panel.set_current_idx(idx)

        if preview_img and not preview_img.isNull():
            self.rotation_slider.setEnabled(True)
            self.pixmap_item = QGraphicsPixmapItem(preview_img)
            self.scene.addItem(self.pixmap_item)

            ref = getattr(ccr_backend.images[idx], "reference_frame", None)
            print("Loaded reference_frame from backend:", ref)
            if ref:
                x1, y1, x2, y2 = ref
                # Backend rect is in full-image coords; shift into the
                # displayed (possibly cropped) pixmap's coordinate space.
                dx, dy = self._crop_display_offset
                rect = QRectF(x1 - dx, y1 - dy, x2 - x1, y2 - y1)
                self.reference_rect_item = QGraphicsRectItem(rect)
                pen = QPen(QColor(255, 0, 0, 180), 2, Qt.DashLine)
                self.reference_rect_item.setPen(pen)
                self.scene.addItem(self.reference_rect_item)

            else:
                # Show hint when no reference frame exists

                self.parent().parent().sliders_panel.set_hint(
                    "<b>Hint:</b><br>Draw a frame around the image + some film base (orange/brown). "
                    "Avoid white backlight or black film holder areas. Left-drag to draw, right-click to remove."
                )


            # Apply transformations which will handle fitting consistently
            self.apply_transformations()
            histogram = ccr_backend.get_histogram_image_by_index(idx)
            self.parent().parent().sliders_panel.set_histogram(histogram)
            
        else:
            self.rotation_slider.setEnabled(False)

        self._update_unconvert_action_state()

    def update_reference_rect(self, start, end):
        # Build the base (coarse) transform
        cx = self.current_pixmap.width() / 2
        cy = self.current_pixmap.height() / 2
        base_transform = QTransform()
        base_transform.translate(cx, cy)

        if self.current_vertical_flip:
            base_transform.scale(1, -1)
        if self.current_horizontal_flip:
            base_transform.scale(-1, 1)
        if self.current_rotation:
            base_transform.rotate(self.current_rotation)
        base_transform.translate(-cx, -cy)

        if not base_transform.isInvertible():
            return
        inv_transform = base_transform.inverted()[0]
        start_local = inv_transform.map(start)
        end_local = inv_transform.map(end)

        x1, y1 = start_local.x(), start_local.y()
        x2, y2 = end_local.x(), end_local.y()
        rect = QRectF(min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1))

        if self.reference_rect_item is None:
            self.reference_rect_item = QGraphicsRectItem(rect)
            pen = QPen(QColor(255, 0, 0, 120), 2, Qt.DashLine)
            self.reference_rect_item.setPen(pen)
            self.scene.addItem(self.reference_rect_item)
        else:
            self.reference_rect_item.setRect(rect)
        self.reference_rect_item.setTransform(base_transform)

    def _fit_view_to_content(self):
        """Consistently fit the view to the content, prioritizing the pixmap item."""
        if self.pixmap_item:
            # Fit to the transformed pixmap item for consistent zoom behavior
            self.view.fitInView(self.pixmap_item, Qt.KeepAspectRatio)
        elif self.scene.sceneRect() and not self.scene.sceneRect().isNull():
            # Fallback to scene rect if no pixmap item
            self.view.fitInView(self.scene.sceneRect(), Qt.KeepAspectRatio)

    def apply_transformations(self):
        if not self.pixmap_item:
            return

        # Center of the image
        cx = self.current_pixmap.width() / 2
        cy = self.current_pixmap.height() / 2

        # Coarse rotation and flips
        base_transform = QTransform()
        base_transform.translate(cx, cy)

        if self.current_vertical_flip:
            base_transform.scale(1, -1)
        if self.current_horizontal_flip:
            base_transform.scale(-1, 1)
        if self.current_rotation:
            base_transform.rotate(self.current_rotation)
        base_transform.translate(-cx, -cy)

        # Fine rotation (only for image)
        img_transform = QTransform(base_transform)
        if self.current_fine_rotation:
            img_transform.translate(cx, cy)
            img_transform.rotate(self.current_fine_rotation / 100.0)
            img_transform.translate(-cx, -cy)

        self.pixmap_item.setTransform(img_transform)

        # Rectangle only gets coarse rotation/flips
        if self.reference_rect_item:
            self.reference_rect_item.setTransform(base_transform)

        # Always fit the view to the content consistently
        self._fit_view_to_content()

    def _end_fine_rot_burst(self):
        self._fine_rot_burst_active = False

    def _on_slider_rotate(self, value):
        # Snapshot once per burst, and only for real user changes (the slider
        # is also set programmatically to the backend value on image switch).
        img = ccr_backend.get_image_by_index(self.current_idx)
        if img is not None and img.fine_rotation_angle != value:
            if not self._fine_rot_burst_active:
                img.push_undo_state()
                self._fine_rot_burst_active = True
            self._fine_rot_burst_timer.start(800)
        self.current_fine_rotation = value
        ccr_backend.set_image_fine_rotation_by_index(self.current_idx, value)
        self.apply_transformations()

    def _on_slider_pressed(self):
        self.show_grid = True
        self.view.viewport().update()

    def _on_slider_released(self):
        self.show_grid = False
        self.view.viewport().update()

    def _push_undo_for_current(self):
        img = ccr_backend.get_image_by_index(self.current_idx)
        if img is not None:
            img.push_undo_state()

    def rotate_left(self):
        self._push_undo_for_current()
        self.current_rotation = (self.current_rotation - 90) % 360
        ccr_backend.set_image_rotation_by_index(self.current_idx, self.current_rotation)
        self.apply_transformations()

    def rotate_right(self):
        self._push_undo_for_current()
        self.current_rotation = (self.current_rotation + 90) % 360
        ccr_backend.set_image_rotation_by_index(self.current_idx, self.current_rotation)
        self.apply_transformations()

    def mirror_vertical(self):
        self._push_undo_for_current()
        flip = ccr_backend.get_image_vertical_flip_by_index(self.current_idx)
        ccr_backend.set_image_vertical_flip_by_index(self.current_idx, not flip)
        self.current_vertical_flip = not flip
        self.apply_transformations()

    def mirror_horizontal(self):
        self._push_undo_for_current()
        flip = ccr_backend.get_image_horizontal_flip_by_index(self.current_idx)
        ccr_backend.set_image_horizontal_flip_by_index(self.current_idx, not flip)
        self.current_horizontal_flip = not flip
        self.apply_transformations()

    def convert_ccr(self):
        if self.current_idx is None:
            QMessageBox.warning(self, "No Image Selected", "Please select an image to convert.")
            return
        if ccr_backend.get_reference_frame_by_index(self.current_idx) is None:
            QMessageBox.warning(self, "No Reference Frame", "Please draw a reference frame before converting.")
            return
        ccr_backend.convert_negative_by_index(self.current_idx)
        self.update_preview(self.current_idx)
        self.parent().parent().thumbnail_list.update_thumbnail(self.current_idx)
        self._update_unconvert_action_state()

    def unconvert_ccr(self):
        if self.current_idx is None:
            QMessageBox.warning(self, "No Image Selected", "Please select an image to convert.")
            return
        ccr_backend.unconvert_negative_by_index(self.current_idx)
        self.update_preview(self.current_idx)
        self.parent().parent().thumbnail_list.update_thumbnail(self.current_idx)
        self._update_unconvert_action_state()

    def _update_unconvert_action_state(self):
        self.current_converted = ccr_backend.get_converted_state_by_index(self.current_idx) if self.current_idx is not None else False
        self.unconvert_action.setEnabled(self.current_converted)
        self.export_action.setEnabled(any(img.converted for img in ccr_backend.images))
        parent = self.parent()
        if hasattr(parent.parent(), "sliders_panel"):
            print("Setting sliders enabled based on current_converted:", self.current_converted)
            parent.parent().sliders_panel.set_sliders_enabled(self.current_converted)
        

    def set_bwpoint_mode(self, mode):
        """mode: 'black' | 'white' | None"""
        self.view.bwpoint_mode = mode
        self.view.wb_pick_mode = False
        self.view.setCursor(Qt.CrossCursor if mode else Qt.ArrowCursor)

    def set_wb_pick_mode(self, enabled):
        """Eyedropper mode: next click on the image picks a neutral point
        for automatic temperature/tint adjustment."""
        self.view.wb_pick_mode = enabled
        self.view.bwpoint_mode = None
        self.view.setCursor(_eyedropper_cursor() if enabled else Qt.ArrowCursor)

    # --- Crop mode -----------------------------------------------------

    def displayed_crop_offset(self):
        """Pixel offset of the displayed pixmap within the full preview image.
        (0, 0) unless a confirmed crop is currently being displayed."""
        return self._crop_display_offset

    @staticmethod
    def _crop_rect_to_pixels(crop, pixmap) -> "QRect | None":
        """Convert a normalized (x1, y1, x2, y2) crop to a QRect in pixmap
        pixels, or None when the rect is degenerate."""
        w, h = pixmap.width(), pixmap.height()
        if w <= 0 or h <= 0:
            return None
        x1 = max(0, min(w - 1, int(round(crop[0] * w))))
        y1 = max(0, min(h - 1, int(round(crop[1] * h))))
        x2 = max(x1 + 1, min(w, int(round(crop[2] * w))))
        y2 = max(y1 + 1, min(h, int(round(crop[3] * h))))
        if (x2 - x1) < 2 or (y2 - y1) < 2:
            return None
        return QRect(x1, y1, x2 - x1, y2 - y1)

    def _base_transform(self):
        """Coarse flip/rotation transform around the displayed pixmap center
        (same construction the reference-frame and B/W tools use)."""
        if self.current_pixmap is None:
            return None
        cx = self.current_pixmap.width() / 2
        cy = self.current_pixmap.height() / 2
        t = QTransform()
        t.translate(cx, cy)
        if self.current_vertical_flip:
            t.scale(1, -1)
        if self.current_horizontal_flip:
            t.scale(-1, 1)
        if self.current_rotation:
            t.rotate(self.current_rotation)
        t.translate(-cx, -cy)
        return t if t.isInvertible() else None

    def enter_crop_mode(self) -> bool:
        """Show the full image with the current crop (if any) as the starting
        selection, so the user can adjust or undo a previous crop."""
        if self.current_idx is None:
            return False
        img_obj = ccr_backend.get_image_by_index(self.current_idx)
        if img_obj is None or self.current_pixmap is None or self.current_pixmap.isNull():
            return False
        # Crop mode replaces any other interactive pick mode
        self.view.bwpoint_mode = None
        self.view.wb_pick_mode = False
        self.crop_mode = True
        self._pending_crop_local = None
        self._crop_rerender = True
        try:
            self.update_preview(self.current_idx)  # re-render un-cropped
        finally:
            self._crop_rerender = False
        crop = getattr(img_obj, "crop_rect", None)
        if crop is not None and self.current_pixmap is not None:
            w, h = self.current_pixmap.width(), self.current_pixmap.height()
            self._pending_crop_local = QRectF(
                crop[0] * w, crop[1] * h,
                (crop[2] - crop[0]) * w, (crop[3] - crop[1]) * h)
        self._draw_crop_overlay()
        self.view.setCursor(Qt.CrossCursor)
        return True

    def update_crop_selection(self, start_scene, end_scene):
        """Update the pending crop selection from a drag (scene coords)."""
        base = self._base_transform()
        if base is None or self.current_pixmap is None:
            return
        inv = base.inverted()[0]
        s = inv.map(start_scene)
        e = inv.map(end_scene)
        w, h = self.current_pixmap.width(), self.current_pixmap.height()
        x1 = max(0.0, min(float(w), min(s.x(), e.x())))
        y1 = max(0.0, min(float(h), min(s.y(), e.y())))
        x2 = max(0.0, min(float(w), max(s.x(), e.x())))
        y2 = max(0.0, min(float(h), max(s.y(), e.y())))
        self._pending_crop_local = QRectF(x1, y1, x2 - x1, y2 - y1)
        self._draw_crop_overlay()

    def _draw_crop_overlay(self):
        """Dim everything outside the pending crop selection so both the
        entire image and the crop target stay visible."""
        if self._crop_overlay_item is not None:
            try:
                self.scene.removeItem(self._crop_overlay_item)
            except RuntimeError:
                pass
            self._crop_overlay_item = None
        if not self.crop_mode or self.current_pixmap is None:
            return
        sel = self._pending_crop_local
        if sel is None or sel.width() < 2 or sel.height() < 2:
            return
        path = QPainterPath()
        path.setFillRule(Qt.OddEvenFill)
        path.addRect(QRectF(0, 0, self.current_pixmap.width(), self.current_pixmap.height()))
        path.addRect(sel)
        item = QGraphicsPathItem(path)
        item.setBrush(QBrush(QColor(0, 0, 0, 110)))
        item.setPen(QPen(QColor(255, 255, 255, 220), 2, Qt.DashLine))
        base = self._base_transform()
        if base is not None:
            item.setTransform(base)
        self.scene.addItem(item)
        self._crop_overlay_item = item

    def confirm_crop(self):
        """Enter pressed in crop mode: store the selection as the new crop.
        Display-level only — the conversion result is untouched."""
        if not self.crop_mode:
            return
        img_obj = ccr_backend.get_image_by_index(self.current_idx)
        sel = self._pending_crop_local
        if (img_obj is not None and sel is not None and self.current_pixmap is not None
                and sel.width() >= 10 and sel.height() >= 10):
            w, h = self.current_pixmap.width(), self.current_pixmap.height()
            new_crop = (max(0.0, sel.left() / w), max(0.0, sel.top() / h),
                        min(1.0, sel.right() / w), min(1.0, sel.bottom() / h))
            # Selecting (almost) the whole image clears the crop
            if (new_crop[0] <= 0.001 and new_crop[1] <= 0.001
                    and new_crop[2] >= 0.999 and new_crop[3] >= 0.999):
                new_crop = None
            if new_crop != img_obj.crop_rect:
                img_obj.push_undo_state()
                img_obj.crop_rect = new_crop
        self._exit_crop_mode()
        self.update_preview(self.current_idx)
        try:
            self.parent().parent().sliders_panel.set_temporary_hint(
                "Crop applied. Ctrl+Z to undo, or press Crop again to adjust.", duration=4000)
        except AttributeError:
            pass

    def cancel_crop_mode(self):
        """Esc pressed in crop mode: leave without changing the crop."""
        if not self.crop_mode:
            return
        self._exit_crop_mode()
        self.update_preview(self.current_idx)

    def clear_crop(self):
        """Right-click in crop mode: remove the crop entirely."""
        img_obj = ccr_backend.get_image_by_index(self.current_idx)
        if img_obj is not None and img_obj.crop_rect is not None:
            img_obj.push_undo_state()
            img_obj.crop_rect = None
        self._exit_crop_mode()
        self.update_preview(self.current_idx)

    def _exit_crop_mode(self):
        self.crop_mode = False
        self._pending_crop_local = None
        self.view._crop_drag_start = None
        if self._crop_overlay_item is not None:
            try:
                self.scene.removeItem(self._crop_overlay_item)
            except RuntimeError:
                pass
            self._crop_overlay_item = None
        self.view.setCursor(Qt.ArrowCursor)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_view_to_content()

    def auto_frame(self):
        # Show progress dialog
        dialog = AutoFrameDialog(self)
        worker = AutoFrameWorker()
        dialog.set_worker(worker)
        worker.progress.connect(dialog.set_progress)
        worker.finished.connect(lambda: self._on_auto_frame_finished(dialog))
        worker.start()
        dialog.exec_()
    
    def _on_auto_frame_finished(self, dialog):
        """Handle auto frame completion."""
        dialog.accept()
        self.parent().parent().thumbnail_list.update_all_thumbnails()
        if self.current_idx is not None:
            self.update_preview(self.current_idx)
        
        # Show completion hint
        self.parent().parent().sliders_panel.set_temporary_hint("Auto frame conversion completed!", duration=2000)

    def open_export_dialog(self):
        if not any(img.converted for img in ccr_backend.images):
            QMessageBox.information(self, "Nothing to Export",
                                    "Convert at least one image before exporting.")
            return

        settings_dialog = ExportSettingsDialog(self, current_idx=self.current_idx)
        if settings_dialog.exec_() != QDialog.Accepted or settings_dialog.plan is None:
            return
        plan = settings_dialog.plan

        progress_dialog = ExportProgressDialog(self)
        self._export_worker = ExportItemsWorker(plan)
        progress_dialog.set_worker(self._export_worker)
        self._export_worker.progress.connect(progress_dialog.set_progress)
        self._export_worker.finished.connect(
            lambda result: self._on_export_finished(progress_dialog, result, plan))
        self._export_worker.start()
        progress_dialog.exec_()

    def _on_export_finished(self, dialog, result, plan):
        dialog.accept()
        lines = [f"Exported: {result.get('exported', 0)}"]
        if plan.skipped:
            lines.append(f"Skipped (already exist): {plan.skipped}")
        if result.get("failed"):
            lines.append(f"Failed: {result['failed']}")
        if result.get("cancelled"):
            lines.append("Export was stopped before completion.")
        lines.append(f"\nFolder: {plan.destination}")
        QMessageBox.information(self, "Export Complete", "\n".join(lines))
        if plan.open_folder:
            QDesktopServices.openUrl(QUrl.fromLocalFile(plan.destination))

class AutoFrameWorker(QThread):
    finished = Signal()
    progress = Signal(int, int)  # current, total

    def __init__(self):
        super().__init__()
        self._stop_requested = False

    def run(self):
        # Define progress callback
        def progress_callback(current, total_count):
            if not self._stop_requested:
                self.progress.emit(current, total_count)
        
        # Use the auto_frame_all_images functionality from backend with progress
        try:
            ccr_backend.auto_frame_all_images(progress_callback=progress_callback)
        except Exception as e:
            print(f"Failed to auto frame images: {e}")
        
        self.finished.emit()

    def stop(self):
        self._stop_requested = True

class AutoFrameDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Auto Framing...")
        self.setModal(True)
        self.setWindowFlags(Qt.Dialog | Qt.CustomizeWindowHint)
        self.setWindowModality(Qt.ApplicationModal)

        self.label = QLabel("Auto framing and converting images", self)
        self.label.setAlignment(Qt.AlignCenter)

        self.progress_label = QLabel("", self)
        self.progress_label.setAlignment(Qt.AlignCenter)

        self.stop_button = QPushButton("Stop", self)
        self.stop_button.clicked.connect(self.on_stop_clicked)

        layout = QVBoxLayout()
        layout.addWidget(self.label)
        layout.addWidget(self.progress_label)
        layout.addWidget(self.stop_button)
        self.setLayout(layout)

        self.setMinimumWidth(250)

        self._dot_count = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.animate)
        self.timer.start(400)
        self.stopping = False

        self.worker = None  # Will be set from outside

        if parent is not None:
            geo = parent.frameGeometry()
            self.move(geo.center() - self.rect().center())

    def animate(self):
        self._dot_count = (self._dot_count + 1) % 4
        if self.stopping:
            self.label.setText("Stopping"+ "." * self._dot_count)
            return
        
        self.label.setText("Auto framing and converting images" + "." * self._dot_count)

    def set_progress(self, current, total):
        self.progress_label.setText(f"{current} / {total}")

    def set_worker(self, worker):
        self.worker = worker

    def on_stop_clicked(self):
        if self.worker is not None:
            self.worker.stop()
        self.stop_button.setEnabled(False)
        self.label.setText("Stopping...")
        self.stopping = True

    def closeEvent(self, event):
        event.ignore()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            event.ignore()
        else:
            super().keyPressEvent(event)

class ExportItemsWorker(QThread):
    finished = Signal(dict)
    progress = Signal(int, int)  # current, total

    def __init__(self, plan):
        super().__init__()
        self.plan = plan
        self._stop_requested = False

    def run(self):
        def progress_callback(current, total_count):
            if not self._stop_requested:
                self.progress.emit(current, total_count)

        try:
            result = ccr_backend.export_items(
                self.plan.items,
                jpg_output=self.plan.jpg_output,
                jpg_quality=self.plan.jpg_quality,
                max_long_side=self.plan.max_long_side,
                progress_callback=progress_callback,
                cancel_check=lambda: self._stop_requested,
            )
        except Exception as e:
            print(f"Failed to export images: {e}")
            result = {"exported": 0, "failed": len(self.plan.items),
                      "cancelled": False, "failures": []}
        self.finished.emit(result)

    def stop(self):
        self._stop_requested = True

class ExportProgressDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Exporting...")
        self.setModal(True)
        self.setWindowFlags(Qt.Dialog | Qt.CustomizeWindowHint)
        self.setWindowModality(Qt.ApplicationModal)

        self.label = QLabel("Exporting", self)
        self.label.setAlignment(Qt.AlignCenter)

        self.progress_label = QLabel("", self)
        self.progress_label.setAlignment(Qt.AlignCenter)

        self.stop_button = QPushButton("Stop", self)
        self.stop_button.clicked.connect(self.on_stop_clicked)

        layout = QVBoxLayout()
        layout.addWidget(self.label)
        layout.addWidget(self.progress_label)
        layout.addWidget(self.stop_button)
        self.setLayout(layout)

        self.setMinimumWidth(200)

        self._dot_count = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.animate)
        self.timer.start(400)
        self.stopping = False

        self.worker = None  # Will be set from outside

        if parent is not None:
            geo = parent.frameGeometry()
            self.move(geo.center() - self.rect().center())

    def animate(self):
        self._dot_count = (self._dot_count + 1) % 4
        if self.stopping:
            self.label.setText("Stopping"+ "." * self._dot_count)
            return
        
        self.label.setText("Exporting" + "." * self._dot_count)

    def set_progress(self, current, total):
        self.progress_label.setText(f"{current} / {total}")

    def set_worker(self, worker):
        self.worker = worker

    def on_stop_clicked(self):
        if self.worker is not None:
            self.worker.stop()
        self.stop_button.setEnabled(False)
        self.label.setText("Stopping...")
        self.stopping = True

    def closeEvent(self, event):
        event.ignore()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            event.ignore()
        else:
            super().keyPressEvent(event)





