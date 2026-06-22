"""
Export settings dialog: scope, destination, filename template with macros,
format + JPEG quality with live size estimate, resize-on-export, conflict
policy and persisted preferences.
"""
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple

from PySide6.QtCore import Qt, QSettings, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QRadioButton,
    QSlider, QVBoxLayout,
)

from core.ccr_backend import ccr_backend
from core import export_estimator
from ui import theme
from utils.export_naming import build_export_names, build_filename, resolve_conflicts
from utils.unicode_path_utils import normalize_unicode_path, validate_unicode_path


@dataclass
class ExportPlan:
    """Everything the export worker needs, resolved by the dialog."""
    items: List[Tuple[int, str]] = field(default_factory=list)  # (image idx, absolute output path)
    jpg_output: bool = False
    jpg_quality: int = 92
    max_long_side: Optional[int] = None  # None = keep original size
    output_colorspace: str = "srgb"  # "srgb" | "prophoto"
    open_folder: bool = False
    destination: str = ""
    skipped: int = 0  # files dropped by the "skip existing" policy


class ExportSettingsDialog(QDialog):
    """
    Modal export configuration dialog. On accept, `self.plan` holds the
    resolved ExportPlan; the caller runs the actual export worker.
    """

    def __init__(self, parent=None, current_idx=None, selected_indices=None,
                 default_scope=None):
        super().__init__(parent)
        self.setWindowTitle("Export")
        self.setModal(True)
        self.setMinimumWidth(440)
        theme.apply_windows_dark_titlebar(self)  # dark native title bar (Win10/11)

        self.plan: Optional[ExportPlan] = None
        self._current_idx = current_idx
        self._default_scope = default_scope
        # In Positive mode every loaded image is exportable (no conversion step);
        # otherwise only converted negatives are. (Variable names kept for churn,
        # but they now mean "exportable".)
        self._positive_mode = bool(getattr(ccr_backend, "positive_mode", False))

        def _exportable(img):
            return img.converted or self._positive_mode

        self._converted_indices = [idx for idx, img in enumerate(ccr_backend.images)
                                   if _exportable(img)]
        self._current_converted = (
            current_idx is not None
            and 0 <= current_idx < len(ccr_backend.images)
            and _exportable(ccr_backend.images[current_idx])
        )
        # Selected thumbnails that are exportable — non-exportable selections
        # are dropped here.
        converted_set = set(self._converted_indices)
        self._selected_converted = [i for i in (selected_indices or []) if i in converted_set]
        self._bpp_cache = {}  # quality -> bytes per pixel sample

        self._settings = QSettings("FreeCCR", "FreeCCR")
        self._build_ui()
        self._restore_settings()
        self._update_example()
        self._schedule_estimate()

    # ---------- UI ----------

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Scope
        scope_group = QGroupBox("Export scope")
        scope_layout = QVBoxLayout(scope_group)
        all_label = "All images" if self._positive_mode else "All converted images"
        self.scope_all_radio = QRadioButton(f"{all_label} ({len(self._converted_indices)})")
        self.scope_all_radio.setChecked(True)
        self.scope_current_radio = QRadioButton("Current image only")
        if not self._current_converted:
            self.scope_current_radio.setEnabled(False)
            self.scope_current_radio.setToolTip("The current image has not been converted yet.")
        self.scope_selected_radio = QRadioButton(f"Selected images ({len(self._selected_converted)})")
        if not self._selected_converted:
            self.scope_selected_radio.setEnabled(False)
            self.scope_selected_radio.setToolTip("No converted images are selected.")
        scope_layout.addWidget(self.scope_all_radio)
        scope_layout.addWidget(self.scope_current_radio)
        scope_layout.addWidget(self.scope_selected_radio)
        # Connect every radio: switching between current and selected does not
        # toggle the "all" radio, so listening to "all" alone misses changes.
        self.scope_all_radio.toggled.connect(self._on_scope_changed)
        self.scope_current_radio.toggled.connect(self._on_scope_changed)
        self.scope_selected_radio.toggled.connect(self._on_scope_changed)
        layout.addWidget(scope_group)

        # Destination
        dest_layout = QHBoxLayout()
        dest_layout.addWidget(QLabel("Destination:"))
        self.dest_edit = QLineEdit()
        self.dest_edit.textChanged.connect(self._on_dest_changed)
        dest_layout.addWidget(self.dest_edit, 1)
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._on_browse)
        dest_layout.addWidget(browse_btn)
        layout.addLayout(dest_layout)

        # Naming
        naming_group = QGroupBox("File naming")
        naming_layout = QVBoxLayout(naming_group)
        fields_layout = QHBoxLayout()
        fields_layout.addWidget(QLabel("Filename"))
        self.filename_edit = QLineEdit("{name}_ccr")
        fields_layout.addWidget(self.filename_edit, 1)
        naming_layout.addLayout(fields_layout)
        hint = QLabel("Macros: {name} = original filename, {date}, {time}, {seq}")
        hint.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        naming_layout.addWidget(hint)
        self.example_label = QLabel("")
        naming_layout.addWidget(self.example_label)
        self.filename_edit.textChanged.connect(self._update_example)
        layout.addWidget(naming_group)

        # Format / quality / estimate / resize / conflicts
        form = QFormLayout()
        self.format_combo = QComboBox()
        self.format_combo.addItem("TIFF 16-bit (lossless)", "tiff")
        self.format_combo.addItem("JPEG", "jpeg")
        self.format_combo.currentIndexChanged.connect(self._on_format_changed)
        form.addRow("Format:", self.format_combo)

        # Output color space. sRGB is the current behaviour (now ICC-tagged);
        # ProPhoto re-encodes the result into the wide-gamut space and embeds
        # the matching ICC profile.
        self.colorspace_combo = QComboBox()
        self.colorspace_combo.addItem("sRGB", "srgb")
        self.colorspace_combo.addItem("ProPhoto RGB (wide gamut)", "prophoto")
        self.colorspace_combo.currentIndexChanged.connect(self._on_colorspace_changed)
        form.addRow("Color space:", self.colorspace_combo)
        self.colorspace_hint = QLabel(
            "8-bit ProPhoto JPEG can band — prefer 16-bit TIFF for wide gamut.")
        self.colorspace_hint.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        self.colorspace_hint.setWordWrap(True)
        form.addRow("", self.colorspace_hint)

        quality_layout = QHBoxLayout()
        self.quality_slider = QSlider(Qt.Horizontal)
        self.quality_slider.setRange(1, 100)
        self.quality_slider.setValue(92)
        self.quality_value_label = QLabel("92")
        self.quality_value_label.setMinimumWidth(28)
        quality_layout.addWidget(self.quality_slider, 1)
        quality_layout.addWidget(self.quality_value_label)
        self.quality_slider.valueChanged.connect(self._on_quality_changed)
        self.quality_row_label = QLabel("Quality:")
        form.addRow(self.quality_row_label, quality_layout)
        self._quality_layout = quality_layout

        self.estimate_label = QLabel("Estimated total size: …")
        form.addRow("", self.estimate_label)

        self.resize_combo = QComboBox()
        self.resize_combo.addItem("Original size", 0)
        self.resize_combo.addItem("4000 px long edge", 4000)
        self.resize_combo.addItem("2000 px long edge", 2000)
        self.resize_combo.addItem("1080 px long edge", 1080)
        self.resize_combo.currentIndexChanged.connect(self._schedule_estimate)
        form.addRow("Resize:", self.resize_combo)

        self.conflict_combo = QComboBox()
        self.conflict_combo.addItem("Auto-rename (-1, -2…)", "rename")
        self.conflict_combo.addItem("Overwrite", "overwrite")
        self.conflict_combo.addItem("Skip existing", "skip")
        form.addRow("If file exists:", self.conflict_combo)
        layout.addLayout(form)

        self.open_folder_checkbox = QCheckBox("Open folder when done")
        layout.addWidget(self.open_folder_checkbox)

        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        self.export_button = QPushButton("Export")
        self.export_button.setDefault(True)
        self.export_button.clicked.connect(self._on_export_clicked)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        button_layout.addWidget(self.export_button)
        button_layout.addWidget(cancel_button)
        layout.addLayout(button_layout)

        # Debounced size estimation
        self._estimate_timer = QTimer(self)
        self._estimate_timer.setSingleShot(True)
        self._estimate_timer.setInterval(300)
        self._estimate_timer.timeout.connect(self._update_estimate)

        self._update_quality_visibility()
        self._update_colorspace_hint()

    # ---------- settings ----------

    def _restore_settings(self):
        s = self._settings
        destination = s.value("export/destination", "", type=str)
        if not destination:
            scoped = self._scope_indices()
            if scoped:
                destination = os.path.dirname(ccr_backend.images[scoped[0]].file_path)
        self.dest_edit.setText(destination)

        # An explicit default_scope (e.g. from the thumbnail right-click menu)
        # wins over the persisted choice; both fall back to "all" when the
        # requested scope has nothing to export.
        scope = self._default_scope or s.value("export/scope", "all", type=str)
        if scope == "current" and self._current_converted:
            self.scope_current_radio.setChecked(True)
        elif scope == "selected" and self._selected_converted:
            self.scope_selected_radio.setChecked(True)
        self.filename_edit.setText(s.value("export/filename_template", "{name}_ccr", type=str))
        fmt_index = self.format_combo.findData(s.value("export/format", "tiff", type=str))
        if fmt_index >= 0:
            self.format_combo.setCurrentIndex(fmt_index)
        cs_index = self.colorspace_combo.findData(s.value("export/colorspace", "srgb", type=str))
        if cs_index >= 0:
            self.colorspace_combo.setCurrentIndex(cs_index)
        self.quality_slider.setValue(s.value("export/jpeg_quality", 92, type=int))
        resize_index = self.resize_combo.findData(s.value("export/resize", 0, type=int))
        if resize_index >= 0:
            self.resize_combo.setCurrentIndex(resize_index)
        conflict_index = self.conflict_combo.findData(s.value("export/conflict", "rename", type=str))
        if conflict_index >= 0:
            self.conflict_combo.setCurrentIndex(conflict_index)
        self.open_folder_checkbox.setChecked(s.value("export/open_folder", False, type=bool))
        self._update_quality_visibility()

    def _save_settings(self):
        s = self._settings
        s.setValue("export/destination", self.dest_edit.text().strip())
        if self.scope_current_radio.isChecked():
            scope = "current"
        elif self.scope_selected_radio.isChecked():
            scope = "selected"
        else:
            scope = "all"
        s.setValue("export/scope", scope)
        s.setValue("export/filename_template", self.filename_edit.text())
        s.setValue("export/format", self.format_combo.currentData())
        s.setValue("export/colorspace", self.colorspace_combo.currentData())
        s.setValue("export/jpeg_quality", self.quality_slider.value())
        s.setValue("export/resize", self.resize_combo.currentData())
        s.setValue("export/conflict", self.conflict_combo.currentData())
        s.setValue("export/open_folder", self.open_folder_checkbox.isChecked())

    # ---------- helpers ----------

    def _scope_indices(self) -> List[int]:
        if self.scope_current_radio.isChecked() and self._current_converted:
            return [self._current_idx]
        if self.scope_selected_radio.isChecked() and self._selected_converted:
            return list(self._selected_converted)
        return list(self._converted_indices)

    def _base_names(self, indices) -> List[str]:
        names = []
        for i in indices:
            img = ccr_backend.images[i]
            # Sliced images carry a distinguishing display name (e.g. _s2)
            base = getattr(img, "display_name", None) or os.path.basename(img.file_path)
            names.append(os.path.splitext(base)[0])
        return names

    def _selected_ext(self) -> str:
        return ".jpg" if self.format_combo.currentData() == "jpeg" else ".tiff"

    def _selected_max_long_side(self) -> Optional[int]:
        value = self.resize_combo.currentData()
        return value if value else None

    def _sample_image(self):
        """Image whose converted preview seeds the JPEG bytes-per-pixel sample."""
        indices = self._scope_indices()
        if self._current_converted and self._current_idx in indices:
            return ccr_backend.images[self._current_idx]
        return ccr_backend.images[indices[0]] if indices else None

    # ---------- slots ----------

    def _on_browse(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Select Export Folder", self.dest_edit.text(),
            QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks
        )
        if folder:
            self.dest_edit.setText(folder)

    def _on_dest_changed(self):
        self.export_button.setEnabled(bool(self.dest_edit.text().strip()))

    def _on_scope_changed(self):
        self._update_example()
        self._schedule_estimate()

    def _on_format_changed(self):
        self._update_quality_visibility()
        self._update_colorspace_hint()
        self._update_example()
        self._schedule_estimate()

    def _on_colorspace_changed(self):
        self._update_colorspace_hint()

    def _update_colorspace_hint(self):
        # The banding caution is only relevant for 8-bit ProPhoto JPEG.
        is_prophoto = self.colorspace_combo.currentData() == "prophoto"
        is_jpeg = self.format_combo.currentData() == "jpeg"
        self.colorspace_hint.setVisible(is_prophoto and is_jpeg)

    def _on_quality_changed(self, value):
        self.quality_value_label.setText(str(value))
        self._schedule_estimate()

    def _update_quality_visibility(self):
        is_jpeg = self.format_combo.currentData() == "jpeg"
        self.quality_row_label.setVisible(is_jpeg)
        for i in range(self._quality_layout.count()):
            widget = self._quality_layout.itemAt(i).widget()
            if widget is not None:
                widget.setVisible(is_jpeg)

    def _update_example(self):
        indices = self._scope_indices()
        if not indices:
            self.example_label.setText("Example: —")
            return
        base = self._base_names(indices[:1])[0]
        seq_width = max(3, len(str(len(indices))))
        name = build_filename(base, self.filename_edit.text(), self._selected_ext(),
                              seq=1, seq_width=seq_width, now=datetime.now())
        self.example_label.setText(f"Example: {name}")

    # ---------- size estimate ----------

    def _schedule_estimate(self, *args):
        self._estimate_timer.start()

    def _update_estimate(self):
        indices = self._scope_indices()
        if not indices:
            self.estimate_label.setText("Estimated total size: —")
            return
        fmt = self.format_combo.currentData()
        max_long_side = self._selected_max_long_side()
        bpp = None
        if fmt == "jpeg":
            quality = self.quality_slider.value()
            bpp = self._bpp_cache.get(quality)
            if bpp is None:
                sample = self._sample_image()
                if sample is not None and sample.resized_raw is not None:
                    bpp = export_estimator.jpeg_bytes_per_pixel(sample.resized_raw, quality)
                    if bpp is not None:
                        self._bpp_cache[quality] = bpp
            if bpp is None:
                self.estimate_label.setText("Estimated total size: —")
                return
        dims_list = [export_estimator.get_original_dims(ccr_backend.images[i]) for i in indices]
        total = export_estimator.estimate_total_bytes(dims_list, fmt, bpp=bpp,
                                                      max_long_side=max_long_side)
        count = len(indices)
        self.estimate_label.setText(
            f"Estimated total size: {export_estimator.format_size(total)}"
            f"  ({count} file{'s' if count != 1 else ''})"
        )

    # ---------- accept ----------

    def _on_export_clicked(self):
        destination = self.dest_edit.text().strip()
        if not destination:
            return
        destination = normalize_unicode_path(destination)
        try:
            os.makedirs(destination, exist_ok=True)
        except OSError as e:
            QMessageBox.warning(self, "Export Folder Error",
                                f"Cannot create the export folder:\n\n{destination}\n\n{e}")
            return
        if not validate_unicode_path(destination):
            QMessageBox.warning(
                self, "Unicode Path Warning",
                f"The export folder path contains characters that may cause issues:\n\n"
                f"{destination}\n\nPlease choose a simpler folder path."
            )
            return

        indices = self._scope_indices()
        if not indices:
            QMessageBox.information(self, "Nothing to Export",
                                    "There are no converted images to export.")
            return

        names = build_export_names(self._base_names(indices), self.filename_edit.text(),
                                   self._selected_ext())
        full_paths = [os.path.join(destination, name) for name in names]
        resolved = resolve_conflicts(full_paths, self.conflict_combo.currentData(),
                                     exists=os.path.exists)

        items = [(idx, path) for idx, path in zip(indices, resolved) if path is not None]
        skipped = len(resolved) - len(items)
        if not items:
            QMessageBox.information(
                self, "Nothing to Export",
                "All files already exist and were skipped.\n"
                "Change the conflict policy or the file naming to export them."
            )
            return

        self.plan = ExportPlan(
            items=items,
            jpg_output=self.format_combo.currentData() == "jpeg",
            jpg_quality=self.quality_slider.value(),
            max_long_side=self._selected_max_long_side(),
            output_colorspace=self.colorspace_combo.currentData(),
            open_folder=self.open_folder_checkbox.isChecked(),
            destination=destination,
            skipped=skipped,
        )
        self._save_settings()
        self.accept()
