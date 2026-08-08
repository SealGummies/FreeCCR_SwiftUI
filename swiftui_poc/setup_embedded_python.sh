#!/bin/bash
# Reproduces the embedded CPython 3.11 environment used by PythonMetalPoC.
# Not committed: the resulting ./python/ directory (~360MB with deps).
set -euo pipefail
cd "$(dirname "$0")"

PY_RELEASE_TAG="20260807"
PY_VERSION="3.11.15+${PY_RELEASE_TAG}"
ASSET="cpython-${PY_VERSION}-aarch64-apple-darwin-install_only.tar.gz"
URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PY_RELEASE_TAG}/${ASSET}"

echo "Fetching ${URL}"
curl -L -o /tmp/cpython_standalone.tar.gz "$URL"
rm -rf ./python
tar -xzf /tmp/cpython_standalone.tar.gz
rm -f /tmp/cpython_standalone.tar.gz

# Phase 1 deps only: everything in ../requirements.txt EXCEPT PySide6 (the
# whole point is Qt must be absent), nuitka (packaging tool, not a runtime
# dep), pyopencl (deprecated on macOS — dropped per the migration plan), and
# onnxruntime-directml (Windows-only).
./python/bin/python3.11 -m pip install --no-user --upgrade pip
./python/bin/python3.11 -m pip install --no-user \
    "numpy>=1.24,<2.0" exifread opencv-python rawpy tifffile imagecodecs \
    requests onnxruntime

echo "Embedded Python ready at $(pwd)/python"
