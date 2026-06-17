#!/usr/bin/env python3
"""Tests for watch-folder tethering (spec/camera-tethering.md).

Covers the hardware-free logic: FolderScanner's size-stability gate and
within-tick RAW/JPEG dedupe, extension classification, B/W-point persistence
encode/decode, and a single worker-wiring integration test (decode → emit).
The real camera path is deliberately absent — there is no camera SDK.
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from core.tether_watcher import (  # noqa: E402
    FolderScanner, TetherWatchWorker, is_supported, is_raw,
    encode_bwpoint, decode_bwpoint)
from core.ccr_backend import ccr_backend  # noqa: E402


# --- extension classification ----------------------------------------------
def test_is_supported():
    assert is_supported("DSC_0001.NEF")
    assert is_supported("scan.jpg")
    assert is_supported("frame.TIFF")
    assert not is_supported("notes.txt")
    assert not is_supported("clip.mp4")
    assert not is_supported("noext")


def test_is_raw():
    assert is_raw("a.cr3")
    assert is_raw("a.NEF")
    assert not is_raw("a.jpg")
    assert not is_raw("a.tiff")
    assert not is_raw("a.png")


# --- FolderScanner: size-stability gate ------------------------------------
def test_new_file_held_until_size_stable():
    s = FolderScanner()
    assert s.feed([("a.nef", 100)]) == []   # first sight: no prior size
    assert s.feed([("a.nef", 200)]) == []   # still growing
    assert s.feed([("a.nef", 200)]) == ["a.nef"]  # stable across a tick
    assert s.feed([("a.nef", 200)]) == []   # handled once, never again


def test_zero_size_never_ready():
    s = FolderScanner()
    assert s.feed([("a.nef", 0)]) == []
    assert s.feed([("a.nef", 0)]) == []


def test_preseeded_files_ignored():
    s = FolderScanner(initial_names=["old.nef"])
    assert s.feed([("old.nef", 100)]) == []
    assert s.feed([("old.nef", 100)]) == []


def test_unsupported_ignored():
    s = FolderScanner()
    assert s.feed([("readme.txt", 50)]) == []
    assert s.feed([("readme.txt", 50)]) == []


# --- FolderScanner: RAW + JPEG dedupe (within a tick) ----------------------
def test_raw_preferred_over_jpeg_same_tick():
    s = FolderScanner()
    s.feed([("shot1.cr3", 1000), ("shot1.jpg", 200)])      # prime sizes
    ready = s.feed([("shot1.cr3", 1000), ("shot1.jpg", 200)])
    assert ready == ["shot1.cr3"]                           # RAW wins
    assert s.feed([("shot1.cr3", 1000), ("shot1.jpg", 200)]) == []  # both seen


def test_distinct_basenames_both_import():
    s = FolderScanner()
    s.feed([("a.nef", 10), ("b.nef", 20)])
    assert sorted(s.feed([("a.nef", 10), ("b.nef", 20)])) == ["a.nef", "b.nef"]


def test_jpeg_only_imports_when_no_raw_sibling():
    s = FolderScanner()
    s.feed([("solo.jpg", 300)])
    assert s.feed([("solo.jpg", 300)]) == ["solo.jpg"]


def test_deleted_file_can_reimport_after_recreate():
    s = FolderScanner()
    s.feed([("a.nef", 100)])
    assert s.feed([("a.nef", 100)]) == ["a.nef"]   # imported once stable
    assert s.feed([]) == []                          # deleted → forgotten
    s.feed([("a.nef", 120)])                         # recreated, same name
    assert s.feed([("a.nef", 120)]) == ["a.nef"]     # re-imports


def test_duplicate_name_coalesced_in_one_tick():
    s = FolderScanner()
    s.feed([("a.nef", 100), ("a.nef", 100)])
    assert s.feed([("a.nef", 100), ("a.nef", 100)]) == ["a.nef"]  # not doubled


# --- B/W point persistence encode/decode -----------------------------------
def test_bwpoint_roundtrip():
    pt = (51234.0, 48010.5, 52001.25)
    enc = encode_bwpoint(pt)
    assert isinstance(enc, str)
    assert decode_bwpoint(enc) == pt


def test_bwpoint_none_and_empty():
    assert encode_bwpoint(None) is None
    assert decode_bwpoint(None) is None
    assert decode_bwpoint("") is None


def test_bwpoint_malformed():
    assert decode_bwpoint("not json") is None
    assert decode_bwpoint("[1, 2]") is None          # wrong length
    assert decode_bwpoint('{"b": 1}') is None         # wrong type
    assert decode_bwpoint('[1, 2, "x"]') is None      # non-numeric


# --- worker wiring: decode → emit captured ---------------------------------
def test_worker_imports_unconverted(tmp_path):
    """With no B/W point set, the worker decodes the file and emits a captured
    CCRImage that is NOT converted."""
    ccr_backend.black_point_bgr = None
    ccr_backend.white_point_bgr = None
    ccr_backend.positive_mode = False

    p = tmp_path / "capture01.png"
    px = (np.random.RandomState(7).rand(120, 180, 3) * 255).astype(np.uint8)
    cv2.imwrite(str(p), px)

    worker = TetherWatchWorker()
    got, errs = [], []
    worker.captured.connect(lambda im: got.append(im))
    worker.captureError.connect(lambda path, msg: errs.append((path, msg)))
    worker.process(str(p))  # direct (same-thread) call → synchronous signals

    assert not errs, errs
    assert len(got) == 1
    assert os.path.normpath(got[0].file_path) == os.path.normpath(str(p))
    assert got[0].converted is False


def test_worker_converts_with_bwpoint(tmp_path):
    """With a B/W point set and Positive mode off, the worker bakes the
    conversion and records conversion_inputs for export/catalog replay."""
    ccr_backend.positive_mode = False
    ccr_backend.black_point_bgr = (60000.0, 60000.0, 60000.0)  # clear base (high)
    ccr_backend.white_point_bgr = (5000.0, 5000.0, 5000.0)     # dense (low)
    try:
        p = tmp_path / "neg.png"
        px = (np.random.RandomState(3).rand(100, 140, 3) * 255).astype(np.uint8)
        cv2.imwrite(str(p), px)

        worker = TetherWatchWorker()
        got = []
        worker.captured.connect(lambda im: got.append(im))
        worker.process(str(p))

        assert len(got) == 1
        img = got[0]
        assert img.converted is True
        assert img.conversion_inputs is not None
        assert img.conversion_inputs["mode"] == "bw"
        bw = img.conversion_inputs["bw"]
        assert tuple(bw[0]) == (60000.0, 60000.0, 60000.0)
        assert tuple(bw[1]) == (5000.0, 5000.0, 5000.0)
    finally:
        ccr_backend.black_point_bgr = None
        ccr_backend.white_point_bgr = None


def test_worker_positive_mode_skips_conversion(tmp_path):
    """Positive mode → captures import as-is even when a B/W point is set."""
    ccr_backend.positive_mode = True
    ccr_backend.black_point_bgr = (60000.0, 60000.0, 60000.0)
    ccr_backend.white_point_bgr = (5000.0, 5000.0, 5000.0)
    try:
        p = tmp_path / "pos.png"
        px = (np.random.RandomState(4).rand(90, 90, 3) * 255).astype(np.uint8)
        cv2.imwrite(str(p), px)

        worker = TetherWatchWorker()
        got = []
        worker.captured.connect(lambda im: got.append(im))
        worker.process(str(p))

        assert len(got) == 1
        assert got[0].converted is False
    finally:
        ccr_backend.positive_mode = False
        ccr_backend.black_point_bgr = None
        ccr_backend.white_point_bgr = None
