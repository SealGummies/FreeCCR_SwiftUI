#!/usr/bin/env python3
"""
Test runner for FreeCCR: runs the full pytest suite.
"""

import os
import sys
import subprocess
from pathlib import Path


def main() -> int:
    tests_dir = Path(__file__).parent
    print("FreeCCR Test Suite")
    print("=" * 60)
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")  # headless Qt for CI
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(tests_dir), "-v"],
        cwd=tests_dir.parent,
        env=env,
    )
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
