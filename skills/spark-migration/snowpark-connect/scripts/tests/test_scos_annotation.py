"""Unit tests for inline ``#EWI`` code normalization in
``generate_scos_reports``.

The fixer writes ``# SCOS: [SPRKCNT...] <message>``. We keep exactly one
comment line per issue with the EWI code inline — no second ``#EWI:`` line and
no duplicated message. For comments that lack a code we inject a generic one;
legacy standalone ``#EWI:`` lines are removed.

Run from the ``snowpark-connect/`` directory:

    pytest scripts/tests/test_scos_annotation.py
"""

from __future__ import annotations

import csv
import os

from generate_scos_reports import (
    _annotate_lines,
    _message_signal,
    _status_from_message,
    annotate_scos_markers,
    ensure_migration_headers,
    find_scos_blocks,
    generate_issues_csv,
    load_ewi_mapping,
    scan_scos_comments,
    status_to_category,
)

MAPPING = load_ewi_mapping("python")


def _write(tmp_path, body: str) -> str:
    migrated = tmp_path / "Output"
    migrated.mkdir()
    (migrated / "job.py").write_text(body)
    return str(migrated)


# --- block extraction --------------------------------------------------------


def test_find_scos_blocks_splits_inline_code_and_flattens():
    src = (
        "# SCOS: [SPRKCNTPY0060] External helper — out-of-scope; isLocal\n"
        "# concern is a false positive.\n"
        "x = 1\n"
    )
    blocks = find_scos_blocks(src.splitlines(), "python")
    assert len(blocks) == 1
    b = blocks[0]
    assert b["code"] == "SPRKCNTPY0060"
    # code is stripped from the description; continuation lines coalesced
    assert b["description"].startswith("External helper")
    assert "false positive." in b["description"]
    assert "SPRKCNTPY0060" not in b["description"]


def test_find_scos_blocks_code_none_when_absent():
    blocks = find_scos_blocks(["# SCOS: TODO - rename duplicate fields", "y = 2"], "python")
    assert blocks[0]["code"] is None
    assert blocks[0]["category"] == "Snowpark Connect TODO"


def test_find_scos_blocks_detects_recipe_warn_and_todo_markers():
    # Annotate-only recipes emit "# SCOS-WARN:" / "# SCOS-TODO:" with a
    # leading recipe id. Both must be discovered and categorised by flavour.
    src = (
        "# SCOS-WARN: self_join_unaliased_warn_annotate: self-join with the\n"
        "# same DataFrame name may be ambiguous.\n"
        "df2 = df.join(df, 'id')\n"
        "# SCOS-TODO: wildcard_file_read_todo_annotate: replace glob with an\n"
        "# explicit file list.\n"
        "spark.read.parquet('data/*.parquet')\n"
    )
    blocks = find_scos_blocks(src.splitlines(), "python")
    assert len(blocks) == 2
    warn, todo = blocks
    assert warn["category"] == "Snowpark Connect Warning"
    assert warn["description"].startswith("self_join_unaliased_warn_annotate:")
    assert "ambiguous." in warn["description"]
    assert todo["category"] == "Snowpark Connect TODO"
    assert todo["description"].startswith("wildcard_file_read_todo_annotate:")
    assert "explicit file list." in todo["description"]


# --- annotation: no duplicate line, code stays inline ------------------------


def test_legacy_ewi_line_removed_and_no_duplicate(tmp_path):
    # Mirrors the reported bug: a standalone #EWI line above a coded # SCOS line.
    body = (
        "from pyspark.sql import Row\n"
        "#EWI: SPRKCNTPY1000 => [SPRKCNTPY0060] External helper — false positive\n"
        "# SCOS: [SPRKCNTPY0060] External helper — false positive\n"
        "x = 1\n"
    )
    migrated = _write(tmp_path, body)
    annotate_scos_markers(migrated, "python", MAPPING)
    lines = open(os.path.join(migrated, "job.py")).read().splitlines()

    # The standalone #EWI line is gone; the coded # SCOS line keeps its base
    # code and gains the deterministic status suffix (category "Fix" -> Warning).
    assert not any(l.lstrip().startswith("#EWI:") for l in lines)
    scos = [l for l in lines if l.lstrip().startswith("# SCOS:")]
    assert scos == ["# SCOS: [SPRKCNTPY0060-Warning] External helper — false positive"]


