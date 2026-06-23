"""Tests for the ONNX frame detector's pure logic + asset wiring (no onnxruntime
needed — boxes_from_prob operates on numpy probability maps)."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core import frame_detect as fd  # noqa: E402


class TestBoxesFromProb(unittest.TestCase):
    def test_excludes_sprocket_strips(self):
        # A central frame band with low-prob 'sprocket' strips top & bottom: the
        # box must exclude the strips (the bug the projection extraction fixes).
        prob = np.zeros((64, 96), np.float32)
        prob[16:48, 8:88] = 0.9
        boxes = fd.boxes_from_prob(prob)
        self.assertEqual(len(boxes), 1)
        x1, y1, x2, y2 = boxes[0]
        self.assertGreater(y1, 0.18); self.assertLess(y2, 0.82)   # strips excluded
        self.assertLess(x1, 0.15); self.assertGreater(x2, 0.85)

    def test_splits_two_frames_on_clear_gap(self):
        prob = np.zeros((64, 120), np.float32)
        prob[16:48, 6:50] = 0.9        # frame A
        prob[16:48, 70:114] = 0.9      # frame B (gap 50..70 is sub-threshold)
        boxes = fd.boxes_from_prob(prob)
        self.assertEqual(len(boxes), 2)
        # largest first; both share the band, neither spans the gap
        self.assertTrue(all(y1 > 0.18 and y2 < 0.82 for (_, y1, _, y2) in boxes))

    def test_no_frame_returns_empty(self):
        self.assertEqual(fd.boxes_from_prob(np.full((48, 64), 0.1, np.float32)), [])

    def test_boxes_sorted_largest_first(self):
        prob = np.zeros((64, 160), np.float32)
        prob[16:48, 4:30] = 0.9        # narrow
        prob[16:48, 50:150] = 0.9      # wide
        boxes = fd.boxes_from_prob(prob)
        self.assertEqual(len(boxes), 2)
        self.assertGreater(boxes[0][2] - boxes[0][0], boxes[1][2] - boxes[1][0])


class TestAssets(unittest.TestCase):
    def test_model_path_points_at_onnx(self):
        self.assertTrue(fd.model_path().endswith("frame_detector.onnx"))

    def test_model_is_bundled(self):
        # The 1.9MB model ships in src/models/ (bundled by Nuitka).
        self.assertTrue(fd.is_model_present(), f"missing model at {fd.model_path()}")


if __name__ == '__main__':
    unittest.main()
