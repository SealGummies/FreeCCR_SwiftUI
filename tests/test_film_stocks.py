#!/usr/bin/env python3
"""
Tests for saved film-stock slopes (spec/film-stock-slopes.md).

A film stock is a per-channel DENSITY-slope triple S[c] = 1/log10(base/dense)
computed from a sampled B/W-point pair and saved under a name. In the
black-point-only conversion the selected stock's slopes replace the baked
scalar DEFAULT_DENSITY_SLOPE:
    out[c] = clip(S[c] * max(log10(base/img), 0), 0, 1)
The slopes ride conversion_inputs["slopes"] by value, so replay (zoom, export,
slice, catalog restore) reproduces the conversion even after the preset is
deleted. With slopes_bgr=None everything is byte-identical to the default
scalar path.

apply_bwpoint_normalization and the film_stocks store are pure (no Qt), so
these run headless.
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.ccr_processor import (  # noqa: E402
    apply_bwpoint_normalization,
    compute_density_slopes,
    DEFAULT_DENSITY_SLOPE,
)
from core.film_stocks import (  # noqa: E402
    decode_film_stocks,
    encode_film_stocks,
    find_film_stock,
    upsert_film_stock,
    remove_film_stock,
)

# The calibration black/white pair (BGR) used across the default-slope tests.
BASE_BGR = (11385.0, 14569.0, 7022.0)    # clear film base (high scan values)
WHITE_BGR = (760.0, 420.0, 117.0)        # dense area (low scan values)


@pytest.fixture(autouse=True)
def _force_legacy_full_range(monkeypatch):
    """Pin the LEGACY full-range inversion (base→0, dense→65535); the windowed
    working space is covered in test_working_space.py."""
    monkeypatch.setenv("FREECCR_WORKING_SPACE", "0")


def _img(pixels_bgr, dtype=np.uint16):
    """Build a (1, N, 3) image from a list of (B,G,R) tuples."""
    return np.array([pixels_bgr], dtype=dtype)


def _stock(name, slopes=(0.85, 0.65, 0.56), **extra):
    rec = {"name": name, "slopes_bgr": list(slopes)}
    rec.update(extra)
    return rec


# --- compute_density_slopes ------------------------------------------------

def test_slopes_match_formula():
    slopes = compute_density_slopes(BASE_BGR, WHITE_BGR)
    assert slopes is not None
    for c in range(3):
        expected = 1.0 / np.log10(BASE_BGR[c] / WHITE_BGR[c])
        assert abs(slopes[c] - expected) < 1e-9
        assert slopes[c] > 0


def test_slopes_reject_degenerate_pairs():
    # Any single bad channel rejects the whole pair.
    assert compute_density_slopes((100.0, 14569.0, 7022.0), WHITE_BGR) is None   # base < dense (B)
    assert compute_density_slopes(BASE_BGR, (760.0, 0.0, 117.0)) is None         # zero dense (G)
    assert compute_density_slopes((0.0, 14569.0, 7022.0), WHITE_BGR) is None     # zero base (B)
    assert compute_density_slopes(WHITE_BGR, WHITE_BGR) is None                  # base == dense
    assert compute_density_slopes((760.0001, 14569.0, 7022.0),
                                  (760.0, 420.0, 117.0)) is None                 # ~zero density range


def test_slopes_light_source_invariant_quantity():
    """The stored quantity itself is invariant to per-channel light scaling
    (both samples scale together when re-sampled under a new light)."""
    k = (1.7, 0.6, 2.3)
    scaled_base = tuple(b * kk for b, kk in zip(BASE_BGR, k))
    scaled_white = tuple(w * kk for w, kk in zip(WHITE_BGR, k))
    a = compute_density_slopes(BASE_BGR, WHITE_BGR)
    b = compute_density_slopes(scaled_base, scaled_white)
    np.testing.assert_allclose(a, b, rtol=1e-9)


# --- black-point-only inversion with stock slopes ---------------------------

def test_none_slopes_match_default_scalar():
    """slopes_bgr=None ≡ the scalar DEFAULT_DENSITY_SLOPE in every channel —
    the regression guarantee that Default keeps today's output."""
    px = [(3000, 2500, 900), (1500, 1200, 400), (500, 300, 120)]
    a = apply_bwpoint_normalization(_img(px), BASE_BGR, None)
    b = apply_bwpoint_normalization(_img(px), BASE_BGR, None,
                                    slopes_bgr=(DEFAULT_DENSITY_SLOPE,) * 3)
    np.testing.assert_array_equal(a, b)


