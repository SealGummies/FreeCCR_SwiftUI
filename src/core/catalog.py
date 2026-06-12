"""
Persistent per-file edit catalog.

Remembers how each processed file was converted, sliced, cropped and
adjusted, and restores that state when the file is opened again — including
re-running the conversion with the recorded inputs so the preview comes back
exactly as the user left it. Stored as JSON in the user's app-data folder;
entries are validated against the file's size/mtime so edits recorded for a
since-modified file are ignored rather than misapplied.
"""
import json
import logging
import os
import tempfile

CATALOG_VERSION = 1


def default_catalog_path() -> str:
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    folder = os.path.join(base, "FreeCCR")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "catalog.json")


def load_catalog(path: str = None) -> dict:
    path = path or default_catalog_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and data.get("version") == CATALOG_VERSION:
            return data
    except FileNotFoundError:
        pass
    except Exception as e:
        logging.warning(f"Could not read catalog {path}: {e}")
    return {"version": CATALOG_VERSION, "files": {}}


def save_catalog(catalog: dict, path: str = None) -> None:
    path = path or default_catalog_path()
    try:
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(catalog, f)
        os.replace(tmp, path)  # atomic: a crash can't corrupt the catalog
    except Exception as e:
        logging.warning(f"Could not write catalog {path}: {e}")


def _file_key(file_path: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(file_path)))


def _file_signature(file_path: str) -> dict:
    st = os.stat(file_path)
    return {"size": st.st_size, "mtime": st.st_mtime}


def _ci_to_json(ci):
    if ci is None:
        return None
    out = dict(ci)
    if out.get("ref") is not None:
        out["ref"] = list(out["ref"])
    if out.get("bw") is not None:
        out["bw"] = [list(out["bw"][0]), list(out["bw"][1])]
    for key in ("p_lo", "p_hi", "od"):
        if out.get(key) is not None:
            out[key] = list(out[key])
    return out


def _ci_from_json(ci):
    if not ci:
        return None
    out = dict(ci)
    if out.get("ref") is not None:
        out["ref"] = tuple(out["ref"])
    if out.get("bw") is not None:
        out["bw"] = (tuple(out["bw"][0]), tuple(out["bw"][1]))
    for key in ("p_lo", "p_hi", "od"):
        if out.get(key) is not None:
            out[key] = tuple(out[key])
    return out


def serialize_image(img) -> dict:
    """Everything needed to bring a CCRImage back to its current state."""
    return {
        "display_name": img.display_name,
        "source_ops": [[int(rot), list(region)] for rot, region in img.source_ops],
        "converted": bool(img.converted),
        "conversion_inputs": _ci_to_json(img.conversion_inputs),
        "adjustment_settings": dict(img.adjustment_settings),
        "crop_rect": list(img.crop_rect) if img.crop_rect else None,
        "crop_angle": float(img.crop_angle or 0.0),
        "rotation_angle": int(img.rotation_angle),
        "fine_rotation_angle": int(img.fine_rotation_angle),
        "horizontal_mirrored": bool(img.horizontal_mirrored),
        "vertical_mirrored": bool(img.vertical_mirrored),
        "contrast_base": int(img.contrast_base),
        "temperature_base": int(img.temperature_base),
        "brightness_base": int(img.brightness_base),
        "tint_balance_factor": float(getattr(img, "tint_balance_factor", 1.0)),
        "reference_frame": list(img.reference_frame) if img.reference_frame else None,
    }


def update_for_images(images, path: str = None) -> None:
    """Write the current state of all loaded images into the catalog,
    grouped by source file (the slices of one file form one entry list, in
    list order). Entries for files not currently loaded are kept."""
    catalog = load_catalog(path)
    grouped = {}
    for img in images:
        grouped.setdefault(_file_key(img.file_path), []).append(img)
    for fkey, imgs in grouped.items():
        try:
            signature = _file_signature(imgs[0].file_path)
        except OSError:
            continue
        catalog["files"][fkey] = {
            "signature": signature,
            "images": [serialize_image(im) for im in imgs],
        }
    save_catalog(catalog, path)


