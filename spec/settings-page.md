# Spec: Settings Page (DaVinci-style) with a Color Management Tab

Status: DRAFT v1
Owner: FreeCCR
Feature branch: `feature/settings-page`

## 1. Summary

Add a **Settings** dialog opened from **File ▸ Settings…** (Ctrl+,), laid out like
DaVinci Resolve's *Project Settings*: a **left category sidebar** + a **right
content pane** + a **footer button row**. The first (and currently only) category
is **Color Management**, which **consolidates the colour-management features that
are today scattered in the File menu** — the input camera profile (Input **ICC**,
**DCP**) and **Create Camera Profile from IT8…** — plus the global **Positive-mode**
toggle (today only a thumbnail-panel checkbox). Those five File-menu items are
**removed from the File menu** and replaced by the single **Settings…** entry; the
controls now live in the Color Management tab.

The dialog reuses the existing, already-tested backend handlers
(`set_input_icc`/`set_input_dcp`/IT8 wizard/positive-mode reprocess) — it is a
**relocated, better-organised front-end**, not new colour logic.

## 2. Goals / Non-goals

### Goals
- A reusable **`SettingsDialog`** (sidebar + stacked pages + footer) styled with
  the app theme (`ui.theme`), extensible to more categories later.
- A **Color Management** page that exposes, in titled sections:
  - **Input camera profile** — mutually-exclusive **None / Input ICC / DCP**, with
    Browse/Clear, the active-profile name, and a **Create Camera Profile from IT8…**
    launcher.
  - **Negative conversion** — the **Positive mode** checkbox (kept in sync with the
    thumbnail-panel checkbox).
- **File ▸ Settings…** (Ctrl+,) opens it; the five colour File-menu actions are
  removed.
- **Immediate-apply** semantics preserved: choosing/clearing a profile or toggling
  Positive mode takes effect immediately (re-decode), exactly as today — the footer
  is a single **Done** button (not a batched Save/Cancel form), because these
  settings have no staged state.
- Live status: the page reflects the **currently active** profile (ICC vs DCP vs
  none) and updates after every action and on open.

### Non-goals
- **No new colour-science behaviour.** Same decode/profile/positive logic; only the
  UI location and grouping change.
- **No batched Save/Cancel.** Profile/positive changes re-decode on click (they
  always have); a staged model would change behaviour and is out of scope.
- **No export colour-space setting here.** It is an export-time choice already
  remembered by the export dialog (`export/colorspace` QSettings); leaving it there
  avoids a second source of truth. (A future "default export colour space" could be
  added as a new section.)
- **No removal of the thumbnail Positive checkbox.** It stays as the quick toggle;
  the Settings checkbox mirrors it.
- **No persisted window geometry / theme picker** in v1 (the sidebar framework
  leaves room for a future General tab).

## 3. Background (researched)

### 3.1 What moves
Today (`main_window.create_menu`, ~lines 355–369) the File menu holds:
`Set Input ICC Profile…`, `Clear Input ICC Profile`, `Load DCP Profile…`,
`Clear DCP Profile`, `Create Camera Profile from IT8…`. **Positive mode** is a
checkbox in `thumbnail_list` driven by `on_positive_mode_toggled` (main_window:535)
and restored from `import/positive_mode` (main_window:177).

The handlers that do the actual work stay: `set_input_icc_profile`,
`_apply_input_icc_path`, `clear_input_icc_profile`, `set_input_dcp_profile`,
`_apply_input_dcp_path`, `clear_input_dcp_profile`,
`create_camera_profile_from_it8`, `on_positive_mode_toggled`. The only menu-coupled
piece is `_refresh_input_icc_menu` (updates `self.input_icc_action` etc.), which is
replaced (§6.2).

### 3.2 DaVinci Project-Settings layout
A modal dialog: a fixed-width **left list of categories** (selected row highlighted)
and a **right pane** that swaps content per category, with a small **footer**
(bottom-right buttons). We reproduce the *structure* (sidebar + `QStackedWidget` +
footer) — the canonical settings shape — using the app's dark theme tokens, not the
Save/Cancel batching (which doesn't fit our immediate-apply settings, §2).

### 3.3 Theme
Use `ui.theme`: `PANEL`/`SURFACE`/`BORDER`/`TEXT`/`TEXT_MUTED`, `style_button`
(primary/danger), `section_separator`, `section_header_qss`, `apply_panel_spacing`,
`GAP_*`, `CONTROL_H`, and `apply_windows_dark_titlebar` on show (like the other
custom dialogs).

## 4. Data model & files

### 4.1 New `src/widgets/settings_dialog.py`
```python
class SettingsDialog(QDialog):
    """DaVinci-style settings: category sidebar + stacked pages + footer.
    Immediate-apply; reuses MainWindow's colour handlers."""
    def __init__(self, main_window, parent=None): ...
    def _add_category(self, name: str, page: QWidget): ...   # sidebar item -> stacked page
    def _build_color_management_page(self) -> QWidget: ...
    def refresh_color_management(self) -> None: ...          # reflect active profile / positive
```
The page's buttons call `self._mw.set_input_icc_profile()` etc. (the existing
handlers, which open the file pickers, apply, re-decode, and call back to refresh).

