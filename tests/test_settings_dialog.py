#!/usr/bin/env python3
"""
Tests for the Settings dialog (DaVinci-style Color Management tab), the
disable-camera-profile toggle, the per-image profile signature + thumbnail
mismatch flagging, and "Replace with current camera profile". See
spec/settings-page.md (§8b).
"""
import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtWidgets import QApplication, QListWidgetItem, QWidget  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])

import cv2  # noqa: E402
from core import color_management as cm  # noqa: E402
from core.ccr_backend import ccr_backend  # noqa: E402
from core.ccr_image import CCRImage  # noqa: E402


class _StubICC:
    description = "MyCam"

    def apply(self, arr):           # no-op identity (decode still succeeds)
        return arr


class _StubDCP:
    name = "Nikon"


@pytest.fixture(autouse=True)
def _reset_profile_state():
    """Each test starts with no profile, not disabled, negative mode."""
    cm.set_active_input_profile(None)
    cm.set_active_dcp_profile(None)
    cm.set_input_profile_disabled(False)
    ccr_backend.positive_mode = False
    ccr_backend.images = []
    yield
    cm.set_active_input_profile(None)
    cm.set_active_dcp_profile(None)
    cm.set_input_profile_disabled(False)
    ccr_backend.positive_mode = False
    ccr_backend.images = []