def test_stock_slopes_map_calibration_pair_to_endpoints():
    """With the slopes computed FROM a pair, that pair's dense sample maps to
    ~white and its base to ~black — the stock reproduces its calibration."""
    slopes = compute_density_slopes(BASE_BGR, WHITE_BGR)
    img = _img([tuple(map(round, BASE_BGR)), tuple(map(round, WHITE_BGR))])
    out = apply_bwpoint_normalization(img, BASE_BGR, None,
                                      slopes_bgr=slopes).astype(int)
    assert np.all(out[0, 0] < 3), f"base expected ~black, got {out[0, 0]}"
    assert np.all(out[0, 1] >= 65400), f"dense expected ~white, got {out[0, 1]}"


def test_stock_slopes_match_density_formula():
    """out[c] = clip(S[c] * max(log10(base/img), 0), 0, 1) * 65535."""
    slopes = compute_density_slopes(BASE_BGR, WHITE_BGR)
    px = [(3000, 2500, 900), (1500, 1200, 400), (500, 300, 120)]
    out = apply_bwpoint_normalization(_img(px), BASE_BGR, None,
                                      slopes_bgr=slopes).astype(int)
    for i, p in enumerate(px):
        for c in range(3):
            d = max(float(np.log10(BASE_BGR[c] / max(float(p[c]), 1.0))), 0.0)
            expected = int(np.clip(slopes[c] * d, 0.0, 1.0) * 65535.0)
            assert abs(out[0, i, c] - expected) <= 1, (i, c, out[0, i, c], expected)


def test_stock_slopes_light_source_invariant_output():
    """Re-sampling the black point under a new light (per-channel factor k on
    base AND img) keeps the output identical for the SAME saved slopes — the
    core promise: slopes travel across sessions, the black point adapts."""
    slopes = compute_density_slopes(BASE_BGR, WHITE_BGR)
    base = np.array(BASE_BGR, dtype=np.float32)
    px = np.array([3000.0, 2500.0, 900.0], dtype=np.float32)
    k = np.array([1.7, 0.6, 2.3], dtype=np.float32)
    out1 = apply_bwpoint_normalization(np.array([[px]], dtype=np.float32),
                                       tuple(base), None,
                                       slopes_bgr=slopes).astype(int)
    out2 = apply_bwpoint_normalization(np.array([[px * k]], dtype=np.float32),
                                       tuple(base * k), None,
                                       slopes_bgr=slopes).astype(int)
    assert np.all(np.abs(out1 - out2) <= 1), (out1, out2)


def test_stock_slopes_actually_differ_from_default():
    """Sanity: this pair's slopes are NOT the default scalar, so the preset
    visibly changes the rendering (otherwise the feature would be a no-op)."""
    slopes = compute_density_slopes(BASE_BGR, WHITE_BGR)
    assert any(abs(s - DEFAULT_DENSITY_SLOPE) > 1e-3 for s in slopes)
    px = [(3000, 2500, 900)]
    a = apply_bwpoint_normalization(_img(px), BASE_BGR, None)
    b = apply_bwpoint_normalization(_img(px), BASE_BGR, None, slopes_bgr=slopes)
    assert not np.array_equal(a, b)


# --- conversion_inputs snapshot round-trip ----------------------------------

def test_catalog_roundtrip_with_slopes():
    from core.catalog import _ci_to_json, _ci_from_json
    slopes = compute_density_slopes(BASE_BGR, WHITE_BGR)
    ci = {"mode": "bw", "bw": (BASE_BGR, None), "fine_rot": 0,
          "density": True, "slopes": slopes}
    j = _ci_to_json(ci)
    assert j["slopes"] == list(slopes)          # JSON-safe list
    back = _ci_from_json(j)
    assert back["slopes"] == tuple(slopes)      # tuple restored
    assert back["bw"][1] is None


def test_catalog_roundtrip_legacy_without_slopes():
    """Legacy records (pre-feature) have no 'slopes' key and must replay with
    the default scalar — ci.get('slopes') is None end to end."""
    from core.catalog import _ci_to_json, _ci_from_json
    ci = {"mode": "bw", "bw": (BASE_BGR, None), "fine_rot": 0}
    back = _ci_from_json(_ci_to_json(ci))
    assert back.get("slopes") is None


