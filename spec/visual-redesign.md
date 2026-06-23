# Spec: Visual Redesign — Neutral Dark Theme System

Status: REFINED v1
Owner: FreeCCR
Feature branch: `feature/ui-redesign`

## 1. Summary

Give FreeCCR a single, coherent **neutral dark** look suited to color-critical
work (film-negative conversion + color correction), replacing today's scattered,
internally-inconsistent per-widget styling. The redesign introduces one
**theme system** — a central design-token palette, a global Qt stylesheet, a
Fusion `QPalette`, theme-aware icon tinting, and a small set of Python paint
constants — installed once at application start. The layout and interaction model
are unchanged; this is a **full reskin**, not an information-architecture change.

The palette is deliberately **neutral grayscale** (no hue cast) so the UI never
biases perception of image color — the same rationale Lightroom / Capture One /
Photoshop use. **Dark-only** for v1 (a light theme is a non-goal but the token
structure leaves the door open).

Chosen direction (confirmed): neutral dark, full reskin via a theme system,
dark-only. Anchor swatches: window `#1e1e1e`, panel `#2a2a2a`, text `#e0e0e0`,
neutral accent `#6e6e6e`.

## 2. Goals / Non-goals

### Goals
- One **`src/ui/theme.py`** module that is the single source of truth for all UI
  color, spacing, radius, and type tokens.
- A global dark theme installed **once** in `src/main.py` (`Fusion` style +
  `QPalette` + global QSS) so every widget, menu, dialog, scrollbar, and tooltip
  inherits it — including dialogs created lazily.
- Refactor the ~30 inline `setStyleSheet` calls so none hardcode literal colors;
  generic controls are covered by the global QSS, and the few semantic/dynamic
  styles (channel-tinted labels, color-band swatches, tether states, the dust
  "Done" accent button) pull their colors from theme tokens.
- A **paint-constants** layer for the colors QSS cannot reach (curve-editor
  canvas, the numpy-rendered histogram, and the on-image overlays), so they are
  centralized and consistent.
- **Theme-aware toolbar icons**: the pure-black toolbar PNGs are tinted at load
  so they are visible on the dark theme.
- The histogram (numpy-rendered) and its QSS container adopt a dark backdrop and
  stay in sync from one token.
- Visual consistency verified with the **`qt-testing`** skill (offscreen
  screenshots of every widget) plus the existing offscreen pytest suite still
  passing.

### Non-goals
- **No layout / IA changes** — panel arrangement, control placement, and flows
  are untouched.
- **No light theme and no runtime theme toggle** in v1 (structure allows adding
  one later; not built now).
- **No new fonts shipped** — keep the system default font family. (The `qt-testing`
  offscreen platform renders boxes for text because PySide6 ships no fonts; the
  real app uses the OS font. Screenshot review for *typography* uses the default
  Windows platform — `init_qt(offscreen=False)`.)
- **No recoloring of image-overlay semantics** — crop/area/slice/dust/reference
  /B-W-point markers and cursors keep their current functional colors (tuned for
  visibility over arbitrary photos); they are only *centralized*, not restyled.
- No icon redesign / new icon set; existing PNGs are tinted, not replaced.

## 3. Visual design system (tokens)

All values live in `src/ui/theme.py`. Names are stable; widgets reference names,
never literals. (`*` = changed from a current hardcoded value.)

### 3.1 Surfaces (neutral grays — zero hue)
| Token | Hex | Use |
|---|---|---|
| `WINDOW` | `#1e1e1e` | app background, menu bar |
| `CANVAS` | `#1a1a1a` * | image viewport behind the photo (`QGraphicsView`) |
| `PANEL` | `#2a2a2a` | side panels, dialogs, group backgrounds |
| `SURFACE` | `#333333` * | inputs, combos, line edits, unchecked buttons |
| `SURFACE_HOVER` | `#3c3c3c` | hover state |
| `SURFACE_ACTIVE` | `#454545` * | pressed / checked / selected background |
| `BORDER` | `#3c3c3c` | 1px control borders, separators |
| `BORDER_STRONG` | `#5a5a5a` | emphasized borders |

