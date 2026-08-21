"""Unit tests for ``glue_applymapping_to_select_rewrite`` (Glue recipe G4).

Run from the ``snowpark-connect/`` directory:
    pytest scripts/tests/test_glue_applymapping_to_select_rewrite.py
"""
from __future__ import annotations

import textwrap
from pathlib import Path

from recipes import _common

_RECIPES_DIR = Path(__file__).resolve().parents[1] / "recipes"
_NAME = "glue_applymapping_to_select_rewrite"
_F_IMPORT = "from pyspark.sql import functions as F"


def _apply(source: str):
    source = textwrap.dedent(source).lstrip("\n")
    return _common.load_recipe_module(str(_RECIPES_DIR / _NAME)).apply(source, file="t.py")


def _code(source: str) -> str:
    """Strip comment lines so assertions target code, not SCOS markers."""
    return "\n".join(l for l in source.splitlines() if not l.lstrip().startswith("#"))


_BASIC = (
    'Out = ApplyMapping.apply(frame=DyF, mappings=[\n'
    '    ("src_id", "string", "id", "bigint"),\n'
    '    ("src_name", "string", "name", "string"),\n'
    '])\n'
)


# ---------------------------------------------------------------------------
# positive cases
# ---------------------------------------------------------------------------
def test_keyword_form_rewritten_to_select():
    res = _apply(_BASIC)
    code = _code(res.source)
    assert 'DyF.select(F.col("`src_id`").cast("long").alias("id")' in code
    assert 'F.col("`src_name`").cast("string").alias("name")' in code
    assert "ApplyMapping" not in code
    assert "[SPRKCNTPY3603-Fixed]" in res.source
    assert _NAME + ":" in res.source
    assert len(res.edits) == 1
    compile(res.source, "t.py", "exec")


def test_positional_form_rewritten():
    src = 'Out = ApplyMapping.apply(DyF, [("a", "string", "b", "string")])\n'
    code = _code(_apply(src).source)
    assert 'DyF.select(F.col("`a`").cast("string").alias("b"))' in code


def test_mixed_form_rewritten():
    src = 'Out = ApplyMapping.apply(DyF, mappings=[("a", "string", "b", "int")])\n'
    code = _code(_apply(src).source)
    assert 'DyF.select(F.col("`a`").cast("int").alias("b"))' in code


def test_dotted_receiver_rewritten():
    src = (
        "Out = awsglue.transforms.ApplyMapping.apply(\n"
        '    frame=DyF, mappings=[("a", "string", "b", "bigint")])\n'
    )
    code = _code(_apply(src).source)
    assert 'DyF.select(F.col("`a`").cast("long").alias("b"))' in code
    assert "ApplyMapping" not in code


def test_tuple_literal_mappings_container_rewritten():
    src = 'Out = ApplyMapping.apply(frame=DyF, mappings=(("a", "string", "b", "date"),))\n'
    code = _code(_apply(src).source)
    assert 'F.col("`a`").cast("date").alias("b")' in code


def test_expression_frame_receiver_preserved():
    src = (
        "Out = ApplyMapping.apply(\n"
        '    frame=df.toDF(), mappings=[("a", "string", "b", "string")])\n'
    )
    code = _code(_apply(src).source)
    assert 'df.toDF().select(F.col("`a`").cast("string").alias("b"))' in code


# ---------------------------------------------------------------------------
# the projection guard -- the whole point of G4
# ---------------------------------------------------------------------------
def test_projection_semantics_no_withcolumnrenamed_and_unmapped_dropped():
    """ApplyMapping is a PROJECTION: a withColumnRenamed/withColumn chain would
    keep unmapped columns and silently change the output schema."""
    src = (
        "Out = ApplyMapping.apply(frame=DyF, mappings=[\n"
        '    ("src_id", "string", "id", "bigint"),\n'
        '    ("keep_me", "string", "kept", "string"),\n'
        "])\n"
    )
    res = _apply(src)
    out = res.source
    code = _code(out)
    assert "withColumnRenamed" not in out
    assert "withColumn" not in out
    # exactly one select projection, no chained per-column ops
    assert code.count(".select(") == 1
    # an unmapped column never appears in the output -- only mapped sources do
    assert "unmapped_col" not in out
    assert 'F.col("`src_id`")' in code and 'F.col("`keep_me`")' in code
    # the comment names the projection semantics
    assert "projection" in out


