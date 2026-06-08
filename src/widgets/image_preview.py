from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QToolBar, QMessageBox, QSlider, QGraphicsView, QGraphicsScene,
    QGraphicsPixmapItem, QGraphicsRectItem, QStyleOptionSlider, QFileDialog, QDialog,
    QLabel, QPushButton, QStyle, QCheckBox, QSizePolicy  
)
from PySide6.QtGui import QPixmap, QIcon, QTransform, QPen, QColor, QAction, QPainter
from PySide6.QtCore import Qt, QSize, Signal, QRectF, QPointF, QThread, QTimer
from core.ccr_backend import ccr_backend
import sys
import os

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


def normalize_unicode_path(path):
    """Normalize Unicode path - fallback if utils not available"""
    return os.path.normpath(os.path.abspath(path))


def validate_unicode_path(path):
    """Validate Unicode path - fallback if utils not available"""
    try:
        return os.path.exists(path)
    except (UnicodeError, OSError):
        return False

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

        # Disable scrollbars
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    def mousePressEvent(self, event):
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

    def mouseReleaseEvent(self, event):
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
                x1 = int(min(s.x(), e.x()))
                y1 = int(min(s.y(), e.y()))
                x2 = int(max(s.x(), e.x()))
                y2 = int(max(s.y(), e.y()))

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
                ccr_backend.set_reference_frame_by_index(
                    self.parent_widget.current_idx,
                    (int(x), int(y), int(x2), int(y2))
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

        self.export_action = QAction("Export", self)  # <-- assign to self
        self.export_action.triggered.connect(self.export_image)
        self.toolbar.addAction(self.export_action)
        add_spacer()

        self.export_all_action = QAction("Export All", self)  # <-- assign to self
        self.export_all_action.triggered.connect(self.export_all_images)
        self.toolbar.addAction(self.export_all_action)
        add_spacer()

        # --- Add Export jpgs checkbox ---
        self.export_jpgs_checkbox = QCheckBox("Export jpgs")
        self.export_jpgs_checkbox.setChecked(False)
        self.toolbar.addWidget(self.export_jpgs_checkbox)
        # --- End checkbox addition ---

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
        # Enter: convert image
        QShortcut(QKeySequence(Qt.Key_Return), self, self.convert_ccr)
        QShortcut(QKeySequence(Qt.Key_Enter), self, self.convert_ccr)

    def update_preview(self, idx):
        ''' Update the UI image based on the backend, using the index from the thumbnail list. '''
        if idx is None or not (0 <= idx < len(ccr_backend.images)):
            print("Invalid index for update_preview:", idx)
            return
        preview_img = ccr_backend.get_preview_by_index(idx)
        self.current_idx = idx
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

        self.parent().parent().sliders_panel.set_current_idx(idx)

        if preview_img and not preview_img.isNull():
            self.rotation_slider.setEnabled(True)
            self.pixmap_item = QGraphicsPixmapItem(preview_img)
            self.scene.addItem(self.pixmap_item)

            ref = getattr(ccr_backend.images[idx], "reference_frame", None)
            print("Loaded reference_frame from backend:", ref)
            if ref:
                x1, y1, x2, y2 = ref
                rect = QRectF(x1, y1, x2 - x1, y2 - y1)
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

    def _on_slider_rotate(self, value):
        self.current_fine_rotation = value
        ccr_backend.set_image_fine_rotation_by_index(self.current_idx, value)
        self.apply_transformations()

    def _on_slider_pressed(self):
        self.show_grid = True
        self.view.viewport().update()

    def _on_slider_released(self):
        self.show_grid = False
        self.view.viewport().update()

    def rotate_left(self):
        self.current_rotation = (self.current_rotation - 90) % 360
        ccr_backend.set_image_rotation_by_index(self.current_idx, self.current_rotation)
        self.apply_transformations()

    def rotate_right(self):
        self.current_rotation = (self.current_rotation + 90) % 360
        ccr_backend.set_image_rotation_by_index(self.current_idx, self.current_rotation)
        self.apply_transformations()

    def mirror_vertical(self):
        flip = ccr_backend.get_image_vertical_flip_by_index(self.current_idx)
        ccr_backend.set_image_vertical_flip_by_index(self.current_idx, not flip)
        self.current_vertical_flip = not flip
        self.apply_transformations()

    def mirror_horizontal(self):
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
        self.export_action.setEnabled(self.current_converted)
        parent = self.parent()
        if hasattr(parent.parent(), "sliders_panel"):
            print("Setting sliders enabled based on current_converted:", self.current_converted)
            parent.parent().sliders_panel.set_sliders_enabled(self.current_converted)
        

    def set_bwpoint_mode(self, mode):
        """mode: 'black' | 'white' | None"""
        self.view.bwpoint_mode = mode
        self.view.setCursor(Qt.CrossCursor if mode else Qt.ArrowCursor)

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

    def export_image(self):
        if self.current_idx is None:
            QMessageBox.warning(self, "No Image Selected", "Please select an image to convert.")
            return

        ccr_img = ccr_backend.images[self.current_idx]
        base_name = os.path.splitext(os.path.basename(ccr_img.file_path))[0]

        export_jpg = self.export_jpgs_checkbox.isChecked()  # <-- Check the checkbox state

        if export_jpg:
            default_name = f"{base_name}_ccr.jpg"
            file_filter = "JPEG Files (*.jpg *.jpeg)"
        else:
            default_name = f"{base_name}_ccr.tiff"
            file_filter = "TIFF Files (*.tiff *.tif)"

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Normalized Image",
            default_name,
            file_filter
        )
        if not file_path:
            return
        
        # Validate Unicode path
        normalized_path = normalize_unicode_path(file_path)
        if not validate_unicode_path(os.path.dirname(normalized_path)):
            QMessageBox.warning(
                self,
                "Unicode Path Warning",
                f"The export path contains characters that may cause issues:\n\n{file_path}\n\nPlease choose a simpler path name."
            )
            return

        # Ensure correct extension
        if export_jpg:
            if not (normalized_path.lower().endswith(".jpg") or normalized_path.lower().endswith(".jpeg")):
                normalized_path += ".jpg"
        else:
            if not (normalized_path.lower().endswith(".tiff") or normalized_path.lower().endswith(".tif")):
                normalized_path += ".tiff"

        dialog = ExportDialog(self)
        worker = ExportWorker(self.current_idx, normalized_path, export_jpg)  # <-- Pass flag to worker
        worker.finished.connect(lambda path: self._on_export_all_finished(dialog, path))
        worker.start()
        dialog.exec_()

    def export_all_images(self):
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Folder to Export All Images",
            "",
            QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks
        )
        if not folder:
            return
        
        # Validate Unicode path
        normalized_folder = normalize_unicode_path(folder)
        if not validate_unicode_path(normalized_folder):
            QMessageBox.warning(
                self,
                "Unicode Path Warning",
                f"The export folder path contains characters that may cause issues:\n\n{folder}\n\nPlease choose a simpler folder path."
            )
            return

        export_jpg = self.export_jpgs_checkbox.isChecked()  # <-- Check the checkbox state

        dialog = ExportDialog(self)
        worker = ExportAllWorker(normalized_folder, export_jpg)  # <-- Pass flag to worker
        dialog.set_worker(worker)
        worker.progress.connect(dialog.set_progress)
        worker.finished.connect(lambda path: self._on_export_all_finished(dialog, path))
        worker.start()
        dialog.exec_()

    def _on_export_all_finished(self, dialog, path):
        dialog.accept()
        QMessageBox.information(self, "Export Complete", f"All images exported to:\n{path}")

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

