"""Tests for the AST block-extraction coverage fix in ``analyze_pyspark``.

Part A: visit_Assign now uses a dotted-call check (like visit_Expr) instead
of a bare substring check, fixing a bug where short CSV method names (e.g.
"e") matched almost any assignment.

Part B: a fallback sweep now covers calls to APIs the analyzer doesn't
recognize (AWS Glue, Azure Synapse, other vendor SDKs), which previously
produced zero blocks no matter what kind of statement they were in.

Run from the ``snowpark-connect/`` directory:

    pytest scripts/tests/test_fallback_block_extraction.py
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip(
    "snowflake.snowpark",
    reason="Fallback block extraction tests need the full SCOS dependency stack (CI only)",
)

from analyze_pyspark import (  # noqa: E402
    _process_single_block,
    extract_code_blocks_from_source,
    load_api_compatibility,
)
from rag.trigger_kb import TriggerKB  # noqa: E402

_DATA = Path(__file__).resolve().parents[1] / "data" / "api_compatibility.csv"


def _methods():
    _, methods = load_api_compatibility(_DATA)
    return methods


def _blocks(src: str):
    return extract_code_blocks_from_source(src, _methods())


def _block_types(src: str) -> list[str]:
    return [b.block_type for b in _blocks(src)]


# --------------------------------------------------------------------------- #
# Part A: visit_Assign "e"-bug fix.
# --------------------------------------------------------------------------- #


def test_generic_non_spark_assignment_reclassified_as_fallback_not_assignment():
    """Before the fix this was mislabeled "assignment" (the "e"-bug). Now
    it's correctly tagged "fallback_call" instead."""
    types = _block_types("xyz_qwrt = compute_thing(5)")
    assert types == ["fallback_call"]


def test_real_spark_assignment_still_produces_a_block():
    """Regression guard: genuine PySpark assignments must still be detected
    by the (now stricter) dotted-call check."""
    blocks = _blocks('df = spark.read.parquet("/data")')
    assert len(blocks) == 1
    assert blocks[0].block_type == "assignment"


def test_dotted_pyspark_method_assignment_still_produces_a_block():
    """A real dotted PySpark method call in assignment form (no literal
    "spark"/"session" text) must still be recognized via has_pyspark_method."""
    blocks = _blocks('result = source_df.withColumn("x", F.lit(1))')
    assert len(blocks) == 1
    assert blocks[0].block_type == "assignment"


def test_assignment_with_no_call_nodes_still_skipped():
    """Unrelated to Part A/B: pure literal assignments still produce no
    block (unchanged behavior, not in scope of this fix)."""
    assert _blocks('path = "/some/literal/string"') == []


# --------------------------------------------------------------------------- #
# Part B: fallback sweep for calls invisible to the Spark-aware visitors.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "code",
    [
        "job.commit()",
        'mssparkutils.fs.mount("abfss://c@a.dfs.core.windows.net/p", "/mounted")',
        "job.init(args)",
    ],
)
def test_bare_vendor_sdk_call_now_produces_fallback_block(code: str):
    """Bare (unassigned) statement calls to non-PySpark vendor SDKs (Glue's
    ``job``, Synapse's ``mssparkutils``) used to produce ZERO blocks because
    ``visit_Expr``'s dotted-call check only recognizes named PySpark methods.
    They must now be covered by a fallback_call block."""
    blocks = _blocks(code)
    assert len(blocks) == 1
    assert blocks[0].block_type == "fallback_call"
    assert blocks[0].code == code


def test_augmented_assignment_with_call_produces_fallback_block():
    blocks = _blocks("counter += get_increment()")
    assert len(blocks) == 1
    assert blocks[0].block_type == "fallback_call"


def test_augmented_assignment_without_call_produces_no_block():
    assert _blocks("counter += 1") == []


def test_return_with_call_produces_fallback_block():
    blocks = _blocks("def f():\n    return do_something(x)\n")
    assert len(blocks) == 1
    assert blocks[0].block_type == "fallback_call"
    assert "do_something(x)" in blocks[0].code


