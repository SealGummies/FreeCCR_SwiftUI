# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

FreeCCR is a cross-platform desktop application for batch image preview, selection, negative film conversion, and color correction. It supports RAW and standard image formats with a PySide6 (Qt) GUI and compiles to a standalone executable via Nuitka.

## Dev Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Generate version file from git tags (required before first run)
python write_version.py

# Run the app in development
python src/main.py

# Run all tests
python tests/run_tests.py

# Run with pytest
pytest tests/ -v
pytest tests/test_pytest_activation.py -v   # specific file

# Build standalone exe (Windows)
./build_exe.bat
# or via Makefile:
make build-windows

# macOS build
make build
make build-compatible    # older macOS targets (MACOSX_DEPLOYMENT_TARGET=10.15)

# Clean build artifacts
make clean
```

**Critical version requirement**: Python 3.11.0 exactly — newer versions fail with Nuitka compilation.

## Architecture

The app follows a layered MVC-like pattern:

```
src/main.py                 → QApplication setup, launches MainWindow
src/ui/main_window.py       → Main window, menus, file dialogs (~380 lines)
src/core/ccr_backend.py     → Singleton managing all loaded CCRImage instances
src/core/ccr_image.py       → Image model — RAW/standard format abstraction
src/core/ccr_processor.py   → Color adjustment math, lens correction, OpenCL GPU kernels
src/widgets/thumbnail_list.py  → Sidebar thumbnails with async loading dialog
src/widgets/image_preview.py   → Central canvas: histogram, zoom, reference frame
src/widgets/sliders_panel.py   → Adjustment controls (Kelvin, tint, exposure, etc.)
src/activation/activation.py   → License validation (disabled — always returns True)
src/utils/unicode_path_utils.py → Cross-platform Unicode filename handling
```

**CCRBackend** is a global singleton — always access loaded images through it rather than holding direct CCRImage references.

## Key Patterns

**Async image loading**: `ImageLoaderWorker` (QObject subclass) runs in QThread with a `ThreadPoolExecutor` of up to 8 workers. Images are sorted by filename after the parallel load completes. A cancellable progress dialog is shown during batch loads.

**Image processing pipeline**: RAW files decoded via rawpy → 16-bit numpy arrays → resized to 1080px max side → `ccr_processor.adjust_image()` applies color corrections. OpenCL GPU acceleration is optional and conditionally compiled. Thumbnails (8-bit) are generated separately from full previews.

**Negative inversion**: Uses the v0.2.3 method — per-channel linear black/white-point normalisation against the reference crop (1st/99th percentiles), an optical-density mean-equalisation for cast balance, a linear `65535 - v` inversion, then `apply_postinvert_look` (saturation boost + shadow warmth). `ccr_normalize_with_reference` (auto reference frame) and `ccr_normalize_with_bwpoint` (user-sampled clear/dense points) share this path; the resolution-independent replay (`compute_reference_norm_params` → `apply_reference_normalization`, params `p_lo`/`p_hi`/`od_factors`) reproduces it for zoom/slice/export.

> **Parked experiment — density-space inversion** (branch `feature/density-tone-rendering`, do not delete): a physically-faithful Cineon/negadoctor-style inversion in optical-density (log) space (subtract `Dmin` offset, divide by film gamma, recover scene-linear `H = 10^(d/γ)`, then a display render). It was made permanent on `main` for a few releases (PRs #23/#24) but **reverted** here because converted frames came out far too dark (subject near-black, needed ~+50 brightness) with amplified chroma noise in the shadows — the faithful log stretch reveals scan/grain noise that the v0.2.3 affine pre-balance hid. The branch carries the full density pipeline + a brightness-norm/simple-gamma tone stage and env knobs (`FREECCR_DENSITY_*`); revisit if attempting a faithful inversion again. Note: a per-channel *multiply* before the log is a clean density offset (no tone-dependent color shift); a per-channel *black-subtract*-before-log (v0.2.3's stretch) warps each channel differently and is what trades faithfulness for the brighter, lower-noise look.

**Unicode path handling**: Windows non-ASCII filenames require `normalize_unicode_path()` and `validate_unicode_path()` from `src/utils/unicode_path_utils.py`. Always validate paths before passing to image loaders.

**Resource resolution**: `resource_path()` in `image_preview.py` handles both dev and Nuitka-bundled contexts. Nuitka embeds `src/icons/` and `LICENSES/` directories at build time.

**Activation system**: The license verification code in `src/activation/` is present but bypassed — `is_activated()` always returns `True` in this build. Tests in `tests/test_activation_security.py` still validate the HMAC-SHA256 signing logic.

## Build Output

- Windows: `freeccr.exe` (standalone, no Python runtime needed)
- macOS: `.app` bundle (via build scripts in `macos_build_scripts/`)
- Version string is read from `src/version.py`, generated by `write_version.py` from git tags

## Windows Installer Build

```bash
# Step 1: build the standalone exe
./build_exe.bat

# Step 2: build the installer package
# Run the installer build script inside windows_build_scripts/
```

## Workflow

For every major change: create a new branch, make the changes, then open a PR back to `main`.
