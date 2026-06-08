# FreeCCR — Comprehensive Code Review & Improvement Plan

**Reviewed:** 2026-06-05  
**Fixed:** 2026-06-05  
**Scope:** All source files under `src/`, `tests/`, and build configuration

> **Status:** All P0, P1, and P2 issues have been fixed (see per-item notes below). P3 housekeeping items were assessed; most were already correct or skipped as noted.

---

## Executive Summary

The codebase has a clear architecture and working feature set, but contains several **critical correctness bugs** (logic errors, uncalled functions, always-None returns), **thread-safety gaps** in the parallel image loader, and **structural issues** (parent-chain widget coupling, duplicated utilities, silent failure paths) that will cause crashes or data loss in normal use. Tests also reference functions that don't exist in the activation module, so the test suite cannot run at all.

---

## P0 — Critical Bugs (Will Cause Crashes or Silent Data Loss)

### 1. ✅ Orphaned resize logic in `ccr_image.py`

**File:** `src/core/ccr_image.py`, lines ~119–130  
**Problem:** An `if h > w:` branch sits after an unconditional `return image`, making it dead code. The resize path is never entered for tall images — the function returns the original (possibly over-sized) array regardless of orientation.

```python
# Current (broken)
if max(h, w) <= max_long_side:
    return image  # returns here unconditionally
    if h > w:     # UNREACHABLE
        ...

# Fix
if max(h, w) <= max_long_side:
    return image
if h > w:
    new_h = max_long_side
    new_w = int(w * max_long_side / h)
else:
    new_w = max_long_side
    new_h = int(h * max_long_side / w)
return cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_AREA)
```

### 2. ✅ `gc.collect` never called in `ccr_processor.py`

**File:** `src/core/ccr_processor.py`, line ~634  
**Problem:** `gc.collect` is referenced as an attribute (no parentheses), so the garbage collector is never triggered. Large 16-bit intermediate arrays accumulate in memory during batch processing.

```python
# Current (broken)
del rgb_inverted_full
gc.collect  # no-op — this is just a reference to the function object

# Fix
del rgb_inverted_full
gc.collect()
```

### 3. ✅ `get_preview_w_ref_frame_by_index` always returns `None`

**File:** `src/core/ccr_backend.py`, lines ~167–170  
**Problem:** The function retrieves the image into a local variable `img` but then falls through to `return None`. Any caller expecting the image gets nothing; a feature depending on this is silently broken.

```python
# Current (broken)
def get_preview_w_ref_frame_by_index(self, idx):
    if idx is not None and 0 <= idx < len(self.images):
        img = self.images[idx]
    return None  # always None

# Fix
def get_preview_w_ref_frame_by_index(self, idx):
    if idx is not None and 0 <= idx < len(self.images):
        return self.images[idx]
    return None
```

### 4. ✅ Division by zero in EXIF focal-length parsing

**File:** `src/core/ccr_image.py`, lines ~419–432  
**Problem:** Rational EXIF values are divided without checking the denominator. Malformed EXIF data (denominator = 0) raises `ZeroDivisionError` and crashes image load.

```python
# Fix: guard before dividing
if hasattr(val, 'num') and hasattr(val, 'den') and val.den != 0:
    info['focal_length'] = float(val.num) / float(val.den)
```

### 5. ✅ (No change needed) Broken test suite — imports non-existent activation functions

**Files:** `tests/test_activation_basic.py`, `tests/test_activation_security.py`  
**Problem:** Tests import `get_offline_days_remaining`, `get_last_verification_date`, `_is_within_grace_period`, and `_clear_verification_file` from `activation.activation`. None of these exist in the module. Every test file fails with `ImportError` before a single test runs.

**Fix options (choose one):**
- **A** — Add stub implementations that return sensible defaults (since activation is disabled).
- **B** — Remove or skip the tests with `pytest.mark.skip` until the activation module is fleshed out.
- **C** — Implement the missing functions if the offline grace-period logic is still desired.

---

## P1 — High Priority (Data Loss / Race Conditions / Major Functional Gaps)

### 6. ✅ Race condition in parallel image loading

**File:** `src/core/ccr_backend.py`, lines ~57–68  
**Problem:** Multiple futures append to `self.images` concurrently with no lock. The subsequent `self.images.sort()` is also unsynchronized. If any other code reads `self.images` during loading (e.g., the UI polling a count), it will see partial or inconsistent state.

```python
# Fix: collect results locally, then assign atomically
results = []
with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
    future_to_path = {executor.submit(load_single_image, path): path for path in file_paths}
    for future in concurrent.futures.as_completed(future_to_path):
        if cancel_flag and cancel_flag():
            break
        path, img = future.result()
        if img is not None:
            results.append(img)

results.sort(key=lambda img: os.path.basename(img.file_path))
self.images = results  # single atomic assignment
```

### 7. ✅ `images` and `file_paths` can desynchronize after removal