def test_code_injected_inline_when_missing(tmp_path):
    body = "# SCOS: TODO - rename duplicate fields\nschema = build()\n"
    migrated = _write(tmp_path, body)
    n1 = annotate_scos_markers(migrated, "python", MAPPING)
    n2 = annotate_scos_markers(migrated, "python", MAPPING)  # idempotent
    assert n1 == 1 and n2 == 0

    lines = open(os.path.join(migrated, "job.py")).read().splitlines()
    scos = next(l for l in lines if l.lstrip().startswith("# SCOS:"))
    # exactly one line, code+status inserted between marker and message, message
    # once. A bare advisory TODO with no runtime-failure signal -> Warning
    # (``-Error`` is reserved for code that fails when run on SCOS).
    assert scos == "# SCOS: [SPRKCNTPY1000-Warning] TODO - rename duplicate fields"
    assert sum(1 for l in lines if "rename duplicate fields" in l) == 1


def test_code_after_todo_prefix_is_not_duplicated(tmp_path):
    # Regression: the agent puts the code AFTER "TODO - "; the annotator used
    # to only detect a code at the start and injected a second one.
    body = (
        "# SCOS: TODO - [SPRKCNTPY1000] Out-of-scope dependency (store_utils.foo).\n"
        "# Verify this helper is SCOS-compatible.\n"
        "open_stores = foo(spark)\n"
    )
    migrated = _write(tmp_path, body)
    n1 = annotate_scos_markers(migrated, "python", MAPPING)
    n2 = annotate_scos_markers(migrated, "python", MAPPING)
    assert n1 == 1 and n2 == 0  # normalized once, then idempotent

    lines = open(os.path.join(migrated, "job.py")).read().splitlines()
    first = lines[0]
    # exactly one code, moved to the canonical position after the marker, with
    # the derived status suffix. An out-of-scope-dependency advisory executes
    # (needs review) -> Warning, not a runtime-failure Error.
    assert first == "# SCOS: [SPRKCNTPY1000-Warning] TODO - Out-of-scope dependency (store_utils.foo)."
    assert first.count("[SPRKCNTPY1000-Warning]") == 1
    assert "[SPRKCNTPY1000]" not in first


def test_existing_code_is_preserved_not_overwritten(tmp_path):
    # The fixer's specific base code (SPRKCNTPY0060) is preserved; only the
    # deterministic status suffix is appended (category "Fix" -> Warning).
    body = "# SCOS: [SPRKCNTPY0060] keep me\nz = 3\n"
    migrated = _write(tmp_path, body)
    n1 = annotate_scos_markers(migrated, "python", MAPPING)
    n2 = annotate_scos_markers(migrated, "python", MAPPING)  # idempotent
    assert n1 == 1 and n2 == 0
    lines = open(os.path.join(migrated, "job.py")).read().splitlines()
    assert "# SCOS: [SPRKCNTPY0060-Warning] keep me" in lines


def test_indentation_preserved(tmp_path):
    body = "def f():\n    # SCOS: Fix - cast value before repartition\n    x = 1\n"
    migrated = _write(tmp_path, body)
    annotate_scos_markers(migrated, "python", MAPPING)
    lines = open(os.path.join(migrated, "job.py")).read().splitlines()
    marker = next(l for l in lines if "# SCOS:" in l)
    assert marker == "    # SCOS: [SPRKCNTPY1000-Warning] Fix - cast value before repartition"


