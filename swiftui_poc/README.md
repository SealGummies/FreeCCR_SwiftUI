# Phase 1 PoC — PythonKit + embedded CPython + FreeCCR core + Metal

Minimal feasibility check for the migration plan's Phase 1
(`/Users/seal/.claude/plans/precious-knitting-spindle.md`): can a Swift app
embed a standalone CPython, call FreeCCR's `src/core` (no PySide6 present),
and get pixels onto a Metal texture?

## Running it

```bash
./setup_embedded_python.sh          # fetches embedded CPython 3.11 + deps into ./python (~360MB, gitignored)
cd PythonMetalPoC
swift build
DYLD_LIBRARY_PATH="$(pwd)/../python/lib" .build/debug/PythonMetalPoC
```

Writes `output_preview.png` (512x384) next to this README.

## What it proves

`Sources/PythonMetalPoC/main.swift` does, in one process:

1. Points PythonKit at a **standalone embedded CPython 3.11** (no system
   Python, no PySide6 anywhere on `sys.path`).
2. Imports `core.ccr_image` / `core.ccr_processor` straight from
   `FreeCCR/src` and confirms `QT_AVAILABLE == False` — the Phase 0
   decoupling is what makes this importable at all.
3. Imports `rawpy` (LibRaw) to confirm the native RAW-decode dependency
   resolves inside the embedded interpreter.
4. Builds a synthetic 16-bit gradient frame and runs it through the exact
   `ccr_processor.adjust_image()` the Qt app's preview/export path calls.
5. Pulls the resulting numpy buffer back into Swift **zero-copy** (via the
   array's `ctypes.data` address, not element-by-element marshalling).
6. Uploads it to an `MTLTexture`, reads it back, and writes a PNG — proving
   the Metal leg of the pipeline.

## Findings against the plan's four validation points

| # | Question | Result |
|---|---|---|
| 1 | Do rawpy/opencv/imagecodecs `.so`s survive codesign under an embedded layout? | **No blocker found.** All three ad-hoc-codesigned cleanly (`codesign -s - --force --deep`, "valid on disk"). Their internal dylib cross-references use `@loader_path`-relative paths (correctly relocatable) — the `/DLC/cv2/.dylibs/...` strings visible in `otool -L` are just those dylibs' own cosmetic install-name (`LC_ID_DYLIB`), left over from the wheel's build environment; nothing actually *loads* them by that absolute path, everyone uses the `@loader_path` reference instead. **Not tested**: a real Developer ID sign + `notarytool` submission — this machine has zero codesigning identities installed (`security find-identity` returns none), so that step needs to happen on a machine with the actual Apple Developer credentials. |
| 2 | Does `xcrun notarytool` accept it? | **Untested — blocked on credentials**, see above. |
| 3 | GIL / call-threading constraints? | Not stress-tested with concurrent calls in this PoC (everything ran on the main thread). The plan's "single serial DispatchQueue for all PythonKit calls" rule should be treated as a requirement, not just a recommendation, going into Phase 3/4. |
| 4 | End-to-end latency? | One cold run: interpreter boot ~343ms, `import core + deps` (numpy/cv2/rawpy/ccr_processor) ~535ms, `adjust_image` on a 512x384 frame ~12ms, Metal upload/readback/PNG ~80ms combined. **Interpreter boot + imports (~880ms) is a one-time startup cost**, not per-frame — a live slider drag would only re-pay the ~12ms `adjust_image` call plus texture upload, which is comfortably interactive. This wasn't run against a real multi-megapixel RAW decode, which is the actual per-image cost that matters for "open a folder of scans" — not measured here. |

## What this PoC does *not* cover

- A real RAW file decode through `rawpy` end-to-end (only import-resolves it;
  no `.arw`/`.dng` fixture was on hand — see the plan's Phase 1 write-up).
- Real Developer ID signing + notarization.
- Concurrent/threaded PythonKit calls (GIL behavior under load).
- Any onnxruntime CoreML inference call (only confirmed the module imports
  and lists `CoreMLExecutionProvider`).