def _scan_png(tmp_path, name="negative.png", w=320, h=240, seed=7):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    base = 20000 + 25000 * (xx / w) + 10000 * (yy / h)
    img = np.stack([base * 1.2, base, base * 0.7], axis=-1)
    img += rng.normal(0, 1200, img.shape)
    img = np.clip(img, 1000, 64000).astype(np.uint16)
    path = str(tmp_path / name)
    cv2.imwrite(path, cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
    return path


# --------------------------------------------------------------------------- #
# color_management: signature + disable + camera_profile_active
# --------------------------------------------------------------------------- #
class TestSignatureAndDisable:
    def test_none_when_no_profile(self):
        assert cm.active_profile_signature() == "none"
        assert cm.camera_profile_active() is False

    def test_icc_active(self):
        cm.set_active_input_profile(_StubICC())
        assert cm.active_profile_signature() == "icc:MyCam"
        assert cm.camera_profile_active() is True

    def test_dcp_takes_precedence(self):
        cm.set_active_input_profile(_StubICC())
        cm.set_active_dcp_profile(_StubDCP())
        assert cm.active_profile_signature() == "dcp:Nikon"

    def test_disabled_reads_as_none(self):
        cm.set_active_input_profile(_StubICC())
        cm.set_input_profile_disabled(True)
        assert cm.active_profile_signature() == "none"
        assert cm.camera_profile_active() is False
        assert cm.input_profile_disabled() is True

    def test_backend_signature_none_in_positive_mode(self):
        cm.set_active_input_profile(_StubICC())
        ccr_backend.positive_mode = True
        assert ccr_backend.active_profile_signature() == "none"
        ccr_backend.positive_mode = False
        assert ccr_backend.active_profile_signature() == "icc:MyCam"


# --------------------------------------------------------------------------- #
# CCRImage: profile-signature stamping + decode gating
# --------------------------------------------------------------------------- #
class TestProfileSignatureStamping:
    def test_stamped_none_on_plain_load(self, tmp_path):
        img = CCRImage(_scan_png(tmp_path))
        assert img.profile_signature == "none"

    def test_stamp_reflects_active_profile(self, tmp_path):
        img = CCRImage(_scan_png(tmp_path))
        cm.set_active_input_profile(_StubICC())
        img._stamp_profile_signature()
        assert img.profile_signature == "icc:MyCam"
        cm.set_input_profile_disabled(True)
        img._stamp_profile_signature()
        assert img.profile_signature == "none"

    def test_reload_restamps_under_current_profile(self, tmp_path):
        img = CCRImage(_scan_png(tmp_path))
        img.profile_signature = "icc:Stale"        # pretend graded under another
        cm.set_active_input_profile(_StubICC())
        assert img.reload_image_decode_only() is True
        assert img.profile_signature == "icc:MyCam"

    def test_will_apply_follows_disable(self, tmp_path):
        img = CCRImage(_scan_png(tmp_path))
        cm.set_active_input_profile(_StubICC())
        assert img._input_icc_will_apply() is True
        cm.set_input_profile_disabled(True)
        assert img._input_icc_will_apply() is False


# --------------------------------------------------------------------------- #
# Backend: reset re-grades an image under the current profile
# --------------------------------------------------------------------------- #
class TestRegradePrimitive:
    def test_reset_restamps_signature(self, tmp_path):
        img = CCRImage(_scan_png(tmp_path))
        ccr_backend.images = [img]
        ccr_backend.file_paths = [img.file_path]
        img.profile_signature = "icc:Stale"
        assert ccr_backend.active_profile_signature() == "none"  # nothing active
        assert ccr_backend.reset_images_by_indices([0]) is True
        assert img.profile_signature == "none"                   # re-graded to active


# --------------------------------------------------------------------------- #
# ThumbnailList: mismatch flagging + replace-action selection
# --------------------------------------------------------------------------- #
class _StubImg:
    def __init__(self, path, sig):
        self.file_path = path
        self.profile_signature = sig
        self.display_name = None


def _thumb_list_with(sigs):
    from widgets.thumbnail_list import ThumbnailList
    ccr_backend.images = [_StubImg(f"{i}.dng", s) for i, s in enumerate(sigs)]
    tl = ThumbnailList(lambda i: None)
    for i in range(len(sigs)):
        it = QListWidgetItem("x")
        it.setData(Qt.UserRole, i)
        tl.thumbnail_list.addItem(it)
    return tl


class TestThumbnailMismatch:
    WARN = "⚠"

    def test_flags_only_mismatched(self):
        cm.set_active_input_profile(_StubICC())            # active = icc:MyCam
        tl = _thumb_list_with(["icc:MyCam", "none", "dcp:Old"])
        tl.refresh_profile_warnings()
        warned = [tl.thumbnail_list.item(i).text().startswith(self.WARN)
                  for i in range(3)]
        assert warned == [False, True, True]
        assert [tl._profile_mismatch(i) for i in range(3)] == [False, True, True]

    def test_reflags_when_profile_disabled(self):
        cm.set_active_input_profile(_StubICC())
        tl = _thumb_list_with(["icc:MyCam", "none"])
        tl.refresh_profile_warnings()
        assert tl._profile_mismatch(0) is False
        cm.set_input_profile_disabled(True)                # active -> none
        tl.refresh_profile_warnings()
        assert tl._profile_mismatch(0) is True             # icc:MyCam now mismatches
        assert tl._profile_mismatch(1) is False

    def test_matched_items_have_no_warning_prefix(self):
        # No profile active: an image graded under 'none' matches -> plain name.
        tl = _thumb_list_with(["none"])
        tl.refresh_profile_warnings()
        assert tl.thumbnail_list.item(0).text() == "0.dng"

    def test_positive_mode_refresh_clears_warnings(self):
        # Regression: toggling Positive mode re-stamps every signature to 'none'
        # and active becomes 'none'; refresh_profile_warnings (now called by
        # on_positive_mode_toggled) must clear the stale ⚠.
        cm.set_active_input_profile(_StubICC())
        tl = _thumb_list_with(["icc:Other", "icc:Other"])
        tl.refresh_profile_warnings()
        assert all(tl._profile_mismatch(i) for i in range(2))
        ccr_backend.positive_mode = True            # active signature -> 'none'
        for img in ccr_backend.images:
            img.profile_signature = "none"          # re-stamped by the re-decode
        tl.refresh_profile_warnings()
        assert not any(tl._profile_mismatch(i) for i in range(2))
        assert not tl.thumbnail_list.item(0).text().startswith(self.WARN)


class TestContentIdSignature:
    def test_content_id_distinguishes_same_named_profiles(self):
        a = _StubICC(); a.content_id = "aaaaaa"
        b = _StubICC(); b.content_id = "bbbbbb"     # same description, different file
        cm.set_active_input_profile(a)
        assert cm.active_profile_signature() == "icc:aaaaaa"
        cm.set_active_input_profile(b)
        assert cm.active_profile_signature() == "icc:bbbbbb"

    def test_falls_back_to_description_without_content_id(self):
        cm.set_active_input_profile(_StubICC())     # no content_id attribute
        assert cm.active_profile_signature() == "icc:MyCam"


class TestIT8FormatSelector:
    """The IT8 wizard exposes an explicit ICC-vs-DCP Output-format selector."""

    def _dlg(self):
        from widgets.it8_profile_dialog import IT8ProfileDialog
        return IT8ProfileDialog(None)

    def test_default_is_icc_with_type_choice(self):
        d = self._dlg()
        assert d._is_dcp() is False
        assert d.type_combo.isEnabled() is True          # matrix/cLUT available
        assert d._default_save_path().endswith(".icc")

    def test_dcp_forces_matrix_and_dcp_extension(self):
        d = self._dlg()
        d.format_combo.setCurrentIndex(1)                # DCP
        assert d._is_dcp() is True
        assert d.type_combo.isEnabled() is False         # cLUT is ICC-only
        assert d.type_combo.currentIndex() == 0          # forced 3×3 matrix
        assert d._default_save_path().endswith(".dcp")

    def test_format_switch_syncs_path_extension(self):
        d = self._dlg()
        d.format_combo.setCurrentIndex(1)
        d.save_path_edit.setText("X/p.dcp")
        d.format_combo.setCurrentIndex(0)                # back to ICC
        assert d.save_path_edit.text().endswith(".icc")


# --------------------------------------------------------------------------- #
# SettingsDialog: structure, status reflection, disable + positive wiring
# --------------------------------------------------------------------------- #
class _StubMW(QWidget):
    def __init__(self):
        super().__init__()
        self.calls = []

    def set_input_icc_profile(self): self.calls.append("set_icc")
    def set_input_dcp_profile(self): self.calls.append("set_dcp")
    def clear_input_icc_profile(self): self.calls.append("clear_icc")
    def clear_input_dcp_profile(self): self.calls.append("clear_dcp")
    def create_camera_profile_from_it8(self): self.calls.append("it8")

    def on_positive_mode_toggled(self, c):
        self.calls.append(("positive", bool(c)))
        ccr_backend.positive_mode = bool(c)

    def set_camera_profile_disabled(self, d):
        self.calls.append(("disable", bool(d)))
        cm.set_input_profile_disabled(bool(d))


def _dialog():
    from widgets.settings_dialog import SettingsDialog
    return SettingsDialog(_StubMW())


class TestSettingsDialog:
    def test_has_color_management_category(self):
        d = _dialog()
        names = [d._sidebar.item(i).text() for i in range(d._sidebar.count())]
        assert names == ["Color Management"]

    def test_status_reflects_active_profile(self):
        ccr_backend.input_icc_name = None
        ccr_backend.input_dcp_name = None
        d = _dialog()
        assert d._status.text() == "Active: None"
        assert d._btn_clear.isEnabled() is False
        ccr_backend.input_icc_name = "MyCam.icc"
        d.refresh_color_management()
        assert "ICC" in d._status.text() and "MyCam.icc" in d._status.text()
        assert d._btn_clear.isEnabled() is True
        ccr_backend.input_icc_name = None
        ccr_backend.input_dcp_name = "Nikon.dcp"
        d.refresh_color_management()
        assert "DCP" in d._status.text() and "Nikon.dcp" in d._status.text()
        ccr_backend.input_dcp_name = None

    def test_buttons_delegate_to_main_window(self):
        d = _dialog()
        d._set_icc(); d._set_dcp(); d._create_it8()
        assert d._mw.calls == ["set_icc", "set_dcp", "it8"]

    def test_disable_checkbox_toggles_backend(self):
        ccr_backend.input_icc_name = "MyCam.icc"
        cm.set_active_input_profile(_StubICC())
        d = _dialog()
        assert d._cb_disable.isChecked() is False
        d._cb_disable.setChecked(True)
        assert ("disable", True) in d._mw.calls
        assert cm.input_profile_disabled() is True
        ccr_backend.input_icc_name = None

    def test_positive_checkbox_toggles_backend(self):
        d = _dialog()
        d._cb_positive.setChecked(True)
        assert ("positive", True) in d._mw.calls
        assert ccr_backend.positive_mode is True