### 3.2 Text
| Token | Hex | Use |
|---|---|---|
| `TEXT` | `#e0e0e0` | primary text |
| `TEXT_MUTED` | `#9a9a9a` * | captions, hints, section labels (was `#888`/`#666`) |
| `TEXT_DISABLED` | `#6a6a6a` | disabled text |
| `TEXT_ON_ACCENT` | `#ffffff` | text on a saturated semantic button |

### 3.3 Accent / focus (neutral)
| Token | Hex | Use |
|---|---|---|
| `ACCENT` | `#6e6e6e` | focus ring, active slider handle, checked outline |
| `SELECTION_BG` | `#454545` | list/thumbnail selection, text selection bg |
| `SELECTION_TEXT` | `#ffffff` | text over selection |

Selection and focus are **neutral gray on purpose** — no blue/teal highlight, to
avoid any color cast in a color-critical UI.

### 3.4 Semantic colors (used sparingly; theme-independent)
| Token | Hex | Use |
|---|---|---|
| `CH_R` / `CH_G` / `CH_B` | `#d06666` / `#66aa66` / `#6688d0` | per-channel R/G/B accents — **unifies** the two near-duplicate sets today (curve `#d06666…` vs sliders `#c66…`) |
| `SUCCESS` | `#3a8f5a` * | dust "Done" / confirm accent (was `#2d7d46`) |
| `SUCCESS_HOVER` / `SUCCESS_PRESSED` | `#46a368` / `#2f7449` | states |
| `DANGER` | `#d9534f` | tether dot, "Stop" button |
| `DANGER_HOVER` / `DANGER_PRESSED` | `#c9302c` / `#ac2925` | states |
| `BAND_*` | red `#c0392b`, skin `#d8956b`, yellow `#c8b900`, green `#27ae60`, cyan `#17a8b4`, blue `#2f6fd0`, purple `#8e44ad` | color-band swatch fills — **unchanged** (they denote real hue bands) |

### 3.5 Tether banner (re-themed from light → dark amber)
Today the banner is amber-on-light (`#fff3cd`) and explicitly "for the light
theme" — it must be redone for dark:
| Token | Hex |
|---|---|
| `WARN_BG` | `#3a2f12` |
| `WARN_BORDER` | `#5a4a1e` |
| `WARN_TEXT` | `#e0c060` |
| `WARN_TEXT_MUTED` | `#b89a48` |

### 3.6 Icons
| Token | Hex | Use |
|---|---|---|
| `ICON` | `#cfcfcf` | tint for monochrome toolbar icons |
| `ICON_DISABLED` | `#6a6a6a` | disabled toolbar icons |

### 3.7 Spacing / radius / type
- Spacing scale: `2, 4, 6, 8, 12, 16` (px) — `SPACE_XS…SPACE_XL`.
- Radius: `RADIUS_SM=3`, `RADIUS_MD=5`, `RADIUS_LG=8`.
- Type scale (system font, no family change): `FS_CAPTION=11`, `FS_BODY=12`,
  `FS_CONTROL=13`, `FS_HEADING=14`; weights normal/bold. Matches sizes already in
  use, just centralized.

### 3.8 Paint constants (non-QSS; see §6)
- **UI chrome (follows theme):** `CURVE_BG=#232323`, `CURVE_GRID=#3d3d3d`,
  `CURVE_GRID_MINOR=#333333`, `CURVE_IDENTITY=#5a5a5a`, `CURVE_NODE=#f0f0f0`,
  `CURVE_NODE_OUTLINE=#222222`, `CURVE_NODE_DISABLED=#777777`,
  `HIST_BG=(42,42,42)`, `HIST_R/G/B=(230,80,80)/(90,200,90)/(100,140,235)`,
  `HIST_PEAK=(235,235,235)`.