# --- film_stocks store -------------------------------------------------------

def test_decode_corrupt_or_wrong_shape_is_empty():
    assert decode_film_stocks("") == []
    assert decode_film_stocks(None) == []
    assert decode_film_stocks("not json {") == []
    assert decode_film_stocks('{"name": "x"}') == []      # not a list


def test_decode_drops_bad_records_keeps_good():
    good = _stock("Portra 400")
    bad = [
        {"slopes_bgr": [1, 1, 1]},                        # no name
        {"name": "  ", "slopes_bgr": [1, 1, 1]},          # blank name
        {"name": "neg", "slopes_bgr": [1, -1, 1]},        # non-positive slope
        {"name": "short", "slopes_bgr": [1, 1]},          # not a triple
        {"name": "nan", "slopes_bgr": [1, float("nan"), 1]},
        {"name": "str", "slopes_bgr": "abc"},
        "not-a-dict",
    ]
    out = decode_film_stocks(encode_film_stocks([good]) )
    assert [s["name"] for s in out] == ["Portra 400"]
    import json
    out = decode_film_stocks(json.dumps([good] + bad))
    assert [s["name"] for s in out] == ["Portra 400"]


def test_decode_dedups_case_insensitive_and_sorts():
    import json
    text = json.dumps([_stock("portra 400"), _stock("Ektar 100"),
                       _stock("PORTRA 400")])   # dup keeps first
    out = decode_film_stocks(text)
    assert [s["name"] for s in out] == ["Ektar 100", "portra 400"]


def test_roundtrip_unicode_and_provenance():
    stock = _stock("フジ 400H", black_bgr=list(BASE_BGR),
                   white_bgr=list(WHITE_BGR), created="2026-07-02")
    out = decode_film_stocks(encode_film_stocks([stock]))
    assert out[0]["name"] == "フジ 400H"
    assert out[0]["black_bgr"] == list(BASE_BGR)
    assert out[0]["white_bgr"] == list(WHITE_BGR)
    assert out[0]["created"] == "2026-07-02"


def test_upsert_adds_replaces_and_sorts():
    stocks = upsert_film_stock([], _stock("Portra 400", (0.8, 0.8, 0.8)))
    stocks = upsert_film_stock(stocks, _stock("Ektar 100"))
    assert [s["name"] for s in stocks] == ["Ektar 100", "Portra 400"]
    # Case-insensitive replace: one record survives, with the new values.
    stocks = upsert_film_stock(stocks, _stock("PORTRA 400", (0.5, 0.5, 0.5)))
    assert len(stocks) == 2
    replaced = find_film_stock(stocks, "portra 400")
    assert replaced["name"] == "PORTRA 400"
    assert replaced["slopes_bgr"] == [0.5, 0.5, 0.5]
    with pytest.raises(ValueError):
        upsert_film_stock(stocks, {"name": "bad", "slopes_bgr": [0, 0, 0]})


def test_remove_and_find():
    stocks = upsert_film_stock([], _stock("Portra 400"))
    assert find_film_stock(stocks, "  portra 400 ") is not None
    assert find_film_stock(stocks, "") is None
    assert find_film_stock(stocks, "Ektar 100") is None
    assert remove_film_stock(stocks, "nope") == stocks     # unknown → no-op
    assert remove_film_stock(stocks, "PORTRA 400") == []


# --- selection is per roll/session -------------------------------------------
def test_new_batch_resets_film_stock_selection():
    # A stock chosen for one roll must not silently convert the next: loading
    # a new batch clears the backend selection (the panel combo is reset by
    # MainWindow on the same load; see spec §4.4). Mirrors the B/W-point reset.
    from core.ccr_backend import ccr_backend
    saved = (ccr_backend.film_stock_name, ccr_backend.film_stock_slopes,
             list(ccr_backend.images), list(ccr_backend.file_paths or []))
    try:
        ccr_backend.set_film_stock("Portra 400", (0.8, 0.7, 0.6))
        assert ccr_backend.film_stock_slopes is not None
        ccr_backend.load_images_from_files([])   # a fresh (empty) batch
        assert ccr_backend.film_stock_name is None
        assert ccr_backend.film_stock_slopes is None
    finally:
        (ccr_backend.film_stock_name, ccr_backend.film_stock_slopes,
         ccr_backend.images, ccr_backend.file_paths) = saved


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
