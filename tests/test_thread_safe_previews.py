#!/usr/bin/env python3
"""Thread-safety of CCRImage preview building.

QPixmap may only be created on the Qt GUI thread, but the batch paths
(initial load, auto-frame-all, B/W convert-all) run
CCRImage.update_thumbnail_and_preview on pool/QThread workers. The fix defers
pixmap construction off the GUI thread: workers stash the rendered 8-bit
pixels, and the first GUI-thread read of .thumbnail/.resized_preview builds
the pixmaps. These tests pin that contract in both directions.
"""
import os
import sys
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])

from core.ccr_image import CCRImage  # noqa: E402


def _make_png(tmp_path, name="scan.png"):
    path = str(tmp_path / name)
    cv2.imwrite(path, np.full((64, 96, 3), 128, dtype=np.uint8))
    return path


def _build_on_worker(path):
    with ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(CCRImage, path).result()


# --- GUI-thread construction keeps the historical eager behaviour ----------

def test_gui_thread_build_is_eager(tmp_path):
    img = CCRImage(_make_png(tmp_path))
    assert img._thumb_np8 is None and img._preview_np8 is None
    assert img.thumbnail is not None and not img.thumbnail.isNull()
    assert img.resized_preview is not None and not img.resized_preview.isNull()


# --- worker-thread construction defers pixmap creation ---------------------

def test_worker_thread_build_defers_pixmaps(tmp_path):
    img = _build_on_worker(_make_png(tmp_path))
    assert img._thumb_np8 is not None and img._preview_np8 is not None
    assert img._thumbnail_pix is None and img._preview_pix is None


def test_gui_thread_read_materializes_and_clears_stash(tmp_path):
    img = _build_on_worker(_make_png(tmp_path))
    pm = img.thumbnail
    assert pm is not None and not pm.isNull() and pm.width() > 0
    assert img._thumb_np8 is None
    pv = img.resized_preview
    assert pv is not None and not pv.isNull()
    assert img._preview_np8 is None


def test_worker_rerender_of_existing_image_defers(tmp_path):
    """reload_image()/update_thumbnail_and_preview() on a QThread (the B/W
    convert-all path) must stash pixels, not build pixmaps."""
    img = CCRImage(_make_png(tmp_path))
    old_pm = img.thumbnail
    with ThreadPoolExecutor(max_workers=1) as ex:
        ex.submit(img.update_thumbnail_and_preview).result()
    assert img._thumb_np8 is not None and img._preview_np8 is not None
    # A worker-thread read must NOT materialize (returns the stale pixmap)
    with ThreadPoolExecutor(max_workers=1) as ex:
        stale = ex.submit(lambda: img.thumbnail).result()
    assert stale is old_pm
    assert img._thumb_np8 is not None
    # ...but a GUI-thread read builds the fresh one
    assert img.thumbnail is not None and img._thumb_np8 is None


def test_setter_clears_pending_stash(tmp_path):
    img = _build_on_worker(_make_png(tmp_path))
    img.thumbnail = None
    img.resized_preview = None
    assert img._thumb_np8 is None and img._preview_np8 is None
    assert img.thumbnail is None and img.resized_preview is None


def test_histogram_data_present_after_worker_build(tmp_path):
    img = _build_on_worker(_make_png(tmp_path))
    assert img.histogram_data is not None
    assert img.histogram_data.shape == (3, 256)


# --- hi-res worker shutdown at app close ------------------------------------

def test_shutdown_workers_waits_out_inflight_renders(tmp_path):
    """closeEvent must be able to quiesce hi-res render threads; a running
    QThread destroyed at teardown hard-aborts the process. Uses the real
    ImagePreview.shutdown_workers body on a minimal host (a full ImagePreview
    needs a MainWindow parent)."""
    from widgets.image_preview import ImagePreview, HiResDetailWorker

    class _Host:
        shutdown_workers = ImagePreview.shutdown_workers

        def __init__(self):
            self._hires_workers = set()

    img = CCRImage(_make_png(tmp_path))
    host = _Host()
    worker = HiResDetailWorker(img, "sig", "adj", None, 512)
    host._hires_workers.add(worker)
    worker.start()
    host.shutdown_workers()
    assert not worker.isRunning()
    assert not host._hires_workers