- **Image overlays (functional, values preserved):** `OVL_REF_FRAME`,
  `OVL_SLICE`, `OVL_DIM`, `OVL_CROP_BORDER`, `OVL_HANDLE_FILL`,
  `OVL_HANDLE_OUTLINE`, `OVL_DUST_STROKE`, `OVL_DUST_CURSOR`, `OVL_BWP_WHITE`,
  `OVL_BWP_DENSE`, `OVL_AREA_LINE`, `OVL_AREA_FEATHER`, `OVL_COMP_GRID`,
  `CURSOR_LIGHT`, `CURSOR_DARK` — exact current RGBA values (no visual change).

## 4. Architecture

### 4.1 The theme module — `src/ui/theme.py`
Pure-PySide6 (imports only `PySide6.QtGui/QtCore`); imports **no** widgets (so no
import cycle — widgets may freely `from ui.theme import …`). Exposes:

- Token constants (§3) as module-level names / small grouped classes
  (`class Paint:`, `class Overlay:`).
- `qcolor(hex_or_rgba) -> QColor` and `c(token)` helpers.
- `build_palette() -> QPalette` — a full dark Fusion palette (Window, Base,
  Text, Button, Highlight, ToolTip, Disabled roles…) derived from §3 tokens.
- `global_qss() -> str` — the central stylesheet (§5), built from tokens via an
  f-string so there is exactly one place colors are defined.
- `apply_theme(app, settings=None) -> None` — `app.setStyle("Fusion")`,
  `app.setPalette(build_palette())`, `app.setStyleSheet(global_qss())`. (`settings`
  reserved for a future theme pref; v1 always applies dark.)
- `load_tinted_icon(abs_path, token=ICON) -> QIcon` — load a monochrome PNG, use
  its alpha as a mask, paint it `token`; returns a `QIcon` with normal+disabled
  pixmaps (disabled uses `ICON_DISABLED`). Used for the toolbar icons (§7).

### 4.2 Install point — `src/main.py`
The only correct global hook (audit-confirmed):
```python
app = QApplication(sys.argv)            # main.py:59 (unchanged)
from ui.theme import apply_theme
apply_theme(app)                        # NEW — before MainWindow() at line 63
app.setWindowIcon(QIcon(_icon_path))    # unchanged
window = MainWindow()
```
`Fusion` is **required**: the native Windows style ignores `QPalette` for menus,
scrollbars, and many controls; Fusion honors it fully and is the standard base
for a cross-platform custom dark theme.

## 5. QSS coverage (control catalog → styled)

`global_qss()` styles every control type the audit found, so nothing is left on
the default look. Each rule's colors come from §3 tokens.

- `QWidget` base (window bg/text), `QMainWindow`, `QDialog`, `QFrame`
- `QLabel` (default `TEXT`; muted caption via `[muted="true"]` property or
  `.caption` class — see §6.3)
- `QPushButton` (+ `:hover/:pressed/:checked/:disabled`), `QToolButton`,
  `QToolBar`
- `QSlider` groove/handle (`ResettableSlider`, `CenteringSlider` inherit)
- `QComboBox` (+ popup `QAbstractItemView`), `QLineEdit`, `QSpinBox`
- `QCheckBox`, `QRadioButton` (indicators)
- `QGroupBox` (title + border), `QListWidget` (item/`:selected`/`:hover`)
- `QScrollArea`, `QScrollBar:vertical/horizontal` (handle/track)
- `QProgressBar` (chunk uses `ACCENT`/`SUCCESS`)
- `QMenuBar`, `QMenu` (item/`:selected`/separator), `QTabWidget`/`QTabBar`
- `QTextEdit`, `QToolTip`, `QGraphicsView` (canvas bg = `CANVAS`)

`QToolButton` text color is set by palette/QSS (not the current hardcoded
`red/black/gray`); the toolbar's hardcoded `setStyleSheet` (image_preview.py:617)
is removed.

## 6. Inline-QSS & paint refactor

### 6.1 Generic inline styles → deleted (covered by global QSS)
Remove the per-widget `setStyleSheet` calls that only restated generic control
styling: collapsible toggle (sliders_panel.py:86), export hints
(export_dialog.py:129/154), dust labels (dust_panel.py:135/157/177/184/223/242),
sliders captions (sliders_panel.py:295/332/351/471/583). Layout-only separator
stylesheets (`margin-*`, no color) may stay as-is.

