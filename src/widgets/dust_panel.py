"""
Dust Removal panel — covers the right-hand sliders panel while in dust mode.

Two sections sharing one non-destructive model (spots stored on the image):
  - Manual: a sized brush; paint over dust on the canvas to inpaint it.
  - AI: an ONNX detector (downloaded on first use) finds dust automatically;
    the same cv2.inpaint fills it.

All ONNX work happens off the GUI thread; the panel imports `dust_detect`
(which never imports onnxruntime at module level), so this widget is safe to
build even when onnxruntime is absent — the AI section then shows an
unavailable/needs-download state and the manual brush is unaffected.
See spec/dust-removal.md.
"""
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                                QPushButton, QSlider, QFrame, QProgressBar)
from PySide6.QtCore import Qt, QObject, QThread, Signal

from core import dust_detect


class _DetectWorker(QObject):
    done = Signal(object)   # probability map (np.ndarray)
    failed = Signal(str)

    def __init__(self, source):
        super().__init__()
        self.source = source

    def run(self):
        try:
            prob = dust_detect.detect(self.source)
            self.done.emit(prob)
        except Exception as e:  # noqa: BLE001 — surfaced to the user
            self.failed.emit(str(e))


class _DownloadWorker(QObject):
    progress = Signal(int, int)   # done_bytes, total_bytes
    done = Signal()
    failed = Signal(str)

    def __init__(self):
        super().__init__()
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            dust_detect.download_model(
                progress_cb=lambda d, t: self.progress.emit(d, t),
                should_cancel=lambda: self._cancel)
            self.done.emit()
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


