# Spec: Camera-profile library + picker

Status: IMPLEMENTED
Branch: `feature/settings-page`

## 1. Goal

Replace the single active-profile slot + "disable" toggle with a **persistent
profile library**: FreeCCR keeps every imported/generated camera profile in its
workspace, the user **picks the active one from a dropdown above the thumbnails**
(including "None"), **manages (deletes) them in Settings → Color Management**, and
the library **survives app updates / reinstalls**.

## 2. Changes

1. **Remove** the "Disable camera profile" checkbox from the Settings dialog — its
   job (temporarily no profile) is now just selecting **None** in the picker.
2. **Profile picker dropdown** at the top of the thumbnail panel: `None` + every
   profile in the library, sorted. Selecting one activates it; "None" deactivates.
3. **Settings → Color Management = management**: a list of library profiles with
   **Delete** (manage which to keep) and **Import profile…** (copy an external
   .icc/.icm/.dcp into the library); plus the IT8 wizard. The active profile is
   shown but chosen via the dropdown.
4. **Persistence**: profiles live in `%APPDATA%/FreeCCR/camera_profiles/` (Roaming),
   which Windows never removes on update/uninstall. Verify the installer does not
   delete it.

## 3. Data model

The library dir already exists (the IT8 wizard saves there). The active profile is
just a **path within the library**; no more single overwriting `input_profile.icc/.dcp`
copy.

`ccr_backend`:
- `camera_profiles_dir()` → `<appdata>/FreeCCR/camera_profiles` (mkdir).
- `list_camera_profiles()` → `[{name, path, kind('icc'|'dcp')}]`, sorted by name.
- `import_camera_profile(src)` → validate-parse then copy into the library (de-dupe
  name); returns the new path. Raises on an unparseable/unsupported file.
- `set_active_profile(path|None)` → activate the library file (icc/dcp by extension)
  or clear; **never copies or deletes** (the library file is the source); sets
  `input_icc_name`/`input_dcp_name` + `content_id`; records `active_profile_path`.
- `delete_camera_profile(path)` → deactivate if active, then remove the file.

Active selection persists in QSettings `import/active_profile_path`; restored at
startup. (Legacy `input_profile.icc/.dcp` single copies are ignored; generated
profiles already in the library appear in the picker.)

## 4. UI

- `ThumbnailList`: a `QComboBox` (`Camera profile: [None ▾]`) above the Positive
  checkbox; `refresh_profile_combo()` repopulates from the library + reflects the
  active path; on change → `MainWindow.set_active_profile_path(path|None)`.
- `SettingsDialog` Color-Management page: drop `_cb_disable`/`_on_disable`; the
  Input-profile section becomes a profile **list** + **Delete** + **Import profile…**
  + the IT8 launcher; status shows the active profile.
- `MainWindow`:
  - `set_active_profile_path(path|None)` → `ccr_backend.set_active_profile`, persist,
    `_refresh_profile_mismatch()` + refresh the combo + the dialog + a hint. (No
    re-decode — same non-destructive mismatch model as before.)
  - `import_camera_profile_dialog()` → file picker → `import_camera_profile` →
    activate + refresh.
  - `delete_camera_profile(path)` → confirm → backend delete → refresh.
  - the IT8 wizard "apply now" → `set_active_profile_path(saved_path)` (already in
    the library); after any save, refresh the combo so the new profile appears.
  - startup: restore `import/active_profile_path`; populate the combo.
  - remove `set_camera_profile_disabled` / `_clear_profile_disabled`.

## 5. Test plan
- `list_camera_profiles` returns generated + imported, sorted, with kind.
- `import_camera_profile` copies a valid .icc/.dcp into the library and de-dupes;
  rejects an unparseable file.
- `set_active_profile(path)` activates (icc/dcp) without copying/deleting; `None`
  clears; `delete_camera_profile` removes and deactivates if active.
- Picker: `refresh_profile_combo` lists None + library, reflects active; selecting
  drives `set_active_profile_path`.
- Settings dialog has no disable checkbox; the list + delete + import work.
- Profiles persist (the dir is under APPDATA; deleting `input_profile.*` legacy
  copies does not affect the library).

## 6. Persistence / installer
`camera_profiles/` is under `%APPDATA%/FreeCCR` (Roaming) — outside the install dir,
so updates/reinstalls leave it intact. **Audited** `windows_build_scripts/inno_setup.iss`:
it only writes `main.dist/*` into `{app}` and has no `[UninstallDelete]` / `{userappdata}`
entry, so install/update/uninstall never touches the workspace. No installer change needed.
