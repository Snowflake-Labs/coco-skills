"""Comparator parity tests for the Scala validation harness.

Mirrors the PR #3664 comparator regression tests added to the pyspark harness
(test_harness.py), adapted for the Scala harness's sibling comparator.py.

Covers:
  P1 — itertuples: _load_parquet still produces correct match/diverge results.
  P3 — _parquet_meta: metadata-only read returns correct names/count.
  P3 — disjoint fast-path: fully-disjoint Parquet schemas return 'diverge'
       with correct shape without materializing rows.
  P2 — no tiers: --tier absent from CLI help, _compute_aggregate_diff gone,
       exit codes 0/1/2 correct.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd
import pytest

# Import from the sibling harness directory
_HARNESS = Path(__file__).resolve().parents[1] / "harness"
if str(_HARNESS) not in sys.path:
    sys.path.insert(0, str(_HARNESS))

import comparator  # noqa: E402 — harness-local


# ---------------------------------------------------------------------------
# P1 — itertuples: _load_parquet correctness
# ---------------------------------------------------------------------------

def test_compare_mixed_types_and_nulls_match(tmp_path):
    """P1: vectorized _load_parquet must still yield a clean match on a frame
    mixing ints, floats, strings, and nulls (identical files -> match)."""
    df = pd.DataFrame({
        "ID": [1, 2, 3],
        "AMT": [1.5, 2.0, float("nan")],
        "NAME": ["a", None, "c"],
    })
    a = tmp_path / "a.parquet"
    b = tmp_path / "b.parquet"
    df.to_parquet(a, index=False)
    df.to_parquet(b, index=False)
    res = comparator.compare(str(a), str(b))
    assert res["result"] in {"match", "match_with_skips"}, res
    # null canonicalization preserved: NaN/None both canon to "" and match.
    assert res["shape"]["baseline"]["rows"] == 3


def test_compare_int_float_value_diff_detected(tmp_path):
    """A genuine value difference is still flagged after the itertuples change."""
    a = tmp_path / "a.parquet"
    b = tmp_path / "b.parquet"
    pd.DataFrame({"ID": [1, 2], "V": [10, 20]}).to_parquet(a, index=False)
    pd.DataFrame({"ID": [1, 2], "V": [10, 999]}).to_parquet(b, index=False)
    res = comparator.compare(str(a), str(b))
    assert res["result"] == "diverge", res


# ---------------------------------------------------------------------------
# P3 — _parquet_meta helper
# ---------------------------------------------------------------------------

def test_parquet_meta_reads_names_and_count(tmp_path):
    """P3 helper: column names (uppercased) + row count from metadata only."""
    p = tmp_path / "t.parquet"
    pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]}).to_parquet(p, index=False)
    result = comparator._parquet_meta(str(p))
    assert result is not None
    names, n = result
    assert names == ["A", "B"] and n == 3
    assert comparator._parquet_meta(str(tmp_path / "missing.parquet")) is None


def test_compare_disjoint_schema_fast_path_matches_full_load(tmp_path):
    """P3: the metadata fast-path result for fully-disjoint schemas is identical
    to what the full-load path produces."""
    a = tmp_path / "a.parquet"
    b = tmp_path / "b.parquet"
    pd.DataFrame({"x": [1, 2], "y": [3, 4]}).to_parquet(a, index=False)
    pd.DataFrame({"p": ["m"], "q": ["n"]}).to_parquet(b, index=False)
    res = comparator.compare(str(a), str(b))
    assert res["result"] == "diverge"
    assert res["summary"] == "No shared columns between baseline and shadow"
    assert res["shape"] == {"baseline": {"rows": 2, "cols": 2},
                            "shadow": {"rows": 1, "cols": 2}}
    assert res["row_count_delta"] == -1
    assert res["schema_diff"]["missing_in_shadow"] == ["X", "Y"]
    assert res["schema_diff"]["extra_in_shadow"] == ["P", "Q"]
    assert res["row_diffs"] == []


# ---------------------------------------------------------------------------
# P2 — no tiers: CLI contract (the Scala harness shells out to comparator.py)
# ---------------------------------------------------------------------------

def test_comparator_cli_exit_codes_and_no_tiers(tmp_path):
    """The Scala runner depends on `comparator.py compare` + exit codes 0/1/2.
    Guard that contract; also confirm the tier machinery stays removed."""
    a = tmp_path / "a.parquet"
    b = tmp_path / "b.parquet"
    c = tmp_path / "c.parquet"
    pd.DataFrame({"id": [1, 2], "v": ["a", "b"]}).to_parquet(a, index=False)
    pd.DataFrame({"id": [1, 2], "v": ["a", "b"]}).to_parquet(b, index=False)
    pd.DataFrame({"id": [1, 2], "v": ["a", "X"]}).to_parquet(c, index=False)

    rc_match = comparator.main(["compare", "--baseline", str(a), "--shadow", str(b),
                                "--output", str(tmp_path / "m.json"), "--key-columns", "id"])
    rc_div = comparator.main(["compare", "--baseline", str(a), "--shadow", str(c),
                              "--output", str(tmp_path / "d.json"), "--ignore-columns", "FOO"])
    rc_miss = comparator.main(["compare", "--baseline", str(tmp_path / "nope.parquet"),
                               "--shadow", str(b), "--output", str(tmp_path / "x.json")])
    rc_none = comparator.main([])
    assert (rc_match, rc_div, rc_miss, rc_none) == (0, 1, 1, 2)

    # --tier option gone; _compute_aggregate_diff removed.
    assert "--tier" not in comparator._build_parser().format_help()
    assert not hasattr(comparator, "_compute_aggregate_diff")
