"""
Create-Camera-Profile-from-IT8 wizard (PySide6).

A 5-step QDialog that walks the user from an IT8 target shot to a saved camera
input ICC profile: pick the shot, pick the batch reference file, locate the patch
grid (draggable 4-corner overlay), review fit quality (deltaE), and save / apply.
Core math lives in core.it8_profile; this module is UI only.
See spec/it8-camera-profile.md.
"""
import os
from datetime import datetime
from typing import Optional

import numpy as np

from PySide6.QtCore import Qt, QPointF, QRectF, QSettings, Signal
from PySide6.QtGui import QImage, QPainter, QPen, QBrush, QColor, QPolygonF
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QMessageBox, QPushButton,
    QSlider, QStackedWidget, QVBoxLayout, QWidget,
)

from core import it8_profile as it8
from core.ccr_backend import ccr_backend
from utils.unicode_path_utils import normalize_unicode_path, validate_unicode_path

_REF_URL = "http://www.targets.coloraid.de/"


def _gamma_stretch_to_qimage(arr_u16: np.ndarray) -> QImage:
    """8-bit gamma-stretched view of a raw-linear 16-bit RGB array (display
    only — the fit always uses the linear data)."""
    a = arr_u16.astype(np.float32)
    # Normalise exposure by a high percentile so the dark linear scan is visible.
    p = np.percentile(a, 99.5)
    if p < 1:
        p = a.max() if a.max() > 0 else 1.0
    norm = np.clip(a / p, 0.0, 1.0)
    disp = np.power(norm, 1.0 / 2.2)
    disp8 = np.ascontiguousarray((disp * 255).astype(np.uint8))
    h, w = disp8.shape[:2]
    img = QImage(disp8.data, w, h, 3 * w, QImage.Format_RGB888)
    out = img.copy()                      # detach from the temporary buffer
    return out


class IT8PatchLocator(QWidget):
    """Shows the target shot with a draggable 4-corner quad over the colour grid
    and a live overlay of all 288 sample dots."""

    changed = Signal()

    HANDLE_R = 7

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(520, 360)
        self.setMouseTracking(True)
        self._qimg: Optional[QImage] = None
        self._iw = self._ih = 0
        self._quad = []                   # 4 (x,y) array-space corners TL,TR,BR,BL
        self._gray_offset = 0.0
        self._drag = -1                   # index of handle being dragged, or -1
        self._scale = 1.0
        self._ox = self._oy = 0.0

    def set_image(self, arr_u16: np.ndarray):
        self._qimg = _gamma_stretch_to_qimage(arr_u16)
        self._ih, self._iw = arr_u16.shape[:2]
        # Default colour-block quad inset from the image edges.
        ix, iy = self._iw, self._ih
        self._quad = [(0.10 * ix, 0.10 * iy), (0.90 * ix, 0.10 * iy),
                      (0.90 * ix, 0.82 * iy), (0.10 * ix, 0.82 * iy)]
        self.update()
        self.changed.emit()

    # --- geometry accessors ---
    def quad(self):
        return list(self._quad)

    def gray_offset(self):
        return self._gray_offset

    def set_gray_offset(self, v: float):
        self._gray_offset = float(v)
        self.update()
        self.changed.emit()

    def flip(self):
        if self._quad:
            self._quad = it8.flip_quad(self._quad)
            self.update()
            self.changed.emit()

    def points(self):
        if not self._quad:
            return {}
        return it8.grid_sample_points(self._quad, self._gray_offset)

    # --- coordinate mapping (array <-> widget) ---
    def _recompute_fit(self):
        if not self._iw:
            return
        W, H = self.width(), self.height()
        self._scale = min(W / self._iw, H / self._ih)
        self._ox = (W - self._iw * self._scale) / 2
        self._oy = (H - self._ih * self._scale) / 2

    def _a2w(self, x, y):
        return QPointF(self._ox + x * self._scale, self._oy + y * self._scale)

    def _w2a(self, x, y):
        return ((x - self._ox) / self._scale, (y - self._oy) / self._scale)

    # --- painting ---
    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(30, 30, 30))
        if self._qimg is None:
            p.setPen(QColor(180, 180, 180))
            p.drawText(self.rect(), Qt.AlignCenter, "No image")
            return
        self._recompute_fit()
        target = QRectF(self._ox, self._oy, self._iw * self._scale,
                        self._ih * self._scale)
        p.drawImage(target, self._qimg)
        p.setRenderHint(QPainter.Antialiasing, True)

        # Quad outline.
        poly = QPolygonF([self._a2w(x, y) for (x, y) in self._quad])
        p.setPen(QPen(QColor(80, 200, 120), 2))
        p.setBrush(Qt.NoBrush)
        p.drawPolygon(poly)

        # Sample dots (colour vs gray differ in colour).
        pts = self.points()
        for sid, (x, y) in pts.items():
            w = self._a2w(x, y)
            if not target.contains(w):
                p.setPen(QPen(QColor(220, 80, 80), 1))   # out of frame -> red
            elif sid.startswith("GS"):
                p.setPen(QPen(QColor(120, 170, 255), 1))
            else:
                p.setPen(QPen(QColor(255, 230, 90), 1))
            p.drawEllipse(w, 1.6, 1.6)

        # Corner handles.
        for i, (x, y) in enumerate(self._quad):
            w = self._a2w(x, y)
            p.setBrush(QBrush(QColor(80, 200, 120)))
            p.setPen(QPen(QColor(20, 20, 20), 1))
            p.drawEllipse(w, self.HANDLE_R, self.HANDLE_R)
        p.end()

    # --- mouse ---
    def mousePressEvent(self, e):
        if self._qimg is None:
            return
        self._recompute_fit()
        pos = e.position() if hasattr(e, "position") else QPointF(e.pos())
        # Pick the nearest handle within grab radius.
        best, bestd = -1, (self.HANDLE_R + 6) ** 2
        for i, (x, y) in enumerate(self._quad):
            w = self._a2w(x, y)
            d = (w.x() - pos.x()) ** 2 + (w.y() - pos.y()) ** 2
            if d < bestd:
                best, bestd = i, d
        self._drag = best

    def mouseMoveEvent(self, e):
        if self._drag < 0:
            return
        pos = e.position() if hasattr(e, "position") else QPointF(e.pos())
        ax, ay = self._w2a(pos.x(), pos.y())
        ax = max(0.0, min(self._iw, ax))
        ay = max(0.0, min(self._ih, ay))
        self._quad[self._drag] = (ax, ay)
        self.update()                      # dots follow live (cheap repaint)

    def mouseReleaseEvent(self, _):
        was_dragging = self._drag >= 0
        self._drag = -1
        if was_dragging:
            # Re-sample (expensive) only once the drag settles, not per move.
            self.changed.emit()