def test_return_without_call_produces_no_block():
    assert _blocks("def f():\n    return x\n") == []


def test_assert_with_call_produces_fallback_block():
    blocks = _blocks("assert job.validate(x), 'bad input'")
    assert len(blocks) == 1
    assert blocks[0].block_type == "fallback_call"


def test_raise_with_call_produces_fallback_block():
    blocks = _blocks("raise ValueError(job.get_error_message())")
    assert len(blocks) == 1
    assert blocks[0].block_type == "fallback_call"


# --- Compound statements: anchor must NOT span the whole body ------------- #


def test_if_test_call_anchors_to_condition_not_whole_body():
    """A call in an ``if`` test must anchor to just the test line, not the
    If node's full line range (which would re-scan the body's own,
    separately-covered blocks and produce duplicate findings)."""
    src = "if job.check_ready():\n    x = 1\n    y = 2\n    z = 3\n"
    blocks = _blocks(src)
    fallback = [b for b in blocks if b.block_type == "fallback_call"]
    assert len(fallback) == 1
    assert fallback[0].line_start == 1
    assert fallback[0].line_end == 1
    # The extracted code is just the test EXPRESSION, not the full
    # "if ...:" line -- see the sub-expression branch in
    # _collect_fallback_call_blocks: slicing the whole line would produce
    # an incomplete (unparseable) compound-statement fragment, which forces
    # TriggerKB.detect()'s internal ast.parse() to fail and fall through to
    # a pointless sqlglot attempt. The isolated expression is valid,
    # independently-parseable Python.
    assert fallback[0].code == "job.check_ready()"


def test_while_test_call_anchors_to_condition_not_whole_body():
    src = "while job.has_more():\n    x = 1\n    y = 2\n"
    blocks = _blocks(src)
    fallback = [b for b in blocks if b.block_type == "fallback_call"]
    assert len(fallback) == 1
    assert fallback[0].line_start == 1
    assert fallback[0].line_end == 1


def test_for_iter_call_anchors_to_iterable_not_whole_body():
    src = 'for item in glueContext.create_dynamic_frame.from_catalog(db="d"):\n    x = 1\n    y = 2\n'
    blocks = _blocks(src)
    fallback = [b for b in blocks if b.block_type == "fallback_call"]
    assert len(fallback) == 1
    assert fallback[0].line_start == 1
    assert fallback[0].line_end == 1


def test_with_context_call_anchors_to_context_expr_not_whole_body():
    """A with's Call parent is a withitem wrapper, not the With statement
    itself -- make sure we unwrap it instead of anchoring to the whole body."""
    src = 'with mssparkutils.fs.open("path") as f:\n    x = 1\n    y = 2\n'
    blocks = _blocks(src)
    fallback = [b for b in blocks if b.block_type == "fallback_call"]
    assert len(fallback) == 1
    assert fallback[0].line_start == 1
    assert fallback[0].line_end == 1


def test_with_multiple_items_dedupes_to_one_block():
    src = "with a.open() as f, b.connect() as g:\n    x = 1\n"
    blocks = _blocks(src)
    fallback = [b for b in blocks if b.block_type == "fallback_call"]
    assert len(fallback) == 1


def test_boolop_in_if_test_dedupes_to_one_block():
    """Two calls combined with ``and`` in one ``if`` test must collapse to a
    single fallback block (same anchor: the BoolOp is cur.test)."""
    blocks = _blocks("if job.check_ready() and job.is_valid():\n    pass\n")
    fallback = [b for b in blocks if b.block_type == "fallback_call"]
    assert len(fallback) == 1


def test_compound_statement_anchor_code_is_independently_parseable():
    """if/while/for/with fallback blocks must be valid Python on their own,
    not a headless fragment like "if job.check_ready():" with no body."""
    import ast as _ast

    cases = [
        "if job.check_ready():\n    x = 1\n",
        "while job.has_more():\n    x = 1\n",
        'for item in glueContext.create_dynamic_frame.from_catalog(db="d"):\n    x = 1\n',
        'with mssparkutils.fs.open("path") as f:\n    x = 1\n',
    ]
    for src in cases:
        blocks = _blocks(src)
        fallback = [b for b in blocks if b.block_type == "fallback_call"]
        assert len(fallback) == 1, src
        _ast.parse(fallback[0].code)  # must not raise SyntaxError
        assert not fallback[0].code.rstrip().endswith(":"), (
            f"{fallback[0].code!r} looks like an incomplete compound "
            f"statement, not an isolated expression"
        )