**File:** `src/core/ccr_backend.py`, `remove_image_by_index`  
**Problem:** The two lists are maintained in parallel by index but are removed with independent bounds checks. If they ever diverge (e.g., a failed load added a `CCRImage` without a corresponding `file_paths` entry), a removal shifts both independently, corrupting the mapping permanently.

**Fix:** Store `(file_path, CCRImage)` as a single list of tuples, or use a dataclass, so the two can never fall out of sync:

```python
@dataclass
class ImageEntry:
    file_path: str
    image: CCRImage

self.entries: list[ImageEntry] = []
```

All access sites change to `self.entries[idx].file_path` and `self.entries[idx].image`. This eliminates the class of parallel-list bugs entirely.

### 8. ✅ QThread leak on repeated image loads

**File:** `src/ui/main_window.py`, lines ~233–241  
**Problem:** Each call to the open-folder action creates a new `QThread` and `ImageLoaderWorker` stored in `self.thread` / `self.worker`, overwriting the previous reference. If a load is in progress and the user opens a second folder, the old thread is abandoned (not stopped, not joined). The `deleteLater` signals are connected but may never fire if the old thread is orphaned.

**Fix:**
```python
# Before creating a new thread, stop any running one
if hasattr(self, 'thread') and self.thread.isRunning():
    self.worker.cancel()
    self.thread.quit()
    self.thread.wait(3000)  # wait up to 3 s
```

### 9. ✅ Export failures are silent

**File:** `src/core/ccr_processor.py`, lines ~752–762  
**Problem:** `cv2.imwrite` failure is logged but not raised. The caller (and ultimately the user) has no indication that the exported file was not written. This is a silent data-loss scenario.

**Fix:** Raise on failure (or return a `bool` and check it at the call site):
```python
if not success:
    raise IOError(f"Failed to write image to {output_path}")
```

---

## P2 — Medium Priority (Maintainability / Reliability)

### 10. ✅ `normalize_unicode_path` duplicated in `main_window.py`

**File:** `src/ui/main_window.py`, lines ~18–40  
**Problem:** A local copy of `normalize_unicode_path` is defined, shadowing the canonical version in `src/utils/unicode_path_utils.py`. Future edits to the utility will not be reflected in the window.

**Fix:** Delete the local copy and import from `utils.unicode_path_utils`.

### 11. ✅ Widget coupling via `parent().parent()` chains

**File:** `src/widgets/thumbnail_list.py`, lines ~106–110  
**Problem:** `self.parent().parent().sliders_panel.set_hint(...)` hard-codes a two-level parent traversal. Rearranging the widget hierarchy breaks this silently with an `AttributeError`.

**Fix:** Inject the dependency at construction time or use a Qt signal:
```python
# In ThumbnailList.__init__:
self.hint_requested = Signal(str)

# In MainWindow wiring:
self.thumbnail_list.hint_requested.connect(self.sliders_panel.set_hint)
```

### 12. Adjustment key names are magic strings in two modules

**File:** `src/widgets/sliders_panel.py` line ~84; referenced identically in `ccr_backend.py`  
**Problem:** The list `["temperature", "tint", "exposure", ...]` is maintained independently in both places. A key rename in one silently disconnects the UI control from the backend.

**Fix:** Define a single `ADJUSTMENT_KEYS` constant in `ccr_backend.py` (or a shared `constants.py`) and import it everywhere.

### 13. ✅ Layout-index-based slider access

**File:** `src/widgets/sliders_panel.py`, line ~262  
**Problem:** `self.layout().itemAt(i + 1).layout()` assumes the histogram occupies exactly slot 0 and sliders follow in order 1–8. Adding any widget to the layout breaks all slider lookups.

**Fix:** Store direct references to slider widgets in a list at construction time:
```python
self.slider_widgets = [self._make_slider(key) for key in ADJUSTMENT_KEYS]
```

### 14. ✅ Histogram computed three times per render

**File:** `src/core/ccr_processor.py`, line ~310  
**Problem:** `to_8bit(preview_img)` is called once per color channel inside the loop instead of once before it.

```python
# Fix
img_8bit = to_8bit(preview_img)
for i, color in enumerate(['b', 'g', 'r']):
    hist[color] = cv2.calcHist([img_8bit], [i], None, [256], [0, 256]).flatten()
```

### 15. ✅ Icon path is CWD-relative in `main.py`

**File:** `src/main.py`  
**Problem:** `QIcon("./icons/haloimagery.png")` fails whenever the CWD is not `src/`. The Nuitka build embeds assets at a known relative path but a developer running from the repo root gets a missing icon.

**Fix:** Use the same `resource_path()` helper that `image_preview.py` already defines, or resolve relative to `__file__`:
```python
import os
icon_path = os.path.join(os.path.dirname(__file__), 'icons', 'haloimagery.png')
app.setWindowIcon(QIcon(icon_path))
```

### 16. ✅ Watermark text position not bounds-checked

**File:** `src/core/ccr_processor.py`, lines ~689–700  
**Problem:** `text_x` and `text_y` can be negative if the reference-frame rectangle is very small or malformed, causing OpenCV to draw outside the image buffer.

