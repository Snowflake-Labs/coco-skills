"""Tests for the unknown-API surface scan (PR2).

Covers _build_covered_api_names, _build_file_import_map, and
_collect_unknown_api_rows, plus the apply_adjudications extensions for
classify_spark / classify_not_spark verdicts.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from analyze_pyspark import (  # noqa: E402
    _BUILTIN_NAMES,
    _STDLIB_MODULE_NAMES,
    _build_covered_api_names,
    _build_file_import_map,
    _collect_unknown_api_rows,
)

_DATA = SCRIPTS / "data" / "api_compatibility.csv"


# --------------------------------------------------------------------------- #
# _build_covered_api_names
# --------------------------------------------------------------------------- #


def test_covered_names_includes_kb_leaves() -> None:
    rules = [{"api": ["pyspark.sql.functions.ceil"], "rule_id": "x"}]
    covered = _build_covered_api_names({}, None, rules)
    assert "ceil" in covered


def test_covered_names_includes_csv_leaves() -> None:
    covered = _build_covered_api_names({"select": MagicMock(), "pyspark.sql.DataFrame.join": MagicMock()}, None, [])
    assert "select" in covered
    assert "join" in covered


def test_covered_names_includes_safe_apis() -> None:
    covered = _build_covered_api_names({}, {"filter", "where"}, [])
    assert "filter" in covered
    assert "where" in covered


# --------------------------------------------------------------------------- #
# _build_file_import_map
# --------------------------------------------------------------------------- #


def test_import_map_standard_import() -> None:
    src = "import numpy as np"
    m = _build_file_import_map(src)
    assert m["np"] == "numpy"


def test_import_map_from_import() -> None:
    src = "from pygeohash import encode"
    m = _build_file_import_map(src)
    assert m["encode"] == "pygeohash"


def test_import_map_from_import_alias() -> None:
    src = "from pygeohash import encode as gh_encode"
    m = _build_file_import_map(src)
    assert m["gh_encode"] == "pygeohash"


def test_import_map_syntax_error_returns_empty() -> None:
    m = _build_file_import_map("def (broken:")
    assert m == {}


def test_import_map_pyspark_from_import() -> None:
    src = "from pyspark.sql import SparkSession"
    m = _build_file_import_map(src)
    assert m["SparkSession"] == "pyspark"


# --------------------------------------------------------------------------- #
# _collect_unknown_api_rows
# --------------------------------------------------------------------------- #


def _make_block(functions: list[str], line_start: int = 1, line_end: int = 1, code: str = "x()") -> MagicMock:
    b = MagicMock()
    b.functions = functions
    b.line_start = line_start
    b.line_end = line_end
    b.code = code
    return b


def test_no_rows_when_all_covered() -> None:
    covered = frozenset(["encode"])
    blocks = [_make_block(["encode"])]
    src = "from pygeohash import encode"
    rows = _collect_unknown_api_rows(Path("test.py"), blocks, covered, src)
    assert rows == []


def test_unknown_third_party_surfaced() -> None:
    covered = frozenset()
    blocks = [_make_block(["encode"])]
    src = "from pygeohash import encode"
    rows = _collect_unknown_api_rows(Path("test.py"), blocks, covered, src)
    assert len(rows) == 1
    assert rows[0]["kind"] == "needs_classification"
    assert rows[0]["import_module"] == "pygeohash"
    assert "encode" in rows[0]["api_names"]
    assert rows[0]["adjudicated"] is False
    assert rows[0]["detected_by"] == "unknown_surface_scan"


def test_stdlib_names_are_dropped() -> None:
    covered = frozenset()
    blocks = [_make_block(["radians"])]
    src = "from math import radians"
    rows = _collect_unknown_api_rows(Path("test.py"), blocks, covered, src)
    assert rows == [], "stdlib imports must be filtered out"


def test_builtin_names_are_dropped() -> None:
    covered = frozenset()
    blocks = [_make_block(["print"])]
    src = ""
    rows = _collect_unknown_api_rows(Path("test.py"), blocks, covered, src)
    assert rows == [], "Python builtins must be filtered out"


def test_unattributed_names_are_skipped() -> None:
    covered = frozenset()
    blocks = [_make_block(["mystery_fn"])]
    src = ""  # no import for mystery_fn
    rows = _collect_unknown_api_rows(Path("test.py"), blocks, covered, src)
    assert rows == [], "names with no traceable import must be skipped"


def test_groups_by_module() -> None:
    covered = frozenset()
    blocks = [_make_block(["encode", "decode"])]
    src = "from pygeohash import encode, decode"
    rows = _collect_unknown_api_rows(Path("test.py"), blocks, covered, src)
    assert len(rows) == 1
    assert rows[0]["import_module"] == "pygeohash"
    assert set(rows[0]["api_names"]) == {"encode", "decode"}


def test_multiple_modules_emit_separate_rows() -> None:
    covered = frozenset()
    blocks = [_make_block(["encode", "put_object"])]
    src = "from pygeohash import encode\nimport boto3"
    rows = _collect_unknown_api_rows(Path("test.py"), blocks, covered, src)
    modules = {r["import_module"] for r in rows}
    assert "pygeohash" in modules
    assert "_unattributed" not in modules or len([r for r in rows if r["import_module"] == "_unattributed"]) == 0


def test_receiver_path_catches_alias_not_in_functions() -> None:
    """fs.mount(...) — the receiver 'fs' is not in block.functions (extract_functions
    strips it), but it IS in block.code. The receiver path must surface it."""
    covered = frozenset()
    block = _make_block([], code='fs.mount("abfss://container@acct.dfs.core.windows.net", "/mnt/data")')
    src = "from mssparkutils import fs"
    rows = _collect_unknown_api_rows(Path("test.py"), [block], covered, src)
    assert len(rows) == 1
    assert rows[0]["import_module"] == "mssparkutils"
    assert "fs" in rows[0]["api_names"]




# --------------------------------------------------------------------------- #
# apply_adjudications: needs_classification rows are NOT the adjudicator's job
# --------------------------------------------------------------------------- #


def test_matcher_does_not_index_needs_classification_rows() -> None:
    """needs_classification rows are handled by classify_unknown_modules.py before
    Phase 1.1b runs -- apply_adjudications should not attempt to match them."""
    from apply_adjudications import Matcher

    rows = [
        {"file": "a.py", "lines": "1-1", "code": "x()", "ewi_code": "",
         "kind": "needs_classification", "adjudicated": False},
        {"file": "b.py", "lines": "5-5", "code": "z()", "ewi_code": "",
         "kind": "needs_adjudication", "adjudicated": False},
    ]
    m = Matcher(rows)
    r1, _ = m.match({"file": "a.py", "lines": "1-1", "code": "x()", "ewi_code": ""})
    r2, _ = m.match({"file": "b.py", "lines": "5-5", "code": "z()", "ewi_code": ""})
    # needs_classification is not the adjudicator's concern
    assert r1 is None
    assert r2 is not None


# --------------------------------------------------------------------------- #
# classify_unknown_modules._apply (no LLM call needed for unit tests)
# --------------------------------------------------------------------------- #


def test_classify_apply_spark_related_promotes_to_needs_adjudication() -> None:
    from classify_unknown_modules import _apply

    rows = [
        {"kind": "needs_classification", "import_module": "mssparkutils", "adjudicated": False},
    ]
    spark, not_spark = _apply(rows, {"mssparkutils": "spark_related"})
    assert spark == 1
    assert not_spark == 0
    assert rows[0]["kind"] == "needs_adjudication"
    assert rows[0].get("adjudicated") is not True


def test_classify_apply_not_spark_marks_safe() -> None:
    from classify_unknown_modules import _apply

    rows = [
        {"kind": "needs_classification", "import_module": "boto3", "adjudicated": False},
    ]
    spark, not_spark = _apply(rows, {"boto3": "not_spark_related"})
    assert spark == 0
    assert not_spark == 1
    assert rows[0]["resolution"] == "safe"
    assert rows[0]["adjudicated"] is True


def test_classify_apply_skips_already_adjudicated() -> None:
    from classify_unknown_modules import _apply

    rows = [
        {"kind": "needs_classification", "import_module": "lib_x", "adjudicated": True},
    ]
    spark, not_spark = _apply(rows, {"lib_x": "spark_related"})
    assert spark == 0
    assert not_spark == 0
    assert rows[0]["kind"] == "needs_classification"


def test_classify_apply_ignores_unrecognized_module() -> None:
    from classify_unknown_modules import _apply

    rows = [
        {"kind": "needs_classification", "import_module": "some_lib", "adjudicated": False},
    ]
    # LLM returned classifications for a different set
    spark, not_spark = _apply(rows, {"other_lib": "spark_related"})
    assert spark == 0
    assert not_spark == 0
    assert rows[0]["kind"] == "needs_classification"


def test_classify_list_modules(tmp_path) -> None:
    import subprocess, sys
    analysis = tmp_path / "analysis.json"
    analysis.write_text(json.dumps([
        {"kind": "needs_classification", "import_module": "mssparkutils", "adjudicated": False},
        {"kind": "needs_adjudication", "adjudicated": False},
    ]))
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "classify_unknown_modules.py"),
         "--analysis", str(analysis), "--list-modules"],
        capture_output=True, text=True
    )
    assert result.returncode == 0
    listed = json.loads(result.stdout)
    assert listed == ["mssparkutils"]
    rows = json.loads(analysis.read_text())
    assert rows[0]["kind"] == "needs_classification"


def test_classify_full_flow(tmp_path) -> None:
    import subprocess, sys
    analysis = tmp_path / "analysis.json"
    analysis.write_text(json.dumps([
        {"kind": "needs_classification", "import_module": "mssparkutils", "file": "f.py",
         "lines": "1-1", "code": "x", "adjudicated": False},
        {"kind": "needs_classification", "import_module": "boto3", "file": "f.py",
         "lines": "2-2", "code": "y", "adjudicated": False},
    ]))
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "classify_unknown_modules.py"),
         "--analysis", str(analysis),
         "--classifications", '{"mssparkutils": "spark_related", "boto3": "not_spark_related"}'],
        capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "classify_spark=1" in result.stdout
    assert "classify_not_spark=1" in result.stdout
    rows = json.loads(analysis.read_text())
    spark_row = next(r for r in rows if r.get("import_module") == "mssparkutils")
    safe_row = next(r for r in rows if r.get("import_module") == "boto3")
    assert spark_row["kind"] == "needs_adjudication"
    assert safe_row["resolution"] == "safe"
    assert safe_row["adjudicated"] is True


def test_classify_missing_classification_errors(tmp_path) -> None:
    import subprocess, sys
    analysis = tmp_path / "analysis.json"
    analysis.write_text(json.dumps([
        {"kind": "needs_classification", "import_module": "mylib", "adjudicated": False}
    ]))
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "classify_unknown_modules.py"),
         "--analysis", str(analysis),
         "--classifications", '{"other_lib": "spark_related"}'],
        capture_output=True, text=True
    )
    assert result.returncode == 3
    assert "missing" in result.stderr.lower()

