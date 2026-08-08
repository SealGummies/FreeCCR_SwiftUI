// Boots the embedded, standalone CPython 3.11 set up by
// swiftui_poc/setup_embedded_python.sh (Phase 1) and points sys.path at
// FreeCCR/src. Dev-stage only: paths are anchored to this checkout via
// #filePath, not to an app bundle — Phase 5 replaces this with a bundled
// Python.framework resolved relative to the .app.

import Foundation
import PythonKit

public enum PythonEnvironment {
    /// FreeCCR checkout root, derived from this source file's own location
    /// (native/Sources/PythonBridge/PythonEnvironment.swift) rather than a
    /// hardcoded path, so it survives the repo being cloned elsewhere.
    public static let repoRoot: String = {
        let thisFile = URL(fileURLWithPath: #filePath)
        // .../<repoRoot>/native/Sources/PythonBridge/PythonEnvironment.swift
        return thisFile
            .deletingLastPathComponent() // -> .../native/Sources/PythonBridge
            .deletingLastPathComponent() // -> .../native/Sources
            .deletingLastPathComponent() // -> .../native
            .deletingLastPathComponent() // -> <repoRoot>
            .path
    }()

    public static let pocPythonHome = repoRoot + "/swiftui_poc/python"

    // Safety argument the compiler can't see: bootIfNeeded() is only ever
    // called from PythonCoreBridge's single serial queue.
    nonisolated(unsafe) private static var didBoot = false

    /// Idempotent: safe to call from every entry point. Must be called
    /// before any other PythonKit usage, and only ever from `PythonCallQueue`
    /// (see below) once the app is running.
    public static func bootIfNeeded() {
        guard !didBoot else { return }
        didBoot = true

        let pythonLib = pocPythonHome + "/lib/libpython3.11.dylib"
        guard FileManager.default.fileExists(atPath: pythonLib) else {
            fatalError("""
                Embedded Python not found at \(pythonLib).
                Run swiftui_poc/setup_embedded_python.sh first (see swiftui_poc/README.md).
                """)
        }

        setenv("PYTHONHOME", pocPythonHome, 1)
        setenv("PYTHON_LIBRARY", pythonLib, 1)
        setenv("PYTHONNOUSERSITE", "1", 1)

        let sys = Python.import("sys")
        sys.path.insert(0, pocPythonHome + "/lib/python3.11/site-packages")
        sys.path.insert(0, repoRoot + "/src")
    }
}
