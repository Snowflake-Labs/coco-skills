#!/usr/bin/env python3
"""
Test the git repository check functionality via sma_api.
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
import sma_api  # noqa: E402


def test_git_check():
    print("=" * 80)
    print("Testing Git Repository Check")
    print("=" * 80)

    current_dir = Path.cwd()
    is_git = sma_api.git_is_repo(str(current_dir))
    print(f"\n1. Current directory: {current_dir}")
    print(f"   Is git repository: {is_git}")

    with tempfile.TemporaryDirectory() as tmpdir:
        is_git = sma_api.git_is_repo(tmpdir)
        print(f"\n2. Temporary directory: {tmpdir}")
        print(f"   Is git repository: {is_git}")

        test_file = Path(tmpdir) / "test.py"
        test_file.write_text('path = "s3://bucket/file.csv"')
        is_git = sma_api.git_is_repo(str(test_file.parent))
        print(f"\n3. File parent in temp directory: {test_file.parent}")
        print(f"   Is git repository: {is_git}")

    print("\n" + "=" * 80)
    print("Test complete!")
    print("=" * 80)


if __name__ == "__main__":
    test_git_check()
