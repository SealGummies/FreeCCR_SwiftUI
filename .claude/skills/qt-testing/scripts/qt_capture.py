#!/usr/bin/env python
"""Qt widget screenshot capture utility (FreeCCR).

Captures screenshots of Qt widgets without displaying them on screen, so an
agent can "see" the FreeCCR UI and verify rendering, layout, and theming.

Adapted from the `qt-testing` skill in talmolab/sleap. FreeCCR-specific bits:
  * `setup_freeccr_path()` puts the repo root and `src/` on sys.path so the
    app's `widgets.*` / `core.*` / `ui.*` imports resolve.
  * Screenshots are anchored to `<repo>/scratch/.qt-screenshots/` regardless of
    the current working directory.
  * `init_qt(offscreen=True)` can force the `offscreen` Qt platform, matching the
    convention used by FreeCCR's own headless tests.
"""

import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtCore import Qt

# Repo root, computed from this file's location:
#   <repo>/.claude/skills/qt-testing/scripts/qt_capture.py
REPO_ROOT = Path(__file__).resolve().parents[4]

# Output directory (anchored to the repo, not the cwd).
OUTPUT_DIR = REPO_ROOT / "scratch" / ".qt-screenshots"


def setup_freeccr_path() -> Path:
    """Put the FreeCCR repo root and ``src/`` on sys.path.

    Call this before importing any FreeCCR module (``widgets.*``, ``core.*``,
    ``ui.*``). Idempotent. Returns the repo root.
    """
    for p in (str(REPO_ROOT), str(REPO_ROOT / "src")):
        if p not in sys.path:
            sys.path.insert(0, p)
    return REPO_ROOT


def init_qt(offscreen: bool = False) -> QApplication:
    """Initialize the Qt application (required once per process).

    Args:
        offscreen: Force ``QT_QPA_PLATFORM=offscreen`` (matches FreeCCR's
            headless tests). Only takes effect if no QApplication exists yet and
            the platform has not already been set. The default Windows platform
            also renders correctly via ``WA_DontShowOnScreen``.
    """
    setup_freeccr_path()
    app = QApplication.instance()
    if app is None:
        if offscreen and "QT_QPA_PLATFORM" not in os.environ:
            os.environ["QT_QPA_PLATFORM"] = "offscreen"
        app = QApplication(sys.argv)
    return app


def timestamp() -> str:
    """Generate timestamp for filename."""
    return datetime.now().strftime("%Y-%m-%d.%H-%M-%S")


def capture_widget(
    widget: QWidget,
    description: str,
    output_dir: Optional[Path] = None,
) -> Path:
    """
    Capture a screenshot of a widget without displaying it on screen.

    Args:
        widget: The QWidget to capture
        description: Short description for filename (use underscores, no spaces)
        output_dir: Override output directory (default: <repo>/scratch/.qt-screenshots)

    Returns:
        Path to saved screenshot
    """
    out = output_dir or OUTPUT_DIR
    out.mkdir(parents=True, exist_ok=True)

    # Configure for invisible rendering
    widget.setAttribute(Qt.WA_DontShowOnScreen, True)
    widget.show()
    QApplication.processEvents()

    # Capture
    pixmap = widget.grab()
    if pixmap.isNull():
        raise RuntimeError("Failed to capture widget - pixmap is null")

    # Save with timestamp
    desc_clean = description.replace(" ", "_").replace("/", "-")
    filename = f"{timestamp()}_{desc_clean}.png"
    filepath = out / filename

    if not pixmap.save(str(filepath)):
        raise RuntimeError(f"Failed to save screenshot to {filepath}")

    # Don't close - caller may want to interact further
    widget.hide()

    return filepath


def capture_and_click(
    widget: QWidget,
    x: int,
    y: int,
    description: str,
) -> "tuple[Path, Optional[QWidget]]":
    """
    Click at coordinates and capture the result.

    Args:
        widget: Parent widget
        x, y: Coordinates to click (in widget coordinate space)
        description: Description for filename

    Returns:
        Tuple of (screenshot path, clicked widget or None)
    """
    widget.setAttribute(Qt.WA_DontShowOnScreen, True)
    widget.show()
    QApplication.processEvents()

    # Find and click widget at coordinates
    target = widget.childAt(x, y)
    if target is not None:
        if hasattr(target, "click"):
            target.click()
        elif hasattr(target, "toggle"):
            target.toggle()
        QApplication.processEvents()

    # Capture result
    path = capture_widget(widget, description)
    return path, target


# Self-test when run directly
if __name__ == "__main__":
    from PySide6.QtWidgets import QPushButton, QVBoxLayout, QLabel

    app = init_qt()

    # Create test widget
    widget = QWidget()
    widget.setWindowTitle("Qt Capture Test")
    widget.setFixedSize(300, 150)

    layout = QVBoxLayout(widget)
    layout.addWidget(QLabel("Qt Capture Test Widget"))
    btn = QPushButton("Test Button")
    layout.addWidget(btn)

    # Capture it
    path = capture_widget(widget, "self_test")
    print(f"Captured: {path}")

    widget.close()
    print("Self-test complete!")