def test_unmapped_source_column_absent_from_output():
    src = (
        "Out = ApplyMapping.apply(frame=DyF, mappings=[\n"
        '    ("kept_col", "string", "kept", "string"),\n'
        "])\n"
    )
    out = _apply(src).source
    assert "kept_col" in out
    # a column that was never in the mapping list must not be projected
    assert "dropped_col" not in out


# ---------------------------------------------------------------------------
# backticks
# ---------------------------------------------------------------------------
def test_dotted_source_name_is_backticked():
    src = 'Out = ApplyMapping.apply(frame=DyF, mappings=[("a.b", "string", "ab", "string")])\n'
    code = _code(_apply(src).source)
    assert 'F.col("`a.b`").cast("string").alias("ab")' in code
    # never the un-backticked nested-field form
    assert 'F.col("a.b")' not in code


def test_source_name_with_space_is_backticked():
    src = (
        'Out = ApplyMapping.apply(frame=DyF, mappings=[("my col", "string", "my_col", "string")])\n'
    )
    code = _code(_apply(src).source)
    assert 'F.col("`my col`")' in code


def test_target_alias_is_not_backticked():
    src = 'Out = ApplyMapping.apply(frame=DyF, mappings=[("a", "string", "tgt", "string")])\n'
    code = _code(_apply(src).source)
    assert '.alias("tgt")' in code
    assert '.alias("`tgt`")' not in code


# ---------------------------------------------------------------------------
# Glue -> Spark type-name mapping
# ---------------------------------------------------------------------------
def test_every_type_name_mapping():
    cases = {
        "bigint": "long",
        "integer": "int",
        "null": "string",
        "string": "string",
        "boolean": "boolean",
        "double": "double",
        "float": "float",
        "short": "short",
        "byte": "byte",
        "decimal": "decimal",
        "timestamp": "timestamp",
        "date": "date",
    }
    for glue_type, spark_type in cases.items():
        src = (
            f'Out = ApplyMapping.apply(frame=DyF, mappings=[("a", "string", "b", "{glue_type}")])\n'
        )
        code = _code(_apply(src).source)
        assert f'.cast("{spark_type}")' in code, f"{glue_type} -> {spark_type}"


def test_type_mapping_is_case_insensitive():
    src = 'Out = ApplyMapping.apply(frame=DyF, mappings=[("a", "string", "b", "BigInt")])\n'
    assert '.cast("long")' in _code(_apply(src).source)


def test_parameterized_type_passes_through_unchanged():
    src = (
        'Out = ApplyMapping.apply(frame=DyF, mappings=[("a", "string", "b", "decimal(10,2)")])\n'
    )
    assert '.cast("decimal(10,2)")' in _code(_apply(src).source)


def test_unknown_type_passes_through_unchanged():
    src = 'Out = ApplyMapping.apply(frame=DyF, mappings=[("a", "string", "b", "binary")])\n'
    assert '.cast("binary")' in _code(_apply(src).source)


# ---------------------------------------------------------------------------
# F import injection
# ---------------------------------------------------------------------------
def test_f_import_injected_exactly_once():
    out = _apply(_BASIC).source
    assert out.count(_F_IMPORT) == 1
    compile(out, "t.py", "exec")


def test_f_import_not_duplicated_when_already_present():
    src = _F_IMPORT + "\n" + _BASIC
    out = _apply(src).source
    assert out.count(_F_IMPORT) == 1


def test_f_import_not_added_when_module_style_import_present():
    src = "import pyspark.sql.functions as F\n" + _BASIC
    out = _apply(src).source
    assert _F_IMPORT not in out
    assert out.count("as F") == 1


