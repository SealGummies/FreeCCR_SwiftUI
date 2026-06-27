"""
DaVinci-style Settings dialog — a left category sidebar + a right content pane +
a footer. The first category, **Color Management**, consolidates the input camera
profile (ICC / DCP), the IT8 camera-profile wizard, and the global Positive-mode
toggle that used to live in the File menu / thumbnail panel.

UI only: it reuses MainWindow's existing colour handlers (file pickers, backend
apply, re-decode) — settings apply immediately on click. See spec/settings-page.md.
"""
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QDialog, QGroupBox, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QPushButton, QScrollArea, QStackedWidget,
    QVBoxLayout, QWidget,
)

from core import color_management
from core.ccr_backend import ccr_backend
from ui import theme


class SettingsDialog(QDialog):
    """Modal settings dialog. Categories on the left, the selected page on the
    right, a single Done button in the footer (settings apply immediately, so
    there is no staged Save/Cancel)."""

    def __init__(self, main_window, parent=None):
        super().__init__(parent or main_window)
        self._mw = main_window
        self.setWindowTitle("Settings")
        self.setModal(True)

        scr = self.screen() or QApplication.primaryScreen()
        avail = scr.availableGeometry() if scr is not None else None
        w, h = 760, 540
        if avail is not None:
            w = min(w, avail.width() - 60)
            h = min(h, avail.height() - 80)
        self.resize(w, h)
        self.setMinimumSize(min(560, w), min(420, h))

        root = QVBoxLayout(self)
        theme.apply_panel_spacing(root)

        body = QHBoxLayout()
        body.setSpacing(theme.GAP_PANEL)
        self._sidebar = QListWidget()
        self._sidebar.setFixedWidth(170)
        self._sidebar.currentRowChanged.connect(self._stack_set)
        body.addWidget(self._sidebar)
        self._stack = QStackedWidget()
        body.addWidget(self._stack, 1)
        root.addLayout(body, 1)

        self._add_category("Color Management", self._build_color_management_page())

        root.addWidget(theme.section_separator())
        footer = QHBoxLayout()
        theme.apply_button_row(footer)
        footer.addStretch(1)
        done = QPushButton("Done")
        theme.style_button(done, "primary", default=True)
        done.clicked.connect(self.accept)
        footer.addWidget(done)
        root.addLayout(footer)

        self._sidebar.setCurrentRow(0)
        theme.apply_windows_dark_titlebar(self)
        self.refresh_color_management()

    def _stack_set(self, i):
        if i >= 0:
            self._stack.setCurrentIndex(i)

    def _add_category(self, name: str, page: QWidget):
        QListWidgetItem(name, self._sidebar)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        self._stack.addWidget(scroll)

    # ------------------------------------------------------------------ #
    # Color Management page
    # ------------------------------------------------------------------ #
    def _muted(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        return lbl

    def _build_color_management_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        theme.apply_panel_spacing(lay, spacing=theme.GAP_SECTION)

        # --- Camera-profile library ------------------------------------ #
        grp = QGroupBox("Camera profiles")
        g = QVBoxLayout(grp)
        g.setSpacing(theme.GAP_ROW)
        g.addWidget(self._muted(
            "Your imported and IT8-generated profiles, kept in FreeCCR's workspace "
            "(they survive updates). Pick the active one from the “Camera profile” "
            "dropdown above the thumbnails; manage which to keep here."))

        self._status = QLabel("Active: None")
        self._status.setStyleSheet(theme.section_header_qss())
        g.addWidget(self._status)

        self._profile_list = QListWidget()
        self._profile_list.setMinimumHeight(150)
        g.addWidget(self._profile_list)

        row = QHBoxLayout()
        theme.apply_button_row(row)
        btn_import = QPushButton("Import profile…")
        btn_import.clicked.connect(self._import)
        self._btn_delete = QPushButton("Delete")
        theme.style_button(self._btn_delete, "danger")
        self._btn_delete.clicked.connect(self._delete)
        row.addWidget(btn_import)
        row.addWidget(self._btn_delete)
        row.addStretch(1)
        g.addLayout(row)

        g.addWidget(theme.section_separator())
        it8_row = QHBoxLayout()
        theme.apply_button_row(it8_row)
        btn_it8 = QPushButton("Create Camera Profile from IT8…")
        btn_it8.clicked.connect(self._create_it8)
        it8_row.addWidget(btn_it8)
        it8_row.addStretch(1)
        g.addLayout(it8_row)
        g.addWidget(self._muted(
            "Build a camera ICC or DCP from a photographed IT8 calibration chart; "
            "it is added to the library above."))
        lay.addWidget(grp)

        # --- Negative conversion --------------------------------------- #
        grp2 = QGroupBox("Negative conversion")
        g2 = QVBoxLayout(grp2)
        g2.setSpacing(theme.GAP_ROW)
        self._cb_positive = QCheckBox(
            "Positive mode (decode RAWs as positives, skip film inversion)")
        self._cb_positive.toggled.connect(self._on_positive)
        g2.addWidget(self._cb_positive)
        g2.addWidget(self._muted(
            "Treat RAWs as ready positive photos instead of film negatives. The "
            "same toggle is on the thumbnail panel."))
        lay.addWidget(grp2)

        # --- Trichrome (3-way RGB-light) capture ----------------------- #
        grp3 = QGroupBox("Trichrome capture")
        g3 = QVBoxLayout(grp3)
        g3.setSpacing(theme.GAP_ROW)
        self._cb_rgb_merge = QCheckBox(
            "3-way RGB merge (combine red/green/blue-light exposures)")
        self._cb_rgb_merge.toggled.connect(self._on_rgb_merge)
        g3.addWidget(self._cb_rgb_merge)
        g3.addWidget(self._muted(
            "Shoot a static scene three times under pure red, then green, then "
            "blue light. On your NEXT import, every 3 RAWs (sorted by filename) "
            "are merged into one colour image — each frame contributes only its "
            "own channel, with no demosaicing — then converted as a negative. "
            "RAW (Bayer) only; the selected count must be a multiple of 3. "
            "Applies to the next import only; merged-image edits are not saved "
            "between sessions."))
        lay.addWidget(grp3)

        lay.addStretch(1)
        return page

    # ------------------------------------------------------------------ #
    # Actions — delegate to MainWindow (file pickers, backend, re-decode);
    # everything applies immediately, then the page refreshes its status.
    # ------------------------------------------------------------------ #
    def _import(self):
        self._mw.import_camera_profile_dialog()
        self.refresh_color_management()

    def _delete(self):
        item = self._profile_list.currentItem()
        if item is None:
            return
        self._mw.delete_camera_profile(item.data(Qt.UserRole))
        self.refresh_color_management()

    def _create_it8(self):
        self._mw.create_camera_profile_from_it8()
        self.refresh_color_management()

    def _on_positive(self, checked: bool):
        if bool(checked) == bool(ccr_backend.positive_mode):
            return                                   # programmatic sync, not a user toggle
        self._mw.on_positive_mode_toggled(bool(checked))

    def _on_rgb_merge(self, checked: bool):
        if bool(checked) == bool(ccr_backend.rgb_merge_mode):
            return                                   # programmatic sync, not a user toggle
        self._mw.on_rgb_merge_mode_toggled(bool(checked))

    def refresh_color_management(self):
        """Reflect the live active profile, the library list, and Positive mode."""
        icc = getattr(ccr_backend, "input_icc_name", None)
        dcp = getattr(ccr_backend, "input_dcp_name", None)
        if getattr(ccr_backend, "active_profile_path", None) == ccr_backend.CAMERA_MATRIX:
            self._status.setText("Active: Camera Matrix  (Adobe RGB)")
        elif dcp:
            self._status.setText(f"Active: {dcp}  (DCP)")
        elif icc:
            self._status.setText(f"Active: {icc}  (ICC)")
        else:
            self._status.setText("Active: None  (raw)")

        active = getattr(ccr_backend, "active_profile_path", None)
        active_n = os.path.normcase(os.path.abspath(active)) if active else None
        self._profile_list.clear()
        for p in ccr_backend.list_camera_profiles():
            label = f"{p['name']}  ({p['kind'].upper()})"
            if active_n and os.path.normcase(os.path.abspath(p["path"])) == active_n:
                label += "   ● active"
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, p["path"])
            self._profile_list.addItem(it)
        self._btn_delete.setEnabled(self._profile_list.count() > 0)

        self._cb_positive.blockSignals(True)
        self._cb_positive.setChecked(bool(ccr_backend.positive_mode))
        self._cb_positive.blockSignals(False)

        self._cb_rgb_merge.blockSignals(True)
        self._cb_rgb_merge.setChecked(bool(ccr_backend.rgb_merge_mode))
        self._cb_rgb_merge.blockSignals(False)
