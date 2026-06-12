"""
Startup update check against GitHub releases.

Pure logic (version parsing, newer/skip rules) is kept separate from the
network fetch so it stays unit-testable. The fetch uses only the stdlib
(urllib) with a short timeout and is run from a worker thread by the UI.
"""
import json
import logging
import re
import urllib.request

GITHUB_REPO = "toonoumi/FreeCCR"
RELEASES_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
RELEASES_PAGE_URL = f"https://github.com/{GITHUB_REPO}/releases/latest"
REPO_URL = f"https://github.com/{GITHUB_REPO}"

_VERSION_RE = re.compile(r"v?(\d+)\.(\d+)(?:\.(\d+))?")


def parse_version(text):
    """(major, minor, patch) from a tag or git-describe string like
    "v0.1.0" or "v0.0.1-2-g9ab2c6f"; None when unparseable."""
    if not text:
        return None
    match = _VERSION_RE.match(text.strip())
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3) or 0))


def extract_release(payload: dict):
    """Pick what we need out of a GitHub /releases/latest payload.
    Returns {"tag", "version", "url"} or None."""
    if not isinstance(payload, dict):
        return None
    tag = payload.get("tag_name") or ""
    version = parse_version(tag)
    if version is None:
        return None
    return {"tag": tag, "version": version,
            "url": payload.get("html_url") or RELEASES_PAGE_URL}


def fetch_latest_release(timeout: float = 2.0):
    """Latest release info from GitHub, or None on any failure. Blocking —
    call from a worker thread."""
    try:
        request = urllib.request.Request(
            RELEASES_API_URL,
            headers={"Accept": "application/vnd.github+json",
                     "User-Agent": "FreeCCR-update-check"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return extract_release(payload)
    except Exception as e:
        logging.info(f"Update check skipped: {e}")
        return None


def should_offer_update(latest_version, current_version, skipped_version=None) -> bool:
    """Whether to show the update dialog.

    - Only when the release is strictly newer than the running version.
    - When the user skipped a version, stay silent until a release with a
      bigger MAJOR or MINOR number than the skipped one appears (patch
      releases beyond the skipped version don't re-prompt).
    """
    if latest_version is None or current_version is None:
        return False
    if latest_version <= current_version:
        return False
    if skipped_version is not None:
        if latest_version[:2] <= skipped_version[:2]:
            return False
    return True
