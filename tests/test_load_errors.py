#!/usr/bin/env python3
"""Reporting for files the loader could not open (core/load_errors.py).

A file that fails to decode used to vanish from the import: read_image returns
None, CCRImage.__init__ raises, the loader catches it and drops the file, and
the only trace is a stdout print no built exe can show. These tests pin the
three links in the new chain — the LibRaw cause survives to the loader, it is
classified into a user-facing reason, and the reasons are grouped into one
message — plus the end-to-end wiring through CCRBackend.
"""
import os
import sys

import cv2
import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])

import pytest  # noqa: E402

from core.load_errors import (  # noqa: E402
    HE_NEF_REASON, MISSING_REASON, PERMISSION_REASON, RAW_UNSUPPORTED_REASON,
    describe_load_failure, format_load_failures)
from core.ccr_backend import CCRBackend  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_backend():
    """CCRBackend is an enforced singleton — leave it empty for other modules."""
    yield
    backend = CCRBackend()
    backend.images = []
    backend.file_paths = []
    backend.last_load_errors = []


def _wrapped(inner):
    """The exception the loader actually sees: CCRImage.__init__'s ValueError
    chained onto the decoder's error."""
    try:
        try:
            raise inner
        except Exception as e:
            raise ValueError("Could not read image from file: x") from e
    except ValueError as outer:
        return outer


def _touch(tmp_path, name):
    """A file that EXISTS but cannot be decoded — the classifier checks the
    filesystem, so an undecodable file must be told apart from a missing one."""
    p = tmp_path / name
    p.write_bytes(b"\x00" * 64)
    return str(p)


# --- classification ---------------------------------------------------------

# LibRaw 0.21.x opens an HE NEF, reports its size, then dies in unpack; 0.22+
# rejects it up front. Both must reach the same explanation.
@pytest.mark.parametrize("message", [
    "Data error or unsupported file format",
    "unknown file: data corrupted at 2823171",
    "Unsupported file format or not RAW file",
])
def test_undecodable_nef_reports_he(message, tmp_path):
    err = _wrapped(RuntimeError(message))
    assert describe_load_failure(
        _touch(tmp_path, "DSC_6887.NEF"), err) == HE_NEF_REASON


def test_he_reason_names_the_dng_workaround():
    """The message is the whole point of the feature: it must tell the user
    what to DO, including the Camera Raw compatibility trap (a DNG 1.7 / JPEG
    XL file is equally unreadable by the bundled LibRaw)."""
    assert "DNG Converter" in HE_NEF_REASON
    assert "15.3" in HE_NEF_REASON


def test_undecodable_non_nef_raw_is_generic(tmp_path):
    """Only NEF gets the Nikon HE explanation — an unsupported CR3 must not."""
    err = _wrapped(RuntimeError("Unsupported file format or not RAW file"))
    assert describe_load_failure(
        _touch(tmp_path, "IMG_0001.CR3"), err) == RAW_UNSUPPORTED_REASON


def test_non_raw_file_never_gets_a_raw_reason(tmp_path):
    err = _wrapped(RuntimeError("Unsupported file format or not RAW file"))
    reason = describe_load_failure(_touch(tmp_path, "scan.jpg"), err)
    assert reason not in (HE_NEF_REASON, RAW_UNSUPPORTED_REASON)


def test_extension_match_is_case_insensitive(tmp_path):
    err = _wrapped(RuntimeError("data error"))
    assert describe_load_failure(_touch(tmp_path, "a.nef"), err) == HE_NEF_REASON
    assert describe_load_failure(_touch(tmp_path, "b.NEF"), err) == HE_NEF_REASON


def test_missing_file_beats_the_decoder_error(tmp_path):
    """rawpy reports a missing RAW as LibRawIOError("Input/output error"), NOT
    FileNotFoundError — so a deleted file used to be explained as an
    undecodable one. The filesystem is the authority here."""
    gone = str(tmp_path / "gone.nef")
    assert describe_load_failure(
        gone, _wrapped(RuntimeError("Input/output error"))) == MISSING_REASON
    assert describe_load_failure(
        gone, _wrapped(FileNotFoundError(2, "No such file"))) == MISSING_REASON


def test_permission_denied(tmp_path):
    assert describe_load_failure(
        _touch(tmp_path, "locked.nef"),
        _wrapped(PermissionError(13, "Denied"))) == PERMISSION_REASON