def test_never_touches_docstring_prose(tmp_path):
    body = (
        '"""\n'
        "Known Limitations:\n"
        '- A "# SCOS:" here is prose inside a docstring, not a comment.\n'
        '"""\n'
        "import os\n"
    )
    migrated = _write(tmp_path, body)
    # A "# SCOS:" line inside the docstring still matches line-wise; ensure we
    # at least never emit a second line / never produce a duplicate message.
    annotate_scos_markers(migrated, "python", MAPPING)
    lines = open(os.path.join(migrated, "job.py")).read().splitlines()
    assert not any(l.lstrip().startswith("#EWI:") for l in lines)


# --- Issues.csv reads the inline code ---------------------------------------


def test_issues_csv_uses_inline_code(tmp_path):
    body = (
        "# SCOS: [SPRKCNTPY0060] External helper — false positive\n"
        "x = 1\n"
        "# SCOS: TODO - rename duplicate fields\n"
        "y = 2\n"
    )
    migrated = _write(tmp_path, body)
    out = str(tmp_path)
    annotate_scos_markers(migrated, "python", MAPPING)

    scanned = scan_scos_comments(migrated, "python")
    assert {c["code"] for c in scanned} == {"SPRKCNTPY0060", "SPRKCNTPY1000"}

    generate_issues_csv("", migrated, out, "python", MAPPING, source_dir=out)
    rows = list(csv.DictReader(open(os.path.join(out, "Reports", "Issues.csv"))))
    codes = {r["Code"] for r in rows}
    # The Code column carries the suffixed code; the real fixer base code wins
    # over the generic one.
    assert any(c.startswith("SPRKCNTPY0060-") for c in codes)
    # description carries the message without the bracketed code
    helper = next(r for r in rows if r["Code"].startswith("SPRKCNTPY0060"))
    assert helper["Description"].startswith("External helper")
    assert "SPRKCNTPY0060" not in helper["Description"]
    # Status column is populated and matches the suffix on the code
    assert helper["Status"] == helper["Code"].split("-", 1)[1]


def test_ensure_migration_headers_skips_agent_written_header(tmp_path):
    # Regression: the agent writes its own '"""SCOS Migration: <file>"""'
    # docstring; ensure_migration_headers must recognize it and NOT stack a
    # second header on top (which produced a corrupted double docstring).
    body = (
        '"""\n'
        "SCOS Migration: a.py\n"
        "Migrated from PySpark to Snowpark Connect (SCOS).\n"
        '"""\n'
        "import os\n"
    )
    migrated = tmp_path / "Output"
    migrated.mkdir()
    (migrated / "a.py").write_text(body)
    patched = ensure_migration_headers(str(migrated), "python")
    assert patched == 0
    content = (migrated / "a.py").read_text()
    assert content.count('"""') == 2  # still a single docstring


def test_ensure_migration_headers_skips_databricks_scala_notebook(tmp_path):
    # Regression: a Databricks exported-text Scala notebook (first line
    # `// Databricks notebook source`) must NOT get a raw header prepended —
    # that destroys the notebook-source marker, breaking detection + re-parsing.
    # Notebook headers are added structurally as a cell by Phase 3 instead.
    nb = (
        "// Databricks notebook source\n"
        "// COMMAND ----------\n"
        "val df = spark.table(\"t\")\n"
    )
    migrated = tmp_path / "Output" / "notebooks"
    migrated.mkdir(parents=True)
    (migrated / "job.scala").write_text(nb)
    patched = ensure_migration_headers(str(tmp_path / "Output"), "scala")
    assert patched == 0
    content = (migrated / "job.scala").read_text()
    # First line is still the Databricks notebook-source marker (intact).
    assert content.splitlines()[0] == "// Databricks notebook source"
    assert "SCOS Migration Output" not in content