# --- Dedup for nested calls ------------------------------------------------- #


def test_nested_calls_dedupe_to_one_block():
    """``f(g(x))`` must produce exactly one fallback block, not one per call."""
    blocks = _blocks("result = outer_call(inner_call(x))")
    assert len(blocks) == 1
    assert blocks[0].block_type == "fallback_call"


# --- Calls already covered by a Spark-aware block must NOT get a duplicate - #


def test_covered_spark_call_does_not_also_get_a_fallback_block():
    """A real PySpark call already captured by visit_Assign as an
    "assignment" block must not ALSO produce an overlapping fallback_call
    block for the same line."""
    types = _block_types('df = spark.read.parquet("/data")')
    assert types == ["assignment"]


def test_sql_string_block_does_not_also_get_a_fallback_block():
    """spark.sql(...) already produces two blocks pre-existing on main
    (method_chain + sql). This fix must not add a third fallback_call."""
    types = _block_types('spark.sql("SELECT 1")')
    assert "fallback_call" not in types
    assert types == ["method_chain", "sql"]


# --------------------------------------------------------------------------- #
# End-to-end repros: AWS Glue and Azure Synapse code proven missing on main,
# now covered, and confirmed to flow through the real detection sources.
# --------------------------------------------------------------------------- #

_GLUE_SNIPPET = """import sys
from awsglue.transforms import *
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from awsglue.context import GlueContext
from awsglue.job import Job

args = getResolvedOptions(sys.argv, ["JOB_NAME"])
sc = SparkContext()
glueContext = GlueContext(sc)
spark = glueContext.spark_session
job = Job(glueContext)
job.init(args["JOB_NAME"], args)

datasource0 = glueContext.create_dynamic_frame.from_catalog(database = "my_db", table_name = "my_table")
applymapping1 = ApplyMapping.apply(frame = datasource0, mappings = [("col1", "string", "col1", "string")])
glueContext.write_dynamic_frame.from_options(frame = applymapping1, connection_type = "s3", connection_options = {"path": "s3://bucket/path"}, format = "parquet")
job.commit()
"""

_SYNAPSE_SNIPPET = """from pyspark.sql import SparkSession
from notebookutils import mssparkutils

spark = SparkSession.builder.getOrCreate()

mssparkutils.fs.mount("abfss://container@account.dfs.core.windows.net/path", "/mounted")
secret_val = mssparkutils.credentials.getSecret("keyvault", "secretname")
mssparkutils.notebook.run("ChildNotebook", 90, {"param": "value"})

df = spark.read.parquet("/mounted/data")
df.write.mode("overwrite").saveAsTable("target_table")

mssparkutils.notebook.exit("success")
"""


def _line_coverage(src: str) -> dict[int, str | None]:
    """``{line_number: block_type or None}`` for every source line."""
    blocks = _blocks(src)
    n_lines = len(src.splitlines())
    coverage: dict[int, str | None] = {i: None for i in range(1, n_lines + 1)}
    for b in blocks:
        for line in range(b.line_start, b.line_end + 1):
            if line in coverage:
                coverage[line] = b.block_type
    return coverage


def test_aws_glue_s3_write_line_was_uncovered_before_fix_now_covered():
    """The bare glueContext.write_dynamic_frame.from_options(...) S3 write
    was proven missing on main; must now be covered."""
    coverage = _line_coverage(_GLUE_SNIPPET)
    write_line_no = next(
        i for i, line in enumerate(_GLUE_SNIPPET.splitlines(), 1)
        if "write_dynamic_frame.from_options" in line
    )
    assert coverage[write_line_no] == "fallback_call", (
        f"line {write_line_no} (the Glue S3 write) must now be covered by a "
        f"fallback_call block; got {coverage[write_line_no]!r}"
    )


