#!/usr/bin/env python3
"""
Test runner for FreeCCR.

Runs each test file in its OWN pytest subprocess (process isolation), then
aggregates the results. This is deliberate, not a convenience: the GUI/native
tests build and tear down hundreds of Qt MainWindows, QThreads, OpenCL contexts
and rawpy decodes, and running them all in a single process accumulates native
heap corruption that segfaults an otherwise-green suite near the end (a *roaming*
crash — the location moves between runs). fork() isn't available on Windows, so
per-file subprocesses are the portable isolation. Every file passes cleanly on
its own, so isolation makes the whole run reliable and reportable.

Usage:
    python tests/run_tests.py                 # whole suite, isolated per file
    python tests/run_tests.py test_crop_panel # one file (name, with/without .py)
    python tests/run_tests.py crop            # substring match across files
"""
import os
import re
import sys
import subprocess
from pathlib import Path

# Matches the counts in pytest's terminal summary, e.g. "2 failed, 25 passed".
_COUNT = re.compile(r"(\d+)\s+(passed|failed|error|errors|skipped|xfailed|xpassed)")


def _select(tests_dir: Path, args: list[str]) -> list[Path]:
    if not args:
        return sorted(tests_dir.glob("test_*.py"))
    found: set[Path] = set()
    for a in args:
        stem = a[:-3] if a.endswith(".py") else a
        matches = list(tests_dir.glob(f"{stem}.py")) or list(tests_dir.glob(f"*{stem}*.py"))
        found.update(m for m in matches if m.name.startswith("test_"))
    return sorted(found)


def _counts(output: str) -> dict:
    """Parse pass/fail/etc. from the last pytest summary line present."""
    for line in reversed(output.splitlines()):
        if any(k in line for k in (" passed", " failed", " error", " skipped")):
            hits = _COUNT.findall(line)
            if hits:
                d: dict = {}
                for n, kind in hits:
                    d[kind.rstrip("s")] = d.get(kind.rstrip("s"), 0) + int(n)
                return d
    return {}


def main() -> int:
    tests_dir = Path(__file__).parent
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")  # headless Qt for CI

    files = _select(tests_dir, sys.argv[1:])
    if not files:
        print("No matching test files.")
        return 5

    print("FreeCCR Test Suite  (per-file process isolation)")
    print("=" * 66)
    total = {"passed": 0, "failed": 0, "error": 0, "skipped": 0}
    problems: list[tuple[str, str]] = []

    for f in files:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(f), "-q", "-p", "no:cacheprovider"],
            cwd=tests_dir.parent, env=env, capture_output=True, text=True)
        out = proc.stdout + proc.stderr
        c = _counts(out)
        for k in total:
            total[k] += c.get(k, 0)
        native_fault = "fatal exception" in out.lower() or "access violation" in out.lower()
        # pytest exit codes: 0 all-pass, 1 tests-failed, 5 no-tests; anything
        # else (or a faulthandler dump) means the process itself died natively.
        if proc.returncode not in (0, 1) or native_fault:
            status = "CRASH"
        elif proc.returncode == 1 or c.get("failed") or c.get("error"):
            status = "FAIL"
        else:
            status = "ok"
        if status != "ok":
            problems.append((f.name, status))
        extra = "".join(f", {c[k]} {k}" for k in ("failed", "error", "skipped") if c.get(k))
        print(f"  {status:5} {f.name:44} {c.get('passed', 0):>4} passed{extra}")

    print("=" * 66)
    print(f"TOTAL  {total['passed']} passed, {total['failed']} failed, "
          f"{total['error']} error, {total['skipped']} skipped  "
          f"({len(files)} files)")
    if problems:
        print("Problem files: " + ", ".join(f"{n} [{s}]" for n, s in problems))
        return 1
    print("All test files passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
