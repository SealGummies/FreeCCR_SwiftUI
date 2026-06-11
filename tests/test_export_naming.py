#!/usr/bin/env python3
"""
pytest tests for the export filename-building logic (src/utils/export_naming.py).
Run with: pytest tests/test_export_naming.py -v
"""

import os
import sys
from datetime import datetime

import pytest

# Add the src directory to the path so we can import the naming module
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils.export_naming import (
    build_export_names,
    build_filename,
    expand_macros,
    resolve_conflicts,
    sanitize_filename,
    uniquify_in_batch,
)

NOW = datetime(2026, 6, 10, 14, 30, 5)


class TestExpandMacros:
    def test_date_time_seq_name(self):
        out = expand_macros("{date}_{time}_{seq}_{name}", base_name="IMG_0042",
                            seq=7, seq_width=3, now=NOW)
        assert out == "2026-06-10_143005_007_IMG_0042"

    def test_unknown_macro_left_literal(self):
        out = expand_macros("{foo}_{date}{bar}", base_name="x", seq=1, seq_width=3, now=NOW)
        assert out == "{foo}_2026-06-10{bar}"

    def test_empty_template(self):
        assert expand_macros("", base_name="x", seq=1, seq_width=3, now=NOW) == ""

    def test_seq_padding_width(self):
        assert expand_macros("{seq}", base_name="x", seq=12, seq_width=5, now=NOW) == "00012"


class TestSanitizeFilename:
    def test_reserved_chars_replaced(self):
        assert sanitize_filename('a<b>c:d"e/f\\g|h?i*j') == "a_b_c_d_e_f_g_h_i_j"

    def test_control_chars_replaced(self):
        assert sanitize_filename("a\x00b\x1fc") == "a_b_c"

    def test_trailing_dots_and_spaces_stripped(self):
        assert sanitize_filename("name.. ") == "name"

    def test_unicode_preserved(self):
        assert sanitize_filename("照片_テスト") == "照片_テスト"


class TestBuildFilename:
    def test_default_ccr_convention(self):
        # Matches the legacy "{base}_ccr.tiff" naming
        out = build_filename("photo", "", "_ccr", ".tiff", seq=1, seq_width=3, now=NOW)
        assert out == "photo_ccr.tiff"

    def test_prefix_and_suffix_with_macros(self):
        out = build_filename("roll1", "{date}_", "_{seq}", ".jpg", seq=2, seq_width=3, now=NOW)
        assert out == "2026-06-10_roll1_002.jpg"

    def test_empty_prefix_suffix_keeps_base(self):
        assert build_filename("scan", "", "", ".tiff", seq=1, seq_width=3, now=NOW) == "scan.tiff"

    def test_sanitization_applied_to_whole_stem(self):
        out = build_filename("a/b", "x:", "", ".jpg", seq=1, seq_width=3, now=NOW)
        assert out == "x_a_b.jpg"


class TestUniquifyInBatch:
    def test_no_duplicates_untouched(self):
        names = ["a.jpg", "b.jpg"]
        assert uniquify_in_batch(names) == names

    def test_duplicates_get_numbered(self):
        out = uniquify_in_batch(["x.jpg", "x.jpg", "x.jpg"])
        assert out == ["x.jpg", "x-1.jpg", "x-2.jpg"]

    def test_case_insensitive(self):
        out = uniquify_in_batch(["Photo.jpg", "photo.jpg"])
        assert out == ["Photo.jpg", "photo-1.jpg"]

    def test_avoids_existing_batch_name(self):
        # The -1 candidate is already taken by a real entry
        out = uniquify_in_batch(["x.jpg", "x-1.jpg", "x.jpg"])
        assert out == ["x.jpg", "x-1.jpg", "x-2.jpg"]


class TestBuildExportNames:
    def test_seq_width_minimum_three(self):
        out = build_export_names(["a"], "{seq}_", "", ".jpg", now=NOW)
        assert out == ["001_a.jpg"]

    def test_seq_width_grows_with_count(self):
        bases = [f"img{i}" for i in range(1500)]
        out = build_export_names(bases, "{seq}_", "", ".jpg", now=NOW)
        assert out[0] == "0001_img0.jpg"
        assert out[-1] == "1500_img1499.jpg"

    def test_date_only_suffix_forces_in_batch_dedup(self):
        # Identical stems after macro expansion must still be unique
        out = build_export_names(["a", "b"], "", "", ".jpg", now=NOW)
        assert out == ["a.jpg", "b.jpg"]
        out = build_export_names(["same", "same"], "", "_{date}", ".jpg", now=NOW)
        assert out == ["same_2026-06-10.jpg", "same_2026-06-10-1.jpg"]

    def test_legacy_convention_for_batch(self):
        out = build_export_names(["p1", "p2"], "", "_ccr", ".tiff", now=NOW)
        assert out == ["p1_ccr.tiff", "p2_ccr.tiff"]


class TestResolveConflicts:
    def test_overwrite_keeps_all(self):
        names = ["a.jpg", "b.jpg"]
        out = resolve_conflicts(names, "overwrite", exists=lambda n: True)
        assert out == names

    def test_skip_drops_existing(self):
        on_disk = {"a.jpg"}
        out = resolve_conflicts(["a.jpg", "b.jpg"], "skip", exists=lambda n: n in on_disk)
        assert out == [None, "b.jpg"]

    def test_rename_bumps_until_free(self):
        on_disk = {"a.jpg", "a-1.jpg"}
        out = resolve_conflicts(["a.jpg", "b.jpg"], "rename", exists=lambda n: n in on_disk)
        assert out == ["a-2.jpg", "b.jpg"]

    def test_rename_avoids_other_batch_names(self):
        # a.jpg exists on disk; its -1 candidate collides with a batch member
        on_disk = {"a.jpg"}
        out = resolve_conflicts(["a.jpg", "a-1.jpg"], "rename", exists=lambda n: n in on_disk)
        assert out == ["a-2.jpg", "a-1.jpg"]

    def test_unknown_policy_raises(self):
        with pytest.raises(ValueError):
            resolve_conflicts(["a.jpg"], "bogus", exists=lambda n: False)


class TestCombinedScenario:
    def test_full_pipeline(self):
        bases = ["roll1_001", "roll1_001", "roll1_002"]
        names = build_export_names(bases, "", "_{date}", ".jpg", now=NOW)
        assert names == [
            "roll1_001_2026-06-10.jpg",
            "roll1_001_2026-06-10-1.jpg",
            "roll1_002_2026-06-10.jpg",
        ]
        on_disk = {"roll1_002_2026-06-10.jpg"}
        final = resolve_conflicts(names, "rename", exists=lambda n: n in on_disk)
        assert final == [
            "roll1_001_2026-06-10.jpg",
            "roll1_001_2026-06-10-1.jpg",
            "roll1_002_2026-06-10-1.jpg",
        ]
