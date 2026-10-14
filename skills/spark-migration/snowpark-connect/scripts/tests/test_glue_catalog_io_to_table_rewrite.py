"""Unit tests for ``glue_catalog_io_to_table_rewrite``.

Run from the ``snowpark-connect/`` directory:
    pytest scripts/tests/test_glue_catalog_io_to_table_rewrite.py
"""
from __future__ import annotations

import textwrap
from pathlib import Path

from recipes import _common

_RECIPES_DIR = Path(__file__).resolve().parents[1] / "recipes"
_NAME = "glue_catalog_io_to_table_rewrite"


def _apply(source: str):
    source = textwrap.dedent(source).lstrip("\n")
    return _common.load_recipe_module(str(_RECIPES_DIR / _NAME)).apply(
        source, file="t.py"
    )


def _code(source: str) -> str:
    """Strip comment lines so assertions target code, not SCOS markers."""
    return "\n".join(l for l in source.splitlines() if not l.lstrip().startswith("#"))


# Every read shape that MUST be rewritten (used by the normalization
# regression test below as well as the individual cases).
_READ_SHAPES = [
    'DyF = glueContext.create_dynamic_frame.from_catalog(database="db", table_name="tbl")\n',
    "DyF = glueContext.create_dynamic_frame.from_catalog(database=db, table_name=tbl)\n",
    'DyF = glueContext.create_dynamic_frame_from_catalog(database="db", table_name="tbl")\n',
    'DyF = glueContext.create_dynamic_frame.from_options(database="db", table_name="tbl")\n',
    'DyF = gc.create_dynamic_frame.from_catalog(database=args["D"], table_name=args["T"])\n',
    (
        "DyF = glueContext.create_dynamic_frame.from_catalog(\n"
        '    database=db, table_name=tbl, transformation_ctx="ctx",\n'
        "    additional_options={"
        '"mergeSchema": "true"})\n'
    ),
]


# ---------------------------------------------------------------------------
# Read path — positive
# ---------------------------------------------------------------------------
def test_read_literal_database_and_table_uses_plain_string():
    src = 'DyF = glueContext.create_dynamic_frame.from_catalog(database="db", table_name="tbl")\n'
    res = _apply(src)
    code = _code(res.source)
    assert 'DyF = glueContext.read.table("db.tbl")' in code
    assert "create_dynamic_frame" not in code
    assert "SPRKCNTPY3602-IO" in res.source
    assert _NAME + ":" in res.source
    assert len(res.edits) == 1
    compile(res.source, "t.py", "exec")


def test_read_expression_database_and_table_uses_fstring():
    src = "DyF = glueContext.create_dynamic_frame.from_catalog(database=db, table_name=tbl)\n"
    code = _code(_apply(src).source)
    assert 'DyF = glueContext.read.table(f"{db}.{tbl}")' in code
    compile(code, "t.py", "exec")


def test_read_subscript_args_uses_single_quoted_fstring():
    src = (
        "DyF = glueContext.create_dynamic_frame.from_catalog("
        'database=args["INPUT_DATABASE"], table_name=args["SRC_TABLE"])\n'
    )
    res = _apply(src)
    code = _code(res.source)
    assert (
        "glueContext.read.table(f'{args[\"INPUT_DATABASE\"]}.{args[\"SRC_TABLE\"]}')"
        in code
    )
    # must still be valid Python (f-string quoting is the trap here)
    compile(res.source, "t.py", "exec")


def test_read_underscore_variant_is_rewritten():
    src = 'DyF = glueContext.create_dynamic_frame_from_catalog(database="d", table_name="t")\n'
    code = _code(_apply(src).source)
    assert 'DyF = glueContext.read.table("d.t")' in code
    assert "create_dynamic_frame_from_catalog" not in code


def test_read_from_options_with_catalog_identity_is_rewritten():
    src = 'DyF = glueContext.create_dynamic_frame.from_options(database="d", table_name="t")\n'
    code = _code(_apply(src).source)
    assert 'DyF = glueContext.read.table("d.t")' in code


def test_read_preserves_actual_receiver_not_hardcoded_spark():
    src = 'DyF = gc.create_dynamic_frame.from_catalog(database="d", table_name="t")\n'
    code = _code(_apply(src).source)
    assert 'DyF = gc.read.table("d.t")' in code
    assert "spark.read.table" not in code


# ---------------------------------------------------------------------------
# THE regression test: lowercase normalization is the silent-data-loss guard
# ---------------------------------------------------------------------------
def test_lowercase_normalization_emitted_on_every_successful_read_rewrite():
    """G2's lowercase normalization is MANDATORY. The Glue Data Catalog exposes
    lowercase column names while a native Snowflake read returns uppercase;
    without this line case-sensitive downstream logic silently drops columns
    (the validated workload lost its primary key this way). Assert it is
    present for every read shape the recipe claims to rewrite."""
    for src in _READ_SHAPES:
        res = _apply(src)
        code = _code(res.source)
        assert "read.table(" in code, f"not rewritten at all: {src!r}"
        assert (
            "DyF = DyF.toDF(*[c.lower() for c in DyF.columns])" in code
        ), f"MISSING lowercase normalization for: {src!r}"
        # the normalization must come AFTER the read, not before
        assert code.index("read.table(") < code.index(".toDF(")
        compile(res.source, "t.py", "exec")


