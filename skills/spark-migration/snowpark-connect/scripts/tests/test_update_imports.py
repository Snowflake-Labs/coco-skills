"""Tests for the deterministic Phase 3 transform (update_imports.py).

These assert two things:
  1. The mechanical transforms match the former import-updater.md agent's
     contract (header, session-init replacement, unsupported-import removal,
     pyspark kept, config preserved).
  2. The output PASSES the deterministic imports gate (scos_gates.py imports),
     which is the same gate the agent's output used to be validated against —
     i.e. functionality is preserved.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import update_imports as ui
from scos_gates import run_imports_gate


def _conv(tmp_path: Path):
    conv = tmp_path / "Conversion-SCOS-TEST"
    output = conv / "Output"
    output.mkdir(parents=True)
    return conv, output


def _state(conv: Path, output: Path, manifest: list[str]) -> Path:
    state_path = conv / "migration_state.json"
    state_path.write_text(json.dumps({
        "conversion_root": str(conv),
        "migrated_dir": str(output),
        "manifest": manifest,
    }))
    return state_path


def _run(conv: Path, output: Path, files: dict[str, str]) -> Path:
    for name, src in files.items():
        p = output / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src)
    state = _state(conv, output, list(files))
    assert ui.main(["--state", str(state)]) == 0
    return state


# --------------------------------------------------------------------------- #
# Source-level transform unit tests
# --------------------------------------------------------------------------- #


def test_header_added_and_idempotent():
    src = "x = 1\n"
    out1, stats1 = ui.transform_python_source(src, "m.py")
    assert stats1["header_added"] is True
    assert "SCOS Migration Output" in out1
    out2, stats2 = ui.transform_python_source(out1, "m.py")
    assert stats2["header_added"] is False
    assert out2.count("SCOS Migration Output") == 1


def test_stub_header_replaced_with_rich_header():
    """A placeholder header from generate_scos_reports must be stripped and
    replaced with a real annotation-derived header (not treated as done)."""
    stub = (
        '"""\nSCOS Migration Output\n'
        "=====================\n"
        "Source File: m.py\n"
        "Migrated on: 2026-01-01\n"
        "\nChanges Overview:\n"
        f"- {ui.STUB_HEADER_SENTINEL}.\n"
        "\nKnown Limitations:\n"
        "- None\n"
        '"""\n'
    )
    body = "x = 1  # SCOS: replaced builder\n"
    out, added = ui.add_migration_header(stub + body, "m.py")
    assert added is True
    assert ui.STUB_HEADER_SENTINEL not in out
    assert out.count("SCOS Migration Output") == 1
    assert "replaced builder" in out
    assert body in out


def test_header_no_changes_uses_placeholders():
    src = "x = 1\n"
    out, _ = ui.transform_python_source(src, "m.py")
    assert "Changes Overview:" in out
    assert "- No compatibility issues detected. No changes required." in out
    assert "Known Limitations:" in out
    assert "- None — all issues resolved" in out


def test_header_changes_overview_lists_scos_comments():
    src = (
        "from databricks.connect import DatabricksSession\n"
        "from snowflake import snowpark_connect\n"
        "spark = snowpark_connect.init_spark_session()\n"
    )
    out, _ = ui.transform_python_source(src, "m.py")
    # The databricks removal annotation must surface in Changes Overview.
    assert "Changes Overview:" in out
    assert "Databricks import" in out.split("Known Limitations:")[0]
    assert "- None — all issues resolved" in out


def test_header_line_reference_points_at_scos_comment():
    src = (
        "from databricks.connect import DatabricksSession\n"
        "from snowflake import snowpark_connect\n"
        "spark = snowpark_connect.init_spark_session()\n"
    )
    out, _ = ui.transform_python_source(src, "m.py")
    out_lines = out.split("\n")
    # Find the "[Line N]" reference in the Changes Overview.
    refs = [
        int(m.group(1))
        for ln in out_lines
        if (m := re.search(r"- \[Line (\d+)\] ", ln))
    ]
    assert refs, "expected at least one [Line N] change entry"
    for n in refs:
        # The referenced migrated-file line must be the SCOS annotation itself.
        assert "# SCOS:" in out_lines[n - 1]