def test_ensure_migration_headers_still_headers_plain_scala(tmp_path):
    # A plain .scala source file (not a notebook) still gets its header.
    migrated = tmp_path / "Output"
    migrated.mkdir()
    (migrated / "M.scala").write_text("package x\nobject M\n")
    patched = ensure_migration_headers(str(migrated), "scala")
    assert patched == 1
    assert "SCOS Migration" in (migrated / "M.scala").read_text()


def test_annotate_lines_leaves_recipe_warn_todo_untouched(tmp_path):
    # Recipe markers carry a recipe-id audit trail and no EWI code. The
    # annotator must NOT rewrite them (no inline code injected, recipe id kept)
    # so the migration_state audit trail stays intact. (Contrast the coded
    # variants in test_annotate_lines_stamps_status_on_coded_todo/warn, which do
    # gain a status suffix.)
    body = (
        "# SCOS-WARN: self_join_unaliased_warn_annotate: ambiguous self-join\n"
        "df2 = df.join(df, 'id')\n"
        "# SCOS-TODO: wildcard_file_read_todo_annotate: replace glob\n"
        "spark.read.parquet('data/*.parquet')\n"
    )
    migrated = _write(tmp_path, body)
    changed = annotate_scos_markers(migrated, "python", MAPPING)
    assert changed == 0
    lines = open(os.path.join(migrated, "job.py")).read().splitlines()
    assert "# SCOS-WARN: self_join_unaliased_warn_annotate: ambiguous self-join" in lines
    assert "# SCOS-TODO: wildcard_file_read_todo_annotate: replace glob" in lines
    assert not any("[SPRKCNT" in l for l in lines)


def test_issues_csv_includes_recipe_warn_todo(tmp_path):
    # End-to-end: recipe-emitted SCOS-WARN / SCOS-TODO comments must surface as
    # rows in Issues.csv (the gap this fix closes).
    body = (
        "# SCOS-WARN: self_join_unaliased_warn_annotate: ambiguous self-join\n"
        "df2 = df.join(df, 'id')\n"
        "# SCOS-TODO: wildcard_file_read_todo_annotate: replace glob\n"
        "spark.read.parquet('data/*.parquet')\n"
    )
    migrated = _write(tmp_path, body)
    out = str(tmp_path)
    annotate_scos_markers(migrated, "python", MAPPING)

    generate_issues_csv("", migrated, out, "python", MAPPING, source_dir=out)
    rows = list(csv.DictReader(open(os.path.join(out, "Reports", "Issues.csv"))))
    descriptions = " || ".join(r["Description"] for r in rows)
    assert "self_join_unaliased_warn_annotate" in descriptions
    assert "wildcard_file_read_todo_annotate" in descriptions
    # every surfaced row still carries a resolved EWI code
    assert all(r["Code"].startswith("SPRKCNT") for r in rows)


def test_annotate_lines_stamps_status_on_coded_todo(tmp_path):
    # A ``# SCOS-TODO:`` marker that already carries a bracketed code but no
    # status suffix (the fixer's dbutils form) must gain the deterministic
    # status from the MESSAGE, not a blanket TODO->Error default.
    # ``dbutils.secrets ... stubbed to None`` was neutralized by the recipe:
    # the emitted line RUNS on SCOS (returns None), so it is a manual follow-up
    # (Warning), NOT a ConversionError. It must also NOT become ``Fixed`` — a
    # stub is not a completed migration.
    body = (
        "# SCOS-TODO: [SPRKCNTPY1000] dbutils_secrets_get_stub_rewrite: "
        "dbutils.secrets has no SCOS equivalent; stubbed to None. Migrate to "
        "Snowflake Secrets.\n"
        "jdbcPassword = None\n"
    )
    migrated = _write(tmp_path, body)
    n1 = annotate_scos_markers(migrated, "python", MAPPING)
    n2 = annotate_scos_markers(migrated, "python", MAPPING)  # idempotent
    assert n1 == 1 and n2 == 0

    lines = open(os.path.join(migrated, "job.py")).read().splitlines()
    marker = next(l for l in lines if l.lstrip().startswith("# SCOS-TODO:"))
    assert "-Warning]" in marker            # stubbed -> runs -> manual follow-up, not Error
    assert "-Error]" not in marker          # zero false-positive Errors
    assert "-Fixed]" not in marker          # a stub is not a completed fix
    assert "[SPRKCNTPY1000]" not in marker  # no code left without a status
    assert marker.startswith("# SCOS-TODO:")  # fixer prefix preserved


