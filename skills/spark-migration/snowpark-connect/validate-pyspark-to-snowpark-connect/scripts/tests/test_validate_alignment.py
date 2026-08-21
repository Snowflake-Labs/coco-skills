"""Tests for validate._check_source_output_aligned notebook-pair handling."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import validate  # noqa: E402


def _mk(root, rel, content="x = 1\n"):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def test_aligned_notebook_pairs_py_ipynb(tmp_path):
    """source/<name>.py aligns with Output/<name>.py.ipynb (SnowConvert naming)
    and bare COMMON_UTILS.py aligns with COMMON_UTILS.ipynb — no exit."""
    src = tmp_path / "source"
    out = tmp_path / "Output"
    _mk(src, "job.py")
    _mk(src, "COMMON_UTILS.py")
    _mk(out, "job.py.ipynb")
    _mk(out, "COMMON_UTILS.ipynb")
    # returns None (no SystemExit) when every source file has a notebook pair
    assert validate._check_source_output_aligned(src, out, src) is None


def test_unaligned_source_file_raises(tmp_path):
    """A source file with no Output counterpart still stops with exit 2."""
    src = tmp_path / "source"
    out = tmp_path / "Output"
    _mk(src, "job.py")
    _mk(src, "orphan.py")
    _mk(out, "job.py.ipynb")
    with pytest.raises(SystemExit):
        validate._check_source_output_aligned(src, out, src)