def test_unknown_error_reports_the_root_cause(tmp_path):
    """The outer ValueError only restates the path; the useful text is the
    decoder's own message at the bottom of the chain."""
    reason = describe_load_failure(_touch(tmp_path, "scan.tif"),
                                   _wrapped(RuntimeError("weird failure")))
    assert "weird failure" in reason
    assert "Could not read image from file" not in reason


def test_describe_never_raises(tmp_path):
    """A reason is cosmetic — reporting a failed load must not fail itself."""
    class Exploding(Exception):
        def __str__(self):
            raise RuntimeError("boom")

    assert describe_load_failure(None, None)
    assert describe_load_failure(_touch(tmp_path, "x.tif"), Exploding())


def test_short_form_is_terse_for_the_tether_hint(tmp_path):
    err = _wrapped(RuntimeError("data error"))
    short = describe_load_failure(_touch(tmp_path, "DSC_1.NEF"), err, short=True)
    assert short != HE_NEF_REASON
    assert len(short) < 60
    assert "DNG" in short


# --- message formatting -----------------------------------------------------

def test_no_failures_formats_to_empty():
    assert format_load_failures([]) == ""


def test_failures_group_by_reason():
    """A whole roll usually shares one cause; the explanation must appear once,
    not repeated under every filename."""
    failures = [(f"/r/DSC_{i}.NEF", HE_NEF_REASON) for i in range(3)]
    failures.append(("/r/gone.tif", MISSING_REASON))
    body = format_load_failures(failures)

    # The reason is wrapped for the dialog, so match a distinctive phrase.
    assert body.count("High Efficiency") == 1
    assert body.startswith("4 files could not be loaded:")
    for i in range(3):
        assert f"DSC_{i}.NEF" in body
    assert "gone.tif" in body


def test_long_group_is_capped():
    failures = [(f"/r/DSC_{i}.NEF", HE_NEF_REASON) for i in range(40)]
    body = format_load_failures(failures, max_per_reason=8)
    assert "DSC_0.NEF" in body
    assert "DSC_39.NEF" not in body
    assert "…and 32 more" in body


def test_singular_wording_for_one_file():
    assert format_load_failures(
        [("/r/x.nef", MISSING_REASON)]).startswith("1 file could not be loaded:")


def test_message_lines_stay_narrow():
    """QMessageBox sizes itself to the longest line — an unwrapped reason would
    stretch the dialog across the screen."""
    body = format_load_failures([("/r/DSC_1.NEF", HE_NEF_REASON)])
    assert max(len(line) for line in body.splitlines()) <= 80
    assert "High Efficiency" in body


def test_basenames_only_no_full_paths():
    body = format_load_failures([(os.path.join("C:", "rolls", "x.nef"),
                                  MISSING_REASON)])
    assert "x.nef" in body
    assert "rolls" not in body


# --- wiring through the real load path --------------------------------------

def test_ccrimage_chains_the_decode_cause(tmp_path):
    """CCRImage must not swallow the decoder's error — the loader classifies
    the ROOT cause, so an unchained ValueError would flatten every failure into
    the generic message."""
    from core.ccr_image import CCRImage
    missing = str(tmp_path / "nope.nef")
    with pytest.raises(ValueError) as excinfo:
        CCRImage(missing)
    assert excinfo.value.__cause__ is not None
    assert describe_load_failure(missing, excinfo.value) == MISSING_REASON


def test_backend_records_failures_and_still_loads_the_good_files(tmp_path):
    good = str(tmp_path / "scan.png")
    cv2.imwrite(good, np.full((32, 48, 3), 120, dtype=np.uint8))
    missing = str(tmp_path / "nope.nef")

    backend = CCRBackend()
    count = backend.load_images_from_files([good, missing])

    assert count == 1                      # the good file still imports
    assert len(backend.images) == 1
    assert [p for p, _ in backend.last_load_errors] == [missing]
    assert backend.last_load_errors[0][1] == MISSING_REASON


def test_successful_load_clears_previous_failures(tmp_path):
    """A stale message must never surface after a later, clean import."""
    good = str(tmp_path / "scan.png")
    cv2.imwrite(good, np.full((32, 48, 3), 120, dtype=np.uint8))

    backend = CCRBackend()
    backend.load_images_from_files([good, str(tmp_path / "nope.nef")])
    assert backend.last_load_errors
    backend.load_images_from_files([good])
    assert backend.last_load_errors == []


def test_cancelled_load_reports_nothing(tmp_path):
    """No error dialog for a load the user deliberately stopped."""
    backend = CCRBackend()
    backend.load_images_from_files([str(tmp_path / "nope.nef")],
                                   cancel_flag=lambda: True)
    assert backend.last_load_errors == []