def test_annotate_lines_stamps_status_on_coded_warn(tmp_path):
    # A ``# SCOS-WARN:`` marker with a bracketed code but no status gets the
    # advisory ``Warning`` disposition (flavour default).
    body = "# SCOS-WARN: [SPRKCNTPY6100] Performance tip - count() may be slow\nx = df.count()\n"
    migrated = _write(tmp_path, body)
    n1 = annotate_scos_markers(migrated, "python", MAPPING)
    n2 = annotate_scos_markers(migrated, "python", MAPPING)
    assert n1 == 1 and n2 == 0
    lines = open(os.path.join(migrated, "job.py")).read().splitlines()
    marker = next(l for l in lines if l.lstrip().startswith("# SCOS-WARN:"))
    assert marker == "# SCOS-WARN: [SPRKCNTPY6100-Warning] Performance tip - count() may be slow"


def test_annotate_lines_coded_todo_not_flipped_to_fixed_when_code_changed():
    # With a source pairing, the stub replaced the original dbutils line, so the
    # code-shape probe reports "changed". A plain ``# SCOS:`` marker would be
    # upgraded to ``Fixed``; a ``-TODO`` must NOT — a stub is a manual follow-up,
    # not a completed fix. The stub lets the code run (returns None), so the
    # disposition is Warning (needs review), never Fixed.
    original = 'jdbcPassword = dbutils.secrets.get("s", "k")\n'
    lines = [
        "# SCOS-TODO: [SPRKCNTPY1000] dbutils_secrets_get_stub_rewrite: stubbed to None",
        "jdbcPassword = None",
    ]
    out, _ = _annotate_lines(lines, "python", MAPPING, original_src=original)
    assert out[0] == (
        "# SCOS-TODO: [SPRKCNTPY1000-Warning] dbutils_secrets_get_stub_rewrite: stubbed to None"
    )


def test_annotate_lines_unit_cell_style_no_newlines():
    # cell source: lines have no trailing newline
    lines = ["#EWI: SPRKCNTPY1000 => dup msg", "# SCOS: dup msg", "x = 1"]
    out, n = _annotate_lines(lines, "python", MAPPING)
    # legacy EWI removed (1) + code+status injected into SCOS (1)
    assert n == 2
    assert out == ["# SCOS: [SPRKCNTPY1000-Warning] dup msg", "x = 1"]
    assert not out[0].endswith("\n")

# --- deterministic refinement + status classification -----------------------


def test_marker_with_code_status_is_preserved(tmp_path):
    # Deterministic path: the fixer embedded [CODE-STATUS]; the reporter keeps it
    # verbatim (no prose refine/derive), idempotently.
    body = (
        "# SCOS: [SPRKCNTPY3200-IO] dbutils.fs.ls() is not available in SCOS; use a stage listing\n"
        "x = 1\n"
        "# SCOS: [SPRKCNTPY6100-Fixed] replaced checkpoint() with cache()\n"
        "y = 2\n"
    )
    migrated = _write(tmp_path, body)
    out = str(tmp_path)
    annotate_scos_markers(migrated, "python", MAPPING)
    annotate_scos_markers(migrated, "python", MAPPING)  # idempotent
    lines = open(os.path.join(migrated, "job.py")).read().splitlines()
    assert any("[SPRKCNTPY3200-IO]" in l for l in lines)
    assert any("[SPRKCNTPY6100-Fixed]" in l for l in lines)
    assert not any("-IO-IO" in l or "-Fixed-Fixed" in l for l in lines)

    generate_issues_csv("", migrated, out, "python", MAPPING, source_dir=out)
    rows = list(csv.DictReader(open(os.path.join(out, "Reports", "Issues.csv"))))
    by_code = {r["Code"]: r for r in rows}
    assert "SPRKCNTPY3200-IO" in by_code and by_code["SPRKCNTPY3200-IO"]["Status"] == "IO"
    assert "SPRKCNTPY6100-Fixed" in by_code and by_code["SPRKCNTPY6100-Fixed"]["Status"] == "Fixed"

    summary = list(csv.DictReader(open(os.path.join(out, "Reports", "EWISummary.csv"))))
    smap = {(r["Metric"], r["Key"]): r for r in summary}
    assert smap[("by_status", "Fixed")]["IssueCount"] == "1"