def test_known_limitations_collected_from_todo_comments():
    # Both SCOS TODO conventions must land in Known Limitations, not Changes
    # Overview: the inline "# SCOS: TODO -" fixer form and the hyphenated
    # "# SCOS-TODO:" recipe form.
    src = (
        "from pyspark.sql import functions as F\n"
        "# SCOS: TODO - manual review of UDF semantics required\n"
        "# SCOS-TODO: add spark.addArtifact('udf.py', pyfile=True)\n"
        "from snowflake import snowpark_connect\n"
        "spark = snowpark_connect.init_spark_session()\n"
    )
    out, _ = ui.transform_python_source(src, "m.py")
    changes_section, limitations_section = out.split("Known Limitations:")
    assert "manual review of UDF semantics required" in limitations_section
    assert "add spark.addArtifact('udf.py', pyfile=True)" in limitations_section
    assert "manual review of UDF semantics required" not in changes_section
    assert "add spark.addArtifact('udf.py', pyfile=True)" not in changes_section
    assert "- None — all issues resolved" not in limitations_section


def test_scos_warn_stays_in_changes_overview():
    src = (
        "# SCOS-WARN: timezone defaults differ; verify downstream\n"
        "from pyspark.sql import functions as F\n"
        "from snowflake import snowpark_connect\n"
        "spark = snowpark_connect.init_spark_session()\n"
    )
    out, _ = ui.transform_python_source(src, "m.py")
    changes_section, limitations_section = out.split("Known Limitations:")
    assert "timezone defaults differ; verify downstream" in changes_section
    assert "- None — all issues resolved" in limitations_section


def test_review_only_notes_dropped_but_changes_and_caveats_kept():
    # "Reviewed ... safe" no-op notes are noise and must NOT appear under
    # Changes Overview. Genuine changes AND behavioral caveats (which change no
    # code but warn of a real difference) must be preserved.
    src = (
        "from pyspark.sql import functions as F\n"
        "# SCOS: [SPRKCNTPY1000] Reviewed - distinct() is terminal here, safe for SCOS\n"
        "# SCOS: [SPRKCNTPY1000] Fixed int() SQL function — replaced with CAST(x AS INTEGER)\n"
        "# SCOS: [SPRKCNTPY1000] row_number may break ties differently on Snowflake\n"
        "from snowflake import snowpark_connect\n"
        "spark = snowpark_connect.init_spark_session()\n"
    )
    out, _ = ui.transform_python_source(src, "m.py")
    changes_section, _ = out.split("Known Limitations:")
    # Noise dropped:
    assert "Reviewed - distinct()" not in changes_section
    # Real change kept:
    assert "Fixed int() SQL function" in changes_section
    # Behavioral caveat kept (no "safe/compatible" phrasing → not review-only):
    assert "break ties differently" in changes_section


def test_is_review_only_classifier_is_conservative():
    # Clear no-op review phrasings → dropped.
    assert ui._is_review_only("Reviewed - distinct() is terminal, safe for SCOS")
    assert ui._is_review_only("[SPRKCNTPY1000] pivot uses a string column. Safe for SCOS.")
    assert ui._is_review_only("createDataFrame + select works in SCOS")
    assert ui._is_review_only("drop() uses a lowercase name, no mixed-casing risk")
    # Genuine changes and real caveats → kept (NOT review-only).
    assert not ui._is_review_only("Fixed int() — replaced with CAST(x AS INTEGER)")
    assert not ui._is_review_only("row_number may break ties differently on Snowflake")
    assert not ui._is_review_only("df.count() may be slow on large tables; consider caching")


def test_simple_builder_replaced_and_import_added():
    src = (
        "from pyspark.sql import SparkSession\n"
        'spark = SparkSession.builder.appName("x").getOrCreate()\n'
    )
    out, stats = ui.transform_python_source(src, "m.py")
    assert stats["builders_replaced"] == 1
    assert "SparkSession.builder" not in out
    assert "snowpark_connect.init_spark_session()" in out
    assert "from snowflake import snowpark_connect" in out


def test_config_chain_preserved_via_recipe():
    src = (
        "from pyspark.sql import SparkSession\n"
        'spark = SparkSession.builder.master("local[*]")'
        '.config("spark.sql.session.timeZone", "UTC").getOrCreate()\n'
    )
    out, stats = ui.transform_python_source(src, "m.py")
    assert "SparkSession.builder" not in out
    assert "snowpark_connect.init_spark_session()" in out
    # The timezone config must survive as a conf.set follow-up.
    assert 'conf.set("spark.sql.session.timeZone", "UTC")' in out


def test_pyspark_imports_kept():
    src = (
        "from pyspark.sql import functions as F\n"
        "from pyspark.sql.types import StringType\n"
        "from snowflake import snowpark_connect\n"
        "spark = snowpark_connect.init_spark_session()\n"
    )
    out, _ = ui.transform_python_source(src, "m.py")
    assert "from pyspark.sql import functions as F" in out
    assert "from pyspark.sql.types import StringType" in out