### 4.2 Edits
- `src/ui/main_window.py`:
  - `create_menu`: drop the five colour actions; add `Settings…` (Ctrl+,) →
    `open_settings`.
  - `open_settings()`: construct/show `SettingsDialog(self)`, hold `self._settings_dialog`
    while open, clear on close.
  - `_refresh_input_icc_menu` → **`_refresh_profile_ui`**: if a settings dialog is
    open, call `refresh_color_management()`; no menu actions to touch. All existing
    callers (the handlers + startup restore) call the renamed method.
  - `on_positive_mode_toggled`: also `thumbnail_list.set_positive_checkbox(checked)`
    so the checkbox stays in step when the toggle comes from the dialog.
- No backend changes.

## 5. Processing / math
None — pure UI relocation. All colour processing is unchanged.

## 6. Integration points

### 6.1 File menu
```python
file_menu.addSeparator()
settings_action = file_menu.addAction("Settings…")
settings_action.setShortcut("Ctrl+,")
settings_action.triggered.connect(self.open_settings)
```
The IT8/ICC/DCP `addAction` lines and the `_refresh_input_icc_menu()` call are
removed.

### 6.2 Profile-state refresh
`_refresh_profile_ui()` replaces `_refresh_input_icc_menu()`:
```python
def _refresh_profile_ui(self):
    dlg = getattr(self, "_settings_dialog", None)
    if dlg is not None and dlg.isVisible():
        dlg.refresh_color_management()
```
Startup restore (main_window:193–202) calls `_refresh_profile_ui()` (a no-op when no
dialog is open). The colour handlers' `self._refresh_input_icc_menu()` calls become
`self._refresh_profile_ui()`.

### 6.3 Dialog ↔ handler flow (immediate apply)
- "Set Input ICC…" → `mw.set_input_icc_profile()` (file picker → `_apply_input_icc_path`
  → backend + re-decode + `_refresh_profile_ui` → dialog `refresh_color_management`).
- "Set DCP…" → `mw.set_input_dcp_profile()`; "Clear" → the matching
  `clear_input_*` ; "Create Camera Profile from IT8…" → `mw.create_camera_profile_from_it8()`.
- Positive checkbox toggled → `mw.on_positive_mode_toggled(checked)` (which now also
  syncs the thumbnail checkbox), then `refresh_color_management`.
The `_settings_dialog` is **modal**; the nested `QFileDialog`/IT8 wizard/reprocess
run on top fine.

### 6.4 Reuse, not reinvent
Backend ⇒ `ccr_backend.set_input_icc/set_input_dcp/clear_*`, `get_active_*`.
Handlers ⇒ existing `main_window` methods. Theme ⇒ `ui.theme`. Dark titlebar ⇒
`apply_windows_dark_titlebar`.

## 7. UX

`SettingsDialog` (modal, ~720×520, clamped to screen):
- **Sidebar** (left, ~170px): a `QListWidget` of categories; **Color Management**
  selected by default. Selecting a row shows its page in the right `QStackedWidget`.
- **Content pane** (right): the selected page in a `QScrollArea`.
- **Footer**: a `section_separator` then a right-aligned **Done** (primary) button.

**Color Management page** — titled sections (group boxes), top-to-bottom:
1. **Input camera profile** — a short explainer; three mutually-exclusive radio
   options **None / ICC profile / DCP profile**; a **Browse…** button (sets the
   selected kind) and **Clear**; a muted **Active: `<name>`** status line. A
   separator, then **Create Camera Profile from IT8…** (launches the wizard) with a
   one-line note that it builds a profile from a chart shot.
2. **Negative conversion** — **Positive mode** checkbox + the existing one-line
   explanation (decode as positives, skip inversion).

The page is the single source of truth for the active profile; opening Settings,
applying/clearing, or running the IT8 wizard all call `refresh_color_management`.

## 8. Test plan
Unit/UI (offscreen Qt):
- **Construction**: `SettingsDialog(main_window)` builds; the sidebar lists
  "Color Management"; selecting it shows the CM page.
- **Reflects state**: with no profile → "Active: None" and the **None** radio; with
  an active ICC (set via `cm.set_active_input_profile`) → the ICC radio + the
  profile name; with an active DCP → the DCP radio + name; `refresh_color_management`
  updates after a state change.
- **Buttons wired**: the Set-ICC / Set-DCP / Clear / IT8 buttons invoke the
  corresponding `main_window` handlers (assert via a stub/monkeypatched MainWindow
  that records calls).
- **Positive sync**: toggling the dialog checkbox calls `on_positive_mode_toggled`
  and leaves the thumbnail checkbox in the same state.