def test_explicit_fixed_not_downgraded_by_code_shape_probe():
    # When a recipe explicitly writes -Fixed but the code-shape probe says the
    # marker line is "unchanged" (e.g. the recipe inserts code BELOW the marker),
    # the explicit suffix must be preserved — not downgraded to -Error via message
    # text matching. Regression test for implicit_spark_inject_bootstrap.
    original = "# Databricks notebook source\npass\n"
    lines = [
        "# SCOS: [SPRKCNTPY1001-Fixed] implicit_spark_inject_bootstrap: the implicitly-provided `spark` global is not available in SCOS; injected snowpark_connect.init_spark_session()",
        "from snowflake import snowpark_connect",
        "spark = snowpark_connect.init_spark_session()",
        "pass",
    ]
    out, _ = _annotate_lines(lines, "python", MAPPING, original_src=original)
    marker = out[0]
    # Must remain -Fixed, NOT be downgraded to -Error by "not available in" regex
    assert "[SPRKCNTPY1001-Fixed]" in marker, f"Expected -Fixed but got: {marker}"
    assert "[SPRKCNTPY1001-Error]" not in marker


def test_marker_without_status_uses_flavour_default(tmp_path):
    # No status suffix: fall back to the marker-flavour default — a plain fix
    # marker -> Warning, a TODO marker -> Error (needs human). No prose regex.
    body = (
        "# SCOS: [SPRKCNTPY4002] some fix note\n"
        "x = 1\n"
        "# SCOS: TODO - [SPRKCNTPY3100] dbutils.secrets.get unsupported; use a Snowflake secret\n"
        "y = 2\n"
    )
    migrated = _write(tmp_path, body)
    annotate_scos_markers(migrated, "python", MAPPING)
    lines = open(os.path.join(migrated, "job.py")).read().splitlines()
    assert any("[SPRKCNTPY4002-Warning]" in l for l in lines)
    assert any("[SPRKCNTPY3100-Error]" in l for l in lines)


# --- duplicate SCOS marker deduplication (Defect 2) ---------------------------


def test_duplicate_stacked_scos_markers_collapsed(tmp_path):
    """Adjacent identical SCOS markers are collapsed to a single one."""
    body = (
        "# SCOS: [SPRKCNTPY0060] External helper — false positive\n"
        "# SCOS: [SPRKCNTPY0060] External helper — false positive\n"
        "# SCOS: [SPRKCNTPY0060] External helper — false positive\n"
        "x = 1\n"
    )
    migrated = _write(tmp_path, body)
    n1 = annotate_scos_markers(migrated, "python", MAPPING)
    lines = open(os.path.join(migrated, "job.py")).read().splitlines()
    scos = [l for l in lines if "SCOS:" in l]
    assert len(scos) == 1  # collapsed from 3 to 1
    assert n1 >= 2  # at least 2 duplicates removed


def test_annotation_is_idempotent_on_duplicate_run(tmp_path):
    """Running annotation twice yields the same output (no additional duplication)."""
    body = (
        "# SCOS: checkpoint() not supported; replaced with cache()\n"
        "x = df.cache()\n"
    )
    migrated = _write(tmp_path, body)
    annotate_scos_markers(migrated, "python", MAPPING)
    first_pass = open(os.path.join(migrated, "job.py")).read()
    annotate_scos_markers(migrated, "python", MAPPING)
    second_pass = open(os.path.join(migrated, "job.py")).read()
    assert first_pass == second_pass