def test_unsupported_imports_commented():
    src = (
        "from databricks.connect import DatabricksSession\n"
        "from delta.tables import DeltaTable\n"
        "from snowflake import snowpark_connect\n"
        "spark = snowpark_connect.init_spark_session()\n"
    )
    out, stats = ui.transform_python_source(src, "m.py")
    assert stats["imports_commented"] == 2
    assert "# from databricks.connect import DatabricksSession" in out
    assert "# from delta.tables import DeltaTable" in out
    # And the commented lines compile away cleanly.
    compile(out, "m.py", "exec")


def test_multiline_unsupported_import_commented_as_block():
    src = (
        "from databricks.sdk.runtime import (\n"
        "    dbutils,\n"
        "    spark,\n"
        ")\n"
        "from snowflake import snowpark_connect\n"
        "spark = snowpark_connect.init_spark_session()\n"
    )
    out, stats = ui.transform_python_source(src, "m.py")
    assert stats["imports_commented"] == 1
    # Every physical line of the statement must be commented (no dangling code).
    assert "# from databricks.sdk.runtime import (" in out
    assert "#     dbutils," in out
    assert "# )" in out
    compile(out, "m.py", "exec")


def test_import_like_text_in_string_not_commented():
    """Structural detection must not comment import-looking text that lives
    inside a docstring or string literal."""
    src = (
        '"""Example usage:\n'
        "    from databricks.connect import DatabricksSession\n"
        '"""\n'
        "MSG = 'from delta.tables import DeltaTable'\n"
        "from snowflake import snowpark_connect\n"
        "spark = snowpark_connect.init_spark_session()\n"
    )
    out, stats = ui.transform_python_source(src, "m.py")
    assert stats["imports_commented"] == 0
    # The docstring / string literal text is preserved verbatim, uncommented.
    assert "    from databricks.connect import DatabricksSession" in out
    assert "MSG = 'from delta.tables import DeltaTable'" in out


def test_unparseable_file_falls_back_to_linebased():
    """A file LibCST cannot parse still has its unsupported imports commented
    via the line-based fallback (gate invariant preserved)."""
    src = (
        "from databricks.connect import DatabricksSession\n"
        "def broken(:\n"  # syntax error -> LibCST parse fails
        "    pass\n"
    )
    out, stats = ui.comment_unsupported_imports(src)
    assert stats == 1
    assert "# from databricks.connect import DatabricksSession" in out


# --------------------------------------------------------------------------- #
# End-to-end: gate must PASS after the deterministic run
# --------------------------------------------------------------------------- #


def test_end_to_end_gate_passes(tmp_path: Path):
    conv, output = _conv(tmp_path)
    state = _run(conv, output, {
        "main.py": (
            "from pyspark.sql import SparkSession\n"
            "from databricks.connect import DatabricksSession\n"
            "from delta.tables import DeltaTable\n"
            'spark = SparkSession.builder.appName("etl").getOrCreate()\n'
            "df = spark.range(10)\n"
        ),
        "helper.py": (
            "from pyspark.sql import functions as F\n"
            "def transform(df):\n"
            "    return df.select(F.col('a'))\n"
        ),
    })
    res = run_imports_gate(state)
    assert res.verdict == "PASS", [f.message for f in res.findings]


def test_end_to_end_records_state(tmp_path: Path):
    conv, output = _conv(tmp_path)
    state = _run(conv, output, {
        "main.py": (
            "from pyspark.sql import SparkSession\n"
            'spark = SparkSession.builder.getOrCreate()\n'
        ),
    })
    recorded = json.loads(state.read_text())["phases_completed"]["3_imports"]
    assert recorded["status"] == "passed"
    assert recorded["files_processed"] == 1
    assert recorded["session_inits_replaced"] == 1


def test_no_builder_workload_gets_entrypoint_import(tmp_path: Path):
    """A workload with no SparkSession.builder still must reference
    snowpark_connect so the gate's entry-point invariant holds."""
    conv, output = _conv(tmp_path)
    state = _run(conv, output, {
        "lib.py": (
            "from pyspark.sql import functions as F\n"
            "def add_col(df):\n"
            "    return df.withColumn('b', F.lit(1))\n"
            "\n"
            "if __name__ == '__main__':\n"
            "    pass\n"
        ),
    })
    res = run_imports_gate(state)
    assert res.verdict == "PASS", [f.message for f in res.findings]
    assert "snowpark_connect" in (output / "lib.py").read_text()


