"""User-facing reasons for files that failed to load.

A file whose decode fails used to disappear from the import with nothing but a
stdout print: `read_image` returns None, `CCRImage.__init__` raises, the loader
catches it and drops the file. A built exe/app has no console, so the file
simply never appeared and the user was given no reason.

The loader now records `(path, reason)` per failed file and the UI shows one
grouped summary after the batch. This module owns the classification and the
message text, kept pure and Qt-free so both are unit-testable without a display.
"""
import os
import textwrap

# QMessageBox does not word-wrap plain text — it widens the dialog to fit the
# longest line. The reasons are full sentences, so wrap them here.
_WRAP = 76

# Extensions read through rawpy/LibRaw — mirrors the branch list in
# ccr_image.read_image (see ccr_image.py, the `ext in [...]` RAW test).
RAW_EXTS = {".cr3", ".cr2", ".nef", ".arw", ".dng", ".rw2", ".orf", ".raf",
            ".srw", ".pef", ".3fr"}

# LibRaw reports an undecodable RAW differently depending on version: 0.21.x
# opens the file, reports its size, then dies during unpack ("Data error or
# unsupported file format" / "data corrupted at N"); 0.22+ rejects it up front
# ("Unsupported file format or not RAW file"). Match either shape.
_LIBRAW_UNSUPPORTED = ("unsupported file format", "data error",
                       "data corrupted", "not raw file")

# Nikon High Efficiency (HE/HE*) NEF — intoPIX TicoRAW. LibRaw decodes it only
# in commercial builds licensed with the intoPIX SDK, so no rawpy release can
# open these; rawspeed/darktable have the same gap. DNG Converter is the way
# out, but only below DNG 1.7: at Camera Raw 16.0+ compatibility it writes
# JPEG XL DNGs that LibRaw 0.21.x also cannot read.
HE_NEF_REASON = (
    "RAW decode failed — most likely a Nikon High Efficiency (HE/HE★) NEF. "
    "No open-source decoder supports that compression. Convert the file with "
    "Adobe DNG Converter (Preferences → Compatibility: Camera Raw 15.3 or "
    "earlier) and open the DNG instead, or shoot Lossless compressed."
)
RAW_UNSUPPORTED_REASON = (
    "RAW decode failed — this camera's format or compression is not supported "
    "by the RAW decoder in this build, or the file is damaged."
)
MISSING_REASON = "File not found — it may have been moved or renamed."
PERMISSION_REASON = "Permission denied — the file could not be opened."

# Terse equivalents for the tethering hint strip, which shows one line inline
# rather than a dialog and cannot carry the full explanation.
SHORT_REASONS = {
    HE_NEF_REASON: "Nikon HE NEF — no open-source decoder; convert to DNG",
    RAW_UNSUPPORTED_REASON: "RAW format unsupported or file damaged",
    MISSING_REASON: "file not found",
    PERMISSION_REASON: "permission denied",
}


def _messages(error):
    """Every message in an exception's cause/context chain, lower-cased.

    The LibRaw error is not what the loader catches: read_image swallows it and
    returns None, and CCRImage.__init__ raises a ValueError chained onto it. The
    classification has to see through that, so walk the chain rather than
    looking only at the outermost exception.
    """
    out, seen, cur = [], set(), error
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        out.append(f"{type(cur).__name__}: {cur}".lower())
        cur = cur.__cause__ or cur.__context__
    return out


def _has_type(error, exc_type):
    seen, cur = set(), error
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, exc_type):
            return True
        cur = cur.__cause__ or cur.__context__
    return False


def _root_cause(error):
    """The deepest exception in the chain — the one that actually knows what
    went wrong. The outermost is CCRImage's "Could not read image from file",
    which only restates the path and explains nothing."""
    seen, cur, last = set(), error, error
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        last = cur
        cur = cur.__cause__ or cur.__context__
    return last


def describe_load_failure(file_path, error, short: bool = False) -> str:
    """One user-facing sentence explaining why `file_path` could not load.
    `short` returns the terse form for the inline tethering hint.

    Never raises: a reason is only ever cosmetic, and a load that already failed
    must not fail again while being reported.
    """
    reason = _describe(file_path, error)
    return SHORT_REASONS.get(reason, reason) if short else reason


def _describe(file_path, error) -> str:
    try:
        ext = os.path.splitext(file_path or "")[1].lower()
        # Ask the filesystem rather than trusting the exception type: rawpy
        # reports a missing file as LibRawIOError("Input/output error"), not
        # FileNotFoundError, so type sniffing alone misses the commonest case.
        if _has_type(error, FileNotFoundError) or (
                file_path and not os.path.exists(file_path)):
            return MISSING_REASON
        if _has_type(error, PermissionError):
            return PERMISSION_REASON
        text = " ".join(_messages(error))
        if ext in RAW_EXTS and any(s in text for s in _LIBRAW_UNSUPPORTED):
            # HE is the overwhelmingly common cause for a NEF LibRaw won't
            # touch, but a damaged file lands here too — hence "most likely".
            return HE_NEF_REASON if ext == ".nef" else RAW_UNSUPPORTED_REASON
        root = _root_cause(error)
        detail = str(root).strip()
        if not detail:
            detail = type(root).__name__ if root is not None else ""
        return f"Could not be read: {detail}" if detail else "Could not be read."
    except Exception:
        return "Could not be read."


def format_load_failures(failures, max_per_reason: int = 8) -> str:
    """Build the summary dialog body from [(path, reason), ...].

    Failures are grouped by reason because the realistic case is a whole roll
    sharing one cause (36 HE NEFs from the same camera) — listing them
    individually would bury the explanation under filenames.
    """
    if not failures:
        return ""
    groups, order = {}, []
    for path, reason in failures:
        if reason not in groups:
            groups[reason] = []
            order.append(reason)
        groups[reason].append(os.path.basename(path))

    n = len(failures)
    parts = [f"{n} {'file' if n == 1 else 'files'} could not be loaded:"]
    for reason in order:
        names = groups[reason]
        shown = names[:max_per_reason]
        listing = "\n".join(f"    • {name}" for name in shown)
        if len(names) > len(shown):
            listing += f"\n    • …and {len(names) - len(shown)} more"
        parts.append(f"\n{textwrap.fill(reason, _WRAP)}\n{listing}")
    return "\n".join(parts)
