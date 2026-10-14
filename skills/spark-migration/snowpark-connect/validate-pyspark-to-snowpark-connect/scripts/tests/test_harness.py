"""Consolidated unit tests for the harness package.

Tests from: test_assemble_analysis.py, test_helpers_io.py,
test_provision_hashes.py, test_comparator_dirparquet.py.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "harness"))
from helpers import (  # noqa: E402
    assemble_analysis,
    _io_id_from_name,
    _load_schemas_json,
    _run_table_captures,
    _table_filename,
    capture_results,
    declared_allow_empty_sink_tables,
    declared_sink_capture_specs,
    declared_sink_tables,
    load_entrypoint,
    load_provision_hashes,
    merge_entrypoint,
    provision_hash_matches,
    requires_nonempty_sink_capture,
    record_provision_hash,
    save_provision_hashes,
    seed_entrypoint,
    split_entrypoint,
    validate_declared_sink_outputs,
)
import comparator  # noqa: E402


# ===========================================================================
# assemble_analysis tests (from test_assemble_analysis.py)
# ===========================================================================


def _write_ep_dir(ep_dir: Path, ep: dict) -> None:
    """Write an entrypoint in the new directory layout under ep_dir."""
    ep_id = ep["id"]
    d = ep_dir / ep_id
    d.mkdir(parents=True, exist_ok=True)
    meta = {k: v for k, v in ep.items() if k != "tables"}
    (d / "_meta.json").write_text(json.dumps(meta))
    tables = ep.get("tables") or {}
    if tables:
        (d / "tables").mkdir(exist_ok=True)
        used: set = set()
        for key, tbl in tables.items():
            fname = _table_filename(key, used)
            tbl_data = dict(tbl)
            tbl_data["_table_key"] = key
            (d / "tables" / (fname + ".json")).write_text(json.dumps(tbl_data))


def test_assemble_from_manifest(tmp_path):
    """Returns entrypoints in manifest order with sorted union of import_roots."""
    schemas_dir = tmp_path / "schemas"
    ep_dir = schemas_dir / "entrypoints"
    ep_dir.mkdir(parents=True)

    ep_a = {
        "id": "ep_alpha",
        "import_roots": ["lib/", "shared/"],
        "tables": {"t1": {"columns": [{"name": "x", "type": "int"}]}},
    }
    ep_b = {
        "id": "ep_beta",
        "import_roots": ["shared/", "utils/"],
        "tables": {"t2": {"columns": [{"name": "y", "type": "string"}]}},
    }

    _write_ep_dir(ep_dir, ep_a)
    _write_ep_dir(ep_dir, ep_b)

    manifest = {
        "entrypoints": [
            {"id": "ep_alpha", "path": "main.py", "dir": "entrypoints/ep_alpha"},
            {"id": "ep_beta", "path": "main.py", "dir": "entrypoints/ep_beta"},
        ]
    }
    (schemas_dir / "manifest.json").write_text(json.dumps(manifest))

    result = assemble_analysis(str(schemas_dir))

    # Entrypoints returned in manifest order
    assert [ep["id"] for ep in result["entrypoints"]] == ["ep_alpha", "ep_beta"]
    # import_roots is the sorted union
    assert result["import_roots"] == ["lib/", "shared/", "utils/"]


def test_assemble_raises_on_manifest_entry_missing_id(tmp_path):
    """A manifest entrypoint ref without 'id' must fail loudly, not be skipped."""
    schemas_dir = tmp_path / "schemas"
    schemas_dir.mkdir(parents=True)
    manifest = {
        "entrypoints": [
            {"path": "main.py", "dir": "entrypoints/ep_alpha"},  # no 'id'
        ]
    }
    (schemas_dir / "manifest.json").write_text(json.dumps(manifest))

    import pytest
    with pytest.raises(ValueError, match="missing 'id'"):
        assemble_analysis(str(schemas_dir))


def test_assemble_fallback_no_manifest(tmp_path):
    """Falls back to globbing entrypoints/*/ DIRECTORIES when manifest is missing."""
    schemas_dir = tmp_path / "schemas"
    ep_dir = schemas_dir / "entrypoints"
    ep_dir.mkdir(parents=True)

    ep_a = {"id": "aaa", "import_roots": ["r1"]}
    ep_b = {"id": "bbb", "import_roots": ["r2"]}

    _write_ep_dir(ep_dir, ep_a)
    _write_ep_dir(ep_dir, ep_b)

    result = assemble_analysis(str(schemas_dir))

    # Globbed alphabetically
    assert [ep["id"] for ep in result["entrypoints"]] == ["aaa", "bbb"]
    assert result["import_roots"] == ["r1", "r2"]


def test_assemble_empty_schemas_dir(tmp_path):
    """Returns empty entrypoints and import_roots when schemas dir is bare."""
    schemas_dir = tmp_path / "schemas"
    schemas_dir.mkdir()

    result = assemble_analysis(str(schemas_dir))
    assert result == {"entrypoints": [], "import_roots": [], "auxiliary_files": []}


# ===========================================================================
# _io_id_from_name and _load_schemas_json tests (from test_helpers_io.py)
# ===========================================================================


class _NoCatalogSession:
    """Fake session whose catalog enumeration yields nothing (raises in .sql)."""

    def sql(self, _query):  # pragma: no cover - exercised via capture_results
        raise RuntimeError("no catalog in this test")


def test_io_id_normalization():
    assert _io_id_from_name("*") == "STAR"
    assert _io_id_from_name("My Table") == "MY_TABLE"
    assert _io_id_from_name("a.b") == "A_B"


def test_load_schemas_json(tmp_path):
    assert _load_schemas_json(str(tmp_path)) == {}

    schemas_dir = tmp_path / "schemas"
    schemas_dir.mkdir()
    data = {"employee": [{"name": "id", "type": "integer"}]}
    (schemas_dir / "schemas.json").write_text(json.dumps(data))
    assert _load_schemas_json(str(tmp_path)) == data

    (schemas_dir / "schemas.json").write_text("{NOT VALID JSON!!!")
    assert _load_schemas_json(str(tmp_path)) == {}


def test_filesystem_sink_slug_has_no_label_prefix(tmp_path):
    """A path-form file sink is captured under its bare io_id (e.g. ``column_data``),
    NOT ``sink__column_data``. This keeps SCOS/local capture names identical to the
    Databricks runtime (which names file sinks by bare io_id), so Phase A and Phase B
    captures of the same sink compare cleanly without manual renames.
    """
    output_dir = tmp_path / "phase_b" / "trial1"
    output_dir.mkdir(parents=True)
    sink_dir = tmp_path / "_sinks"
    # Workload wrote SCOS_SINK_COLUMN_DATA -> <sink_dir>/column_data/part-*.txt
    (sink_dir / "column_data").mkdir(parents=True)
    (sink_dir / "column_data" / "part-00000.txt").write_text("hello\nworld\n")

    manifest = capture_results(
        _NoCatalogSession(), "SOME_SCHEMA", str(output_dir), str(sink_dir)
    )

    names = {t["name"] for t in manifest["tables"]}
    assert "column_data" in names, names
    assert "sink__column_data" not in names, names


class TestRunTableCaptures:
    """_run_table_captures parallelizes independent table captures with a serial fallback."""

    def test_runs_fn_over_all_names_parallel(self):
        names = [f"t{i}" for i in range(12)]
        results = _run_table_captures(names, lambda n: ("ok", n))
        assert sorted(payload for _, payload in results) == sorted(names)

    def test_single_name_runs_serial(self):
        assert _run_table_captures(["only"], lambda n: ("ok", n)) == [("ok", "only")]

    def test_empty_names(self):
        assert _run_table_captures([], lambda n: ("ok", n)) == []

    def test_serial_when_workers_disabled(self, monkeypatch):
        import helpers
        monkeypatch.setattr(helpers, "_TABLE_CAPTURE_WORKERS", 1)
        names = ["a", "b", "c"]
        # workers<=1 → serial path preserves input order
        results = helpers._run_table_captures(names, lambda n: ("ok", n))
        assert [p for _, p in results] == names

    def test_concurrent_reads_are_isolated(self):
        # Each call increments a shared counter under the GIL; all must run.
        seen: list[str] = []
        lock = __import__("threading").Lock()

        def fn(n):
            with lock:
                seen.append(n)
            return ("ok", n)

        names = [f"t{i}" for i in range(20)]
        results = _run_table_captures(names, fn)
        assert len(results) == 20
        assert sorted(seen) == sorted(names)

    def test_concurrency_induced_failure_clears_on_serial_retry(self):
        # A table that 'fails' on its first (parallel) call but succeeds on the
        # serial retry must end 'ok' — the concurrency-artifact guard.
        lock = __import__("threading").Lock()
        calls: dict = {}

        def fn(n):
            with lock:
                calls[n] = calls.get(n, 0) + 1
                first = calls[n] == 1
            return ("fail", {"name": n}) if (n == "t3" and first) else ("ok", n)

        names = [f"t{i}" for i in range(8)]
        results = dict(
            (payload if status == "ok" else payload["name"], status)
            for status, payload in _run_table_captures(names, fn)
        )
        assert results["t3"] == "ok"          # cleared on serial retry
        assert calls["t3"] == 2               # retried exactly once

    def test_genuine_failure_stays_failed_after_retry(self):
        def fn(n):
            return ("fail", {"name": n}) if n == "bad" else ("ok", n)

        results = {
            (p if s == "ok" else p["name"]): s
            for s, p in _run_table_captures(["a", "bad", "c"], fn)
        }
        assert results["bad"] == "fail"

    def test_pool_level_failure_falls_back_to_serial(self, monkeypatch):
        # Force the ThreadPoolExecutor to blow up on construction; the full run
        # must still complete serially.
        import concurrent.futures as cf

        def _boom(*a, **k):
            raise RuntimeError("pool unavailable")

        monkeypatch.setattr(cf, "ThreadPoolExecutor", _boom)
        names = ["a", "b", "c"]
        results = _run_table_captures(names, lambda n: ("ok", n))
        assert [p for _, p in results] == names


class _FakeWriter:
    def __init__(self, targets: list[str]):
        self._targets = targets

    def mode(self, _mode: str):
        return self

    def saveAsTable(self, target: str):
        self._targets.append(target)


class _FakeDataFrame:
    def __init__(self, targets: list[str]):
        self.write = _FakeWriter(targets)


class _FakeSeedSession:
    def __init__(self):
        self.targets: list[str] = []

    def createDataFrame(self, _rows, _schema):
        return _FakeDataFrame(self.targets)


def test_seed_entrypoint_falls_back_to_declared_name_for_file_uri(tmp_path):
    session = _FakeSeedSession()
    entrypoint = {
        "tables": {
            "source_tbl": {
                "access": "write",
                "category": "table",
                "original_path": "s3://bucket/path/source_tbl",
                "columns": [{"name": "id", "type": "int"}],
            }
        }
    }

    seeded = seed_entrypoint(session, entrypoint, str(tmp_path), output_schema="OUT")

    assert seeded == ["out.source_tbl"]
    assert session.targets == ["OUT.source_tbl"]


def test_declared_sink_tables_falls_back_to_declared_name_for_file_uri():
    entrypoint = {
        "tables": {
            "source_tbl": {
                "access": "write",
                "category": "table",
                "original_path": "s3://bucket/path/source_tbl",
            }
        }
    }

    assert declared_sink_tables(entrypoint, "OUT") == ["out.source_tbl"]


def test_entrypoint_schema_supports_allow_empty_reason_string():
    schema_path = Path(__file__).resolve().parents[2] / "schemas" / "entrypoint.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    allow_empty = schema["$defs"]["table_entry"]["properties"]["allow_empty"]
    assert allow_empty["type"] == "string"
    assert allow_empty["minLength"] == 1


def test_declared_sink_capture_specs_normalize_table_and_file_sinks():
    entrypoint = {
        "tables": {
            "orders": {
                "access": "write",
                "category": "table",
                "columns": [{"name": "id", "type": "int"}],
            },
            "daily export": {
                "access": "write",
                "category": "file",
                "allow_empty": "incremental no-op is valid for this fixture",
                "columns": [{"name": "id", "type": "int"}],
            },
        }
    }

    specs = declared_sink_capture_specs(entrypoint)
    assert set(specs) == {"orders", "daily_export"}
    assert specs["orders"]["allow_empty"] == ""
    assert specs["daily_export"]["allow_empty"] == "incremental no-op is valid for this fixture"


def test_declared_allow_empty_sink_tables_includes_table_and_file_sinks():
    entrypoint = {
        "tables": {
            "orders": {
                "access": "write",
                "category": "table",
                "allow_empty": "incremental no-op is valid for this fixture",
                "columns": [{"name": "id", "type": "int"}],
            },
            "daily export": {
                "access": "write",
                "category": "file",
                "allow_empty": "file sink may be empty",
                "columns": [{"name": "id", "type": "int"}],
            },
        }
    }

    assert declared_allow_empty_sink_tables(entrypoint, "OUT") == [
        "out.orders",
        "daily_export",
    ]


def test_requires_nonempty_sink_capture_honors_allow_empty():
    allow_empty_only = {
        "tables": {
            "orders": {
                "access": "write",
                "category": "table",
                "allow_empty": "incremental no-op is valid for this fixture",
                "columns": [{"name": "id", "type": "int"}],
            }
        }
    }
    mixed = {
        "tables": {
            "orders": {
                "access": "write",
                "category": "table",
                "columns": [{"name": "id", "type": "int"}],
            },
            "daily export": {
                "access": "write",
                "category": "file",
                "allow_empty": "file sink may be empty",
                "columns": [{"name": "id", "type": "int"}],
            },
        }
    }

    assert requires_nonempty_sink_capture(allow_empty_only) is False
    assert requires_nonempty_sink_capture(mixed) is True


def test_validate_declared_sink_outputs_requires_rows_and_gives_actionable_message():
    entrypoint = {
        "tables": {
            "orders": {
                "access": "write",
                "category": "table",
                "columns": [{"name": "id", "type": "int"}],
            }
        }
    }

    failures = validate_declared_sink_outputs(entrypoint, {"tables": []})

    assert len(failures) == 1
    assert failures[0]["reason"] == "empty_declared_sink"
    assert failures[0]["critical"] is True
    assert "Fix the mock/schema data so the sink becomes non-empty" in failures[0]["message"]
    assert "set allow_empty to a short reason string" in failures[0]["message"]


def test_validate_declared_sink_outputs_allows_missing_allow_empty_file_sink():
    entrypoint = {
        "tables": {
            "daily export": {
                "access": "write",
                "category": "file",
                "allow_empty": "incremental no-op is valid for this fixture",
                "columns": [{"name": "id", "type": "int"}],
            }
        }
    }

    assert validate_declared_sink_outputs(entrypoint, {"tables": []}) == []


def test_validate_declared_sink_outputs_accepts_partitioned_file_sink_rows():
    entrypoint = {
        "tables": {
            "daily export": {
                "access": "write",
                "category": "file",
                "columns": [{"name": "id", "type": "int"}],
            }
        }
    }
    manifest = {
        "tables": [{
            "name": "daily_export__year_2024",
            "row_count": 3,
            "rel_path": "daily_export/year=2024",
        }]
    }

    assert validate_declared_sink_outputs(entrypoint, manifest) == []


def test_validate_declared_sink_outputs_accepts_artifact_file_sink_output():
    entrypoint = {
        "tables": {
            "daily export": {
                "access": "write",
                "category": "file",
                "columns": [{"name": "id", "type": "int"}],
            }
        }
    }
    manifest = {
        "tables": [],
        "artifacts": [{
            "name": "daily_export",
            "rel_path": "daily_export/report.xlsx",
        }],
    }

    assert validate_declared_sink_outputs(entrypoint, manifest) == []


# ===========================================================================
# provision hash store tests (from test_provision_hashes.py)
# ===========================================================================


def test_load_missing_file(tmp_path):
    """load_provision_hashes returns {} when the file doesn't exist."""
    result = load_provision_hashes(tmp_path)
    assert result == {}


def test_load_corrupt_file(tmp_path):
    """load_provision_hashes returns {} on corrupt JSON."""
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "provision_hashes.json").write_text("NOT JSON{{{")
    result = load_provision_hashes(tmp_path)
    assert result == {}


def test_record_and_match():
    """record_provision_hash then provision_hash_matches returns True."""
    store = {}
    record_provision_hash(store, "scos", "ep1", "my_table", "abc123")
    assert provision_hash_matches(store, "scos", "ep1", "my_table", "abc123")


def test_no_match_changed_hash():
    """provision_hash_matches returns False when hash differs."""
    store = {}
    record_provision_hash(store, "scos", "ep1", "my_table", "abc123")
    assert not provision_hash_matches(store, "scos", "ep1", "my_table", "def456")


def test_no_match_missing_entry():
    """provision_hash_matches returns False for unknown entries."""
    store = {}
    assert not provision_hash_matches(store, "scos", "ep1", "my_table", "abc123")


def test_save_load_roundtrip(tmp_path):
    """save_provision_hashes then load_provision_hashes round-trips."""
    store = {}
    record_provision_hash(store, "scos", "ep1", "tbl_a", "hash_a")
    record_provision_hash(store, "scos", "ep2", "tbl_b", "hash_b")
    save_provision_hashes(tmp_path, store)

    loaded = load_provision_hashes(tmp_path)
    assert loaded == store


def test_save_merge_preserves_other_flavors(tmp_path):
    """save_provision_hashes merges per-flavor — saving scos doesn't erase databricks."""
    # First save databricks entries
    store_dbx = {}
    record_provision_hash(store_dbx, "databricks", "ep1", "tbl_x", "hash_x")
    save_provision_hashes(tmp_path, store_dbx)

    # Then save scos entries
    store_scos = {}
    record_provision_hash(store_scos, "scos", "ep1", "tbl_y", "hash_y")
    save_provision_hashes(tmp_path, store_scos)

    # Both flavors survive
    loaded = load_provision_hashes(tmp_path)
    assert provision_hash_matches(loaded, "databricks", "ep1", "tbl_x", "hash_x")
    assert provision_hash_matches(loaded, "scos", "ep1", "tbl_y", "hash_y")


def test_save_merge_updates_existing_table(tmp_path):
    """save_provision_hashes updates existing table entries within a flavor."""
    store1 = {}
    record_provision_hash(store1, "scos", "ep1", "tbl_a", "old_hash")
    save_provision_hashes(tmp_path, store1)

    store2 = {}
    record_provision_hash(store2, "scos", "ep1", "tbl_a", "new_hash")
    save_provision_hashes(tmp_path, store2)

    loaded = load_provision_hashes(tmp_path)
    assert provision_hash_matches(loaded, "scos", "ep1", "tbl_a", "new_hash")
    assert not provision_hash_matches(loaded, "scos", "ep1", "tbl_a", "old_hash")


# ===========================================================================
# comparator directory-of-parquet tests (from test_comparator_dirparquet.py)
# ===========================================================================

_MATCH = {"match", "match_with_skips"}


def _write_parquet_dir(path: Path, df: pd.DataFrame) -> None:
    """Mimic Spark output: a directory named <name>.parquet with a part-file."""
    path.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path / "part-00000.parquet", index=False)


def test_comparator_reads_parquet_directory_match(tmp_path):
    df = pd.DataFrame({"id": [1, 2], "v": ["a", "b"]})
    b = tmp_path / "b.parquet"; _write_parquet_dir(b, df)
    s = tmp_path / "s.parquet"; _write_parquet_dir(s, df)
    res = comparator.compare(str(b), str(s))
    assert res["result"] in _MATCH


def test_comparator_detects_cell_divergence_in_directory(tmp_path):
    b = tmp_path / "b.parquet"; _write_parquet_dir(b, pd.DataFrame({"id": [1], "v": ["a"]}))
    s = tmp_path / "s.parquet"; _write_parquet_dir(s, pd.DataFrame({"id": [1], "v": ["DIFFERENT"]}))
    res = comparator.compare(str(b), str(s))
    assert res["result"].startswith("diverge")


def test_comparator_missing_baseline_dir(tmp_path):
    s = tmp_path / "s.parquet"; _write_parquet_dir(s, pd.DataFrame({"id": [1]}))
    res = comparator.compare(str(tmp_path / "nope.parquet"), str(s))
    assert res["result"] == "missing_baseline"


# ===========================================================================
# comparator natural-key uniqueness tests (ITEM L)
# ===========================================================================


def test_comparator_nonunique_keys_identical_duplicates_no_diffs(tmp_path):
    """Both sides have identical duplicate-key rows → no diffs (not false _ROW_: missing)."""
    df = pd.DataFrame({"id": [1, 1, 2], "v": ["a", "a", "b"]})
    b = tmp_path / "b.parquet"; _write_parquet_dir(b, df)
    s = tmp_path / "s.parquet"; _write_parquet_dir(s, df)
    res = comparator.compare(str(b), str(s), key_columns=["id"])
    assert res["result"] in _MATCH, res.get("row_diffs")


def test_comparator_nonunique_keys_shadow_missing_one_duplicate(tmp_path):
    """Shadow missing one of two identical baseline duplicates → exactly one _ROW_ diff."""
    b = tmp_path / "b.parquet"
    _write_parquet_dir(b, pd.DataFrame({"id": [1, 1], "v": ["a", "a"]}))
    s = tmp_path / "s.parquet"
    _write_parquet_dir(s, pd.DataFrame({"id": [1], "v": ["a"]}))
    res = comparator.compare(str(b), str(s), key_columns=["id"])
    assert res["result"].startswith("diverge"), res
    row_diffs = res["row_diffs"]
    assert len(row_diffs) == 1
    assert row_diffs[0]["field_diffs"][0]["col"] == "_ROW_"


def test_comparator_unique_keys_regression(tmp_path):
    """Unique-key path: value diffs are still reported correctly after the ITEM L change."""
    b = tmp_path / "b.parquet"
    _write_parquet_dir(b, pd.DataFrame({"id": [1, 2], "v": ["x", "y"]}))
    s = tmp_path / "s.parquet"
    _write_parquet_dir(s, pd.DataFrame({"id": [1, 2], "v": ["x", "DIFFERENT"]}))
    res = comparator.compare(str(b), str(s), key_columns=["id"])
    assert res["result"].startswith("diverge"), res
    all_cols = [fd["col"] for rd in res["row_diffs"] for fd in rd["field_diffs"]]
    assert "V" in all_cols


def test_comparator_document_divergence_suppresses_row_synthetic(tmp_path):
    """Documenting _ROW_ (via ignore_columns) suppresses the synthetic _ROW_ diff
    AND the row-count delta it implies, so the result is no longer a divergence."""
    b = tmp_path / "b.parquet"
    _write_parquet_dir(b, pd.DataFrame({"id": [1, 1], "v": ["a", "a"]}))
    s = tmp_path / "s.parquet"
    _write_parquet_dir(s, pd.DataFrame({"id": [1], "v": ["a"]}))
    # Without documenting, this diverges on a synthetic _ROW_ diff (see test above).
    res = comparator.compare(str(b), str(s), key_columns=["id"], ignore_columns={"_ROW_"})
    assert res["result"] in _MATCH, res
    assert res["row_count_delta"] == 0
    assert "_ROW_" in res["skipped_columns"]
    assert all(
        fd["col"] != "_ROW_" for rd in res["row_diffs"] for fd in rd.get("field_diffs", [])
    )


def test_comparator_document_divergence_row_count_no_key(tmp_path):
    """No-key multiset: a dropped row surfaces as a _ROW_ diff, and documenting
    _ROW_ suppresses it (and zeroes the row-count delta) to yield a match."""
    b = tmp_path / "b.parquet"
    _write_parquet_dir(b, pd.DataFrame({"v": ["a", "b", "c"]}))
    s = tmp_path / "s.parquet"
    _write_parquet_dir(s, pd.DataFrame({"v": ["a", "b"]}))
    res = comparator.compare(str(b), str(s), ignore_columns={"_ROW_"})
    assert res["result"] in _MATCH, res
    assert res["row_count_delta"] == 0


# ===========================================================================
# no-key fallback: sorted-positional comparison with tolerance
# ===========================================================================


def test_comparator_no_key_same_rows_different_order_matches(tmp_path):
    """Without a natural key, identical rows in a different order are a match."""
    b = tmp_path / "b.parquet"
    _write_parquet_dir(b, pd.DataFrame({"id": [1, 2, 3], "v": ["a", "b", "c"]}))
    s = tmp_path / "s.parquet"
    _write_parquet_dir(s, pd.DataFrame({"id": [3, 1, 2], "v": ["c", "a", "b"]}))
    res = comparator.compare(str(b), str(s))
    assert res["result"] in _MATCH, res
    assert res["row_diffs"] == []


def test_comparator_no_key_single_value_diff_is_reported(tmp_path):
    """A genuine value difference is reported as a per-column field_diff (the
    sorted-positional path preserves per-cell reporting, not drop+add)."""
    b = tmp_path / "b.parquet"
    _write_parquet_dir(b, pd.DataFrame({"id": [1, 2, 3], "v": ["a", "b", "c"]}))
    s = tmp_path / "s.parquet"
    _write_parquet_dir(s, pd.DataFrame({"id": [1, 2, 3], "v": ["a", "CHANGED", "c"]}))
    res = comparator.compare(str(b), str(s))
    assert res["result"].startswith("diverge"), res
    vdiffs = [fd for rd in res["row_diffs"] for fd in rd["field_diffs"] if fd["col"] == "V"]
    assert any(
        fd["baseline_value"] == "b" and fd["shadow_value"] == "CHANGED" for fd in vdiffs
    ), res["row_diffs"]


def test_comparator_no_key_float_within_tolerance_matches(tmp_path):
    """The no-key path preserves per-cell numeric tolerance: two rows equal except
    a float within the comparison tolerance are a match (no divergence)."""
    b = tmp_path / "b.parquet"
    _write_parquet_dir(b, pd.DataFrame({"id": [1, 2], "amt": [1.5, 2.5]}))
    s = tmp_path / "s.parquet"
    _write_parquet_dir(s, pd.DataFrame({"id": [1, 2], "amt": [1.5, 2.5000001]}))
    res = comparator.compare(str(b), str(s))
    assert res["result"] in _MATCH, res
    assert res["row_diffs"] == []


# ===========================================================================
# cleanup_session prefix-matching tests
# ===========================================================================

from runtimes.scos_runtime import _schemas_matching_run_prefix  # noqa: E402


def test_schemas_matching_run_prefix():
    """Prefix matching is exact-boundary, case-insensitive, and tolerant of trailing '_'."""
    names = [
        "PROJ_RUN1_EP_GOLDEN",
        "PROJ_RUN1_EP_ABC123",
        "PROJ_RUN10_OTHER_GOLDEN",
        "UNRELATED",
    ]
    assert sorted(_schemas_matching_run_prefix(names, "PROJ_RUN1")) == [
        "PROJ_RUN1_EP_ABC123", "PROJ_RUN1_EP_GOLDEN",
    ]

    names_ci = ["proj_run1_ep_golden", "PROJ_RUN1_EP2_CLONE", "OTHER_SCHEMA"]
    assert sorted(_schemas_matching_run_prefix(names_ci, "proj_run1")) == [
        "PROJ_RUN1_EP2_CLONE", "proj_run1_ep_golden",
    ]

    names_us = ["FOO_BAR_SCHEMA1", "FOO_BAR_SCHEMA2", "FOO_BARZ_NOPE"]
    assert sorted(_schemas_matching_run_prefix(names_us, "FOO_BAR_")) == [
        "FOO_BAR_SCHEMA1", "FOO_BAR_SCHEMA2",
    ]

    assert _schemas_matching_run_prefix([], "PROJ_RUN1") == []


# ===========================================================================
# New tests: directory layout helpers
# ===========================================================================


def test_split_merge_roundtrip():
    """split_entrypoint + merge_entrypoint returns an identical dict."""
    ep = {
        "id": "my_ep",
        "path": "main.py",
        "run_mode": "script",
        "tables": {
            "src": {"columns": [{"name": "id", "type": "string"}]},
            "out": {"access": "write", "columns": []},
        },
    }
    meta, tables = split_entrypoint(ep)
    assert "tables" not in meta
    assert set(tables.keys()) == {"src", "out"}
    merged = merge_entrypoint(meta, tables)
    assert merged == ep


def test_split_merge_no_tables():
    """split_entrypoint works when tables key is absent."""
    ep = {"id": "ep", "path": "x.py"}
    meta, tables = split_entrypoint(ep)
    assert tables == {}
    assert merge_entrypoint(meta, tables) == {"id": "ep", "path": "x.py", "tables": {}}


def test_table_filename_sanitizes_slash_and_colon():
    """Keys with / and : are sanitized to underscores."""
    used: set = set()
    assert _table_filename("db/schema:table", used) == "db_schema_table"
    assert "db_schema_table" in used


def test_table_filename_collision():
    """Collision appends _2, _3, ..."""
    used: set = set()
    f1 = _table_filename("final", used)
    f2 = _table_filename("final", used)  # same key again → collision
    assert f1 == "final"
    assert f2 == "final_2"


def test_load_entrypoint_roundtrip_special_keys(tmp_path):
    """load_entrypoint reconstructs an identical dict including keys with / and :."""
    ep = {
        "id": "ep1",
        "path": "job.py",
        "run_mode": "script",
        "source_runtime": "spark",
        "tables": {
            "db/schema/table": {"columns": [{"name": "id", "type": "string"}]},
            "cat:schema.tbl": {"columns": [{"name": "v", "type": "long"}]},
            "PHONE_NUMBER_INFO": {"access": "write", "columns": []},
        },
    }
    schemas_dir = tmp_path / "schemas"
    ep_dir = schemas_dir / "entrypoints"
    _write_ep_dir(ep_dir, ep)

    loaded = load_entrypoint(schemas_dir, "ep1")
    assert loaded == ep


def test_load_entrypoint_missing_dir(tmp_path):
    """load_entrypoint raises FileNotFoundError when the directory is absent."""
    import pytest
    with pytest.raises(FileNotFoundError):
        load_entrypoint(tmp_path / "schemas", "nonexistent")


def test_load_entrypoint_missing_meta(tmp_path):
    """load_entrypoint raises FileNotFoundError when _meta.json is absent."""
    import pytest
    schemas_dir = tmp_path / "schemas"
    (schemas_dir / "entrypoints" / "ep").mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        load_entrypoint(schemas_dir, "ep")


from runtimes.local_runtime import _resolve_delta_jars  # noqa: E402


# ===========================================================================
# _resolve_delta_jars unit tests (ITEM D)
# ===========================================================================


def test_resolve_delta_jars_live():
    """Returns None or a tuple of existing non-empty files — no network needed."""
    _resolve_delta_jars.cache_clear()
    result = _resolve_delta_jars()
    if result is None:
        return  # acceptable: delta jars not present on this host
    assert isinstance(result, tuple) and len(result) > 0
    for jar in result:
        p = Path(jar)
        assert p.exists(), f"JAR not found: {jar}"
        assert p.stat().st_size > 0, f"Zero-byte JAR: {jar}"


def test_resolve_delta_jars_from_package_dir(tmp_path):
    """Finds jars in the delta package's own jars/ directory."""
    import sys
    from unittest.mock import MagicMock

    pkg_dir = tmp_path / "delta"
    pkg_dir.mkdir()
    init_py = pkg_dir / "__init__.py"
    init_py.write_text("")
    jars_dir = pkg_dir / "jars"
    jars_dir.mkdir()
    (jars_dir / "delta-spark_2.12-3.3.0.jar").write_bytes(b"PK\x03\x04fake")
    (jars_dir / "delta-storage-3.3.0.jar").write_bytes(b"PK\x03\x04fake")

    mock_delta = MagicMock()
    mock_delta.__file__ = str(init_py)

    saved = sys.modules.get("delta")
    sys.modules["delta"] = mock_delta
    _resolve_delta_jars.cache_clear()
    try:
        result = _resolve_delta_jars()
    finally:
        if saved is None:
            sys.modules.pop("delta", None)
        else:
            sys.modules["delta"] = saved

    assert result is not None
    assert any("delta-spark" in Path(j).name for j in result)
    for j in result:
        assert Path(j).exists()


def test_resolve_delta_jars_from_ivy2(tmp_path, monkeypatch):
    """Falls back to ivy2 cache when the delta package has no jars/ directory."""
    import sys
    from unittest.mock import MagicMock

    mock_delta = MagicMock()
    mock_delta.__file__ = str(tmp_path / "delta" / "__init__.py")  # no jars/ subdir

    ivy_jar_dir = tmp_path / ".ivy2" / "cache" / "io.delta" / "delta-spark_2.12" / "jars"
    ivy_jar_dir.mkdir(parents=True)
    (ivy_jar_dir / "delta-spark_2.12-3.3.0.jar").write_bytes(b"PK\x03\x04fake")

    monkeypatch.setenv("HOME", str(tmp_path))
    saved = sys.modules.get("delta")
    sys.modules["delta"] = mock_delta
    _resolve_delta_jars.cache_clear()
    try:
        result = _resolve_delta_jars()
    finally:
        if saved is None:
            sys.modules.pop("delta", None)
        else:
            sys.modules["delta"] = saved

    assert result is not None
    assert any("delta-spark_" in Path(j).name for j in result)


def test_resolve_delta_jars_returns_none_without_delta_spark(tmp_path, monkeypatch):
    """Returns None when no delta-spark JAR exists in either search location."""
    import sys
    from unittest.mock import MagicMock

    mock_delta = MagicMock()
    mock_delta.__file__ = str(tmp_path / "delta" / "__init__.py")

    # ivy2 with only storage jar — not sufficient
    ivy_jar_dir = tmp_path / ".ivy2" / "jars"
    ivy_jar_dir.mkdir(parents=True)
    (ivy_jar_dir / "delta-storage-3.3.0.jar").write_bytes(b"PK\x03\x04fake")

    monkeypatch.setenv("HOME", str(tmp_path))
    saved = sys.modules.get("delta")
    sys.modules["delta"] = mock_delta
    _resolve_delta_jars.cache_clear()
    try:
        result = _resolve_delta_jars()
    finally:
        if saved is None:
            sys.modules.pop("delta", None)
        else:
            sys.modules["delta"] = saved

    assert result is None


def test_assemble_analysis_dir_layout_equals_assembled(tmp_path):
    """assemble_analysis on dir layout returns the same entrypoints."""
    ep = {
        "id": "ep_x",
        "path": "x.py",
        "run_mode": "script",
        "import_roots": ["lib/"],
        "tables": {"t": {"columns": [{"name": "n", "type": "int"}]}},
    }
    schemas_dir = tmp_path / "schemas"
    ep_dir = schemas_dir / "entrypoints"
    ep_dir.mkdir(parents=True)
    _write_ep_dir(ep_dir, ep)
    manifest = {
        "entrypoints": [{"id": "ep_x", "path": "x.py", "dir": "entrypoints/ep_x"}]
    }
    (schemas_dir / "manifest.json").write_text(json.dumps(manifest))

    result = assemble_analysis(str(schemas_dir))
    assert len(result["entrypoints"]) == 1
    assert result["entrypoints"][0] == ep


# ---------------------------------------------------------------------------
# Comparator helper unit tests (correctness-critical normalization/equality)
# ---------------------------------------------------------------------------

def test_normalize_struct_key_order_and_repr_independence():
    ns = comparator._normalize_struct
    # JSON object key order is irrelevant.
    assert ns('{"b": 1, "a": 2}') == ns('{"a": 2, "b": 1}')
    # Spark Row repr `{a=1, b=2}` is order-independent.
    assert ns("{b=2, a=1}") == ns("{a=1, b=2}")
    # Python single-quote repr equals JSON for the same array<struct>.
    assert ns("[{'a': 1}]") == ns('[{"a": 1}]')
    # Non-struct scalars pass through unchanged.
    assert ns("hello") == "hello"


def test_normalize_collection_repr_independence():
    nc = comparator._normalize_collection
    # numpy ndarray repr (space-separated) == JSON array.
    assert nc("[ 47.1 -122.9 ]") == nc("[47.1, -122.9]")
    assert nc("[]") == "[]"
    assert nc("not-a-list") is None


def test_canon_null_and_is_null():
    assert comparator._canon_null(None) == ""
    assert comparator._canon_null("NaN") == ""
    assert comparator._canon_null("\\N") == ""
    assert comparator._canon_null("N/A") == ""
    assert comparator._canon_null("keep") == "keep"
    assert comparator._is_null("") is True
    assert comparator._is_null("NULL") is True
    assert comparator._is_null("x") is False


def test_canonical_sort_cell_null_notion_matches_equality():
    """The sort key's null token uses the same null notion as the equality path, so
    every _canon_null-folded repr (nan/N/A/\\N/NULL/None/"") collapses to one token
    and sorts together instead of splitting off as a distinct string bucket."""
    sc = comparator._canonical_sort_cell
    null_token = sc("", tolerance=1e-9)
    for v in ("NaN", "nan", "N/A", "\\N", "NULL", None):
        assert sc(v, tolerance=1e-9) == null_token
    assert sc("keep", tolerance=1e-9) != null_token


def test_cells_equal_null_numeric_struct_and_case():
    ce = comparator._cells_equal
    # Both null reprs are equal; one-sided null is a real diff.
    assert ce("", "NULL", tolerance=1e-9) == (True, "")
    assert ce("1", "", tolerance=1e-9)[0] is False
    assert ce("1", "", tolerance=1e-9)[1] == "null"
    # struct JSON vs Python-repr (the SCOS serialization artifact) compares equal.
    assert ce('{"a": 1, "b": 2}', "{'b': 2, 'a': 1}", tolerance=1e-9) == (True, "")
    # Numeric tolerance: tiny relative diff is equal; large diff is flagged.
    assert ce("100.0", "100.00001", tolerance=1e-3) == (True, "")
    eq, kind = ce("100", "200", tolerance=1e-9)
    assert eq is False and kind == "numeric_tol"
    # Case-insensitive string equality.
    assert ce("Foo", "foo", tolerance=1e-9) == (True, "")


def test_types_compatible_groups_and_csv_roundtrip():
    tc = comparator._types_compatible
    assert tc("int", "int") is True
    assert tc("int", "bigint") is True            # same integer group
    assert tc("int", "double") is False           # different groups
    assert tc("int", "double", nan_widened=True) is True
    assert tc("date", "string") is True           # CSV-roundtrip serialization
    assert tc("string", "int") is False



# ===========================================================================
# Comparator perf-change regression tests (P1 itertuples, P3 disjoint fast-path)
# ===========================================================================

def test_compare_mixed_types_and_nulls_match(tmp_path):
    """P1: vectorized _load_parquet must still yield a clean match on a frame
    mixing ints, floats, strings, and nulls (identical files -> match)."""
    df = pd.DataFrame({
        "ID": [1, 2, 3],
        "AMT": [1.5, 2.0, float("nan")],
        "NAME": ["a", None, "c"],
    })
    a = tmp_path / "a.parquet"; b = tmp_path / "b.parquet"
    df.to_parquet(a, index=False); df.to_parquet(b, index=False)
    res = comparator.compare(str(a), str(b))
    assert res["result"] in _MATCH, res
    # null canonicalization preserved: NaN/None both canon to "" and match.
    assert res["shape"]["baseline"]["rows"] == 3


def test_compare_int_float_value_diff_detected(tmp_path):
    """A genuine value difference is still flagged after the itertuples change."""
    a = tmp_path / "a.parquet"; b = tmp_path / "b.parquet"
    pd.DataFrame({"ID": [1, 2], "V": [10, 20]}).to_parquet(a, index=False)
    pd.DataFrame({"ID": [1, 2], "V": [10, 999]}).to_parquet(b, index=False)
    res = comparator.compare(str(a), str(b))
    assert res["result"] == "diverge", res


def test_parquet_meta_reads_names_and_count(tmp_path):
    """P3 helper: column names (uppercased) + row count from metadata only."""
    p = tmp_path / "t.parquet"
    pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]}).to_parquet(p, index=False)
    names, n = comparator._parquet_meta(str(p))
    assert names == ["A", "B"] and n == 3
    assert comparator._parquet_meta(str(tmp_path / "missing.parquet")) is None


def test_compare_disjoint_schema_fast_path_matches_full_load(tmp_path):
    """P3: the metadata fast-path result for fully-disjoint schemas is identical
    to what the full-load path produces (compare via both file and dir forms)."""
    a = tmp_path / "a.parquet"; b = tmp_path / "b.parquet"
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


# ===========================================================================
# Comparator CLI contract (the Scala harness shells out to `comparator.py compare`)
# ===========================================================================

def test_comparator_cli_exit_codes_and_no_tiers(tmp_path):
    """The Scala runner depends on `comparator.py compare` + exit codes 0/1/2.
    Guard that contract; also confirm the tier machinery stays removed."""
    a = tmp_path / "a.parquet"; b = tmp_path / "b.parquet"; c = tmp_path / "c.parquet"
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

    # No --tier option, and the tier helpers are gone.
    assert "--tier" not in comparator._build_parser().format_help()
    assert not hasattr(comparator, "_compute_aggregate_diff")
