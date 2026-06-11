"""
Pure filename-building logic for the export dialog: macro expansion,
sanitization, in-batch uniquification, and on-disk conflict resolution.

This module must stay free of Qt/cv2 imports so it remains testable headless.
"""
import os
import re
from datetime import datetime
from typing import Callable, List, Optional, Sequence

MACRO_RE = re.compile(r"\{(date|time|seq|name)\}")

# Windows-reserved filename characters plus ASCII control characters
_INVALID_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def expand_macros(template: str, *, base_name: str, seq: int, seq_width: int, now: datetime) -> str:
    """
    Expand {date}, {time}, {seq} and {name} macros in a prefix/suffix template.
    Unknown {...} tokens are left literal.

    Args:
        template (str): The prefix or suffix template
        base_name (str): Original file base name (no extension)
        seq (int): 1-based sequence number of this file in the batch
        seq_width (int): Zero-padding width for {seq}
        now (datetime): Timestamp used for {date} and {time}

    Returns:
        str: The expanded string
    """
    values = {
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H%M%S"),
        "seq": str(seq).zfill(seq_width),
        "name": base_name,
    }
    return MACRO_RE.sub(lambda m: values[m.group(1)], template)


def sanitize_filename(name: str) -> str:
    """
    Replace characters that are invalid in Windows filenames with underscores
    and strip trailing dots/spaces (invalid at the end of a Windows name).
    """
    return _INVALID_CHARS_RE.sub("_", name).rstrip(". ")


def build_filename(base_name: str, prefix: str, suffix: str, ext: str, *,
                   seq: int, seq_width: int, now: datetime) -> str:
    """
    Build a single output filename: expanded prefix + base name + expanded
    suffix + extension, sanitized for the filesystem.

    Args:
        ext (str): Extension including the leading dot, e.g. ".tiff"
    """
    stem = (
        expand_macros(prefix, base_name=base_name, seq=seq, seq_width=seq_width, now=now)
        + base_name
        + expand_macros(suffix, base_name=base_name, seq=seq, seq_width=seq_width, now=now)
    )
    stem = sanitize_filename(stem)
    if not stem:
        stem = base_name or "image"
    return stem + ext


def uniquify_in_batch(names: Sequence[str]) -> List[str]:
    """
    Make a list of filenames unique within itself (case-insensitive). The
    first occurrence keeps its name; later duplicates get -1, -2, ... before
    the extension. Always applied regardless of conflict policy, since a
    macro-only suffix (e.g. just {date}) would otherwise collide every file.
    """
    seen = set()
    result = []
    for name in names:
        key = os.path.normcase(name)
        if key not in seen:
            seen.add(key)
            result.append(name)
            continue
        stem, ext = os.path.splitext(name)
        n = 0
        while True:
            n += 1
            candidate = f"{stem}-{n}{ext}"
            candidate_key = os.path.normcase(candidate)
            if candidate_key not in seen:
                break
        seen.add(candidate_key)
        result.append(candidate)
    return result


def build_export_names(base_names: Sequence[str], prefix: str, suffix: str, ext: str,
                       now: Optional[datetime] = None) -> List[str]:
    """
    Build the full list of output filenames for a batch, expanding macros and
    guaranteeing in-batch uniqueness.

    Args:
        base_names: Original file base names (no extension), in export order
        ext (str): Extension including the leading dot, e.g. ".jpg"
        now: Timestamp for {date}/{time}; defaults to datetime.now()

    Returns:
        list[str]: Output filenames (no directory component)
    """
    if now is None:
        now = datetime.now()
    seq_width = max(3, len(str(len(base_names))))
    names = [
        build_filename(base, prefix, suffix, ext, seq=i + 1, seq_width=seq_width, now=now)
        for i, base in enumerate(base_names)
    ]
    return uniquify_in_batch(names)


def resolve_conflicts(names: Sequence[str], policy: str,
                      exists: Callable[[str], bool]) -> List[Optional[str]]:
    """
    Apply the file-conflict policy against existing files on disk.

    Args:
        names: In-batch-unique filenames (or full paths; `exists` receives
               the same strings)
        policy: "overwrite" | "skip" | "rename"
        exists: Predicate checking whether a name is already taken on disk
                (injected so tests don't need a real filesystem)

    Returns:
        list: Same order as input; entries are the (possibly renamed) name,
              or None when the file is skipped under the "skip" policy.
    """
    if policy == "overwrite":
        return list(names)
    if policy == "skip":
        return [None if exists(name) else name for name in names]
    if policy == "rename":
        claimed = {os.path.normcase(name) for name in names}
        result = []
        for name in names:
            if not exists(name):
                result.append(name)
                continue
            stem, ext = os.path.splitext(name)
            n = 0
            while True:
                n += 1
                candidate = f"{stem}-{n}{ext}"
                key = os.path.normcase(candidate)
                if key not in claimed and not exists(candidate):
                    break
            claimed.add(key)
            result.append(candidate)
        return result
    raise ValueError(f"Unknown conflict policy: {policy!r}")