class DustRemovalPanel(QWidget):
    def __init__(self, main_window, image_preview, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.image_preview = image_preview
        self._downloading = False
        self._detecting = False
        self._download_thread = None
        self._download_worker = None
        self._detect_thread = None
        self._detect_worker = None
        # Cached detector probability map for the current image so the
        # Sensitivity slider re-thresholds without re-running the net.
        self._prob = None
        self._prob_image_ref = None
        # The image a running detection was started on — results are discarded
        # if the user switches images before it finishes.
        self._detect_image_ref = None

        self._build_ui()
        self._refresh_ai_section()

    # --- UI ---------------------------------------------------------------
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        header = QLabel("Dust Removal")
        header.setAlignment(Qt.AlignCenter)
        header.setStyleSheet("font-size: 14px; font-weight: bold; margin: 4px;")
        layout.addWidget(header)

        # --- Manual section ---
        layout.addWidget(self._section_label("Manual"))
        brush_row = QHBoxLayout()
        brush_lbl = QLabel("Brush size")
        brush_lbl.setMinimumWidth(70)
        self.brush_slider = QSlider(Qt.Horizontal)
        self.brush_slider.setMinimum(2)     # r = value/1000  ->  0.002 .. 0.200
        self.brush_slider.setMaximum(200)
        self.brush_slider.setValue(12)      # 0.012 (1.2% of image width)
        self.brush_value = QLabel("1.2%")
        self.brush_value.setMinimumWidth(40)
        self.brush_slider.valueChanged.connect(self._on_brush_changed)
        brush_row.addWidget(brush_lbl)
        brush_row.addWidget(self.brush_slider)
        brush_row.addWidget(self.brush_value)
        layout.addLayout(brush_row)

        hint = QLabel("Click or drag over dust to remove it.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(hint)

        manual_btns = QHBoxLayout()
        self.undo_btn = QPushButton("Undo last spot")
        self.undo_btn.clicked.connect(self._on_undo_last)
        self.clear_btn = QPushButton("Clear all")
        self.clear_btn.clicked.connect(self._on_clear_all)
        manual_btns.addWidget(self.undo_btn)
        manual_btns.addWidget(self.clear_btn)
        layout.addLayout(manual_btns)

        layout.addWidget(self._separator())

        # --- AI section ---
        layout.addWidget(self._section_label("AI Dust Detection"))

        self.ai_unavailable_label = QLabel(
            "AI detection unavailable in this build.")
        self.ai_unavailable_label.setWordWrap(True)
        self.ai_unavailable_label.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(self.ai_unavailable_label)

        self.download_label = QLabel(
            "Download the local AI model (~150 MB) to detect dust "
            "automatically. One-time download.")
        self.download_label.setWordWrap(True)
        self.download_label.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(self.download_label)
        self.download_btn = QPushButton("Download AI model (~150 MB)")
        self.download_btn.clicked.connect(self._on_download)
        layout.addWidget(self.download_btn)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        layout.addWidget(self.progress_bar)

        sens_row = QHBoxLayout()
        self.sensitivity_label = QLabel("Sensitivity")
        self.sensitivity_label.setMinimumWidth(70)
        self.sensitivity_slider = QSlider(Qt.Horizontal)
        self.sensitivity_slider.setMinimum(0)
        self.sensitivity_slider.setMaximum(100)
        self.sensitivity_slider.setValue(50)
        self.sensitivity_value = QLabel("50")
        self.sensitivity_value.setMinimumWidth(40)
        self.sensitivity_slider.valueChanged.connect(self._on_sensitivity_changed)
        sens_row.addWidget(self.sensitivity_label)
        sens_row.addWidget(self.sensitivity_slider)
        sens_row.addWidget(self.sensitivity_value)
        self._sens_row = sens_row
        layout.addLayout(sens_row)

        self.detect_btn = QPushButton("Detect && Remove")
        self.detect_btn.clicked.connect(self._on_detect)
        layout.addWidget(self.detect_btn)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(self.status_label)

        layout.addStretch(1)

        self.done_btn = QPushButton("Done")
        self.done_btn.clicked.connect(self._on_done)
        layout.addWidget(self.done_btn)

    @staticmethod
    def _section_label(text):
        lbl = QLabel(text)
        lbl.setStyleSheet("color: #aaa; font-size: 11px; font-weight: bold; "
                          "margin-top: 4px;")
        return lbl

    @staticmethod
    def _separator():
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        sep.setStyleSheet("margin-top: 6px; margin-bottom: 2px;")
        return sep

    # --- Public API used by MainWindow / ImagePreview ---------------------
    def bind_image(self):
        """Refresh per-image state when the panel is shown. The detector prob
        cache is per image, so invalidate it on a different image."""
        img = self._current_image()
        if img is not self._prob_image_ref:
            self._prob = None
            self._prob_image_ref = None
        # Push the current brush size to the canvas so they agree on entry.
        self.image_preview.set_dust_brush_size(self.brush_slider.value() / 1000.0)
        self._refresh_ai_section()
        self.status_label.setText("")

    def sync_brush_size(self, r_norm: float):
        """Reflect a wheel-driven brush change from the canvas without
        re-emitting back to the canvas."""
        v = max(2, min(200, int(round(r_norm * 1000))))
        self.brush_slider.blockSignals(True)
        self.brush_slider.setValue(v)
        self.brush_slider.blockSignals(False)
        self.brush_value.setText(f"{v / 10.0:.1f}%")

    # --- Manual handlers --------------------------------------------------
    def _on_brush_changed(self, value):
        self.brush_value.setText(f"{value / 10.0:.1f}%")
        self.image_preview.set_dust_brush_size(value / 1000.0)

    def _on_undo_last(self):
        if not self.image_preview.dust_undo_last():
            self.status_label.setText("Nothing to undo.")

    def _on_clear_all(self):
        if self.image_preview.dust_clear_all():
            self._prob = None  # spots gone; a re-detect should start fresh
            self.status_label.setText("Cleared all dust spots.")
        else:
            self.status_label.setText("No dust spots to clear.")

    def _on_done(self):
        self.main_window.toggle_dust_removal(False)

    # --- AI handlers ------------------------------------------------------
    def _on_sensitivity_changed(self, value):
        self.sensitivity_value.setText(str(value))
        # Cheap re-threshold of the cached prob map (no net re-run).
        if self._prob is not None and self._current_image() is self._prob_image_ref:
            spots = dust_detect.prob_to_spots(self._prob, float(value))
            n = self.image_preview.apply_detected_spots(spots)
            self.status_label.setText(
                f"Removed {n} spot{'s' if n != 1 else ''}." if n
                else "No dust found at this sensitivity.")

    def _on_detect(self):
        if self._detecting or self._downloading:
            return
        source = self.image_preview.dust_detect_source()
        if source is None:
            self.status_label.setText("No image to detect on.")
            return
        self._detecting = True
        self._detect_image_ref = self._current_image()
        self.detect_btn.setEnabled(False)
        self.detect_btn.setText("Detecting…")
        self.status_label.setText("Running AI detection…")
        self._detect_thread = QThread()
        self._detect_worker = _DetectWorker(source)
        self._detect_worker.moveToThread(self._detect_thread)
        self._detect_thread.started.connect(self._detect_worker.run)
        self._detect_worker.done.connect(self._on_detect_done)
        self._detect_worker.failed.connect(self._on_detect_failed)
        self._detect_worker.done.connect(self._detect_thread.quit)
        self._detect_worker.failed.connect(self._detect_thread.quit)
        self._detect_worker.done.connect(self._detect_worker.deleteLater)
        self._detect_worker.failed.connect(self._detect_worker.deleteLater)
        self._detect_thread.finished.connect(self._clear_detect_thread)
        self._detect_thread.start()

    def _on_detect_done(self, prob):
        # Discard results if the user navigated to a different image (or out of
        # dust mode) while detection was running — the prob map is for the old
        # image and must not be applied to the current one.
        if (self._current_image() is not self._detect_image_ref
                or not self.image_preview.dust_mode):
            self._finish_detect()
            return
        self._prob = prob
        self._prob_image_ref = self._current_image()
        spots = dust_detect.prob_to_spots(
            prob, float(self.sensitivity_slider.value()))
        n = self.image_preview.apply_detected_spots(spots)
        self.status_label.setText(
            f"Removed {n} spot{'s' if n != 1 else ''}." if n
            else "No dust found.")
        self._finish_detect()

    def _on_detect_failed(self, msg):
        self.status_label.setText(f"AI detection failed: {msg}")
        self._finish_detect()

    def _finish_detect(self):
        self._detecting = False
        self.detect_btn.setEnabled(True)
        self.detect_btn.setText("Detect && Remove")

    def _clear_detect_thread(self):
        self._detect_thread = None
        self._detect_worker = None

    def _on_download(self):
        if self._downloading:
            return
        self._downloading = True
        self.progress_bar.setValue(0)
        self.status_label.setText("Downloading AI model…")
        self._refresh_ai_section()
        self._download_thread = QThread()
        self._download_worker = _DownloadWorker()
        self._download_worker.moveToThread(self._download_thread)
        self._download_thread.started.connect(self._download_worker.run)
        self._download_worker.progress.connect(self._on_download_progress)
        self._download_worker.done.connect(self._on_download_done)
        self._download_worker.failed.connect(self._on_download_failed)
        self._download_worker.done.connect(self._download_thread.quit)
        self._download_worker.failed.connect(self._download_thread.quit)
        self._download_worker.done.connect(self._download_worker.deleteLater)
        self._download_worker.failed.connect(self._download_worker.deleteLater)
        self._download_thread.finished.connect(self._clear_download_thread)
        self._download_thread.start()

    def _on_download_progress(self, done, total):
        if total > 0:
            self.progress_bar.setValue(int(done * 100 / total))

    def _on_download_done(self):
        self._downloading = False
        self.status_label.setText("AI model ready.")
        self._refresh_ai_section()

    def _on_download_failed(self, msg):
        self._downloading = False
        self.status_label.setText(f"Download failed: {msg}")
        self._refresh_ai_section()

    def _clear_download_thread(self):
        self._download_thread = None
        self._download_worker = None

    # --- helpers ----------------------------------------------------------
    def _refresh_ai_section(self):
        avail = dust_detect.is_available()
        present = avail and dust_detect.is_model_present()
        need_dl = avail and not present and not self._downloading

        self.ai_unavailable_label.setVisible(not avail)
        self.download_label.setVisible(need_dl)
        self.download_btn.setVisible(need_dl)
        self.progress_bar.setVisible(self._downloading)
        self.sensitivity_label.setVisible(present)
        self.sensitivity_slider.setVisible(present)
        self.sensitivity_value.setVisible(present)
        self.detect_btn.setVisible(present)

    def _current_image(self):
        from core.ccr_backend import ccr_backend
        idx = self.image_preview.current_idx
        return ccr_backend.get_image_by_index(idx) if idx is not None else None

    def cancel_jobs(self):
        """Cancel an in-flight model download when leaving dust mode (Done/Esc).
        The download worker honours cancel(); the (short) ONNX detection has no
        interrupt, but its result is already discarded if the image changed."""
        wkr = self._download_worker
        if wkr is not None:
            try:
                wkr.cancel()
            except Exception:
                pass

    def shutdown(self):
        """Stop any in-flight download/detection thread (called on app close).
        Mirrors MainWindow._stop_loader_if_running: cancel, quit, wait, then
        drop the refs so isRunning() is never called on a freed C++ object."""
        if self._download_worker is not None:
            try:
                self._download_worker.cancel()
            except Exception:
                pass
        for attr_thr, attr_wkr in (("_download_thread", "_download_worker"),
                                   ("_detect_thread", "_detect_worker")):
            thr = getattr(self, attr_thr, None)
            if thr is not None:
                try:
                    if thr.isRunning():
                        thr.quit()
                        thr.wait(3000)
                except RuntimeError:
                    pass
            setattr(self, attr_thr, None)
            setattr(self, attr_wkr, None)