def test_idempotent_end_to_end(tmp_path: Path):
    conv, output = _conv(tmp_path)
    files = {
        "main.py": (
            "from pyspark.sql import SparkSession\n"
            "from delta.tables import DeltaTable\n"
            'spark = SparkSession.builder.appName("x").getOrCreate()\n'
        ),
    }
    state = _run(conv, output, files)
    first = (output / "main.py").read_text()
    # Run again over the already-migrated output.
    assert ui.main(["--state", str(state)]) == 0
    second = (output / "main.py").read_text()
    assert first == second
    assert second.count("SCOS Migration Output") == 1


def test_notebook_header_and_builder(tmp_path: Path):
    conv, output = _conv(tmp_path)
    nb = {
        "cells": [
            {"cell_type": "code", "metadata": {}, "outputs": [], "execution_count": None,
             "source": [
                 "from pyspark.sql import SparkSession\n",
                 "spark = SparkSession.builder.getOrCreate()\n",
             ]},
        ],
        "metadata": {"language_info": {"name": "python"}},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    (output / "nb.ipynb").write_text(json.dumps(nb))
    state = _state(conv, output, ["nb.ipynb"])
    assert ui.main(["--state", str(state)]) == 0
    data = json.loads((output / "nb.ipynb").read_text())
    all_src = "\n".join("".join(c.get("source", [])) for c in data["cells"])
    assert "SCOS Migration Output" in all_src
    assert "snowpark_connect.init_spark_session()" in all_src
    assert "SparkSession.builder" not in all_src


# --------------------------------------------------------------------------- #
# Standalone .sql header (SQL comment style, per-file accurate)
# --------------------------------------------------------------------------- #

def test_sql_header_uses_dash_comments_not_python_docstring():
    sql = "SELECT a FROM t;\n"
    out, added = ui.add_migration_header(sql, "q.sql", comment_prefix="--")
    assert added is True
    assert out.startswith("-- SCOS Migration Output")
    assert '"""' not in out                 # never a Python docstring on SQL
    assert "Source File: q.sql" in out
    assert sql in out


def test_sql_header_reflects_per_file_scos_annotations():
    sql = (
        "-- SCOS: [detector:window_without_order_by] added ORDER BY\n"
        "-- SCOS: TODO - [detector:lca_alias_collision] rename the alias\n"
        "SELECT 1 FROM t;\n"
    )
    out, _ = ui.add_migration_header(sql, "q.sql", comment_prefix="--")
    changes, lims = out.split("Known Limitations:")
    assert "window_without_order_by" in changes      # a Change
    assert "lca_alias_collision" in lims              # a TODO/limitation
    # not the constant placeholder, because the file has real annotations
    assert "No compatibility issues detected" not in changes


def test_sql_header_idempotent():
    sql = "-- SCOS: [detector:explain_ddl_rejected] dropped EXPLAIN\nCREATE TABLE t AS SELECT 1;\n"
    first, a1 = ui.add_migration_header(sql, "q.sql", comment_prefix="--")
    assert a1 is True
    second, a2 = ui.add_migration_header(first, "q.sql", comment_prefix="--")
    assert a2 is False
    assert second == first
    assert second.count("SCOS Migration Output") == 1


def test_sql_wrong_style_python_header_is_replaced():
    # A .sql file that earlier got the (buggy) Python docstring header must be
    # repaired to a SQL `--` header on re-run.
    broken = (
        '"""\nSCOS Migration Output\n=====================\n'
        "Source File: q.sql\nMigrated on: 2026-01-01\n\n"
        "Changes Overview:\n- No compatibility issues detected. No changes required.\n\n"
        "Known Limitations:\n- None — all issues resolved\n\"\"\"\n"
        "SELECT 1 FROM t;\n"
    )
    out, added = ui.add_migration_header(broken, "q.sql", comment_prefix="--")
    assert added is True
    assert '"""' not in out
    assert out.startswith("-- SCOS Migration Output")
    assert out.count("SCOS Migration Output") == 1


def test_transform_file_skips_python_header_on_sql(tmp_path):
    conv, output = _conv(tmp_path)
    p = output / "q.sql"
    p.write_text("SELECT ROW_NUMBER() OVER (PARTITION BY x) AS rn FROM t;\n")
    stats = ui.transform_file(str(p), "q.sql")
    out = p.read_text()
    assert stats["header_added"] is True
    assert out.startswith("-- SCOS Migration Output")
    assert '"""' not in out                 # the original bug: no Python docstring
    assert stats["builders_replaced"] == 0   # no Python passes on SQL
