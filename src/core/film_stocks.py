"""Named film-stock slope presets for the black-point-only conversion.

A "film stock" is a per-channel DENSITY-slope triple saved from a sampled
B/W-point pair (compute_density_slopes) under a user-chosen name. Selecting a
stock makes _default_slope_invert use its slopes instead of the baked scalar
DEFAULT_DENSITY_SLOPE; the black point is still re-sampled per session / light
source, which is what keeps colour consistent across lights. See
spec/film-stock-slopes.md.

Pure JSON helpers only (no Qt): the sliders panel persists the encoded string
in QSettings under convert/film_stocks and the selected name under
convert/film_stock_selected. Records are validated defensively on decode so a
hand-edited or corrupt settings value can never crash the panel. Names are
unique case-insensitively; lists returned by the helpers are always new lists
sorted case-insensitively by name.
"""
import json
import math


def _triple(value, positive=False):
    """Coerce a 3-sequence of finite numbers to a [float]*3 list, else None.
    With positive=True, every component must also be > 0 (slopes)."""
    if isinstance(value, (str, bytes)) or value is None:
        return None
    try:
        vals = [float(x) for x in value]
    except (TypeError, ValueError):
        return None
    if len(vals) != 3 or any(not math.isfinite(x) for x in vals):
        return None
    if positive and any(x <= 0.0 for x in vals):
        return None
    return vals


def _valid_stock(record):
    """Validate/normalise one stock record into the canonical dict shape;
    None if unusable (no name, or no valid positive slope triple). The
    black/white source points are provenance only — optional."""
    if not isinstance(record, dict):
        return None
    name = str(record.get("name") or "").strip()
    slopes = _triple(record.get("slopes_bgr"), positive=True)
    if not name or slopes is None:
        return None
    return {
        "name": name,
        "slopes_bgr": slopes,
        "black_bgr": _triple(record.get("black_bgr")),
        "white_bgr": _triple(record.get("white_bgr")),
        "created": str(record.get("created") or ""),
    }


def _sorted_stocks(stocks):
    return sorted(stocks, key=lambda s: s["name"].casefold())


def decode_film_stocks(text):
    """JSON string → validated, name-sorted list of stock dicts. Corrupt JSON
    or a non-list top level → []; bad records are dropped individually and
    case-insensitive duplicate names keep the first occurrence."""
    try:
        data = json.loads(text) if text else []
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    out, seen = [], set()
    for record in data:
        stock = _valid_stock(record)
        if stock is None or stock["name"].casefold() in seen:
            continue
        seen.add(stock["name"].casefold())
        out.append(stock)
    return _sorted_stocks(out)


def encode_film_stocks(stocks):
    """Inverse of decode_film_stocks (records re-validated on the way out so a
    bad in-memory record can't poison the persisted store)."""
    clean = [s for s in map(_valid_stock, stocks or []) if s is not None]
    return json.dumps(clean)


def find_film_stock(stocks, name):
    """Case-insensitive lookup by name; None when absent."""
    key = str(name or "").strip().casefold()
    if not key:
        return None
    for stock in stocks or []:
        if stock["name"].casefold() == key:
            return stock
    return None


def upsert_film_stock(stocks, stock):
    """Insert `stock`, replacing any existing record with the same name
    (case-insensitive). Returns a NEW sorted list; raises ValueError on an
    invalid record so a caller bug surfaces instead of silently dropping."""
    stock = _valid_stock(stock)
    if stock is None:
        raise ValueError("invalid film stock record")
    key = stock["name"].casefold()
    out = [s for s in (stocks or []) if s["name"].casefold() != key]
    out.append(stock)
    return _sorted_stocks(out)


def remove_film_stock(stocks, name):
    """Drop the record matching `name` (case-insensitive); returns a NEW list.
    Unknown names are a no-op — the result is simply the surviving records."""
    key = str(name or "").strip().casefold()
    return [s for s in (stocks or []) if s["name"].casefold() != key]
