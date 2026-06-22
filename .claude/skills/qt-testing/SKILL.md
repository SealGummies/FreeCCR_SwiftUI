---
name: qt-testing
description: Capture and visually inspect FreeCCR's PySide6/Qt GUI widgets using screenshots. Use when asked to verify GUI rendering, test widget appearance, check layouts, theming/QSS, or visually inspect any FreeCCR widget (image preview, sliders panel, curve editor, thumbnail list, dust panel, export dialog, main window). Enables Claude to "see" the Qt interface by capturing offscreen screenshots and analyzing them with vision.
---

# Qt GUI Testing (FreeCCR)

Capture screenshots of FreeCCR's Qt widgets for visual inspection without
displaying windows on screen, then read the image back and critique it.

## Quick Start

```python
# Capture any widget
import sys
sys.path.insert(0, ".claude/skills/qt-testing")   # so `from scripts.qt_capture import ...` works
from scripts.qt_capture import capture_widget, init_qt, setup_freeccr_path

setup_freeccr_path()          # puts repo root + src/ on sys.path
app = init_qt()               # creates the QApplication

path = capture_widget(my_widget, "description_here")
# Then read the screenshot with the Read tool
```

## Core Script

Run `scripts/qt_capture.py` or import helpers from it:

```bash
# Standalone self-test (renders a throwaway widget, saves a PNG)
python .claude/skills/qt-testing/scripts/qt_capture.py
```

Helpers exposed by `scripts/qt_capture.py`:

- `setup_freeccr_path()` — add the repo root and `src/` to `sys.path` (call before importing FreeCCR modules).
- `init_qt(offscreen=False)` — create/return the `QApplication`. Pass `offscreen=True` to force the `offscreen` Qt platform (matches FreeCCR's headless tests); the default Windows platform also works.
- `capture_widget(widget, "desc")` — render offscreen and save a PNG, returns the path.
- `capture_and_click(widget, x, y, "desc")` — click the child at `(x, y)`, then capture.

## Output Location

All screenshots save to: `<repo>/scratch/.qt-screenshots/` (git-ignored).

Naming: `{YYYY-MM-DD.HH-MM-SS}_{description}.png`

## Workflow

1. Create/obtain the FreeCCR widget to test (see examples below)
2. Call `capture_widget(widget, "description")`
3. Read the saved screenshot with the Read tool
4. Analyze with vision to verify correctness (layout, spacing, theming, labels)

## FreeCCR Widget Cheat-Sheet

Import after `setup_freeccr_path()`. None require images loaded; the
`ccr_backend` singleton is created on import.

```python
from core.ccr_backend import ccr_backend          # ensures the global singleton exists

from ui.main_window import MainWindow              # MainWindow()              — full app shell
from widgets.sliders_panel import SlidersPanel     # SlidersPanel(None)        — Kelvin/tint/exposure controls
from widgets.image_preview import ImagePreview     # ImagePreview(None)        — central canvas + toolbar
from widgets.curve_editor import CurveEditor       # CurveEditor(None)         — tone curve tool
from widgets.thumbnail_list import ThumbnailList   # ThumbnailList(lambda *a: None)  — sidebar (needs a callback)
from widgets.tether_banner import TetherBanner     # TetherBanner(None)        — tether status strip

# Widgets that need collaborators — pass lightweight stubs:
from widgets.dust_panel import DustRemovalPanel    # DustRemovalPanel(main_window, image_preview)
from widgets.export_dialog import ExportSettingsDialog  # ExportSettingsDialog()  — reads ccr_backend.images
```

## Example: Capture the sliders panel

```python
import sys
sys.path.insert(0, ".claude/skills/qt-testing")
from scripts.qt_capture import capture_widget, init_qt, setup_freeccr_path

setup_freeccr_path()
app = init_qt()

from core.ccr_backend import ccr_backend           # singleton ready
from widgets.sliders_panel import SlidersPanel

panel = SlidersPanel(None)
panel.resize(360, 700)                              # give it a sensible size before grab()
path = capture_widget(panel, "sliders_panel_default")
print(f"Inspect: {path}")
```

## Example: Capture the full main window

```python
import sys
sys.path.insert(0, ".claude/skills/qt-testing")
from scripts.qt_capture import capture_widget, init_qt, setup_freeccr_path

setup_freeccr_path()
app = init_qt()

from ui.main_window import MainWindow

win = MainWindow()
win.resize(1280, 800)
path = capture_widget(win, "main_window_empty")
win.close()
print(f"Inspect: {path}")
```

## Interaction Pattern

To interact with widgets (click buttons, etc.):

```python
# Find widget at coordinates (from vision analysis)
target = widget.childAt(x, y)

# Trigger it directly (not mouse events)
if hasattr(target, 'click'):
    target.click()
    QApplication.processEvents()

# Capture result
capture_widget(widget, "after_click")
```

## Key Points

- Uses `Qt.WA_DontShowOnScreen` - no window popup.
- Renders identically to on-screen display (verified upstream).
- Call `setup_freeccr_path()` before importing FreeCCR modules; import `ccr_backend` first so the singleton exists.
- Give widgets a size (`resize`/`setFixedSize`) before `grab()` — an unsized widget may render at its minimum.
- Call `processEvents()` after interactions before capture.
- Use `childAt(x, y)` to map vision coordinates to widgets.
- Direct method calls (`.click()`) work; simulated mouse events don't.
- `DustRemovalPanel` requires a `main_window` and `image_preview`; pass real instances or minimal stubs exposing the attributes it reads.
