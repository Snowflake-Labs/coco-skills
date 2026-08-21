"""Tests for datagen range-join overlap (V1) and the ``unique: true`` column flag.

Exercises the row-generation logic directly on in-memory rows (dicts) so no
parquet/pyarrow write is needed; seed_workload tests mock materialize() to
capture rows, matching test_datagen_hash.py.
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import datagen  # noqa: E402


_SCHEMA = [
    {"name": "key_col", "type": "string", "nullable": False},   # join key (pooled)
    {"name": "order_col", "type": "date", "nullable": True},
    {"name": "amount", "type": "double", "nullable": True},
]
_POOLS = {"key_col": ["A", "B", "C", "D"]}


# ---------------------------------------------------------------------------
# unique: true — column generated with all-distinct values
# ---------------------------------------------------------------------------

def test_unique_column_has_no_repeats():
    schema = [
        {"name": "pk", "type": "integer", "nullable": False, "unique": True},
        {"name": "label", "type": "string", "nullable": True},
    ]
    rows = datagen.generate_rows(schema, n=30, seed=42)
    pks = [r["pk"] for r in rows]
    assert len(pks) == len(set(pks)), f"unique column repeated a value: {pks}"


def test_unique_string_column_has_no_repeats():
    schema = [
        {"name": "uid", "type": "string", "nullable": False, "unique": True},
        {"name": "amount", "type": "double", "nullable": True},
    ]
    rows = datagen.generate_rows(schema, n=25, seed=7)
    uids = [r["uid"] for r in rows]
    assert len(uids) == len(set(uids)), f"unique string column repeated a value: {uids}"


def test_unique_yields_to_shared_join_pool():
    """A column marked ``unique`` that is ALSO in key_pools keeps the pool's
    (repeating) values — join overlap wins the documented tension."""
    schema = [{"name": "key_col", "type": "string", "nullable": False, "unique": True}]
    rows = datagen.generate_rows(schema, n=30, seed=42, key_pools=_POOLS)
    assert {r["key_col"] for r in rows} <= set(_POOLS["key_col"]), \
        "unique overrode the shared join pool (join overlap destroyed)"


def test_unique_flag_isolated_to_flagged_column():
    """Adding ``unique: true`` to ONE column must not change the generated values
    of the OTHER (non-unique, non-key) columns.

    Proves the feature is isolated/backward-compatible: a schema that gains the
    flag on a single column produces byte-identical values for every untouched
    column, so unaffected columns behave exactly as before the feature existed.
    """
    baseline = datagen.generate_rows(_SCHEMA, n=30, seed=42, key_pools=_POOLS)
    flagged = [dict(c, unique=True) if c["name"] == "order_col" else dict(c)
               for c in _SCHEMA]
    with_flag = datagen.generate_rows(flagged, n=30, seed=42, key_pools=_POOLS)

    assert len(baseline) == len(with_flag)
    for col in ("key_col", "amount"):
        assert [r[col] for r in baseline] == [r[col] for r in with_flag], \
            f"unique flag on order_col perturbed untouched column {col!r}"
    # sanity: the flag did take effect on its own column (values now all-distinct).
    order_vals = [r["order_col"] for r in with_flag]
    assert len(order_vals) == len(set(order_vals))


# ---------------------------------------------------------------------------
# V1 — range-join columns generated so >=1 row satisfies low <= point <= high
# ---------------------------------------------------------------------------

def _range_ep():
    return {
        "id": "ep1", "path": "m.py", "run_mode": "script", "import_roots": ["."],
        "entrypoint_kwargs": {}, "source_runtime": "spark", "joins": [],
        "range_join_edges": [
            {"point": "right_tbl.val", "low": "left_tbl.lo",
             "high": "left_tbl.hi"}
        ],
        "tables": {
            "right_tbl": {"relational": True, "category": "table", "access": "read",
                          "format": "parquet", "columns": [
                              {"name": "val", "type": "timestamp", "nullable": True},
                              {"name": "amt", "type": "double", "nullable": True}]},
            "left_tbl": {"relational": True, "category": "table", "access": "read",
                         "format": "parquet", "columns": [
                             {"name": "lo", "type": "timestamp", "nullable": True},
                             {"name": "hi", "type": "timestamp", "nullable": True},
                             {"name": "lbl", "type": "string", "nullable": True}]},
        },
    }


def test_v1_range_join_has_satisfying_pair(tmp_path):
    captured: dict = {}

    def _fake_materialize(rows, cols, path, fmt, opts=None):
        captured[str(path)] = [dict(r) for r in rows]

    with patch("datagen.materialize", side_effect=_fake_materialize):
        datagen.seed_workload([_range_ep()], str(tmp_path), n=12, force_all=True)

    right = next(v for k, v in captured.items() if k.endswith("right_tbl.parquet"))
    left = next(v for k, v in captured.items() if k.endswith("left_tbl.parquet"))
    pts = [r["val"] for r in right]
    ivals = [(r["lo"], r["hi"]) for r in left]
    sat = [1 for p in pts for lo, hi in ivals
           if None not in (p, lo, hi) and lo <= p <= hi]
    assert sat, "no row satisfied low <= point <= high (empty range sink)"


def test_unique_range_boundary_column_stays_distinct(tmp_path):
    """A column that is BOTH ``unique`` and a range boundary must not get a
    duplicate from anchor injection. Range wins (the predicate is still
    satisfied) while the guard writes the shared anchor into a single row, so the
    unique column keeps all-distinct values."""
    ep = _range_ep()
    # Flag the point column as unique — it is also a range boundary.
    for c in ep["tables"]["right_tbl"]["columns"]:
        if c["name"] == "val":
            c["unique"] = True

    captured: dict = {}

    def _fake_materialize(rows, cols, path, fmt, opts=None):
        captured[str(path)] = [dict(r) for r in rows]

    with patch("datagen.materialize", side_effect=_fake_materialize):
        datagen.seed_workload([ep], str(tmp_path), n=12, force_all=True)

    right = next(v for k, v in captured.items() if k.endswith("right_tbl.parquet"))
    left = next(v for k, v in captured.items() if k.endswith("left_tbl.parquet"))
    vals = [r["val"] for r in right]
    assert len(vals) == len(set(vals)), f"unique range-boundary column repeated: {vals}"
    ivals = [(r["lo"], r["hi"]) for r in left]
    sat = [1 for p in vals for lo, hi in ivals
           if None not in (p, lo, hi) and lo <= p <= hi]
    assert sat, "unique guard broke range overlap (empty range sink)"