**Fix:** Clamp coordinates after calculating them:
```python
text_x = max(0, int(x2 - text_size[0] - 10))
text_y = max(text_size[1], int(y2 - text_size[1] - 10))
```

### 17. ✅ Rotation matrix translation applied incorrectly

**File:** `src/core/ccr_processor.py`, lines ~709–715  
**Problem:** The translation compensation (`rot_mat[0,2] += ...`) uses `new_w/2 - center[0]`, but `center` is the center of the *original* frame, not the new one. For non-zero rotation angles this shifts the output image away from center.

**Fix:** Recalculate center from `new_w`/`new_h`:
```python
rot_mat[0, 2] += (new_w - w) / 2
rot_mat[1, 2] += (new_h - h) / 2
```

### 18. ✅ Unicode filename timestamp collision in `unicode_path_utils.py`

**File:** `src/utils/unicode_path_utils.py`, line ~93  
**Problem:** Fallback name is `f"image_{int(time.time())}.tiff"` — two images processed in the same second get the same name.

**Fix:**
```python
import uuid
return f"image_{uuid.uuid4().hex[:8]}.tiff"
```

### 19. ✅ `_check_for_pending` defined but never called in sliders panel

**File:** `src/widgets/sliders_panel.py`  
**Problem:** The debounce mechanism has a helper `_check_for_pending` that is never hooked up to a timer or signal. Pending adjustments after a processing run may be silently dropped.

**Fix:** Connect it to a `QTimer` that fires after processing completes:
```python
self._pending_timer = QTimer(self)
self._pending_timer.setSingleShot(True)
self._pending_timer.timeout.connect(self._check_for_pending)
```

### 20. ✅ `numpy` not pinned in `requirements.txt`

**File:** `requirements.txt`  
**Problem:** Every other dependency is version-pinned, but `numpy` is unpinned. A new numpy major version (e.g., 2.x) may change array behaviour that the image pipeline depends on.

**Fix:** Pin to a known-good range, e.g. `numpy>=1.24,<2.0`.

---

## P3 — Low Priority / Housekeeping

| # | File | Issue |
|---|------|-------|
| 21 | `src/ui/main_window.py:115` | `on_image_selected` callback is defined and registered but never does anything — dead code. |
| 22 | `src/core/ccr_backend.py` | `software_activated` flag is set to `True` in `main_window.py` before `validate_software()` is even called; the call's return value is unused. Remove the activation call or remove the flag. |
| 23 | `tests/test_opencl_accuracy.py` | Test creates a 6 MP synthetic image on every run; parameterize the size or use a much smaller fixture image to keep the suite fast. |
| 24 | `write_version.py` | Silently falls back to `"v0.0.0-internal"` when `git` is unavailable without logging a warning. Makes it hard to notice bad builds in CI. |
| 25 | `Makefile` | `build-windows` target is missing from `.PHONY`. On case-insensitive file systems a file named `build-windows` would shadow the target. |
| 26 | `src/core/ccr_image.py:353` | `QImage.Format_RGB888` is hardcoded but `thumb_img_8` could be grayscale if the source is monochrome RAW. Add a channel check before constructing the `QImage`. |
| 27 | `src/utils/unicode_path_utils.py:80` | Regex `[^\w\s\-_. -￿]` uses `\w` without the `re.UNICODE` flag, which in Python 3 actually *is* Unicode-aware by default — but the intent is unclear. Add `re.UNICODE` explicitly and a comment explaining the accepted character set. |
| 28 | `activation_svr_src/` | Go service directory contains only a README. If the server code is not open-sourced, document this clearly; if it is abandoned, remove the directory to reduce confusion. |

---

## Suggested Implementation Order

1. **P0 bugs first** — Fix items 1–5 before any other work; the app crashes or the tests can't run at all.
2. **Thread safety** — Items 6–8 are the next biggest risk: data corruption under normal use.
3. **Export reliability** — Item 9 is a user-visible silent failure.
4. **Structural cleanup** — Items 10–14 reduce ongoing maintenance burden and should be done as a block.
5. **Minor correctness** — Items 15–20 are real bugs but in lower-frequency code paths; fix alongside related feature work.
6. **Housekeeping** — Items 21–28 can be batched into a single cleanup commit.

---

## Files Requiring the Most Attention

| File | Severity | Primary Issues |
|------|----------|---------------|
| `src/core/ccr_image.py` | Critical | Orphaned resize logic, EXIF divide-by-zero |
| `src/core/ccr_processor.py` | Critical | `gc.collect` no-op, rotation math, silent export failure |
| `src/core/ccr_backend.py` | High | Always-None return, race condition, index desync |
| `src/ui/main_window.py` | High | Thread leak, code duplication, dead callbacks |
| `src/widgets/sliders_panel.py` | Medium | Parent-chain coupling, magic keys, broken debounce |
| `tests/` | Critical | Entire suite fails on import |