def test_f_import_injected_once_for_two_rewrites():
    src = (
        'A = ApplyMapping.apply(frame=d1, mappings=[("a", "string", "b", "bigint")])\n'
        'B = ApplyMapping.apply(frame=d2, mappings=[("c", "string", "d", "integer")])\n'
    )
    res = _apply(src)
    assert res.source.count(_F_IMPORT) == 1
    assert len(res.edits) == 2


def test_f_bound_to_something_else_falls_back_to_todo():
    src = "F = 3\n" + _BASIC
    res = _apply(src)
    code = _code(res.source)
    assert "ApplyMapping.apply(" in code  # unchanged
    assert ".select(" not in code
    assert "SCOS: TODO" in res.source
    assert _F_IMPORT not in res.source


# ---------------------------------------------------------------------------
# negative / TODO cases -- code must be unchanged
# ---------------------------------------------------------------------------
def _assert_code_unchanged_with_todo(src: str):
    src = textwrap.dedent(src).lstrip("\n")
    res = _apply(src)
    assert _code(res.source) == src.rstrip("\n"), res.source
    assert "SCOS: TODO" in res.source
    assert f"[SPRKCNTPY3603-Fixed] {_NAME}:" in res.source
    assert len(res.edits) == 1
    # the TODO spells out the required target shape
    assert "select" in res.source
    assert "PROJECTION" in res.source
    assert "backtick" in res.source
    assert "bigint->long" in res.source
    compile(res.source, "t.py", "exec")


def test_variable_mappings_is_todo():
    _assert_code_unchanged_with_todo(
        "Out = ApplyMapping.apply(frame=DyF, mappings=my_mappings)\n"
    )


def test_comprehension_mappings_is_todo():
    _assert_code_unchanged_with_todo(
        "Out = ApplyMapping.apply(frame=DyF, mappings=[(c, \"string\", c, \"string\") for c in cols])\n"
    )


def test_non_literal_tuple_member_is_todo():
    _assert_code_unchanged_with_todo(
        'Out = ApplyMapping.apply(frame=DyF, mappings=[(src, "string", "b", "string")])\n'
    )


def test_wrong_arity_tuple_is_todo():
    _assert_code_unchanged_with_todo(
        'Out = ApplyMapping.apply(frame=DyF, mappings=[("a", "string", "b")])\n'
    )


def test_call_mappings_is_todo():
    _assert_code_unchanged_with_todo(
        "Out = ApplyMapping.apply(frame=DyF, mappings=build_mappings(df))\n"
    )


def test_missing_mappings_is_todo():
    _assert_code_unchanged_with_todo("Out = ApplyMapping.apply(frame=DyF)\n")


def test_empty_literal_mappings_is_todo():
    _assert_code_unchanged_with_todo(
        "Out = ApplyMapping.apply(frame=DyF, mappings=[])\n"
    )


# ---------------------------------------------------------------------------
# must not fire at all
# ---------------------------------------------------------------------------
def test_benign_pyspark_untouched():
    src = 'df = spark.read.table("t")\ndf.select("a").show()\n'
    res = _apply(src)
    assert res.source == src and res.edits == []


def test_other_glue_transform_untouched():
    src = 'Out = ResolveChoice.apply(frame=DyF, choice="match_catalog")\n'
    res = _apply(src)
    assert res.source == src and res.edits == []


def test_unrelated_apply_untouched():
    src = "out = pdf.apply(fn, axis=1)\n"
    res = _apply(src)
    assert res.source == src and res.edits == []


# ---------------------------------------------------------------------------
# idempotency
# ---------------------------------------------------------------------------
def test_idempotent_rewrite():
    once = _apply(_BASIC).source
    twice = _apply(once).source
    assert once == twice
    assert _apply(once).edits == []


def test_idempotent_todo():
    src = "Out = ApplyMapping.apply(frame=DyF, mappings=my_mappings)\n"
    once = _apply(src).source
    twice = _apply(once).source
    assert once == twice
    assert _apply(once).edits == []
