import Foundation

/// A dedicated background `Thread` with a generously large stack, used as
/// the single serial execution context for every PythonKit call.
///
/// Why not `DispatchQueue`: numpy's linear-algebra module (`_umath_linalg`,
/// backed by OpenBLAS) does a stack-hungry self-check on `import numpy` —
/// large enough to blow the ~512KB stack GCD gives its pooled worker
/// threads, crashing with `EXC_BAD_ACCESS` inside `dgetrf_parallel` /
/// `___chkstk_darwin` (confirmed via lldb while wiring this app's canvas up
/// to a `DispatchQueue`-based executor). A plain `Thread` lets us set
/// `stackSize` explicitly before it starts.
final class SerialPythonExecutor: @unchecked Sendable {
    private let semaphore = DispatchSemaphore(value: 0)
    private let lock = NSLock()
    private var workItems: [() -> Void] = []

    init(stackSize: Int = 16 * 1024 * 1024) {
        let thread = Thread { [weak self] in
            self?.runLoop()
        }
        thread.stackSize = stackSize
        thread.name = "com.freeccr.pythonkit"
        thread.start()
    }

    private func runLoop() {
        while true {
            semaphore.wait()
            let item: (() -> Void)?
            lock.lock()
            item = workItems.isEmpty ? nil : workItems.removeFirst()
            lock.unlock()
            item?()
        }
    }

    func async(_ work: @escaping () -> Void) {
        lock.lock()
        workItems.append(work)
        lock.unlock()
        semaphore.signal()
    }
}
