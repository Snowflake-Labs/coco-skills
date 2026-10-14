"""Tests for analyze_scala.analyze_files_concurrently (T2-1 file-level parallelism).

Guarantees: output is concatenated in file order regardless of completion order
(determinism), and a failing file surfaces its exception in lowest-file-index
order (fail-fast preserved).
"""

from __future__ import annotations

import time

import pytest

import analyze_scala as a


def test_concurrent_preserves_file_order():
    files = [f"f{i}" for i in range(6)]

    def one(fp):
        # Earlier files sleep longer so they COMPLETE later — this scrambles
        # completion order; output must still come back in file order.
        idx = int(fp[1:])
        time.sleep(0.02 * (6 - idx))
        return [{"file": fp}]

    serial = a.analyze_files_concurrently(files, one, file_workers=1)
    parallel = a.analyze_files_concurrently(files, one, file_workers=6)
    expected = [{"file": f} for f in files]
    assert serial == expected
    assert parallel == expected


def test_concurrent_flattens_multi_issue_files_in_order():
    files = ["a", "b"]

    def one(fp):
        return [{"file": fp, "n": 1}, {"file": fp, "n": 2}]

    out = a.analyze_files_concurrently(files, one, file_workers=2)
    assert out == [
        {"file": "a", "n": 1}, {"file": "a", "n": 2},
        {"file": "b", "n": 1}, {"file": "b", "n": 2},
    ]


def test_concurrent_fail_fast_lowest_index_wins():
    files = ["f0", "f1", "f2"]

    def one(fp):
        if fp in ("f1", "f2"):
            raise ValueError(f"boom-{fp}")
        return [{"file": fp}]

    with pytest.raises(ValueError) as ei:
        a.analyze_files_concurrently(files, one, file_workers=3)
    # f1 is the lowest-indexed failing file → its error must be the one raised.
    assert "boom-f1" in str(ei.value)


def test_concurrent_empty_returns_empty():
    assert a.analyze_files_concurrently([], lambda fp: [{"x": 1}], file_workers=4) == []


def test_concurrent_clamps_workers_to_file_count():
    files = ["only"]
    # file_workers far exceeds file count — must still work (clamped).
    out = a.analyze_files_concurrently(files, lambda fp: [{"file": fp}], file_workers=99)
    assert out == [{"file": "only"}]