# --- over-Error correction: message-driven disposition ----------------------


def test_message_signal_priority_io_advisory_genuine():
    # IO covers DATA input/output plumbing: external-storage paths, unsupported
    # file formats, external data-source connectors, and filesystem access.
    assert _message_signal("write to s3://bucket/x; use a Snowflake stage") == "IO"
    assert _message_signal("spark.read.parquet('s3://b/x') external path") == "IO"
    assert _message_signal("dbutils.fs.ls is a DBFS filesystem op; use a stage") == "IO"
    assert _message_signal("JDBC source requires a JVM driver; use the connector") == "IO"
    assert _message_signal("SCOS has no ORC reader") == "IO"
    # Advisory/hedge beats genuine so conditional 'unsupported…validate' -> Warning.
    assert _message_signal("to_date patterns are unsupported; validate output") == "Warning"
    assert _message_signal("from_json coercion differs on SCOS; verify") == "Warning"
    assert _message_signal("count() may be slow on large data") == "Warning"
    # A construct a recipe already neutralized RUNS -> Warning, not Error.
    assert _message_signal("dbutils.secrets has no SCOS equivalent; stubbed to None") == "Warning"
    # Genuinely-unsupported CODE-LOGIC APIs (no equivalent) -> Error, NOT IO.
    assert _message_signal("RDD has no SCOS equivalent; rewrite with DataFrame API") == "Error"
    assert _message_signal("PIVOT with struct-typed values is unsupported in SCOS") == "Error"
    assert _message_signal("CREATE TABLE USING DELTA not supported") == "Error"
    assert _message_signal("DataFrameWriter.bucketBy is Unsupported in Snowpark Connect") == "Error"
    assert _message_signal("dbutils.jobs.taskValues is Databricks-only") == "Error"
    # No signal -> None (caller defaults to Warning).
    assert _message_signal("rename duplicate fields") is None
    assert _status_from_message("rename duplicate fields") == "Warning"


def test_inline_error_reevaluated_on_hedge_but_absolute_kept(tmp_path):
    # A fixer-stamped inline -Error on a hedge/path message is downgraded; a
    # genuinely-unsupported API or absolute failure keeps Error.
    body = (
        "# SCOS: [SPRKCNTPY5400-Error] TODO - qualified col refs may fail to resolve; verify\n"
        "a = 1\n"
        "# SCOS: [SPRKCNTPY1000-Error] TODO - writing to external s3 path; use a Snowflake stage\n"
        "b = 2\n"
        "# SCOS: [SPRKCNTPY3200-Error] TODO - dbutils.fs.ls filesystem op; use a Snowflake stage\n"
        "c = 3\n"
        "# SCOS: [SPRKCNTPY3400-Error] TODO - CREATE TABLE USING DELTA not supported\n"
        "d = 4\n"
    )
    migrated = _write(tmp_path, body)
    annotate_scos_markers(migrated, "python", MAPPING)
    text = open(os.path.join(migrated, "job.py")).read()
    assert "[SPRKCNTPY5400-Warning]" in text   # hedge -> Warning
    assert "[SPRKCNTPY1000-IO]" in text         # external path repoint -> IO
    assert "[SPRKCNTPY3200-IO]" in text         # dbutils.fs filesystem -> data I/O repoint
    assert "[SPRKCNTPY3400-Error]" in text      # absolute Delta DDL -> kept Error


def test_status_to_category_reserves_conversion_error_for_error():
    assert status_to_category("Error") == "ConversionError"
    assert status_to_category("Warning") == "Warning"
    assert status_to_category("IO") == "Warning"
    assert status_to_category("Fixed") == "Warning"