class IT8ProfileDialog(QDialog):
    """5-step wizard. On success the profile is written; `self.saved_path` holds
    the .icc path and `self.apply_now` whether to activate it as input profile."""

    PAGE_TARGET, PAGE_REF, PAGE_LOCATE, PAGE_BUILD, PAGE_SAVE = range(5)

    def __init__(self, parent=None, current_path: Optional[str] = None):
        super().__init__(parent)
        self.setWindowTitle("Create Camera Profile from IT8")
        self.setModal(True)
        self.setMinimumSize(720, 560)
        self._settings = QSettings("FreeCCR", "FreeCCR")

        self._current_path = current_path
        self._target_path: Optional[str] = None
        self._target_img: Optional[np.ndarray] = None
        self._ref: Optional[it8.IT8Reference] = None
        self._fit: Optional[it8.CameraFit] = None
        self._locator_arr = None           # array currently loaded in the locator
        self.saved_path: Optional[str] = None
        self.apply_now = False

        self.stack = QStackedWidget(self)
        self.stack.addWidget(self._build_target_page())
        self.stack.addWidget(self._build_ref_page())
        self.stack.addWidget(self._build_locate_page())
        self.stack.addWidget(self._build_build_page())
        self.stack.addWidget(self._build_save_page())

        self.back_btn = QPushButton("Back")
        self.next_btn = QPushButton("Next")
        self.cancel_btn = QPushButton("Cancel")
        self.back_btn.clicked.connect(self._go_back)
        self.next_btn.clicked.connect(self._go_next)
        self.cancel_btn.clicked.connect(self.reject)

        nav = QHBoxLayout()
        nav.addWidget(self.cancel_btn)
        nav.addStretch(1)
        nav.addWidget(self.back_btn)
        nav.addWidget(self.next_btn)

        root = QVBoxLayout(self)
        root.addWidget(self.stack, 1)
        root.addLayout(nav)
        self._update_nav()

    # ------------------------------------------------------------------ #
    # Pages
    # ------------------------------------------------------------------ #
    def _build_target_page(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("<b>Step 1 — IT8 target shot</b>"))
        guide = QLabel(
            "Photograph a printed IT8 chart under your capture light, then "
            "select that photo here.<br><br>"
            "<b>Capture tips</b> — broad-spectrum daylight/strobe light (not "
            "tungsten); even, glare-free lighting; chart flat and square to the "
            "camera; fill the centre of the frame; expose so the lightest "
            "grayscale patch is bright but <i>not</i> clipped. <b>Shoot RAW</b> — "
            "the profile is built from the raw-linear sensor data, the same "
            "space FreeCCR converts negatives in.<br><br>"
            "The profile is valid only under the light it was shot in.")
        guide.setWordWrap(True)
        lay.addWidget(guide)
        row = QHBoxLayout()
        self.use_current_btn = QPushButton("Use current image")
        self.use_current_btn.setEnabled(bool(self._current_path))
        self.use_current_btn.clicked.connect(self._use_current)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_target)
        row.addWidget(self.use_current_btn)
        row.addWidget(browse)
        row.addStretch(1)
        lay.addLayout(row)
        self.target_label = QLabel("<i>No target selected.</i>")
        self.target_label.setWordWrap(True)
        lay.addWidget(self.target_label)
        lay.addStretch(1)
        return w

    def _build_ref_page(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("<b>Step 2 — Batch reference file</b>"))
        guide = QLabel(
            "Load the reference data file that matches your chart's printed "
            "<b>batch/serial number</b>. Each IT8 chart batch is measured "
            "separately — the wrong file gives wrong colour.<br>"
            f"Wolf Faust provides free per-batch files: <a href='{_REF_URL}'>"
            f"{_REF_URL}</a>")
        guide.setWordWrap(True)
        guide.setOpenExternalLinks(True)
        lay.addWidget(guide)
        row = QHBoxLayout()
        browse = QPushButton("Browse reference…")
        browse.clicked.connect(self._browse_ref)
        row.addWidget(browse)
        row.addStretch(1)
        lay.addLayout(row)
        self.ref_label = QLabel("<i>No reference loaded.</i>")
        self.ref_label.setWordWrap(True)
        lay.addWidget(self.ref_label)
        lay.addStretch(1)
        return w

    def _build_locate_page(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel(
            "<b>Step 3 — Locate the patches</b> — drag the four green corners "
            "onto the outer corners of the 12×22 colour grid. Yellow dots are "
            "colour patches, blue dots the grayscale strip."))
        self.locator = IT8PatchLocator()
        self.locator.changed.connect(self._update_locate_status)
        lay.addWidget(self.locator, 1)
        ctl = QHBoxLayout()
        flip = QPushButton("Flip 180°")
        flip.clicked.connect(self.locator.flip)
        ctl.addWidget(flip)
        ctl.addWidget(QLabel("Gray strip:"))
        self.gray_slider = QSlider(Qt.Horizontal)
        self.gray_slider.setMinimum(-100)
        self.gray_slider.setMaximum(100)
        self.gray_slider.setValue(0)
        self.gray_slider.setFixedWidth(160)
        self.gray_slider.valueChanged.connect(
            lambda v: self.locator.set_gray_offset(v / 1000.0))
        ctl.addWidget(self.gray_slider)
        ctl.addStretch(1)
        self.locate_status = QLabel("")
        ctl.addWidget(self.locate_status)
        lay.addLayout(ctl)
        return w

    def _build_build_page(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("<b>Step 4 — Review fit quality</b>"))
        self.quality_label = QLabel("")
        self.quality_label.setWordWrap(True)
        lay.addWidget(self.quality_label)
        self.worst_list = QListWidget()
        self.worst_list.setMaximumHeight(150)
        lay.addWidget(self.worst_list)
        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.illum_combo = QComboBox()
        self.illum_combo.setEditable(True)
        self.illum_combo.addItems(["Daylight", "Strobe/Flash", "Tungsten",
                                   "Fluorescent", "Other"])
        form.addRow("Profile name:", self.name_edit)
        form.addRow("Illuminant:", self.illum_combo)
        lay.addLayout(form)
        lay.addStretch(1)
        return w

    def _build_save_page(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("<b>Step 5 — Save</b>"))
        self.save_label = QLabel("")
        self.save_label.setWordWrap(True)
        lay.addWidget(self.save_label)
        row = QHBoxLayout()
        self.save_path_edit = QLineEdit()
        choose = QPushButton("Choose…")
        choose.clicked.connect(self._choose_save_path)
        row.addWidget(self.save_path_edit, 1)
        row.addWidget(choose)
        lay.addLayout(row)
        self.apply_check = QCheckBox("Set as input profile now")
        self.apply_check.setChecked(True)
        lay.addWidget(self.apply_check)
        lay.addStretch(1)
        return w

    # ------------------------------------------------------------------ #
    # Page 1 — target
    # ------------------------------------------------------------------ #
    def _use_current(self):
        if self._current_path:
            self._set_target(self._current_path)

    def _browse_target(self):
        start = self._settings.value("files/last_open_dir", "", type=str)
        path, _ = QFileDialog.getOpenFileName(
            self, "Select IT8 target shot", start,
            "Images (*.dng *.tif *.tiff *.arw *.nef *.cr2 *.cr3 *.raf *.png "
            "*.jpg *.jpeg *.rw2 *.3fr *.fff);;All Files (*)")
        if path:
            self._set_target(path)

    def _set_target(self, path):
        self._target_path = path
        self._target_img = None            # force re-decode on Next
        raw = ", ".join((".dng", ".arw", ".nef", ".cr2", ".cr3", ".raf",
                         ".rw2", ".3fr", ".fff"))
        is_raw = os.path.splitext(path)[1].lower() in raw
        warn = ("" if is_raw else
                "<br><span style='color:#cc7a00'>Note: not a RAW file — for "
                "FreeCCR's pipeline a RAW shot gives the matching device "
                "space.</span>")
        self.target_label.setText(f"Selected: <b>{os.path.basename(path)}</b>{warn}")
        self._update_nav()

    def _decode_target(self) -> bool:
        if self._target_img is not None:
            return True
        norm = normalize_unicode_path(self._target_path)
        if not validate_unicode_path(norm):
            QMessageBox.warning(self, "Unicode Path",
                                "This file path can't be processed. Please move "
                                "it to a simpler path.")
            return False
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            arr = it8.decode_target(norm)
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Decode Error",
                                 f"Could not read the target shot:\n\n{e}")
            return False
        QApplication.restoreOverrideCursor()
        if arr is None or arr.ndim != 3:
            QMessageBox.critical(self, "Decode Error",
                                 "Could not decode the target shot.")
            return False
        self._target_img = arr
        return True

    # ------------------------------------------------------------------ #
    # Page 2 — reference
    # ------------------------------------------------------------------ #
    def _browse_ref(self):
        start = self._settings.value("files/last_it8_ref_dir", "", type=str)
        path, _ = QFileDialog.getOpenFileName(
            self, "Select IT8 reference file", start,
            "IT8 reference (*.it8 *.txt *.cie);;All Files (*)")
        if not path:
            return
        self._settings.setValue("files/last_it8_ref_dir", os.path.dirname(path))
        try:
            ref = it8.parse_it8_reference(path)
        except it8.IT8ReferenceError as e:
            QMessageBox.warning(self, "Reference File", str(e))
            return
        except Exception as e:
            QMessageBox.warning(self, "Reference File",
                                f"Could not read the reference file:\n\n{e}")
            return
        self._ref = ref
        n = len(ref.matched_ids)
        self.ref_label.setText(
            f"Loaded: <b>{os.path.basename(path)}</b><br>"
            f"Chart type: {ref.chart_type or 'unknown'}<br>"
            f"Batch / serial: <b>{ref.batch or 'unknown'}</b> — confirm this "
            f"matches the number printed on your chart.<br>"
            f"Patches recognised: {n}")
        self._update_nav()

    # ------------------------------------------------------------------ #
    # Page 3 — locate
    # ------------------------------------------------------------------ #
    def _update_locate_status(self):
        if self._target_img is None or self._ref is None:
            return
        pts = self.locator.points()
        samples = it8.sample_patches(self._target_img, pts, self.locator.quad())
        valid = sum(1 for sid in self._ref.matched_ids
                    if sid in samples and samples[sid].valid)
        total = len(self._ref.matched_ids)
        warn = ""
        if ("GS0" in samples and "GS23" in samples
                and samples["GS0"].valid and samples["GS23"].valid):
            if samples["GS0"].rgb.mean() < samples["GS23"].rgb.mean():
                warn = "  ⚠ GS0 darker than GS23 — try Flip 180°"
        self.locate_status.setText(f"Valid patches: {valid}/{total}{warn}")

    # ------------------------------------------------------------------ #
    # Page 4 — build
    # ------------------------------------------------------------------ #
    def _run_fit(self) -> bool:
        err = None
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            pts = self.locator.points()
            samples = it8.sample_patches(self._target_img, pts, self.locator.quad())
            fit = it8.fit_camera_matrix(samples, self._ref)
        except it8.IT8ReferenceError as e:
            fit, err = None, str(e)
        finally:
            QApplication.restoreOverrideCursor()
        if err is not None:
            QMessageBox.warning(self, "Fit", err)
            return False
        self._fit = fit
        if fit.avg_de < 2.0:
            chip, colour = "Good", "#3a8a3a"
        elif fit.avg_de < 4.0:
            chip, colour = "OK", "#b08a00"
        else:
            chip, colour = "Check placement / capture", "#b03030"
        self.quality_label.setText(
            f"<span style='background:{colour};color:white;padding:2px 8px;'>"
            f"{chip}</span>  &nbsp; "
            f"avg ΔE2000 <b>{fit.avg_de:.2f}</b> · median {fit.med_de:.2f} · "
            f"95th {fit.p95_de:.2f} · max {fit.max_de:.2f}<br>"
            f"Patches used: {len(fit.used_ids)} · dropped "
            f"(clipped/missing): {len(fit.dropped_ids)} · "
            f"white-balanced on {fit.wb_id}")
        self.worst_list.clear()
        for sid, de in fit.per_patch[:20]:
            self.worst_list.addItem(f"{sid}\tΔE {de:.2f}")
        if not self.name_edit.text().strip():
            illum = self.illum_combo.currentText().strip() or "Daylight"
            self.name_edit.setText(
                f"Camera {illum} {datetime.now():%Y-%m-%d}")
        return True

    # ------------------------------------------------------------------ #
    # Page 5 — save
    # ------------------------------------------------------------------ #
    def _default_save_path(self) -> str:
        from core.catalog import default_catalog_path
        folder = os.path.join(os.path.dirname(default_catalog_path()),
                              "camera_profiles")
        os.makedirs(folder, exist_ok=True)
        name = self.name_edit.text().strip() or "Camera Profile"
        safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in name)
        return os.path.join(folder, f"{safe}.icc")

    def _choose_save_path(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save camera ICC profile", self.save_path_edit.text(),
            "ICC Profiles (*.icc)")
        if path:
            if not path.lower().endswith(".icc"):
                path += ".icc"
            self.save_path_edit.setText(path)

    def _do_save(self) -> bool:
        path = self.save_path_edit.text().strip()
        if not path:
            QMessageBox.warning(self, "Save", "Choose a destination file.")
            return False
        illum = self.illum_combo.currentText().strip()
        name = self.name_edit.text().strip() or "Camera Profile"
        desc = f"{name} ({illum})" if illum else name
        try:
            icc = it8.build_camera_icc(self._fit, desc)
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            with open(path, "wb") as f:
                f.write(icc)
        except Exception as e:
            QMessageBox.critical(self, "Save Error",
                                 f"Could not write the profile:\n\n{e}")
            return False
        self.saved_path = path
        self.apply_now = self.apply_check.isChecked()
        return True

    # ------------------------------------------------------------------ #
    # Navigation
    # ------------------------------------------------------------------ #
    def _go_back(self):
        i = self.stack.currentIndex()
        if i > 0:
            self.stack.setCurrentIndex(i - 1)
            self._update_nav()

    def _go_next(self):
        i = self.stack.currentIndex()
        if i == self.PAGE_TARGET:
            if not self._target_path:
                QMessageBox.information(self, "Target", "Select a target shot.")
                return
            if not self._decode_target():
                return
        elif i == self.PAGE_REF:
            if self._ref is None:
                QMessageBox.information(self, "Reference",
                                        "Load a reference file.")
                return
        elif i == self.PAGE_LOCATE:
            if not self._run_fit():
                return
        elif i == self.PAGE_BUILD:
            if not self.save_path_edit.text().strip():
                self.save_path_edit.setText(self._default_save_path())
            self.save_label.setText(
                "The profile will be written here. With “Set as input profile "
                "now” ticked it is applied to every loaded image immediately "
                "(File ▸ Input ICC).")
        elif i == self.PAGE_SAVE:
            if self._do_save():
                self.accept()
            return

        self.stack.setCurrentIndex(i + 1)
        # On entering the locate page, (re)load the decoded target into the
        # locator whenever the underlying array changed (a re-decode produces a
        # new array object — identity check catches a new same-size target too).
        if self.stack.currentIndex() == self.PAGE_LOCATE:
            if self._target_img is not self._locator_arr:
                self.locator.set_image(self._target_img)
                self._locator_arr = self._target_img
            self._update_locate_status()
        self._update_nav()

    def _update_nav(self):
        i = self.stack.currentIndex()
        self.back_btn.setEnabled(i > 0)
        self.next_btn.setText("Finish" if i == self.PAGE_SAVE else "Next")
