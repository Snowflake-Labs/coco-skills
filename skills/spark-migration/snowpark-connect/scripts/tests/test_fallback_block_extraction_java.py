"""Tests for the fallback call sweep in ``analyze_java``.

Vendor SDK calls like AWS Glue's ``GlueContext`` / ``Job`` APIs don't match
any ``spark_keyword`` in ``_extract_java_blocks_from_source`` and were
silently dropped before the fallback sweep was added. These tests confirm they
are now covered as ``fallback_call`` blocks, and that existing Spark blocks are
not duplicated.

Run from the ``snowpark-connect/`` directory:

    pytest scripts/tests/test_fallback_block_extraction_java.py
"""
from __future__ import annotations

import pytest

from analyze_java import (  # noqa: E402
    JavaCodeBlock,
    _collect_fallback_call_blocks_java,
    _extract_java_blocks_from_source,
)


def _blocks(src: str) -> list[JavaCodeBlock]:
    return _extract_java_blocks_from_source(src)


def _block_types(src: str) -> list[str]:
    return [b.block_type for b in _blocks(src)]


# --------------------------------------------------------------------------- #
# Vendor SDK calls now produce a fallback_call block.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "code",
    [
        "job.commit();",
        'glueContext.write_dynamic_frame.from_options(frame, "s3", opts, "parquet");',
        "job.init(jobName, glueContext, args);",
    ],
)
def test_bare_vendor_sdk_call_produces_fallback_block(code: str):
    """Calls to Glue's job/glueContext must now be covered by a fallback_call block."""
    blocks = _blocks(code)
    assert len(blocks) == 1
    assert blocks[0].block_type == "fallback_call"
    assert blocks[0].code == code


# --------------------------------------------------------------------------- #
# Existing Spark blocks are NOT duplicated.
# --------------------------------------------------------------------------- #


def test_real_spark_statement_not_duplicated():
    """A genuine Spark call already captured as a statement block must not
    also produce a fallback_call block for the same lines."""
    types = _block_types('Dataset<Row> df = spark.read().parquet("/data");')
    assert "fallback_call" not in types
    assert types == ["statement"]


def test_spark_sql_call_not_duplicated():
    """spark.sql(...) captured as a statement must not also get a fallback."""
    types = _block_types('Dataset<Row> result = spark.sql("SELECT 1");')
    assert types == ["statement"]


# --------------------------------------------------------------------------- #
# Continuation lines collapse into one block.
# --------------------------------------------------------------------------- #


def test_chained_vendor_call_collapses_to_one_block():
    src = (
        "glueContext.write_dynamic_frame\n"
        "  .from_options(frame,\n"
        '               "s3", opts, "parquet");'
    )
    blocks = _blocks(src)
    fallback = [b for b in blocks if b.block_type == "fallback_call"]
    assert len(fallback) == 1
    assert fallback[0].line_start == 1
    assert fallback[0].line_end == 3


# --------------------------------------------------------------------------- #
# Lines with no method call produce no fallback block.
# --------------------------------------------------------------------------- #


def test_plain_value_assignment_no_fallback():
    assert _blocks("int x = 42;") == []


def test_comment_only_no_fallback():
    assert _blocks("// just a comment") == []


# --------------------------------------------------------------------------- #
# Cell-id propagation.
# --------------------------------------------------------------------------- #


def test_fallback_block_carries_cell_id():
    blocks = _extract_java_blocks_from_source("job.commit();", cell_id=3)
    fallback = [b for b in blocks if b.block_type == "fallback_call"]
    assert len(fallback) == 1
    assert fallback[0].cell_id == 3


# --------------------------------------------------------------------------- #
# End-to-end repro: AWS Glue Java snippet.
# --------------------------------------------------------------------------- #

_GLUE_JAVA_SNIPPET = """\
import com.amazonaws.services.glue.GlueContext;
import com.amazonaws.services.glue.util.Job;
import org.apache.spark.SparkContext;
import org.apache.spark.api.java.JavaSparkContext;

SparkContext sc = new SparkContext();
GlueContext glueContext = new GlueContext(sc);
SparkSession spark = glueContext.getSparkSession();
Job.init(args.get("JOB_NAME"), glueContext, args);

DynamicFrame datasource = glueContext.getCatalogSource(
    "my_db", "my_table", "", "", null, null
).getDynamicFrame();

glueContext.write_dynamic_frame.from_options(
    datasource, "s3", opts, "parquet"
);
job.commit();
"""


def _line_coverage(src: str) -> dict[int, str | None]:
    blocks = _blocks(src)
    n = len(src.splitlines())
    coverage: dict[int, str | None] = {i: None for i in range(1, n + 1)}
    for b in blocks:
        for ln in range(b.line_start, b.line_end + 1):
            if ln in coverage:
                coverage[ln] = b.block_type
    return coverage


def test_glue_java_write_line_now_covered():
    """The bare glueContext.write_dynamic_frame.from_options(...) Java call
    was previously missed; it must now be covered by a fallback_call block."""
    coverage = _line_coverage(_GLUE_JAVA_SNIPPET)
    write_line = next(
        i
        for i, line in enumerate(_GLUE_JAVA_SNIPPET.splitlines(), 1)
        if "write_dynamic_frame.from_options" in line
    )
    assert coverage[write_line] == "fallback_call", (
        f"line {write_line} (Glue write) must be covered; got {coverage[write_line]!r}"
    )


def test_glue_java_job_commit_now_covered():
    coverage = _line_coverage(_GLUE_JAVA_SNIPPET)
    commit_line = next(
        i
        for i, line in enumerate(_GLUE_JAVA_SNIPPET.splitlines(), 1)
        if "job.commit()" in line
    )
    assert coverage[commit_line] == "fallback_call"
