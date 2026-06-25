"""
DaVinci-style Settings dialog — a left category sidebar + a right content pane +
a footer. The first category, **Color Management**, consolidates the input camera
profile (ICC / DCP), the IT8 camera-profile wizard, and the global Positive-mode
toggle that used to live in the File menu / thumbnail panel.

UI only: it reuses MainWindow's existing colour handlers (file pickers, backend
apply, re-decode) — settings apply immediately on click. See spec/settings-page.md.
"""
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

        # --- Input camera profile -------------------------------------- #
        grp = QGroupBox("Input camera profile")
        g = QVBoxLayout(grp)
        g.setSpacing(theme.GAP_ROW)
        g.addWidget(self._muted(
            "A single global camera profile applied to every scan at decode time, "
            "before negative conversion. ICC and DCP are mutually exclusive."))

        self._status = QLabel("Active: None")
        self._status.setStyleSheet(theme.section_header_qss())
        g.addWidget(self._status)

        row = QHBoxLayout()
        theme.apply_button_row(row)
        btn_icc = QPushButton("Set ICC profile…")
        btn_icc.clicked.connect(self._set_icc)
        btn_dcp = QPushButton("Load DCP profile…")
        btn_dcp.clicked.connect(self._set_dcp)
        self._btn_clear = QPushButton("Clear")
        theme.style_button(self._btn_clear, "danger")
        self._btn_clear.clicked.connect(self._clear)
        row.addWidget(btn_icc)
        row.addWidget(btn_dcp)
        row.addWidget(self._btn_clear)
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
            "Build a camera ICC or DCP from a photographed IT8 calibration chart."))
        g.addWidget(theme.section_separator())
        self._cb_disable = QCheckBox("Disable camera profile (don't apply the ICC/DCP)")
        self._cb_disable.setToolTip(
            "Temporarily ignore the active camera profile at decode without "
            "clearing it. Remembered across sessions. Already-graded images are "
            "flagged for re-grading rather than re-decoded.")
        self._cb_disable.toggled.connect(self._on_disable)
        g.addWidget(self._cb_disable)
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

        lay.addStretch(1)
        return page

    # ------------------------------------------------------------------ #
    # Actions — delegate to MainWindow (file pickers, backend, re-decode);
    # everything applies immediately, then the page refreshes its status.
    # ------------------------------------------------------------------ #
    def _set_icc(self):
        self._mw.set_input_icc_profile()
        self.refresh_color_management()

    def _set_dcp(self):
        self._mw.set_input_dcp_profile()
        self.refresh_color_management()

    def _clear(self):
        if color_management.get_active_dcp_profile() is not None:
            self._mw.clear_input_dcp_profile()
        elif color_management.get_active_input_profile() is not None:
            self._mw.clear_input_icc_profile()
        self.refresh_color_management()

    def _create_it8(self):
        self._mw.create_camera_profile_from_it8()
        self.refresh_color_management()

    def _on_positive(self, checked: bool):
        if bool(checked) == bool(ccr_backend.positive_mode):
            return                                   # programmatic sync, not a user toggle
        self._mw.on_positive_mode_toggled(bool(checked))

    def _on_disable(self, checked: bool):
        if bool(checked) == bool(color_management.input_profile_disabled()):
            return
        self._mw.set_camera_profile_disabled(bool(checked))
        self.refresh_color_management()

    def refresh_color_management(self):
        """Reflect the live active profile + positive-mode state on the page."""
        icc = getattr(ccr_backend, "input_icc_name", None)
        dcp = getattr(ccr_backend, "input_dcp_name", None)
        if dcp:
            self._status.setText(f"Active: DCP — {dcp}")
        elif icc:
            self._status.setText(f"Active: ICC — {icc}")
        else:
            self._status.setText("Active: None")
        self._btn_clear.setEnabled(bool(icc or dcp))
        self._cb_disable.blockSignals(True)
        self._cb_disable.setChecked(color_management.input_profile_disabled())
        self._cb_disable.setEnabled(bool(icc or dcp))
        self._cb_disable.blockSignals(False)
        self._cb_positive.blockSignals(True)
        self._cb_positive.setChecked(bool(ccr_backend.positive_mode))
        self._cb_positive.blockSignals(False)