### 6.2 Semantic / dynamic styles → keep, but source colors from tokens
These stay local (they vary per item or carry meaning) but must inject token
values instead of literals:
- **Color-band swatches** (sliders_panel.py:508–525): fills from `BAND_*`,
  borders from `BORDER`/`ACCENT`.
- **Curve channel buttons** (curve_editor.py:362): bg `SURFACE`, text
  `CH_R/G/B`/`TEXT`, checked outline `ACCENT`.
- **Per-channel slider labels** (sliders_panel.py:480/489/498): `CH_R/G/B`.
- **Tether banner** (tether_banner.py:21–42): `WARN_*` + `DANGER*`.
- **Dust "Done" button** (dust_panel.py:231): `SUCCESS*`.

### 6.3 Caption convention
Replace the repeated `color:#888; font-size:11px` labels with a single mechanism:
set a dynamic property `caption=true` on those `QLabel`s and style
`QLabel[caption="true"] { color: TEXT_MUTED; font-size: 11px; }` in global QSS.
One rule, no per-label stylesheet.

### 6.4 Paint constants (QSS can't reach these)
- **Curve editor** (`curve_editor.py` `paintEvent`, `_LINE_COLORS`, `_BTN_TINT`):
  replace `QColor("#2b2b2b"/"#555"/"#3d3d3d"/"#666"/"#222"/"#f0f0f0"/"#777")` and
  the channel dicts with `Paint.CURVE_*` and `CH_*`. (Already dark — values are
  close; this just centralizes them.)
- **Histogram** (`ccr_image.py:591/601-602/613`): numpy backdrop `180 →
  Paint.HIST_BG`, channel colors `→ Paint.HIST_R/G/B`, peak `→ HIST_PEAK`. The
  container QSS background (sliders_panel.py:257) is driven by the **same**
  `HIST_BG` token so they never drift (audit flagged this coupling).
- **Image overlays** (`image_preview.py` many lines, listed in §8): move every
  `QColor(...)` literal to `Overlay.*` / `CURSOR_*` constants with **identical
  values**. De-duplicates the crop (2792–2810) and area (3347–3410) handle pairs
  that are byte-identical today. **No visual change** — these are tuned for
  contrast against photos, not the UI theme.

## 7. Icons

The 5 toolbar PNGs (`auto`, `rotate-left`, `rotate-right`, `vertical-mirror`,
`horizontal-mirror`) are pure-black line art on alpha → invisible on dark. Route
their loading (image_preview.py:630–667) through `theme.load_tinted_icon(
resource_path('icons/<name>.png'), ICON)`. `resource_path()` (dev + Nuitka) is
unchanged. The app/window logo and `.ico/.icns` packaging icons are untouched.

## 8. Integration points

| File | Change |
|---|---|
| **add** `src/ui/theme.py` | tokens, `build_palette`, `global_qss`, `apply_theme`, `load_tinted_icon`, `Paint`, `Overlay`. |
| `src/main.py` (after :59) | `from ui.theme import apply_theme; apply_theme(app)` before `MainWindow()`. |
| `src/widgets/image_preview.py` | remove toolbar `setStyleSheet` (:617); tint toolbar icons (:630–667); move all overlay/cursor `QColor` literals (:30,54,278,491,1103,1164,2012,2037,2159,2431,2451,2765,2779,2792,2810,3347,3390,3410) to `Overlay.*`; set `QGraphicsView` bg = `CANVAS`. |
| `src/widgets/curve_editor.py` | `paintEvent` + `_LINE_COLORS` + `_BTN_TINT` → `Paint.CURVE_*`/`CH_*`; channel-button QSS from tokens. |
| `src/widgets/sliders_panel.py` | drop generic caption stylesheets (use `caption` property); channel labels → `CH_*`; band swatches → `BAND_*`/tokens; histogram container bg → `HIST_BG`. |
| `src/widgets/dust_panel.py` | drop caption stylesheets; "Done" button → `SUCCESS*`. |
| `src/widgets/tether_banner.py` | re-theme to `WARN_*` + `DANGER*`; update its "light theme" docstring. |
| `src/widgets/export_dialog.py` | drop `color:gray` hint stylesheets (caption property). |
| `src/core/ccr_image.py` | histogram numpy colors → `Paint.HIST_*`. |
| **add** `tests/test_theme.py` | token/palette/QSS/icon-tint unit tests (§9). |

