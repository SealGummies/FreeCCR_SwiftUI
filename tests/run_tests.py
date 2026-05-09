#!/usr/bin/env python3
"""
Test runner for FreeCCR activation system tests.
Run this script to execute all activation-related tests.
"""

import os
import sys
import subprocess
from pathlib import Path

def run_test_file(test_file: str) -> bool:
    """Run a single test file and return success status."""
    print(f"\n{'='*60}")
    print(f"Running {test_file}")
    print(f"{'='*60}")
    
    try:
        result = subprocess.run(
            [sys.executable, test_file],
            cwd=Path(__file__).parent,
            capture_output=False,
            text=True
        )
        return result.returncode == 0
    except Exception as e:
        print(f"Error running {test_file}: {e}")
        return False

def main():
    """Run all activation tests."""
    print("FreeCCR Activation System Test Suite")
    print("=" * 60)
    
    tests_dir = Path(__file__).parent
    test_files = [
        "test_activation_basic.py",
        "test_activation_security.py"
    ]
    
    results = {}
    
    for test_file in test_files:
        test_path = tests_dir / test_file
        if test_path.exists():
            results[test_file] = run_test_file(str(test_path))
        else:
            print(f"Warning: Test file {test_file} not found")
            results[test_file] = False
    
    # Print summary
    print(f"\n{'='*60}")
    print("TEST SUMMARY")
    print(f"{'='*60}")
    
    passed = 0
    total = len(results)
    
    for test_file, success in results.items():
        status = "✅ PASSED" if success else "❌ FAILED"
        print(f"{test_file:<40} {status}")
        if success:
            passed += 1
    
    print(f"\nResults: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All tests passed!")
        return 0
    else:
        print("⚠️  Some tests failed!")
        return 1

if __name__ == "__main__":
    sys.exit(main())