class ExportWorker(QThread):
    finished = Signal(str)

    def __init__(self, idx, file_path, jpg_output=False):  # <-- Add jpg_output
        super().__init__()
        self.idx = idx
        self.file_path = file_path
        self.jpg_output = jpg_output  # <-- Store flag

    def run(self):
        ccr_backend.export_image_by_index(self.idx, self.file_path, jpg_output=self.jpg_output)  # <-- Pass flag
        self.finished.emit(self.file_path)

class ExportAllWorker(QThread):
    finished = Signal(str)
    progress = Signal(int, int)  # current, total

    def __init__(self, output_folder, jpg_output=False):  # <-- Add jpg_output
        super().__init__()
        self.output_folder = output_folder
        self.jpg_output = jpg_output  # <-- Store flag
        self._stop_requested = False

    def run(self):
        images = ccr_backend.images
        total = sum(1 for img in images if img.converted)
        if not os.path.exists(self.output_folder):
            os.makedirs(self.output_folder)
        self.progress.emit(0, total)
        
        # Define progress callback
        def progress_callback(current, total_count):
            if not self._stop_requested:
                self.progress.emit(current, total_count)
        
        # Use the parallel export functionality from backend with progress
        try:
            ccr_backend.export_all_images(self.output_folder, jpg_output=self.jpg_output, progress_callback=progress_callback)
        except Exception as e:
            print(f"Failed to export images: {e}")
        
        self.finished.emit(self.output_folder)

    def stop(self):
        self._stop_requested = True

class ExportDialog(QDialog):
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