- **Menu**: after `create_menu`, the File menu contains a **Settings…** action and
  **none** of the old colour actions (`input_icc_action` etc. are gone).
- **Open/close**: `open_settings()` sets `_settings_dialog` while visible and clears
  it on close; `_refresh_profile_ui()` is a no-op when closed.

Manual:
- Set/clear an ICC and a DCP from the page; confirm exclusivity + re-decode +
  status update; run the IT8 wizard and "apply now"; toggle Positive mode and see
  the thumbnail checkbox follow; restart and confirm the active profile + positive
  mode restore; verify the dark titlebar + theming match the other dialogs.

## 8b. Refinement (v2) — disable toggle + per-image profile mismatch

Two follow-ups; the core decision is that **changing/clearing/disabling the camera
profile NO LONGER re-decodes loaded images** (it used to full-reset them all).
Instead each image remembers what it was graded under, a mismatch is flagged, and
the user re-grades on demand (single or bulk). Confirmed model:

### 8b.1 Disable camera profile (persistent)
- A **"Disable camera profile"** checkbox in the Color Management page (next to
  Positive mode). When on, the active ICC/DCP is **not applied** at decode (the
  decode reverts to the unprofiled Adobe path), without clearing the profile.
- Persisted: QSettings `import/input_profile_disabled`, restored at startup.
- Backend: `color_management.set_input_profile_disabled` /
  `input_profile_disabled` / `camera_profile_active()` (active *and* not disabled).
  `_input_icc_will_apply` → `camera_profile_active()`; `_apply_input_icc` /
  `_apply_input_dcp` no-op when disabled.
- Toggling it does **not** reprocess; it just refreshes the mismatch flags
  (non-destructive, per the user's choice).

### 8b.2 Per-image profile signature + mismatch warning
- `color_management.active_profile_signature()` → `"icc:<desc>"` / `"dcp:<name>"` /
  `"none"` (the latter when disabled or no profile). `ccr_backend.active_profile_signature()`
  wraps it and returns `"none"` in Positive mode (the profile isn't applied then).
- `CCRImage.profile_signature` records the signature at every working decode
  (stamped in `__init__`, `reload_image`, `reload_image_decode_only`). Not
  persisted — a fresh load/restart re-decodes under the active profile, so a
  mismatch only arises mid-session after the active profile changes.
- The thumbnail item shows a **⚠ prefix + amber text + tooltip** when
  `img.profile_signature != active_profile_signature()`. `ThumbnailList.refresh_profile_warnings()`
  recomputes it; it is called on load/append and whenever the active profile
  changes (`MainWindow._refresh_profile_mismatch`).

### 8b.3 Replace with current camera profile (single / bulk)
- The thumbnail right-click menu gains **"Replace with current camera profile"**
  when the selection contains mismatched images. It **resets & re-decodes** the
  mismatched image(s) under the current profile (`reset_images_by_indices`, the
  same primitive as Reset — drops that image's edits/conversion), then clears the
  warnings. Works on a multi-selection exactly like Reset.

### 8b.4 Behaviour change (the load-bearing one)
`MainWindow`'s colour handlers (`_apply_input_icc_path`, `clear_input_icc_profile`,
`_apply_input_dcp_path`, `clear_input_dcp_profile`, the new disable toggle) **drop
the `_reprocess_after_input_icc_change()` call** (now removed) and instead call
`_refresh_profile_mismatch()`. Loaded images keep their decode; the mismatch flag +
opt-in Replace are the new (non-destructive) path.

### 8b.5 Review fixes (adversarial pass)
- **Positive-mode toggle also re-flags warnings.** `on_positive_mode_toggled`
  re-decodes (re-stamps every signature, → `none` in Positive mode) but
  `update_all_thumbnails()` only repaints icons, so it must also call
  `refresh_profile_warnings()` or stale ⚠ persist with no in-app clear.
- **Content-hash signature.** The signature uses the profile file's content hash
  (`profile.content_id`), not just the description/name, so two different profiles
  with the same (or empty) name don't collide and hide a real mismatch.
- **Disable flag resets on clear.** Clearing the ICC/DCP also resets
  `import/input_profile_disabled` (`_clear_profile_disabled`) so a stale
  `disabled=True` can't silently re-arm and ignore the next profile.

## 9. Risks & mitigations
- **Dangling menu refs** after removing actions → `_refresh_input_icc_menu` is fully
  replaced by `_refresh_profile_ui`; a grep ensures no `self.input_icc_action` /
  `clear_input_dcp_action` references remain.
- **Stale status** if the dialog isn't refreshed after a handler → every colour
  handler ends in `_refresh_profile_ui`, and the page also refreshes on show.
- **Positive desync** (dialog vs thumbnail checkbox) → `on_positive_mode_toggled`
  now also sets the thumbnail checkbox; the modal dialog prevents concurrent edits.
- **Discoverability** (users used to the File-menu items) → the consolidated tab is
  named clearly and Ctrl+, is standard; the items are grouped, not lost.
