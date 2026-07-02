#!/usr/bin/env python3
"""Batch-load cancel/supersede semantics (CCRBackend._commit_load).

Cancelling a load calls ccr_backend.clear() on the GUI thread while the
loader thread is still collecting results; the loader used to finish with
`self.images = results`, silently resurrecting the partial batch (and a
stale, abandoned loader could clobber a newer load the same way). The fix
publishes a batch only when the load is uncancelled AND its generation
snapshot is still current. These tests pin that contract.
"""
import os
import sys
from types import SimpleNamespace

import cv2
import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])

import pytest  # noqa: E402

from core.ccr_backend import CCRBackend  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_backend():
    """CCRBackend is an enforced singleton — every CCRBackend() below is the
    global instance, so leave it empty for other test modules."""
    yield
    backend = CCRBackend()
    backend.images = []
    backend.file_paths = []


def _make_pngs(tmp_path, n=3):
    paths = []
    for i in range(n):
        p = str(tmp_path / f"scan_{i}.png")
        cv2.imwrite(p, np.full((32, 48, 3), 100 + i, dtype=np.uint8))
        paths.append(p)
    return paths


def _stub_image(name="x.png"):
    return SimpleNamespace(file_path=name)


# --- happy path is unchanged ------------------------------------------------

def test_uncancelled_load_publishes_batch(tmp_path):
    backend = CCRBackend()
    paths = _make_pngs(tmp_path)
    count = backend.load_images_from_files(paths)
    assert count == len(paths)
    assert len(backend.images) == len(paths)
    assert backend.file_paths == [img.file_path for img in backend.images]


# --- cancel must not resurrect the partial batch ----------------------------

def test_cancelled_load_publishes_nothing(tmp_path):
    backend = CCRBackend()
    paths = _make_pngs(tmp_path)
    calls = {"n": 0}

    def cancel_after_a_few():
        calls["n"] += 1
        return calls["n"] > 2  # cancel lands mid-load, like the Cancel button

    count = backend.load_images_from_files(paths, cancel_flag=cancel_after_a_few)
    assert count == 0
    assert backend.images == []
    assert backend.file_paths == []


def test_immediately_cancelled_load_publishes_nothing(tmp_path):
    backend = CCRBackend()
    count = backend.load_images_from_files(_make_pngs(tmp_path),
                                           cancel_flag=lambda: True)
    assert count == 0
    assert backend.images == []


# --- generation guard: stale loaders must not clobber newer state -----------

def test_commit_refused_for_stale_generation():
    backend = CCRBackend()
    backend._load_generation += 1
    stale_gen = backend._load_generation
    current = [_stub_image("current.png")]
    backend.images = current
    backend.file_paths = ["current.png"]
    backend._load_generation += 1  # a newer load/clear superseded stale_gen
    assert backend._commit_load(stale_gen, [_stub_image("stale.png")]) is False
    assert backend.images is current
    assert backend.file_paths == ["current.png"]


def test_commit_accepted_for_current_generation():
    backend = CCRBackend()
    backend._load_generation += 1
    img = _stub_image("fresh.png")
    assert backend._commit_load(backend._load_generation, [img]) is True
    assert backend.images == [img]
    assert backend.file_paths == ["fresh.png"]


def test_clear_invalidates_inflight_commit():
    backend = CCRBackend()
    backend._load_generation += 1
    gen = backend._load_generation
    backend.clear()  # the Cancel handler's clear() bumps the generation
    assert backend._commit_load(gen, [_stub_image()]) is False
    assert backend.images == []


def test_commit_without_generation_still_honors_cancel():
    backend = CCRBackend()
    assert backend._commit_load(None, [_stub_image()], cancel_flag=lambda: True) is False
    assert backend._commit_load(None, [_stub_image()]) is True
