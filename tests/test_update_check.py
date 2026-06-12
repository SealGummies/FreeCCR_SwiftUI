#!/usr/bin/env python3
"""
Tests for the startup update check: version parsing from tags and
git-describe strings, release payload extraction, and the show/skip rules.
No network involved — fetch_latest_release is exercised only for its
failure path.
"""

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils import update_check  # noqa: E402
from utils.update_check import (  # noqa: E402
    extract_release, fetch_latest_release, parse_version, should_offer_update,
    RELEASES_PAGE_URL)


class TestParseVersion:
    def test_plain_tag(self):
        assert parse_version("v0.1.0") == (0, 1, 0)

    def test_no_v_prefix(self):
        assert parse_version("1.2.3") == (1, 2, 3)

    def test_git_describe_suffix_ignored(self):
        assert parse_version("v0.0.1-2-g9ab2c6f") == (0, 0, 1)

    def test_missing_patch_defaults_to_zero(self):
        assert parse_version("v1.2") == (1, 2, 0)

    def test_whitespace_tolerated(self):
        assert parse_version("  v0.1.0\n") == (0, 1, 0)

    def test_garbage_returns_none(self):
        assert parse_version("not-a-version") is None

    def test_empty_and_none(self):
        assert parse_version("") is None
        assert parse_version(None) is None


class TestExtractRelease:
    def test_normal_payload(self):
        release = extract_release({
            "tag_name": "v0.2.0",
            "html_url": "https://github.com/toonoumi/FreeCCR/releases/tag/v0.2.0",
        })
        assert release == {
            "tag": "v0.2.0",
            "version": (0, 2, 0),
            "url": "https://github.com/toonoumi/FreeCCR/releases/tag/v0.2.0",
        }

    def test_missing_url_falls_back_to_releases_page(self):
        release = extract_release({"tag_name": "v0.2.0"})
        assert release["url"] == RELEASES_PAGE_URL

    def test_unparseable_tag_returns_none(self):
        assert extract_release({"tag_name": "latest"}) is None

    def test_missing_tag_returns_none(self):
        assert extract_release({"html_url": "x"}) is None

    def test_non_dict_returns_none(self):
        assert extract_release(None) is None
        assert extract_release([1, 2]) is None


class TestShouldOfferUpdate:
    def test_newer_patch_offers(self):
        assert should_offer_update((0, 1, 1), (0, 1, 0))

    def test_equal_version_silent(self):
        assert not should_offer_update((0, 1, 0), (0, 1, 0))

    def test_older_release_silent(self):
        assert not should_offer_update((0, 1, 0), (0, 2, 0))

    def test_unknown_versions_silent(self):
        assert not should_offer_update(None, (0, 1, 0))
        assert not should_offer_update((0, 2, 0), None)

    def test_skipped_version_suppresses_itself(self):
        assert not should_offer_update((0, 1, 3), (0, 1, 0),
                                       skipped_version=(0, 1, 3))

    def test_skipped_suppresses_newer_patch(self):
        # User skipped 0.1.3 → 0.1.4 stays silent (same major.minor)
        assert not should_offer_update((0, 1, 4), (0, 1, 0),
                                       skipped_version=(0, 1, 3))

    def test_skipped_does_not_suppress_minor_bump(self):
        # ... but 0.2.0 is a "big version" change and shows again
        assert should_offer_update((0, 2, 0), (0, 1, 0),
                                   skipped_version=(0, 1, 3))

    def test_skipped_does_not_suppress_major_bump(self):
        assert should_offer_update((1, 0, 0), (0, 1, 0),
                                   skipped_version=(0, 9, 9))

    def test_skip_never_hides_when_nothing_newer_anyway(self):
        assert not should_offer_update((0, 1, 0), (0, 1, 0),
                                       skipped_version=(0, 1, 0))

    def test_skipped_older_than_latest_minor_line(self):
        # Skipped 0.1.x long ago; a 0.3.x release must still prompt
        assert should_offer_update((0, 3, 2), (0, 1, 0),
                                   skipped_version=(0, 1, 9))


class TestFetchFailurePath:
    def test_network_failure_returns_none(self, monkeypatch):
        def boom(*args, **kwargs):
            raise OSError("no network")
        monkeypatch.setattr(update_check.urllib.request, "urlopen", boom)
        assert fetch_latest_release(timeout=0.01) is None

    def test_bad_json_returns_none(self, monkeypatch):
        class FakeResponse:
            def read(self):
                return b"<html>rate limited</html>"

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        monkeypatch.setattr(update_check.urllib.request, "urlopen",
                            lambda *a, **k: FakeResponse())
        assert fetch_latest_release(timeout=0.01) is None


class TestWorkerAndDialog:
    """Qt-side behavior: the daemon-thread worker and the update dialog."""

    @pytest.fixture(autouse=True)
    def qt_app(self):
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication(sys.argv[:1])

    def test_worker_emits_release(self, monkeypatch):
        import ui.main_window as mw
        release = {"tag": "v9.9.9", "version": (9, 9, 9), "url": "u"}
        monkeypatch.setattr(mw, "fetch_latest_release", lambda timeout: release)
        worker = mw.UpdateCheckWorker()
        received = []
        worker.finished.connect(received.append)
        worker._run()  # synchronous call; emit delivers directly
        assert received == [release]

    def test_worker_discards_overdue_result(self, monkeypatch):
        import ui.main_window as mw
        monkeypatch.setattr(mw, "fetch_latest_release",
                            lambda timeout: {"tag": "v9.9.9"})
        worker = mw.UpdateCheckWorker()
        monkeypatch.setattr(worker, "MAX_TOTAL_SECONDS", -1.0)
        received = []
        worker.finished.connect(received.append)
        worker._run()
        assert received == []

    def test_dialog_escapes_release_tag(self):
        from PySide6.QtWidgets import QLabel
        import ui.main_window as mw
        dialog = mw.UpdateAvailableDialog(
            None, {"tag": "v1.2.3-<rc1>", "version": (1, 2, 3), "url": "u"},
            "v0.1.0")
        label = dialog.findChild(QLabel)
        assert "&lt;rc1&gt;" in label.text()

    def test_dialog_result_codes(self):
        import ui.main_window as mw
        d = mw.UpdateAvailableDialog
        # REMIND must alias QDialog.Rejected so closing via X/Esc == remind
        from PySide6.QtWidgets import QDialog
        assert d.REMIND == int(QDialog.DialogCode.Rejected)
        assert len({d.REMIND, d.UPDATE, d.SKIP}) == 3


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
