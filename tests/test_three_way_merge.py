#!/usr/bin/env python3
"""
Tests for 3-way RGB-light merge (trichrome capture).

Covers the pure, rawpy-free core: input validation (multiple-of-3, RAW-only),
filename sorting + triplet grouping, the color_desc -> channel-index mapping,
and the channel-combine maths (correct frame->channel selection, per-frame
white-level scaling, size-mismatch cropping, output contract).

merge_raw_channels itself (the only rawpy-touching function) and slice/duplicate
of merged images need a real aligned trichrome triplet, which the repo does not
ship (example_raw/ has only 2 unrelated ARWs) — see spec/three-way-rgb-merge.md.
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core import ccr_merge  # noqa: E402


# --------------------------------------------------------------------------- #
# is_raw_path / RAW_EXTENSIONS
# --------------------------------------------------------------------------- #
def test_is_raw_path_accepts_every_raw_extension():
    for ext in ccr_merge.RAW_EXTENSIONS:
        assert ccr_merge.is_raw_path(f"C:/shots/frame{ext}")
        assert ccr_merge.is_raw_path(f"/shots/frame{ext.upper()}")  # case-insensitive


def test_is_raw_path_rejects_non_raw_and_fff_and_heic():
    for ext in (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".fff", ".heic", ".heif"):
        assert not ccr_merge.is_raw_path(f"frame{ext}"), ext


# --------------------------------------------------------------------------- #
# validate_merge_inputs
# --------------------------------------------------------------------------- #
def test_validate_empty_is_invalid():
    ok, msg = ccr_merge.validate_merge_inputs([])
    assert not ok and msg


def test_validate_three_and_six_raw_are_valid():
    ok3, _ = ccr_merge.validate_merge_inputs(["a.arw", "b.arw", "c.arw"])
    ok6, _ = ccr_merge.validate_merge_inputs([f"{n}.nef" for n in range(6)])
    assert ok3 and ok6


def test_validate_count_not_multiple_of_three_reports_the_count():
    ok, msg = ccr_merge.validate_merge_inputs(["a.arw", "b.arw", "c.arw", "d.arw"])
    assert not ok
    assert "4" in msg                       # the offending count appears


def test_validate_non_raw_present_lists_the_file():
    ok, msg = ccr_merge.validate_merge_inputs(["r.arw", "g.arw", "still.jpg"])
    assert not ok
    assert "still.jpg" in msg               # the non-RAW file is named


# --------------------------------------------------------------------------- #
# sort_for_merge / group_into_triplets
# --------------------------------------------------------------------------- #
def test_sort_for_merge_by_basename_ignoring_directory_and_case():
    paths = ["z/B.arw", "a/c.ARW", "m/a.arw"]
    assert [os.path.basename(p) for p in ccr_merge.sort_for_merge(paths)] == \
        ["a.arw", "B.arw", "c.ARW"]


def test_sort_for_merge_is_stable_for_basename_collisions_across_dirs():
    paths = ["b/img.arw", "a/img.arw"]
    out = ccr_merge.sort_for_merge(paths)
    # Deterministic: same basename -> ordered by the (normcased) full path.
    assert out == ccr_merge.sort_for_merge(list(reversed(paths)))
    assert [os.path.basename(p) for p in out] == ["img.arw", "img.arw"]


def test_group_into_triplets_of_six():
    paths = [f"f{n}.arw" for n in range(6)]
    triplets = ccr_merge.group_into_triplets(paths)
    assert triplets == [("f0.arw", "f1.arw", "f2.arw"),
                        ("f3.arw", "f4.arw", "f5.arw")]


def test_group_into_triplets_drops_trailing_remainder():
    assert ccr_merge.group_into_triplets(["a", "b", "c", "d"]) == [("a", "b", "c")]


# --------------------------------------------------------------------------- #
# bayer_channel_indices
# --------------------------------------------------------------------------- #
def test_bayer_indices_canonical_rggb():
    assert ccr_merge.bayer_channel_indices(b"RGBG") == (0, 1, 2)


def test_bayer_indices_permuted_desc():
    # BGGR: B at 0, G at 1, R at 3.
    assert ccr_merge.bayer_channel_indices(b"BGGR") == (3, 1, 0)


def test_bayer_indices_rejects_monochrome_and_non_rgb():
    for desc in (b"G", b"GRAY", b"CYGM"):       # monochrome / 4-colour, no R/B
        with pytest.raises(ValueError):
            ccr_merge.bayer_channel_indices(desc)


# --------------------------------------------------------------------------- #
# is_monochrome_sensor
# --------------------------------------------------------------------------- #
def test_monochrome_detected_by_num_colors():
    assert ccr_merge.is_monochrome_sensor(1, b"RGBG") is True
    assert ccr_merge.is_monochrome_sensor(1, b"G") is True


def test_monochrome_detected_by_grey_desc():
    for desc in (b"G", b"GRAY", b"GREY"):
        assert ccr_merge.is_monochrome_sensor(3, desc) is True, desc


def test_monochrome_detected_by_flat_rgbg_pattern():
    flat = np.zeros((2, 2), dtype=np.int32)            # all one colour index
    assert ccr_merge.is_monochrome_sensor(3, b"RGBG", flat) is True


def test_bayer_is_not_monochrome():
    normal = np.array([[0, 1], [3, 2]], dtype=np.int32)  # real RGGB pattern
    assert ccr_merge.is_monochrome_sensor(3, b"RGBG", normal) is False
    assert ccr_merge.is_monochrome_sensor(3, b"RGBG") is False   # no pattern given
    assert ccr_merge.is_monochrome_sensor(4, b"RGBE") is False   # 4-colour, not mono


# --------------------------------------------------------------------------- #
# extract_cfa_channel (crosstalk-free per-colour extraction from the mosaic)
# --------------------------------------------------------------------------- #
def _ramp(n=4):
    # value = row*10 + col, so each site's origin is identifiable
    return np.array([[i * 10 + j for j in range(n)] for i in range(n)], dtype=np.float32)


# RGGB tile: index 0=R@(0,0), 1=G@(0,1), 3=G2@(1,0), 2=B@(1,1); desc 'RGBG'.
_RGGB_COLORS = np.array([[0, 1, 0, 1],
                         [3, 2, 3, 2],
                         [0, 1, 0, 1],
                         [3, 2, 3, 2]])


def test_extract_channel_red_and_blue_are_single_site():
    m = _ramp(4)
    r = ccr_merge.extract_cfa_channel(m, _RGGB_COLORS, b"RGBG", "R")
    b = ccr_merge.extract_cfa_channel(m, _RGGB_COLORS, b"RGBG", "B")
    assert r.tolist() == [[0, 2], [20, 22]]      # R@(0,0)
    assert b.tolist() == [[11, 13], [31, 33]]    # B@(1,1)


def test_extract_channel_green_averages_the_two_green_sites():
    m = _ramp(4)
    g = ccr_merge.extract_cfa_channel(m, _RGGB_COLORS, b"RGBG", "G")
    # green sites: (0,1)->[[1,3],[21,23]] and (1,0)->[[10,12],[30,32]]; averaged
    assert np.allclose(g, [[5.5, 7.5], [25.5, 27.5]])


def test_extract_channel_green_average_when_both_greens_share_one_index():
    # Some cameras map both greens to index 1 (color_desc 'RGB', 3 colours).
    colors = np.array([[0, 1, 0, 1],
                       [1, 2, 1, 2],
                       [0, 1, 0, 1],
                       [1, 2, 1, 2]])
    m = _ramp(4)
    g = ccr_merge.extract_cfa_channel(m, colors, b"RGB", "G")
    # green index-1 phases (0,1) and (1,0) averaged — same result as RGGB above
    assert np.allclose(g, [[5.5, 7.5], [25.5, 27.5]])


def test_extract_channel_subtracts_black_per_index():
    m = _ramp(4)
    r = ccr_merge.extract_cfa_channel(m, _RGGB_COLORS, b"RGBG", "R",
                                      black_levels=[100, 200, 300, 200])
    assert r.tolist() == [[0 - 100, 2 - 100], [20 - 100, 22 - 100]]


def test_extract_channel_follows_actual_phase():
    # Shifted CFA phase: R no longer at (0,0).
    colors = np.array([[1, 0, 1, 0],
                       [2, 3, 2, 3],
                       [1, 0, 1, 0],
                       [2, 3, 2, 3]])
    m = _ramp(4)
    r = ccr_merge.extract_cfa_channel(m, colors, b"RGBG", "R")
    assert r.tolist() == [[1, 3], [21, 23]]      # R@(0,1)


def test_extract_channel_missing_colour_raises():
    colors = np.array([[0, 1], [1, 0]])          # only R/G in the tile, no B
    with pytest.raises(ValueError):
        ccr_merge.extract_cfa_channel(np.zeros((2, 2)), colors, b"RGBG", "B")


# --------------------------------------------------------------------------- #
# combine_channels (the pure merge core)
# --------------------------------------------------------------------------- #
def _const_plane(h, w, value):
    return np.full((h, w), value, dtype=np.float32)


def test_combine_picks_correct_frame_and_channel():
    # Each frame's chosen plane is a distinct constant; no scaling (wl=65535).
    r = _const_plane(8, 6, 100)
    g = _const_plane(8, 6, 200)
    b = _const_plane(8, 6, 300)
    out = ccr_merge.combine_channels(r, g, b, [65535, 65535, 65535])
    assert out.shape == (8, 6, 3)
    assert np.all(out[..., 0] == 100)        # R from the red frame's R-plane
    assert np.all(out[..., 1] == 200)        # G from the green frame's G-plane
    assert np.all(out[..., 2] == 300)        # B from the blue frame's B-plane


def test_combine_applies_per_frame_white_level_scaling():
    # white_level 32767 -> scale ~2x toward full range; 65535 -> unchanged.
    r = _const_plane(4, 4, 100)
    g = _const_plane(4, 4, 100)
    b = _const_plane(4, 4, 100)
    out = ccr_merge.combine_channels(r, g, b, [32767, 65535, 65535])
    assert int(out[0, 0, 0]) == round(100 * 65535 / 32767)   # ~200
    assert int(out[0, 0, 1]) == 100                          # unchanged
    assert out[0, 0, 0] > out[0, 0, 1]


def test_combine_clips_to_uint16_range():
    r = _const_plane(2, 2, 60000)
    out = ccr_merge.combine_channels(r, r, r, [30000, 65535, 65535])  # R overshoots
    assert out.dtype == np.uint16
    assert out[..., 0].max() == 65535        # clipped, no wrap-around


def test_combine_crops_size_mismatch_to_common_min():
    r = _const_plane(8, 6, 10)
    g = _const_plane(8, 7, 20)               # one column wider
    b = _const_plane(9, 6, 30)               # one row taller
    out = ccr_merge.combine_channels(r, g, b, [65535, 65535, 65535])
    assert out.shape == (8, 6, 3)            # common (min H, min W); no raise


def test_combine_requires_three_white_levels():
    p = _const_plane(2, 2, 1)
    with pytest.raises(ValueError):
        ccr_merge.combine_channels(p, p, p, [65535, 65535])