def entries_for_path(file_path: str, path: str = None):
    """Catalog entries for a file, or None when absent or when the file has
    changed since it was cataloged (stale edits must not be misapplied)."""
    catalog = load_catalog(path)
    record = catalog["files"].get(_file_key(file_path))
    if not record:
        return None
    try:
        signature = _file_signature(file_path)
    except OSError:
        return None
    stored = record.get("signature") or {}
    if (stored.get("size") != signature["size"]
            or abs(stored.get("mtime", 0) - signature["mtime"]) > 1.0):
        return None
    return record.get("images") or None


def create_images_for_path(file_path: str, path: str = None) -> list:
    """Create the CCRImage(s) for a file, restoring cataloged state when
    available (slices, conversion, adjustments, crop, orientation). Falls
    back to a plain load on any failure."""
    from core.ccr_image import CCRImage
    entries = entries_for_path(file_path, path)
    if not entries:
        return [CCRImage(file_path)]
    images = []
    for state in entries:
        try:
            images.append(_restore_image(file_path, state))
        except Exception as e:
            logging.warning(f"Catalog restore failed for {file_path}: {e}")
    return images or [CCRImage(file_path)]


def _restore_image(file_path: str, state: dict):
    from core.ccr_image import CCRImage
    source_ops = [(int(rot), tuple(region))
                  for rot, region in (state.get("source_ops") or [])]
    img = CCRImage(
        file_path,
        adjustment_settings=dict(state.get("adjustment_settings") or {}),
        rotation_angle=state.get("rotation_angle", 0),
        fine_rotation_angle=state.get("fine_rotation_angle", 0),
        horizontal_mirrored=state.get("horizontal_mirrored", False),
        vertical_mirrored=state.get("vertical_mirrored", False),
        source_ops=source_ops,
        display_name=state.get("display_name"),
    )
    ref = state.get("reference_frame")
    img.reference_frame = tuple(ref) if ref else None
    crop = state.get("crop_rect")
    img.crop_rect = tuple(crop) if crop else None
    img.crop_angle = state.get("crop_angle", 0.0) or 0.0
    img.tint_balance_factor = state.get("tint_balance_factor",
                                        getattr(img, "tint_balance_factor", 1.0))

    ci = _ci_from_json(state.get("conversion_inputs"))
    if state.get("converted") and ci is not None:
        _replay_conversion(img, ci)

    # Bases AFTER the replay (the bw pipeline writes its own defaults)
    img.contrast_base = state.get("contrast_base", img.contrast_base)
    img.temperature_base = state.get("temperature_base", img.temperature_base)
    img.brightness_base = state.get("brightness_base", img.brightness_base)
    img.update_thumbnail_and_preview()
    return img


def _replay_conversion(img, ci) -> None:
    """Re-run the recorded conversion so the preview comes back exactly as
    it was. Uses the inputs captured at convert time, not live state."""
    from core.ccr_processor import (ccr_normalize_with_reference,
                                    ccr_normalize_with_bwpoint,
                                    apply_reference_normalization)
    mode = ci.get("mode")
    if mode == "ref":
        saved_fine = img.fine_rotation_angle
        saved_ref = img.reference_frame
        img.fine_rotation_angle = ci.get("fine_rot", 0)
        img.reference_frame = ci["ref"]
        try:
            processed = ccr_normalize_with_reference(img)
        finally:
            img.fine_rotation_angle = saved_fine
            img.reference_frame = saved_ref if saved_ref is not None else ci["ref"]
        img.resized_raw = processed
    elif mode == "ref_params":
        img.resized_raw = apply_reference_normalization(
            img.resized_raw, ci["p_lo"], ci["p_hi"], ci["od"])
    elif mode == "bw":
        saved_fine = img.fine_rotation_angle
        img.fine_rotation_angle = ci.get("fine_rot", 0)
        try:
            black_point, white_point = ci["bw"]
            processed = ccr_normalize_with_bwpoint(img, black_point, white_point)
        finally:
            img.fine_rotation_angle = saved_fine
        img.resized_raw = processed
    else:
        return
    img.converted = True
    img.conversion_inputs = ci