def test_normalization_uses_the_actual_assignment_target_name():
    src = 'silver = glueContext.create_dynamic_frame.from_catalog(database="d", table_name="t")\n'
    code = _code(_apply(src).source)
    assert "silver = silver.toDF(*[c.lower() for c in silver.columns])" in code


# ---------------------------------------------------------------------------
# Read path — dropped kwargs
# ---------------------------------------------------------------------------
def test_transformation_ctx_dropped_with_bookmark_todo():
    src = (
        "DyF = glueContext.create_dynamic_frame.from_catalog("
        'database="d", table_name="t", transformation_ctx="ctx")\n'
    )
    res = _apply(src)
    code = _code(res.source)
    assert "transformation_ctx" not in code
    assert "SPRKCNTPY3606-Error" in res.source
    assert "bookmark" in res.source
    assert "G8" in res.source
    assert 'DyF = glueContext.read.table("d.t")' in code


def test_additional_and_format_options_dropped_with_warning():
    src = (
        "DyF = glueContext.create_dynamic_frame.from_catalog("
        'database="d", table_name="t", additional_options={"mergeSchema": "true"},'
        ' format_options={"withHeader": True})\n'
    )
    res = _apply(src)
    code = _code(res.source)
    assert "additional_options" not in code and "format_options" not in code
    assert "SPRKCNTPY3602-Warning" in res.source
    assert "'additional_options='" in res.source
    assert "'format_options='" in res.source
    compile(res.source, "t.py", "exec")


def test_no_warning_when_no_glue_specific_kwargs():
    src = 'DyF = glueContext.create_dynamic_frame.from_catalog(database="d", table_name="t")\n'
    res = _apply(src)
    assert "SCOS-WARN" not in res.source
    assert "SPRKCNTPY3606" not in res.source


# ---------------------------------------------------------------------------
# Read path — conservative TODOs (code left unchanged)
# ---------------------------------------------------------------------------
def test_non_name_assignment_target_is_todo_not_rewritten():
    """A tuple/attribute/subscript target means the normalization statement is
    unrepresentable, so the recipe must refuse to rewrite."""
    for src in (
        'self.dyf = glueContext.create_dynamic_frame.from_catalog(database="d", table_name="t")\n',
        'frames["a"] = glueContext.create_dynamic_frame.from_catalog(database="d", table_name="t")\n',
        'a, b = glueContext.create_dynamic_frame.from_catalog(database="d", table_name="t")\n',
    ):
        res = _apply(src)
        code = _code(res.source)
        assert "create_dynamic_frame.from_catalog" in code, src
        assert "read.table" not in code, src
        assert "SCOS: TODO" in res.source
        assert "toDF" not in code
        assert len(res.edits) == 1


def test_chained_todf_on_catalog_read_is_todo():
    src = 'df = glueContext.create_dynamic_frame.from_catalog(database="d", table_name="t").toDF()\n'
    res = _apply(src)
    assert "create_dynamic_frame.from_catalog" in _code(res.source)
    assert "read.table" not in _code(res.source)
    assert "SCOS: TODO" in res.source


def test_read_without_database_or_table_name_is_todo():
    src = (
        "DyF = glueContext.create_dynamic_frame.from_options("
        'connection_type="s3", connection_options={"paths": ["s3://b/p"]})\n'
    )
    res = _apply(src)
    code = _code(res.source)
    assert "create_dynamic_frame.from_options" in code
    assert "read.table" not in code
    assert "SCOS: TODO - [SPRKCNTPY3602-IO]" in res.source
    assert len(res.edits) == 1


def test_positional_table_identity_is_todo():
    src = 'DyF = glueContext.create_dynamic_frame.from_catalog("d", "t")\n'
    res = _apply(src)
    assert "read.table" not in _code(res.source)
    assert "SCOS: TODO" in res.source


# ---------------------------------------------------------------------------
# Write path
# ---------------------------------------------------------------------------
def test_write_from_catalog_to_saveastable():
    src = (
        "glueContext.write_dynamic_frame.from_catalog("
        'frame=out, database="d", table_name="t")\n'
    )
    res = _apply(src)
    code = _code(res.source)
    assert 'out.write.mode("append").saveAsTable("d.t")' in code
    assert "write_dynamic_frame" not in code
    assert "SPRKCNTPY3609-IO" in res.source
    assert "append" in res.source
    assert len(res.edits) == 1
    compile(res.source, "t.py", "exec")


def test_write_underscore_and_jdbc_variants():
    for method in (
        "write_dynamic_frame_from_catalog",
        "write_dynamic_frame_from_options",
        "write_dynamic_frame_from_jdbc_conf",
    ):
        src = f'glueContext.{method}(frame=out, database="d", table_name="t")\n'
        code = _code(_apply(src).source)
        assert 'out.write.mode("append").saveAsTable("d.t")' in code, method

    src = 'glueContext.write_dynamic_frame.from_jdbc_conf(frame=out, database=d, table_name=t)\n'
    code = _code(_apply(src).source)
    assert 'out.write.mode("append").saveAsTable(f"{d}.{t}")' in code