`thumbnail_list.py` needs no color change (its `QPainter` only composites a
pixmap); it inherits the global QSS for the list/scrollbar.

## 9. Test plan

### 9.1 Unit (`tests/test_theme.py`, offscreen pytest)
- `build_palette()` returns a `QPalette` with `Window`/`Base`/`Text` matching the
  tokens; `apply_theme(app)` sets style name `Fusion` and a non-empty stylesheet.
- `global_qss()` contains a rule for each control type in §5 and references no
  raw hex that isn't a token value (guard against re-introducing literals).
- `load_tinted_icon()` on a black test PNG yields a non-null icon whose pixmap’s
  opaque pixels equal `ICON` (and disabled pixmap equals `ICON_DISABLED`).
- Histogram coupling: the QSS container background value equals `HIST_BG`.
- Overlay constants equal the pre-refactor literals (regression table) — proves
  "no visual change" for on-image overlays.

### 9.2 Visual (qt-testing skill, default platform for real fonts)
Capture each widget **before** (current `main`) and **after**, read the PNGs, and
confirm: dark neutral surfaces, legible text (≥ AA contrast for body text on
`PANEL`), visible toolbar icons, themed scrollbars/menus, histogram on dark
backdrop, and that **on-image overlays are unchanged**. Targets: `MainWindow`,
`SlidersPanel`, `ImagePreview` (incl. toolbar), `CurveEditor`, `ThumbnailList`,
`DustRemovalPanel`, `ExportSettingsDialog`, `TetherBanner`, menu bar.

### 9.3 Regression
- `python tests/run_tests.py` (full offscreen suite) stays green.
- App launches (`python src/main.py`) with no QSS parse warnings.

## 10. Refinement (v1) — resolved decisions, risks

1. **Fusion is mandatory**, not optional — without it the dark palette won't take
   on native Windows menus/scrollbars. Accepted side effect: Fusion changes
   control metrics slightly vs. native; acceptable for a deliberate custom look.
2. **Palette alone is insufficient** — ~30 inline stylesheets override it
   per-widget, so the inline refactor (§6) is part of the core work, not optional
   polish. Global QSS + palette + inline cleanup together.
3. **Chrome vs. overlay split** is the key correctness call: UI-surface paint
   (curve canvas, histogram) adopts theme tokens; on-image overlays keep their
   functional RGBA. Mixing these up would either wash out overlays on photos or
   leave a light histogram. The §9.1 regression table locks overlay values.
4. **Histogram two-sided coupling** (numpy fill ↔ QSS container) is collapsed to a
   single `HIST_BG` token referenced from both sites.
5. **Channel-color unification**: adopt the curve editor's `#d06666/#66aa66/
   #6688d0` as canonical `CH_*` and retire the near-duplicate `#c66/#6a6/#66c`.
6. **No literals rule**: after the refactor, a grep for hex colors in
   `src/widgets` and `src/ui` should return only token definitions / the
   `theme.py` module (enforced loosely by the §9.1 QSS test).
7. **Icons tinted, not replaced** — keeps the build/`resource_path` and Nuitka
   bundling untouched; no new assets.
8. **Light theme / toggle deferred** — `apply_theme(app, settings)` and the token
   table make a future light variant a localized change, but it is out of scope.

## 11. Files touched / added (summary)
- **add**: `src/ui/theme.py`, `tests/test_theme.py`
- **edit**: `src/main.py`, `src/widgets/image_preview.py`,
  `src/widgets/curve_editor.py`, `src/widgets/sliders_panel.py`,
  `src/widgets/dust_panel.py`, `src/widgets/tether_banner.py`,
  `src/widgets/export_dialog.py`, `src/core/ccr_image.py`