def test_aws_glue_job_lifecycle_calls_now_covered():
    coverage = _line_coverage(_GLUE_SNIPPET)
    for needle in ("job.init(", "job.commit()"):
        line_no = next(
            i for i, line in enumerate(_GLUE_SNIPPET.splitlines(), 1) if needle in line
        )
        assert coverage[line_no] == "fallback_call", f"{needle!r} must be covered"


def test_aws_glue_fallback_block_fires_kb_rules():
    """The S3-write fallback block, run through the real trigger KB, must
    produce real matches -- proving it's not just a stub block."""
    blocks = _blocks(_GLUE_SNIPPET)
    write_block = next(
        b for b in blocks
        if b.block_type == "fallback_call" and "write_dynamic_frame.from_options" in b.code
    )
    kb = TriggerKB.load()
    matches = kb.detect(write_block.code)
    anchors = {m.anchor for m in matches}
    assert "external cloud storage path" in anchors, (
        "the s3:// path in the now-covered fallback block must trigger the "
        "cloud-storage-path KB rule"
    )


def test_aws_glue_fallback_block_flows_through_process_single_block():
    """Confirms the fallback block survives _process_single_block, the same
    function every other block goes through, so it will end up in
    analysis.json -- not just get a raw TriggerKB.detect() hit."""
    from scos_session import build_rag

    blocks = _blocks(_GLUE_SNIPPET)
    write_block = next(
        b for b in blocks
        if b.block_type == "fallback_call" and "write_dynamic_frame.from_options" in b.code
    )
    api_compat, _ = load_api_compatibility(_DATA)
    rag = build_rag(None, "trigger")
    rdd_result, block_data = _process_single_block(
        write_block, rag, api_compat, Path("glue_job.py"), 0.55, None
    )
    assert rdd_result is None
    assert block_data is not None
    assert block_data["matching_patterns"], "expected at least one KB match"


def test_azure_synapse_mount_line_was_uncovered_before_fix_now_covered():
    """The bare mssparkutils.fs.mount(...) call was proven missing on
    main; must now be covered."""
    coverage = _line_coverage(_SYNAPSE_SNIPPET)
    mount_line_no = next(
        i for i, line in enumerate(_SYNAPSE_SNIPPET.splitlines(), 1)
        if "mssparkutils.fs.mount" in line
    )
    assert coverage[mount_line_no] == "fallback_call"


def test_azure_synapse_notebook_lifecycle_calls_now_covered():
    coverage = _line_coverage(_SYNAPSE_SNIPPET)
    for needle in ("mssparkutils.notebook.run(", "mssparkutils.notebook.exit("):
        line_no = next(
            i for i, line in enumerate(_SYNAPSE_SNIPPET.splitlines(), 1) if needle in line
        )
        assert coverage[line_no] == "fallback_call", f"{needle!r} must be covered"


def test_azure_synapse_fallback_block_fires_kb_rules():
    blocks = _blocks(_SYNAPSE_SNIPPET)
    mount_block = next(
        b for b in blocks if b.block_type == "fallback_call" and "fs.mount" in b.code
    )
    kb = TriggerKB.load()
    matches = kb.detect(mount_block.code)
    anchors = {m.anchor for m in matches}
    assert "external cloud storage path" in anchors


def test_azure_synapse_genuine_spark_lines_unaffected():
    """The genuine PySpark lines in the snippet must keep their original
    block types, not get reclassified as fallback_call."""
    coverage = _line_coverage(_SYNAPSE_SNIPPET)
    lines = _SYNAPSE_SNIPPET.splitlines()
    spark_session_line = next(i for i, l in enumerate(lines, 1) if "SparkSession.builder" in l)
    read_line = next(i for i, l in enumerate(lines, 1) if "spark.read.parquet" in l)
    write_line = next(i for i, l in enumerate(lines, 1) if "saveAsTable" in l)
    assert coverage[spark_session_line] == "assignment"
    assert coverage[read_line] == "assignment"
    assert coverage[write_line] == "method_chain"
