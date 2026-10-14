"""Tests for the unknown-API surface scan — Scala and Java parity.

Mirrors test_unknown_api_surface.py for the Scala and Java analyzers.
Covers _build_covered_api_names_*, _build_file_import_map_*, and
_collect_unknown_api_rows_*, with Scala-specific cases for curly-brace
multi-imports (import a.b.{C, D}) and Java-specific cases for static imports.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

SCRIPTS = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from analyze_scala import (  # noqa: E402
    _build_covered_api_names_scala,
    _build_file_import_map_scala,
    _collect_unknown_api_rows_scala,
)
from analyze_java import (  # noqa: E402
    _build_covered_api_names_java,
    _build_file_import_map_java,
    _collect_unknown_api_rows_java,
)


def _make_block(functions: list[str], line_start: int = 1, line_end: int = 1, code: str = "x()") -> MagicMock:
    b = MagicMock()
    b.functions = functions
    b.line_start = line_start
    b.line_end = line_end
    b.code = code
    return b


# --------------------------------------------------------------------------- #
# _build_covered_api_names_scala / _build_covered_api_names_java
# --------------------------------------------------------------------------- #


def test_covered_names_scala_includes_kb_leaves() -> None:
    rules = [{"api": ["groupBy"], "rule_id": "x"}]
    covered = _build_covered_api_names_scala(None, rules)
    assert "groupby" in covered


def test_covered_names_scala_includes_safe_apis() -> None:
    covered = _build_covered_api_names_scala({"filter", "where"}, [])
    assert "filter" in covered
    assert "where" in covered


def test_covered_names_java_includes_kb_leaves() -> None:
    rules = [{"api": ["join"], "rule_id": "x"}]
    covered = _build_covered_api_names_java(None, rules)
    assert "join" in covered


def test_covered_names_java_includes_safe_apis() -> None:
    covered = _build_covered_api_names_java({"select", "groupBy"}, [])
    assert "select" in covered
    assert "groupby" in covered


# --------------------------------------------------------------------------- #
# _build_file_import_map_scala
# --------------------------------------------------------------------------- #


def test_scala_import_map_simple() -> None:
    src = "import com.amazonaws.services.glue.GlueContext"
    m = _build_file_import_map_scala(src)
    assert m["GlueContext"] == "com"


def test_scala_import_map_curly_brace_two_symbols() -> None:
    """import a.b.{C, D} must register both C and D, not just the package 'b'."""
    src = "import com.amazonaws.services.glue.util.{Job, JsonOptions}"
    m = _build_file_import_map_scala(src)
    assert m.get("Job") == "com", "Job must map to top-level 'com'"
    assert m.get("JsonOptions") == "com", "JsonOptions must map to top-level 'com'"
    assert "util" not in m, "'util' is a package, not an importable symbol"


def test_scala_import_map_curly_brace_rename() -> None:
    """import a.b.{C => Alias} must register the alias, not the original name."""
    src = "import org.graphframes.{GraphFrame => GF}"
    m = _build_file_import_map_scala(src)
    assert m.get("GF") == "org"
    assert "GraphFrame" not in m


def test_scala_import_map_wildcard_skipped() -> None:
    """import a.b._ must not produce any entry."""
    src = "import org.apache.spark._"
    m = _build_file_import_map_scala(src)
    assert "_" not in m


def test_scala_import_map_stdlib_top_package_captured() -> None:
    """scala.* imports are captured but filtered later by _JVM_STDLIB_TOP_PACKAGES."""
    src = "import scala.collection.mutable"
    m = _build_file_import_map_scala(src)
    assert m.get("mutable") == "scala"


def test_scala_import_map_empty_source() -> None:
    assert _build_file_import_map_scala("") == {}


# --------------------------------------------------------------------------- #
# _build_file_import_map_java
# --------------------------------------------------------------------------- #


def test_java_import_map_simple() -> None:
    src = "import com.amazonaws.services.glue.GlueContext;"
    m = _build_file_import_map_java(src)
    assert m["GlueContext"] == "com"


def test_java_import_map_static() -> None:
    src = "import static org.apache.spark.sql.functions.col;"
    m = _build_file_import_map_java(src)
    assert m.get("col") == "org"


def test_java_import_map_wildcard() -> None:
    """import a.b.*; — wildcard maps the package name, not individual symbols."""
    src = "import com.amazonaws.services.glue.*;"
    m = _build_file_import_map_java(src)
    # wildcard doesn't register any usable class name; just verify no crash
    assert isinstance(m, dict)


def test_java_import_map_no_semicolon_not_matched() -> None:
    """A Java import without trailing semicolon must not be matched."""
    src = "import com.example.Foo"
    m = _build_file_import_map_java(src)
    assert "Foo" not in m


def test_java_import_map_empty_source() -> None:
    assert _build_file_import_map_java("") == {}


# --------------------------------------------------------------------------- #
# _collect_unknown_api_rows_scala
# --------------------------------------------------------------------------- #


def test_scala_no_rows_when_all_covered() -> None:
    covered = frozenset(["commit"])
    blocks = [_make_block(["commit"])]
    src = "import com.amazonaws.services.glue.util.Job"
    rows = _collect_unknown_api_rows_scala(Path("test.scala"), blocks, covered, src)
    assert rows == []


def test_scala_unknown_third_party_surfaced() -> None:
    covered = frozenset()
    blocks = [_make_block(["init"], code="Job.init(args)")]
    src = "import com.amazonaws.services.glue.util.Job"
    rows = _collect_unknown_api_rows_scala(Path("test.scala"), blocks, covered, src)
    assert len(rows) == 1
    assert rows[0]["kind"] == "needs_classification"
    assert rows[0]["import_module"] == "com"
    assert rows[0]["adjudicated"] is False
    assert rows[0]["detected_by"] == "unknown_surface_scan"


def test_scala_curly_import_surfaced() -> None:
    """Job from a curly-brace import must produce a needs_classification row."""
    covered = frozenset()
    blocks = [_make_block(["init"], code='Job.init(args("JOB_NAME"), glueContext, args)')]
    src = "import com.amazonaws.services.glue.util.{Job, JsonOptions}"
    rows = _collect_unknown_api_rows_scala(Path("test.scala"), blocks, covered, src)
    # The receiver scan should find Job → com
    assert any(r["import_module"] == "com" for r in rows), (
        "Job from curly-brace import must be surfaced; got no 'com' row"
    )


def test_scala_stdlib_filtered() -> None:
    covered = frozenset()
    blocks = [_make_block(["mutable"], code="mutable.Map()")]
    src = "import scala.collection.mutable"
    rows = _collect_unknown_api_rows_scala(Path("test.scala"), blocks, covered, src)
    assert rows == [], "scala stdlib must be filtered by _JVM_STDLIB_TOP_PACKAGES"


def test_scala_unimported_name_skipped() -> None:
    covered = frozenset()
    blocks = [_make_block(["mysteryfn"])]
    src = ""
    rows = _collect_unknown_api_rows_scala(Path("test.scala"), blocks, covered, src)
    assert rows == []


def test_scala_receiver_path_catches_alias_not_in_functions() -> None:
    """GlueContext used as a receiver but not in block.functions must still surface."""
    covered = frozenset()
    block = _make_block([], code="GlueContext.getSparkSession()")
    src = "import com.amazonaws.services.glue.GlueContext"
    rows = _collect_unknown_api_rows_scala(Path("test.scala"), [block], covered, src)
    assert len(rows) == 1
    assert rows[0]["import_module"] == "com"
    assert "GlueContext" in rows[0]["api_names"]


def test_scala_groups_by_module() -> None:
    covered = frozenset()
    blocks = [_make_block(["Job", "JsonOptions"])]
    src = "import com.amazonaws.services.glue.util.{Job, JsonOptions}"
    rows = _collect_unknown_api_rows_scala(Path("test.scala"), blocks, covered, src)
    assert len(rows) == 1
    assert rows[0]["import_module"] == "com"


# --------------------------------------------------------------------------- #
# _collect_unknown_api_rows_java
# --------------------------------------------------------------------------- #


def test_java_no_rows_when_all_covered() -> None:
    covered = frozenset(["commit"])
    blocks = [_make_block(["commit"])]
    src = "import com.amazonaws.services.glue.util.Job;"
    rows = _collect_unknown_api_rows_java(Path("test.java"), blocks, covered, src)
    assert rows == []


def test_java_unknown_third_party_surfaced() -> None:
    covered = frozenset()
    blocks = [_make_block(["init"], code="Job.init(args)")]
    src = "import com.amazonaws.services.glue.util.Job;"
    rows = _collect_unknown_api_rows_java(Path("test.java"), blocks, covered, src)
    assert len(rows) == 1
    assert rows[0]["kind"] == "needs_classification"
    assert rows[0]["import_module"] == "com"
    assert rows[0]["adjudicated"] is False


def test_java_stdlib_filtered() -> None:
    covered = frozenset()
    blocks = [_make_block(["sort"], code="Arrays.sort(arr)")]
    src = "import java.util.Arrays;"
    rows = _collect_unknown_api_rows_java(Path("test.java"), blocks, covered, src)
    assert rows == [], "java stdlib must be filtered by _JVM_STDLIB_TOP_PACKAGES"


def test_java_receiver_path_catches_alias_not_in_functions() -> None:
    covered = frozenset()
    block = _make_block([], code="glueContext.write_dynamic_frame.from_options(frame, opts)")
    src = "import com.amazonaws.services.glue.GlueContext;"
    rows = _collect_unknown_api_rows_java(Path("test.java"), [block], covered, src)
    assert len(rows) == 1
    assert rows[0]["import_module"] == "com"
    assert "glueContext" in rows[0]["api_names"]


def test_java_groups_by_module() -> None:
    covered = frozenset()
    blocks = [_make_block(["GlueContext", "Job"])]
    src = "import com.amazonaws.services.glue.GlueContext;\nimport com.amazonaws.services.glue.util.Job;"
    rows = _collect_unknown_api_rows_java(Path("test.java"), blocks, covered, src)
    assert len(rows) == 1
    assert rows[0]["import_module"] == "com"
    assert set(rows[0]["api_names"]) >= {"GlueContext", "Job"}