def test_write_transformation_ctx_dropped_with_bookmark_todo():
    src = (
        "glueContext.write_dynamic_frame.from_catalog("
        'frame=out, database="d", table_name="t", transformation_ctx="ctx")\n'
    )
    res = _apply(src)
    assert "transformation_ctx" not in _code(res.source)
    assert "SPRKCNTPY3606-Error" in res.source


def test_write_bookmark_todo_wording_is_write_specific():
    """A sink's transformation_ctx is the bookmark COMMIT handle, not a read
    cursor: the write path must not claim the read became a full reprocess."""
    src = (
        "glueContext.write_dynamic_frame.from_catalog("
        'frame=out, database="d", table_name="t", transformation_ctx="ctx")\n'
    )
    res = _apply(src)
    assert "SPRKCNTPY3606-Error" in res.source
    assert "this read is now a FULL reprocess" not in res.source
    assert "RE-EMIT" in res.source
    assert "MERGE/dedup key" in res.source
    assert "no per-run write checkpoint" in res.source
    assert "G8" in res.source
    compile(res.source, "t.py", "exec")


def test_read_bookmark_todo_keeps_read_specific_wording():
    src = (
        "DyF = glueContext.create_dynamic_frame.from_catalog("
        'database="d", table_name="t", transformation_ctx="ctx")\n'
    )
    res = _apply(src)
    assert "this read is now a FULL reprocess" in res.source
    assert "RE-EMIT" not in res.source
    assert "G8" in res.source


def test_write_bound_to_variable_is_todo():
    src = (
        "res = glueContext.write_dynamic_frame.from_catalog("
        'frame=out, database="d", table_name="t")\n'
    )
    res = _apply(src)
    assert "write_dynamic_frame" in _code(res.source)
    assert "saveAsTable" not in _code(res.source)
    assert "SCOS: TODO - [SPRKCNTPY3609-IO]" in res.source


def test_write_without_frame_is_todo():
    src = 'glueContext.write_dynamic_frame.from_catalog(database="d", table_name="t")\n'
    res = _apply(src)
    assert "saveAsTable" not in _code(res.source)
    assert "SCOS: TODO - [SPRKCNTPY3609-IO]" in res.source


def test_write_from_options_with_connection_options_is_todo():
    src = (
        "glueContext.write_dynamic_frame.from_options(frame=out, "
        'connection_type="s3", connection_options={"path": "s3://b/p"})\n'
    )
    res = _apply(src)
    assert "write_dynamic_frame.from_options" in _code(res.source)
    assert "saveAsTable" not in _code(res.source)
    assert "SCOS: TODO" in res.source


# ---------------------------------------------------------------------------
# Negative cases — byte-identical, no edits
# ---------------------------------------------------------------------------
def test_plain_pyspark_untouched():
    for src in (
        'df = spark.read.table("t")\n',
        'df.write.saveAsTable("t")\n',
        'df.write.mode("append").saveAsTable("d.t")\n',
        'df = spark.read.format("parquet").load(p)\n',
        "df = DynamicFrame.fromDF(pdf, gc, \"ctx\")\n",
        "sf = dyf.toDF()\n",
    ):
        res = _apply(src)
        assert res.source == src, src
        assert res.edits == [], src


def test_multi_statement_module_only_touches_target_lines():
    src = """
    df = spark.read.table("keep")
    DyF = glueContext.create_dynamic_frame.from_catalog(database="d", table_name="t")
    df.select("a").show()
    """
    res = _apply(src)
    code = _code(res.source)
    assert 'df = spark.read.table("keep")' in code
    assert 'df.select("a").show()' in code
    assert 'DyF = glueContext.read.table("d.t")' in code
    assert len(res.edits) == 1


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------
def test_idempotent_on_rewrite():
    src = (
        "DyF = glueContext.create_dynamic_frame.from_catalog("
        'database="d", table_name="t", transformation_ctx="ctx")\n'
    )
    once = _apply(src).source
    second = _apply(once)
    twice = second.source
    assert once == twice
    assert second.edits == []


def test_idempotent_on_todo():
    src = 'self.dyf = glueContext.create_dynamic_frame.from_catalog(database="d", table_name="t")\n'
    once = _apply(src).source
    second = _apply(once)
    assert once == second.source
    assert second.edits == []


def test_output_always_compiles():
    for src in _READ_SHAPES + [
        'glueContext.write_dynamic_frame.from_catalog(frame=out, database="d", table_name="t")\n',
        'self.dyf = glueContext.create_dynamic_frame.from_catalog(database="d", table_name="t")\n',
        'DyF = glueContext.create_dynamic_frame.from_catalog("d", "t")\n',
    ]:
        res = _apply(src)
        compile(res.source, "t.py", "exec")
